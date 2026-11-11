import asyncio
import os
import random
import re
import threading
import time
from typing import Any

import json_repair
from bs4 import BeautifulSoup
from curl_cffi import requests
from curl_cffi.requests import AsyncSession

import fetch_proxies
from host_strategy import Strategy, host_strategy
from log import logger

_tls = threading.local()


def _get_tls_session() -> requests.Session:
    """Get or create a thread-local persistent Session for connection pooling."""
    if not hasattr(_tls, "session") or _tls.session is None:
        _tls.session = requests.Session(impersonate="chrome146")
    return _tls.session


class DataFetcher:
    """Fetch data from a Funnelback search host or from published course content pages.

    By default, the DataFetcher uses `BASE_URL` (Funnelback search) and the endpoint
    is expected to be a query string that starts with `?`.

    Pass `use_class_url=True` to use `BASE_INFO_URL` instead and treat the endpoint
    as a path under the course content base URL.
    """

    BASE_URL = "https://uosa-search.funnelback.squiz.cloud/s/search.html"
    BASE_INFO_URL = "https://adelaide.edu.au"
    PROXY_FILE = "src/working_proxies.txt"

    _WARP_ACTIVE = os.getenv("USE_WARP", "").lower() in ("true", "1", "yes")
    _WARP_PORT = os.getenv("WARP_SOCKS_PORT", "40000")
    _WARP_PROXIES = {
        "http": f"socks5h://127.0.0.1:{_WARP_PORT}",
        "https": f"socks5h://127.0.0.1:{_WARP_PORT}",
    }

    DEFAULT_TIMEOUT = 3.0
    MAX_RETRIES = 3
    MAX_PROXY_FAILURES = 3
    _proxies = None
    _proxy_failures = {}
    _active_proxies = set()
    _proxy_lock = threading.Lock()
    _funnelback_semaphore = threading.BoundedSemaphore(12)
    _direct_blocked = False
    _direct_blocked_until = 0.0
    _direct_blocked_permanently = False

    # Non-blocking background proxy refresh attributes
    _refill_thread = None
    _refill_wake_event = threading.Event()
    _refill_stop_event = threading.Event()
    _refill_lock = threading.Lock()  # Single-flight refill guard
    _last_refill_start_time = 0.0
    _refill_started = False

    @classmethod
    def _should_attempt_direct(cls) -> bool:
        """Check if direct connection should be attempted."""
        if os.getenv("GITHUB_ACTIONS") == "true" or os.getenv("CI") == "true":
            return False
        if cls._direct_blocked_permanently:
            return False
        if cls._direct_blocked:
            if (
                cls._direct_blocked_until > 0
                and time.time() >= cls._direct_blocked_until
            ):
                cls._direct_blocked = False
                cls._direct_blocked_until = 0.0
                return True
            return False
        return True

    @classmethod
    def _record_direct_failure(cls, status_code: int | None = None) -> None:
        """Record direct failure and set cooldown or permanent block."""
        cls._direct_blocked = True
        cls._direct_blocked_until = time.time() + 15.0
        if (
            status_code in (403, 429)
            or os.getenv("GITHUB_ACTIONS") == "true"
            or os.getenv("CI") == "true"
        ):
            cls._direct_blocked_permanently = True
            logger.warning(
                f"Direct connection 403/429 detected (HTTP {status_code}). "
                "Permanently routing through proxy layer."
            )
        else:
            logger.warning(
                f"Direct connection rate-limited/blocked by WAF (HTTP {status_code}). "
                "Pausing direct requests for 15s cooldown and trying proxies."
            )

    @classmethod
    def is_direct_blocked(cls) -> bool:
        """Check if direct connection is currently paused due to rate limiting or cooldown."""
        return not cls._should_attempt_direct()

    @staticmethod
    def _sanitise_for_log(value: Any) -> str:
        """Sanitise a value for safe logging to avoid log injection.

        Removes newlines and truncates long values.
        """
        s = str(value)
        s = s.replace("\r", "").replace("\n", "")
        if len(s) > 200:
            s = s[:200] + "...(truncated)"
        return s

    @classmethod
    def _is_warp_active(cls) -> bool:
        return cls._WARP_ACTIVE or os.getenv("USE_WARP", "").lower() in (
            "true",
            "1",
            "yes",
        )

    @classmethod
    def _get_warp_proxies(cls) -> dict:
        port = os.getenv("WARP_SOCKS_PORT", cls._WARP_PORT)
        return {
            "http": f"socks5h://127.0.0.1:{port}",
            "https": f"socks5h://127.0.0.1:{port}",
        }

    def __init__(
        self,
        endpoint: str = "",
        year: int | None = None,
        full_url: str | None = None,
        use_class_url: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
        course_code: str | None = None,
        use_proxy: bool = True,
    ) -> None:
        self.course_code = course_code or endpoint or ""
        self.endpoint = endpoint or course_code or ""
        self.year = year
        self.use_class_url = use_class_url
        self.timeout = timeout
        if full_url:
            self.url = full_url
        elif self.use_class_url:
            clean_endpoint = str(self.endpoint).strip()
            if clean_endpoint.startswith("/"):
                path = clean_endpoint
            else:
                slug = re.sub(
                    r"([a-zA-Z]+)\s*([0-9]+)", r"\1-\2", clean_endpoint
                ).lower()
                path = (
                    f"/study/courses/{self.year}/{slug}/"
                    if self.year
                    else f"/study/courses/{slug}/"
                )
            self.url = self.BASE_INFO_URL.rstrip("/") + path
        else:
            self.url = self.BASE_URL + self.endpoint
        self.data = None
        self.last_response = None
        self.last_used_proxy = None
        scraper_use_proxies = os.environ.get("SCRAPER_USE_PROXIES", "true").lower() in (
            "true",
            "1",
            "yes",
        )
        self.use_proxy = use_proxy and scraper_use_proxies

        # Load proxies globally if not already loaded and not using WARP
        if not self._is_warp_active():
            with DataFetcher._proxy_lock:
                if DataFetcher._proxies is None:
                    DataFetcher._proxies = self.load_proxies()

    @classmethod
    def get_working_proxy(cls) -> dict | None:
        """Helper to get a random working proxy dictionary."""
        fetcher = cls("", use_proxy=True)
        return fetcher.get_random_proxy()

    @classmethod
    def _load_proxies(cls) -> list:
        """Load proxies from file, checking candidate paths."""
        candidates = [
            cls.PROXY_FILE,
            "src/working_proxies.txt",
            "working_proxies.txt",
            os.path.join(os.path.dirname(__file__), "working_proxies.txt"),
        ]
        for path in candidates:
            if os.path.exists(path):
                try:
                    with open(path, "r") as file:
                        proxies = [line.strip() for line in file if line.strip()]
                        if proxies:
                            logger.info(f"Loaded {len(proxies)} proxies from {path}.")
                            return proxies
                except Exception as e:
                    logger.debug(f"Error reading proxies from {path}: {e}")
        logger.debug("No working proxies file found among candidates.")
        return []

    @classmethod
    def start_background_refresher(cls) -> None:
        """Start the single background daemon thread for non-blocking proxy refills."""
        with cls._proxy_lock:
            if cls._refill_started:
                return
            cls._refill_stop_event.clear()
            cls._refill_wake_event.clear()
            thread = threading.Thread(
                target=cls._background_refill_worker,
                name="ProxyPoolRefresher",
                daemon=True,
            )
            thread.start()
            cls._refill_thread = thread
            cls._refill_started = True
            logger.debug("Background proxy refresher thread started.")

    @classmethod
    def stop_background_refresher(cls) -> None:
        """Signal the background refresher thread to stop."""
        cls._refill_stop_event.set()
        cls._refill_wake_event.set()
        cls._refill_started = False

    @classmethod
    def trigger_refill_if_low(cls) -> None:
        """Check pool size and trigger the background refresher if below watermark."""
        if not cls._should_use_proxies():
            return
        low_watermark = int(os.environ.get("PROXY_LOW_WATERMARK", "100"))
        with cls._proxy_lock:
            pool_size = len(cls._proxies) if cls._proxies is not None else 0
        if pool_size < low_watermark:
            cls.start_background_refresher()
            cls._refill_wake_event.set()

    @classmethod
    def _should_use_proxies(cls) -> bool:
        return os.environ.get("SCRAPER_USE_PROXIES", "true").lower() in (
            "true",
            "1",
            "yes",
        )

    @classmethod
    def _background_refill_worker(cls) -> None:
        """Worker loop for background proxy discovery."""
        low_watermark = int(os.environ.get("PROXY_LOW_WATERMARK", "100"))
        refill_target = int(os.environ.get("PROXY_REFILL_TARGET", "150"))

        while not cls._refill_stop_event.is_set():
            # Wait until woken up or check periodically
            cls._refill_wake_event.wait(timeout=10.0)
            cls._refill_wake_event.clear()

            if cls._refill_stop_event.is_set():
                break

            with cls._proxy_lock:
                current_count = len(cls._proxies) if cls._proxies is not None else 0

            if current_count >= low_watermark:
                continue

            # Hard cooldown: minimum 30s between refill starts
            now = time.time()
            if now - cls._last_refill_start_time < 30.0:
                time.sleep(1.0)
                continue

            # Single-flight guard
            if not cls._refill_lock.acquire(blocking=False):
                continue

            try:
                cls._last_refill_start_time = time.time()
                needed = max(refill_target - current_count, 20)
                logger.warning(
                    f"Proxy pool low ({current_count} remaining). Refill started in background."
                )

                with cls._proxy_lock:
                    exclude = set(cls._proxies or []) | set(
                        k
                        for k, v in cls._proxy_failures.items()
                        if v >= cls.MAX_PROXY_FAILURES
                    )

                start_refill = time.time()
                discovered = fetch_proxies.discover_working_proxies(
                    target=needed,
                    stop_event=cls._refill_stop_event,
                    exclude=exclude,
                )
                duration = time.time() - start_refill

                if not discovered:
                    logger.warning(
                        "Background proxy refill found 0 new proxies. Backing off 60s."
                    )
                    # Backoff so we don't spin endlessly on exhausted public lists
                    cls._refill_stop_event.wait(timeout=60.0)
                    continue

                with cls._proxy_lock:
                    if cls._proxies is None:
                        cls._proxies = []
                    existing_set = set(cls._proxies)
                    added = 0
                    for p in discovered:
                        if p not in existing_set:
                            cls._proxies.append(p)
                            existing_set.add(p)
                            added += 1
                    total_pool = len(cls._proxies)
                    proxies_to_save = list(cls._proxies)

                fetch_proxies.save_working_proxies(
                    proxies_to_save, filename=cls.PROXY_FILE
                )
                logger.info(
                    f"Background proxy refill completed: added {added} proxies in {duration:.1f}s. "
                    f"Pool now has {total_pool} proxies."
                )
            except Exception as e:
                logger.error(f"Error in background proxy refill: {e}")
            finally:
                cls._refill_lock.release()

    def load_proxies(self) -> list:
        """Load proxies from file, checking candidate paths or environment."""
        return self._load_proxies()

    def get_random_proxy(self) -> dict | None:
        """Get a proxy from the loaded list, preferring idle proxies to prevent stampedes."""
        if not self.use_proxy:
            return None

        proxy_url = os.getenv("PROXY_URL")
        if proxy_url:
            clean_url = (
                proxy_url
                if proxy_url.startswith(("http://", "https://"))
                else f"http://{proxy_url}"
            )
            return {"http": clean_url, "https": clean_url}

        self.trigger_refill_if_low()

        with DataFetcher._proxy_lock:
            if not DataFetcher._proxies:
                DataFetcher._proxies = self.load_proxies()
                DataFetcher._proxy_failures.clear()

            if not DataFetcher._proxies:
                logger.warning("No proxies available. Proceeding without a proxy.")
                return None

            # Prefer proxies not currently in use by another thread to eliminate collisions and resets
            idle_proxies = [
                p for p in DataFetcher._proxies if p not in DataFetcher._active_proxies
            ]
            if idle_proxies:
                proxy = random.choice(idle_proxies)
            else:
                proxy = random.choice(DataFetcher._proxies)

            proxy_clean = proxy.replace("http://", "").replace("https://", "")
            DataFetcher._active_proxies.add(proxy_clean)

        http_val = (
            proxy
            if proxy.startswith("http://") or proxy.startswith("https://")
            else f"http://{proxy}"
        )
        return {
            "http": http_val,
            "https": http_val,
        }

    @classmethod
    def release_proxy(cls, proxy: dict | None) -> None:
        """Release an active proxy back to the idle pool."""
        if not proxy:
            return
        proxy_str = (
            proxy.get("http", "").replace("http://", "").replace("https://", "")
            if isinstance(proxy, dict)
            else str(proxy).replace("http://", "").replace("https://", "")
        )
        if not proxy_str:
            return
        with cls._proxy_lock:
            cls._active_proxies.discard(proxy_str)

    def report_proxy_failure(self, proxy: dict, fatal: bool = False) -> None:
        """Record a failure for a proxy. Remove it only if it exceeds MAX_PROXY_FAILURES or fatal is True."""
        if not proxy:
            return
        proxy_str = proxy.get("http", "").replace("http://", "").replace("https://", "")
        if not proxy_str:
            return
        should_trigger = False
        with DataFetcher._proxy_lock:
            count = DataFetcher._proxy_failures.get(proxy_str, 0) + 1
            DataFetcher._proxy_failures[proxy_str] = count
            if fatal or count >= self.MAX_PROXY_FAILURES:
                if DataFetcher._proxies and proxy_str in DataFetcher._proxies:
                    try:
                        DataFetcher._proxies.remove(proxy_str)
                        DataFetcher._active_proxies.discard(proxy_str)
                        logger.info(
                            f"Removed bad proxy: {proxy_str} (failures: {count}). Remaining: {len(DataFetcher._proxies)}"
                        )
                        should_trigger = True
                    except ValueError:
                        pass
            else:
                logger.debug(
                    f"Proxy {proxy_str} failure count: {count}/{self.MAX_PROXY_FAILURES}"
                )

        if should_trigger:
            self.trigger_refill_if_low()

    def report_proxy_success(self, proxy: dict) -> None:
        """Reset failure count for working proxy."""
        if not proxy:
            return
        proxy_str = proxy.get("http", "").replace("http://", "").replace("https://", "")
        if not proxy_str:
            return
        with DataFetcher._proxy_lock:
            DataFetcher._proxy_failures.pop(proxy_str, None)

    def remove_proxy(self, proxy: dict) -> None:
        """Explicitly remove a proxy (fatal failure)."""
        self.report_proxy_failure(proxy, fatal=True)

    def _process_response(self, response):
        """Process and store a successful response based on endpoint type."""
        self.last_response = response
        if not self.use_class_url:
            resp = json_repair.loads(response.text)
            if not isinstance(resp, dict):
                return None
            if resp.get("response", {}).get("resultPacket"):
                self.data = resp.get("response", {})
                return self.data
            elif resp.get("resultPacket"):
                self.data = resp
                return self.data
            return None
        else:
            soup = BeautifulSoup(response.content, "html.parser")
            main_tag = soup.find("main")
            text = main_tag.get_text() if main_tag else soup.get_text()
            h1_tag = soup.find("h1")
            h1_text = h1_tag.get_text().strip() if h1_tag else ""
            self.data = {
                "h1": h1_text,
                "data": re.sub(r"\n+", "\n", text),
                "html": response.text,
            }
            return self.data

    def _get_headers(self) -> dict:
        """Flexible headers depending on target endpoint (html page vs json api)."""
        if self.use_class_url:
            return {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.adelaide.edu.au/",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="146", "Google Chrome";v="146"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"macOS"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }
        else:
            return {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.adelaide.edu.au/",
                "Origin": "https://www.adelaide.edu.au",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "X-Requested-With": "XMLHttpRequest",
                "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="146", "Google Chrome";v="146"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"macOS"',
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "cross-site",
            }

    def _execute_http_get(
        self,
        url: str,
        headers: dict,
        timeout: float | tuple[float, float],
        proxy: dict | None = None,
    ):
        """Execute HTTP GET using persistent Session pooling, falling back to requests.get if mocked."""
        if hasattr(requests.get, "assert_called") or hasattr(
            requests.get, "_mock_return_value"
        ):
            if proxy:
                return requests.get(
                    url,
                    proxies=proxy,
                    headers=headers,
                    timeout=timeout,
                    impersonate="chrome146",
                )
            return requests.get(
                url,
                headers=headers,
                timeout=timeout,
                impersonate="chrome146",
            )
        sess = _get_tls_session()
        if proxy:
            return sess.get(url, proxies=proxy, headers=headers, timeout=timeout)
        return sess.get(url, headers=headers, timeout=timeout)

    def get(self, max_retries: int = 3) -> dict | None:
        """Fetch data from the API, selecting direct vs proxy based on per-host strategy and circuit breaker."""
        logger.debug("Fetching %s...", self._sanitise_for_log(self.endpoint))
        if self.data is not None:
            return self.data

        if not self.url:
            logger.error("Error: No URL provided.")
            return {}

        headers = self._get_headers()

        # Route through Cloudflare WARP SOCKS5 proxy if active
        if self._is_warp_active():
            warp_proxies = self._get_warp_proxies()
            for attempt in range(max_retries):
                try:
                    resp = self._execute_http_get(
                        self.url,
                        headers=headers,
                        timeout=(3.0, self.timeout),
                        proxy=warp_proxies,
                    )
                    self.last_response = resp
                    self.last_used_proxy = "warp-socks5"
                    if resp.status_code == 200:
                        parsed = self._process_response(resp)
                        return (
                            parsed
                            if parsed is not None
                            else {"html": resp.text, "status_code": 200}
                        )
                    if resp.status_code == 404:
                        return (
                            {"html": "", "status_code": 404, "classes": []}
                            if self.use_class_url
                            else {}
                        )
                    if resp.status_code in (403, 429):
                        logger.warning(
                            f"WARP SOCKS5 returned HTTP {resp.status_code} on {self.url}. "
                            f"Retrying ({attempt + 1}/{max_retries})..."
                        )
                        time.sleep(1.0 + attempt * 0.5)
                except Exception as e:
                    logger.debug(
                        f"WARP SOCKS5 request error on {self.url} (attempt {attempt + 1}/{max_retries}): {e}"
                    )
                    time.sleep(0.5)
            return None if self.use_class_url else {}

        # If proxy usage is disabled, route direct
        if not self.use_proxy:
            for attempt in range(max_retries):
                try:
                    resp = self._execute_http_get(
                        self.url,
                        headers=headers,
                        timeout=(3.0, self.timeout),
                    )
                    self.last_response = resp
                    self.last_used_proxy = "direct"
                    if resp.status_code == 200:
                        parsed = self._process_response(resp)
                        return (
                            parsed
                            if parsed is not None
                            else {"html": resp.text, "status_code": 200}
                        )
                    if resp.status_code == 404:
                        return (
                            {"html": "", "status_code": 404, "classes": []}
                            if self.use_class_url
                            else {}
                        )
                    if resp.status_code in (403, 429):
                        DataFetcher._record_direct_failure(resp.status_code)
                        logger.warning(
                            f"Direct connection challenged (HTTP {resp.status_code}) on {self.url}. "
                            f"Retrying ({attempt + 1}/{max_retries})..."
                        )
                        time.sleep(1.0 + attempt * 0.5)
                except Exception as e:
                    logger.debug(
                        f"Direct request error on {self.url} (attempt {attempt + 1}/{max_retries}): {e}"
                    )
                    time.sleep(0.5)
            return None if self.use_class_url else {}

        host = host_strategy.get_host(self.url)
        strategy = host_strategy.get_strategy(host)
        is_ci = bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))

        # Timeouts: connect 5s, read 20s for direct search/listing; normal timeouts otherwise
        if not self.use_class_url:
            direct_timeout = (5.0, 20.0)
        else:
            direct_timeout = (3.0, 5.0) if is_ci else (3.0, max(self.timeout, 5.0))

        proxy_timeout = (3.0, 5.0)
        capped_retries = min(max_retries, 3)

        def _try_direct_request() -> dict | None:
            """Execute a single direct attempt if circuit breaker allows it."""
            breaker = host_strategy.get_breaker(host)
            if not breaker.is_direct_allowed():
                return None

            sem = None
            try:
                sem = host_strategy.acquire_direct_slot(host)
                logger.debug(
                    "Attempting direct connection for %s...",
                    self._sanitise_for_log(self.endpoint),
                )
                direct_resp = self._execute_http_get(
                    self.url, headers=headers, timeout=direct_timeout
                )
                self.last_response = direct_resp
                self.last_used_proxy = "direct"

                status_code = direct_resp.status_code
                if status_code == 200:
                    parsed = self._process_response(direct_resp)
                    if parsed is not None:
                        host_strategy.record_direct_outcome(host, success=True)
                        return parsed
                    else:
                        logger.debug(
                            "Direct response unparseable/invalid for %s", self.endpoint
                        )
                        host_strategy.record_direct_outcome(
                            host, success=False, status_code=200
                        )
                        return None
                elif status_code == 404:
                    logger.warning("HTTP 404 - Not Found: %s", self.url)
                    host_strategy.record_direct_outcome(host, success=True)
                    return {}
                elif status_code == 429:
                    retry_after_hdr = direct_resp.headers.get("Retry-After")
                    retry_after_val = None
                    if retry_after_hdr:
                        try:
                            retry_after_val = float(retry_after_hdr)
                        except (ValueError, TypeError):
                            pass
                    DataFetcher._record_direct_failure(429)
                    host_strategy.record_direct_outcome(
                        host,
                        success=False,
                        status_code=429,
                        retry_after=retry_after_val,
                    )
                    return None
                elif status_code == 403:
                    DataFetcher._record_direct_failure(403)
                    host_strategy.record_direct_outcome(
                        host, success=False, status_code=403
                    )
                    return None
                else:
                    host_strategy.record_direct_outcome(
                        host, success=False, status_code=status_code
                    )
                    return None
            except Exception as e:
                logger.debug(
                    "Direct connection attempt failed (%s): %s",
                    self._sanitise_for_log(e),
                    self.endpoint,
                )
                host_strategy.record_direct_outcome(host, success=False)
                return None
            finally:
                if sem:
                    host_strategy.release_direct_slot(host)

        def _try_proxy_loop(attempts_limit: int) -> dict | None:
            """Execute proxy attempts up to attempts_limit."""
            if not self.use_proxy:
                return None

            retries = 0
            while retries < attempts_limit:
                proxy = self.get_random_proxy()
                if not proxy:
                    retries += 1
                    time.sleep(0.3)
                    continue

                self.last_used_proxy = (
                    proxy.get("http") if isinstance(proxy, dict) else str(proxy)
                )

                try:
                    logger.debug("Using proxy: %s", self._sanitise_for_log(proxy))
                    response = self._execute_http_get(
                        self.url, headers=headers, timeout=proxy_timeout, proxy=proxy
                    )
                    self.last_response = response

                    if response.status_code == 429:
                        logger.warning("HTTP 429 - Too Many Requests via proxy.")
                        self.report_proxy_failure(proxy, fatal=False)
                        host_strategy.record_proxy_outcome(host, success=False)
                        retries += 1
                        continue

                    if response.status_code == 404:
                        self.report_proxy_success(proxy)
                        host_strategy.record_proxy_outcome(host, success=True)
                        logger.warning("HTTP 404 - Not Found: %s", self.url)
                        return {}

                    if response.status_code == 403:
                        logger.warning("HTTP 403 - Forbidden for proxy: %s", proxy)
                        self.report_proxy_failure(proxy, fatal=False)
                        host_strategy.record_proxy_outcome(host, success=False)
                        retries += 1
                        continue

                    if response.status_code != 200:
                        logger.error("HTTP %s via proxy", response.status_code)
                        self.report_proxy_failure(proxy, fatal=False)
                        host_strategy.record_proxy_outcome(host, success=False)
                        retries += 1
                        continue

                    # Parse proxy response to ensure it actually returned valid content
                    parsed = self._process_response(response)
                    if parsed is not None:
                        self.report_proxy_success(proxy)
                        host_strategy.record_proxy_outcome(host, success=True)
                        return parsed
                    else:
                        self.report_proxy_failure(proxy, fatal=False)
                        host_strategy.record_proxy_outcome(host, success=False)
                    retries += 1

                except requests.exceptions.ProxyError as e:
                    logger.debug(
                        "Proxy tunnel error (%s): %s",
                        self._sanitise_for_log(e),
                        self._sanitise_for_log(proxy),
                    )
                    self.report_proxy_failure(proxy, fatal=True)
                    host_strategy.record_proxy_outcome(host, success=False)
                    retries += 1
                except (
                    requests.exceptions.Timeout,
                    requests.exceptions.RequestException,
                ) as e:
                    logger.debug(
                        "Proxy error/timeout (%s): %s",
                        self._sanitise_for_log(e),
                        self._sanitise_for_log(proxy),
                    )
                    self.report_proxy_failure(proxy, fatal=False)
                    host_strategy.record_proxy_outcome(host, success=False)
                    retries += 1
                except Exception as e:
                    logger.debug(
                        "Unexpected error on proxy (%s): %s",
                        self._sanitise_for_log(e),
                        self._sanitise_for_log(proxy),
                    )
                    self.report_proxy_failure(proxy, fatal=False)
                    host_strategy.record_proxy_outcome(host, success=False)
                    retries += 1
                finally:
                    DataFetcher.release_proxy(proxy)

            return None

        # Execute connection strategy
        if strategy == Strategy.DIRECT_ONLY:
            res = _try_direct_request()
            return res if res is not None else {}

        elif strategy == Strategy.PROXY_ONLY:
            res = _try_proxy_loop(capped_retries)
            return res if res is not None else {}

        elif strategy == Strategy.DIRECT_FIRST:
            # Attempt 1: Direct
            res = _try_direct_request()
            if res is not None:
                return res

            # Attempt 2: Direct after short backoff to tolerate transient network blip
            time.sleep(0.3)
            res = _try_direct_request()
            if res is not None:
                return res

            # Fall back to proxies
            res = _try_proxy_loop(capped_retries)
            if res is not None:
                return res

            return {}

        else:  # Strategy.PROXY_FIRST (default for adelaide.edu.au course pages)
            # Legacy behaviour: try direct first ONLY IF non-CI and direct is not blocked
            if not is_ci and not DataFetcher.is_direct_blocked():
                res = _try_direct_request()
                if res is not None:
                    return res

            res = _try_proxy_loop(capped_retries)
            if res is not None:
                return res

            return {}

    async def async_get(
        self, session: AsyncSession | None = None, max_retries: int = 3
    ) -> dict:
        """Asynchronously fetch data with browser TLS impersonation and connection reuse."""
        if self.data is not None:
            return self.data
        if not self.url:
            return {}

        headers = self._get_headers()
        is_ci = bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))
        base_timeout = (3.0, 5.0) if is_ci else (3.0, max(self.timeout, 5.0))

        if self._is_warp_active():
            warp_proxies = self._get_warp_proxies()
            for _ in range(min(max_retries, 3)):
                try:
                    if session is not None:
                        resp = await session.get(
                            self.url,
                            headers=headers,
                            timeout=base_timeout,
                            proxies=warp_proxies,
                        )
                    else:
                        async with AsyncSession(impersonate="chrome146") as local_sess:
                            resp = await local_sess.get(
                                self.url,
                                headers=headers,
                                timeout=base_timeout,
                                proxies=warp_proxies,
                            )
                    self.last_response = resp
                    self.last_used_proxy = "warp-socks5"
                    if resp.status_code == 200:
                        parsed = self._process_response(resp)
                        if parsed is not None:
                            return parsed
                    elif resp.status_code == 404:
                        return {}
                except Exception as e:
                    logger.debug(f"WARP SOCKS5 async connection attempt failed: {e}")
            return {}

        if not DataFetcher.is_direct_blocked():
            try:
                if session is not None:
                    resp = await session.get(
                        self.url, headers=headers, timeout=base_timeout
                    )
                else:
                    async with AsyncSession(impersonate="chrome146") as local_sess:
                        resp = await local_sess.get(
                            self.url, headers=headers, timeout=base_timeout
                        )
                self.last_response = resp
                if resp.status_code == 200:
                    parsed = self._process_response(resp)
                    if parsed is not None:
                        return parsed
                elif resp.status_code == 404:
                    return {}
                elif resp.status_code in (403, 429):
                    DataFetcher._record_direct_failure(resp.status_code)
            except Exception as e:
                logger.debug(f"Direct async connection attempt failed: {e}")

        if not self.use_proxy:
            return {}

        retries = 0
        proxy_timeout = (3.0, 5.0)
        max_retries = min(max_retries, 3)
        while retries < max_retries:
            proxy = self.get_random_proxy()
            if not proxy:
                retries += 1
                await asyncio.sleep(0.2)
                continue
            try:
                if session is not None:
                    resp = await session.get(
                        self.url,
                        proxies=proxy,
                        headers=headers,
                        timeout=proxy_timeout,
                    )
                else:
                    async with AsyncSession(impersonate="chrome146") as local_sess:
                        resp = await local_sess.get(
                            self.url,
                            proxies=proxy,
                            headers=headers,
                            timeout=proxy_timeout,
                        )
                self.last_response = resp
                if resp.status_code == 200:
                    parsed = self._process_response(resp)
                    if parsed is not None:
                        self.report_proxy_success(proxy)
                        return parsed
                    self.report_proxy_failure(proxy, fatal=True)
                elif resp.status_code == 404:
                    self.report_proxy_success(proxy)
                    return {}
                else:
                    self.report_proxy_failure(proxy, fatal=True)
                retries += 1
            except Exception:
                self.report_proxy_failure(proxy, fatal=True)
                retries += 1
        return {}
