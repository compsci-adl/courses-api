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

    MAX_PROXY_FAILURES = 3
    _proxies = None
    _proxy_failures = {}
    _proxy_lock = threading.Lock()
    _funnelback_semaphore = threading.BoundedSemaphore(12)
    _direct_blocked = False
    _direct_blocked_until = 0.0

    @classmethod
    def is_direct_blocked(cls) -> bool:
        """Check if direct connection is currently paused due to rate limiting or cooldown."""
        if cls._direct_blocked:
            if (
                cls._direct_blocked_until > 0
                and time.time() >= cls._direct_blocked_until
            ):
                cls._direct_blocked = False
                cls._direct_blocked_until = 0.0
                return False
            return True
        return False

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

    def __init__(
        self,
        endpoint: str,
        use_class_url: bool = False,
        full_url: str = None,
        use_proxy: bool = True,
        timeout: float = 3.0,
    ) -> None:
        self.endpoint = endpoint
        self.use_class_url = use_class_url
        self.timeout = timeout
        if full_url:
            self.url = full_url
        elif self.use_class_url:
            # Build a full URL for course page content. Ensure endpoint is a path.
            path = (
                self.endpoint if self.endpoint.startswith("/") else f"/{self.endpoint}"
            )
            self.url = self.BASE_INFO_URL.rstrip("/") + path
        else:
            self.url = self.BASE_URL + endpoint
        self.data = None
        self.last_response = None
        self.use_proxy = use_proxy

        # Load proxies globally if not already loaded
        with DataFetcher._proxy_lock:
            if DataFetcher._proxies is None:
                DataFetcher._proxies = self.load_proxies()

    @classmethod
    def get_working_proxy(cls) -> dict | None:
        """Helper to get a random working proxy dictionary."""
        fetcher = cls("", use_proxy=True)
        return fetcher.get_random_proxy()

    def load_proxies(self) -> list:
        """Load proxies from file, checking candidate paths."""
        candidates = [
            self.PROXY_FILE,
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

    def get_random_proxy(self) -> dict:
        """Get a proxy from the loaded list, prioritising known working proxies."""
        if not self.use_proxy:
            return None
        with DataFetcher._proxy_lock:
            if not DataFetcher._proxies:
                DataFetcher._proxies = self.load_proxies()
                DataFetcher._proxy_failures.clear()
            if not DataFetcher._proxies:
                logger.warning("No proxies available. Proceeding without a proxy.")
                return None
            # 70% of the time pick from the top 5 known good proxies if available
            if len(DataFetcher._proxies) > 5 and random.random() < 0.7:
                proxy = random.choice(DataFetcher._proxies[:5])
            else:
                proxy = random.choice(DataFetcher._proxies)
        http_val = (
            proxy
            if proxy.startswith("http://") or proxy.startswith("https://")
            else f"http://{proxy}"
        )
        return {
            "http": http_val,
            "https": http_val,
        }

    def report_proxy_failure(self, proxy: dict, fatal: bool = False) -> None:
        """Record a failure for a proxy. Remove it only if it exceeds MAX_PROXY_FAILURES or fatal is True."""
        if not proxy:
            return
        proxy_str = proxy.get("http", "").replace("http://", "")
        if not proxy_str:
            return
        with DataFetcher._proxy_lock:
            count = DataFetcher._proxy_failures.get(proxy_str, 0) + 1
            DataFetcher._proxy_failures[proxy_str] = count
            if fatal or count >= self.MAX_PROXY_FAILURES:
                if DataFetcher._proxies and proxy_str in DataFetcher._proxies:
                    try:
                        DataFetcher._proxies.remove(proxy_str)
                        logger.info(
                            f"Removed bad proxy: {proxy_str} (failures: {count}). Remaining: {len(DataFetcher._proxies)}"
                        )
                    except ValueError:
                        pass
            else:
                logger.debug(
                    f"Proxy {proxy_str} failure count: {count}/{self.MAX_PROXY_FAILURES}"
                )

    def report_proxy_success(self, proxy: dict) -> None:
        """Reset failure count and promote working proxy to the top of the pool."""
        if not proxy:
            return
        proxy_str = proxy.get("http", "").replace("http://", "")
        if not proxy_str:
            return
        with DataFetcher._proxy_lock:
            DataFetcher._proxy_failures.pop(proxy_str, None)
            if DataFetcher._proxies and proxy_str in DataFetcher._proxies:
                DataFetcher._proxies.remove(proxy_str)
                DataFetcher._proxies.insert(0, proxy_str)

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
        timeout: float,
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

    def get(self, max_retries: int = 10) -> dict:
        """Fetch data from the API, trying direct connection first with smart proxy fallback."""
        logger.debug("Fetching %s...", self._sanitise_for_log(self.endpoint))
        if self.data is not None:
            return self.data

        if not self.url:
            logger.error("Error: No URL provided.")
            return {}

        headers = self._get_headers()

        # Direct Connection First
        is_ci = bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))
        base_timeout = max(self.timeout, 8.0) if is_ci else max(self.timeout, 5.0)

        if not DataFetcher.is_direct_blocked():
            sem_ctx = (
                DataFetcher._funnelback_semaphore
                if not self.use_class_url
                else threading.Lock()
            )
            with sem_ctx:
                try:
                    logger.debug(
                        "Attempting direct connection for %s...",
                        self._sanitise_for_log(self.endpoint),
                    )
                    direct_resp = self._execute_http_get(
                        self.url, headers=headers, timeout=base_timeout
                    )
                    self.last_response = direct_resp
                    if direct_resp.status_code == 200:
                        parsed = self._process_response(direct_resp)
                        if parsed is not None:
                            return parsed
                    elif direct_resp.status_code == 404:
                        logger.warning("HTTP 404 - Not Found: %s", self.url)
                        return {}
                    elif direct_resp.status_code in (403, 429):
                        DataFetcher._direct_blocked = True
                        DataFetcher._direct_blocked_until = time.time() + 15.0
                        logger.warning(
                            "Direct connection rate-limited/blocked by WAF (HTTP %s). "
                            "Pausing direct requests for 15s cooldown and trying proxies.",
                            direct_resp.status_code,
                        )
                except Exception as e:
                    logger.debug(
                        "Direct connection attempt failed (%s). Falling back to proxies.",
                        self._sanitise_for_log(e),
                    )

        # If proxy is disabled, return without proxy fallback
        if not self.use_proxy:
            return {}

        # Smart Proxy Fallback with generous timeout and priority queue of working proxies
        retries = 0
        proxy_timeout = min(max(self.timeout, 6.0), 12.0)
        with DataFetcher._proxy_lock:
            pool_size = len(DataFetcher._proxies or [])
        max_retries = max(min(max_retries, pool_size), 15)

        while retries < max_retries:
            # Re-attempt direct connection after 2 proxy failures only if not blocked by WAF
            if retries == 2 and not DataFetcher.is_direct_blocked():
                try:
                    direct_resp = self._execute_http_get(
                        self.url, headers=headers, timeout=base_timeout
                    )
                    self.last_response = direct_resp
                    if direct_resp.status_code == 200:
                        parsed = self._process_response(direct_resp)
                        if parsed is not None:
                            return parsed
                except Exception:
                    pass

            proxy = self.get_random_proxy()
            if not proxy:
                retries += 1
                time.sleep(0.3)
                continue

            try:
                logger.debug("Using proxy: %s", self._sanitise_for_log(proxy))
                response = self._execute_http_get(
                    self.url, headers=headers, timeout=proxy_timeout, proxy=proxy
                )
                self.last_response = response

                if response.status_code == 429:
                    logger.warning("HTTP 429 - Too Many Requests via proxy.")
                    self.report_proxy_failure(proxy, fatal=True)
                    retries += 1
                    continue

                if response.status_code == 404:
                    self.report_proxy_success(proxy)
                    logger.warning("HTTP 404 - Not Found: %s", self.url)
                    return {}

                if response.status_code == 403:
                    logger.warning("HTTP 403 - Forbidden for proxy: %s", proxy)
                    self.report_proxy_failure(proxy, fatal=True)
                    retries += 1
                    continue

                if response.status_code != 200:
                    logger.error("HTTP %s via proxy", response.status_code)
                    self.report_proxy_failure(proxy, fatal=True)
                    retries += 1
                    continue

                # Parse proxy response to ensure it actually returned valid content
                parsed = self._process_response(response)
                if parsed is not None:
                    self.report_proxy_success(proxy)
                    return parsed
                else:
                    self.report_proxy_failure(proxy, fatal=True)
                retries += 1

            except (
                requests.exceptions.Timeout,
                requests.exceptions.ProxyError,
                requests.exceptions.RequestException,
            ) as e:
                logger.debug(
                    "Proxy error/timeout (%s): %s",
                    self._sanitise_for_log(e),
                    self._sanitise_for_log(proxy),
                )
                self.report_proxy_failure(proxy, fatal=True)
                retries += 1
            except Exception as e:
                logger.debug(
                    "Unexpected error on proxy (%s): %s",
                    self._sanitise_for_log(e),
                    self._sanitise_for_log(proxy),
                )
                self.report_proxy_failure(proxy, fatal=True)
                retries += 1

        logger.error(
            "Failed to fetch data from %s after %s attempts.",
            self.url,
            max_retries,
        )
        return {}

    async def async_get(
        self, session: AsyncSession | None = None, max_retries: int = 10
    ) -> dict:
        """Asynchronously fetch data with browser TLS impersonation and connection reuse."""
        if self.data is not None:
            return self.data
        if not self.url:
            return {}

        headers = self._get_headers()
        base_timeout = max(self.timeout, 6.0)

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
                    DataFetcher._direct_blocked = True
                    DataFetcher._direct_blocked_until = time.time() + 15.0
            except Exception as e:
                logger.debug(f"Direct async connection attempt failed: {e}")

        if not self.use_proxy:
            return {}

        retries = 0
        proxy_timeout = min(max(self.timeout, 6.0), 12.0)
        while retries < min(max_retries, 15):
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
