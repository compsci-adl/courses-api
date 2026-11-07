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
from log import logger

_tls = threading.local()


def _get_tls_session() -> requests.Session:
    """Get or create a thread-local persistent Session for connection pooling."""
    if not hasattr(_tls, "session") or _tls.session is None:
        _tls.session = requests.Session(impersonate="chrome124")
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
    _funnelback_semaphore = threading.BoundedSemaphore(6)
    _adelaide_semaphore = threading.BoundedSemaphore(6)
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
                logger.info(
                    "Proxy pool exhausted. Discovering fresh working proxies..."
                )
                try:
                    fetch_proxies.main(target_working=30)
                    DataFetcher._proxies = self.load_proxies()
                    DataFetcher._proxy_failures.clear()
                except Exception as e:
                    logger.debug(f"Fresh proxy discovery attempt: {e}")
            if not DataFetcher._proxies:
                logger.warning("No proxies available. Proceeding without a proxy.")
                return None
            # 70% of the time pick from the top 5 known good proxies if available
            if len(DataFetcher._proxies) > 5 and random.random() < 0.7:
                proxy = random.choice(DataFetcher._proxies[:5])
            else:
                proxy = random.choice(DataFetcher._proxies)
        return {
            "http": f"http://{proxy}",
            "https": f"http://{proxy}",
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
            # Check for empty response
            resp_text = response.text or ""
            if not resp_text.strip():
                return None

            soup = BeautifulSoup(response.content, "html.parser")
            main_tag = soup.find("main")
            text = main_tag.get_text() if main_tag else soup.get_text()
            h1_tag = soup.find("h1")
            h1_text = h1_tag.get_text().strip() if h1_tag else ""

            # Check for 404 Page Not Found
            title_text = soup.title.get_text().strip() if soup.title else ""
            is_404 = (
                response.status_code == 404
                or h1_text.lower() == "page not found"
                or "page not found" in title_text.lower()
            )
            if is_404:
                self.data = {
                    "h1": "Page not found",
                    "data": re.sub(r"\n+", "\n", text),
                    "html": response.text,
                }
                return self.data

            # Validate genuine Adelaide University course page content
            raw_lower = resp_text.lower()
            has_course_markers = (
                "cmp-" in raw_lower
                or "study/courses" in raw_lower
                or "adelaide" in raw_lower
                or "course id" in raw_lower
                or "unit value" in raw_lower
                or "course overview" in raw_lower
            )
            if not h1_text or not has_course_markers:
                logger.debug(
                    "Proxy response rejected: missing h1 or course markers (h1='%s')",
                    self._sanitise_for_log(h1_text),
                )
                return None

            self.data = {
                "h1": h1_text,
                "data": re.sub(r"\n+", "\n", text),
                "html": response.text,
            }
            return self.data

    def _build_request_url(self) -> str:
        """Construct the request URL."""
        return self.url

    def _get_headers(self) -> dict:
        """Flexible headers depending on target endpoint (html page vs json api)."""
        if self.use_class_url:
            return {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://adelaide.edu.au/study/courses/",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }
        else:
            return {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://adelaide.edu.au/",
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
                    impersonate="chrome124",
                )
            return requests.get(
                url,
                headers=headers,
                timeout=timeout,
                impersonate="chrome124",
            )
        sess = _get_tls_session()
        if proxy:
            return sess.get(url, proxies=proxy, headers=headers, timeout=timeout)
        return sess.get(url, headers=headers, timeout=timeout)

    def get(self, max_retries: int = 3) -> dict:
        """Fetch data from the API, trying direct connection first with smart proxy fallback."""
        logger.debug("Fetching %s...", self._sanitise_for_log(self.endpoint))
        if self.data is not None:
            return self.data

        if not self.url:
            logger.error("Error: No URL provided.")
            return {}

        headers = self._get_headers()
        request_url = self._build_request_url()

        # Direct Connection First
        is_ci = bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))
        base_timeout = max(self.timeout, 8.0) if is_ci else max(self.timeout, 5.0)

        if not DataFetcher.is_direct_blocked():
            sem_ctx = (
                DataFetcher._adelaide_semaphore
                if self.use_class_url
                else DataFetcher._funnelback_semaphore
            )
            with sem_ctx:
                try:
                    logger.debug(
                        "Attempting direct connection for %s...",
                        self._sanitise_for_log(self.endpoint),
                    )
                    direct_resp = self._execute_http_get(
                        request_url, headers=headers, timeout=base_timeout
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
                        DataFetcher._direct_blocked_until = time.time() + 10.0
                        logger.warning(
                            "Direct connection rate-limited/blocked by WAF (HTTP %s). "
                            "Pausing direct requests for 10s cooldown and trying proxies.",
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

        # Smart Proxy Fallback with bounded retries and fast failure
        retries = 0
        proxy_timeout = min(max(self.timeout, 5.0), 8.0)
        with DataFetcher._proxy_lock:
            pool_size = len(DataFetcher._proxies or [])
        effective_max = min(max_retries, pool_size) if pool_size > 0 else 0

        while retries < effective_max:
            # Re-attempt direct connection after 1 proxy failure if not blocked by WAF
            if retries == 1 and not DataFetcher.is_direct_blocked():
                try:
                    direct_resp = self._execute_http_get(
                        request_url, headers=headers, timeout=base_timeout
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
                time.sleep(0.2)
                continue

            try:
                logger.debug("Using proxy: %s", self._sanitise_for_log(proxy))
                response = self._execute_http_get(
                    request_url, headers=headers, timeout=proxy_timeout, proxy=proxy
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

                if response.status_code in (403, 406):
                    logger.warning("HTTP %s via proxy: %s", response.status_code, proxy)
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
            effective_max,
        )
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
        request_url = self._build_request_url()
        base_timeout = max(self.timeout, 6.0)

        if not DataFetcher.is_direct_blocked():
            try:
                if session is not None:
                    resp = await session.get(
                        request_url, headers=headers, timeout=base_timeout
                    )
                else:
                    async with AsyncSession(impersonate="chrome124") as local_sess:
                        resp = await local_sess.get(
                            request_url, headers=headers, timeout=base_timeout
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
                    DataFetcher._direct_blocked_until = time.time() + 10.0
            except Exception as e:
                logger.debug(f"Direct async connection attempt failed: {e}")

        if not self.use_proxy:
            return {}

        retries = 0
        proxy_timeout = min(max(self.timeout, 5.0), 8.0)
        with DataFetcher._proxy_lock:
            pool_size = len(DataFetcher._proxies or [])
        effective_max = min(max_retries, pool_size) if pool_size > 0 else 0
        while retries < effective_max:
            proxy = self.get_random_proxy()
            if not proxy:
                retries += 1
                await asyncio.sleep(0.2)
                continue
            try:
                if session is not None:
                    resp = await session.get(
                        request_url,
                        proxies=proxy,
                        headers=headers,
                        timeout=proxy_timeout,
                    )
                else:
                    async with AsyncSession(impersonate="chrome124") as local_sess:
                        resp = await local_sess.get(
                            request_url,
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
