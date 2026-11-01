import random
import re
import threading
import time
from typing import Any

import json_repair
from bs4 import BeautifulSoup
from curl_cffi import requests

from log import logger


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
        """Load proxies from the file."""
        try:
            with open(self.PROXY_FILE, "r") as file:
                proxies = file.read().splitlines()
                logger.debug(f"Loaded {len(proxies)} proxies from {self.PROXY_FILE}.")
                return proxies
        except FileNotFoundError:
            logger.error(f"Proxy file {self.PROXY_FILE} not found.")
            return []

    def get_random_proxy(self) -> dict:
        """Get a random proxy from the loaded list. Reloads from file if empty."""
        if not self.use_proxy:
            return None
        with DataFetcher._proxy_lock:
            if not DataFetcher._proxies:
                DataFetcher._proxies = self.load_proxies()
                DataFetcher._proxy_failures.clear()
            if not DataFetcher._proxies:
                logger.warning("No proxies available. Proceeding without a proxy.")
                return None
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
        """Reset the failure count for a working proxy."""
        if not proxy:
            return
        proxy_str = proxy.get("http", "").replace("http://", "")
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
            if resp.get("response", {}).get("resultPacket"):
                self.data = resp.get("response", {})
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

    def get(self, max_retries: int = 10) -> dict:
        """Fetch data from the API, trying direct connection first with smart proxy fallback."""
        logger.debug("Fetching %s...", self._sanitise_for_log(self.endpoint))
        if self.data is not None:
            return self.data

        if not self.url:
            logger.error("Error: No URL provided.")
            return {}

        # Flexible headers depending on target endpoint (html page vs json api)
        if self.use_class_url:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="146", "Google Chrome";v="146"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"macOS"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }
        else:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "X-Requested-With": "XMLHttpRequest",
            }

        # --- RECOMMENDATION 1: Direct Connection First ---
        try:
            logger.debug(
                "Attempting direct connection for %s...",
                self._sanitise_for_log(self.endpoint),
            )
            direct_resp = requests.get(
                self.url,
                headers=headers,
                timeout=min(self.timeout, 5.0),
                impersonate="chrome146",
            )
            if direct_resp.status_code == 200:
                parsed = self._process_response(direct_resp)
                if parsed is not None:
                    return parsed
            elif direct_resp.status_code == 404:
                self.last_response = direct_resp
                logger.warning("HTTP 404 - Not Found: %s", self.url)
                return {}
            elif direct_resp.status_code in (403, 429):
                logger.warning(
                    "Direct connection throttled (HTTP %s). Falling back to proxy pool.",
                    direct_resp.status_code,
                )
        except Exception as e:
            logger.debug(
                "Direct connection attempt failed (%s). Falling back to proxies.",
                self._sanitise_for_log(e),
            )

        # --- RECOMMENDATION 3: Fast Proxy Fallback with Tight 3.0s Timeout & Immediate Drops ---
        retries = 0
        proxy_timeout = min(self.timeout, 3.0)

        while retries < max_retries:
            # Re-attempt direct connection after 2 proxy failures in case throttling cleared
            if retries == 2:
                try:
                    direct_resp = requests.get(
                        self.url,
                        headers=headers,
                        timeout=min(self.timeout, 4.0),
                        impersonate="chrome146",
                    )
                    if direct_resp.status_code == 200:
                        parsed = self._process_response(direct_resp)
                        if parsed is not None:
                            return parsed
                except Exception:
                    pass

            proxy = self.get_random_proxy()
            if not proxy:
                retries += 1
                time.sleep(0.5)
                continue

            try:
                logger.debug("Using proxy: %s", self._sanitise_for_log(proxy))
                response = requests.get(
                    self.url,
                    proxies=proxy,
                    headers=headers,
                    timeout=proxy_timeout,
                    impersonate="chrome146",
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

                # Proxy succeeded
                self.report_proxy_success(proxy)
                parsed = self._process_response(response)
                if parsed is not None:
                    return parsed
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
