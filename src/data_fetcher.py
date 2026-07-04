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
    BASE_INFO_URL = "https://adelaideuni.edu.au"
    PROXY_FILE = "src/working_proxies.txt"

    # Global proxy list and lock to share working proxies across all scraper threads
    _proxies = None
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
    ) -> None:
        self.endpoint = endpoint
        self.use_class_url = use_class_url
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
        """Get a random proxy from the loaded list."""
        if not self.use_proxy:
            return None
        with DataFetcher._proxy_lock:
            if not DataFetcher._proxies:
                logger.warning("No proxies available. Proceeding without a proxy.")
                return None
            proxy = random.choice(DataFetcher._proxies)
        return {
            "http": f"http://{proxy}",
            "https": f"http://{proxy}",
        }

    def remove_proxy(self, proxy: dict) -> None:
        """Remove a bad/blocked proxy from the global list."""
        if not proxy:
            return
        proxy_str = proxy.get("http", "").replace("http://", "")
        if not proxy_str:
            return
        with DataFetcher._proxy_lock:
            if DataFetcher._proxies and proxy_str in DataFetcher._proxies:
                try:
                    DataFetcher._proxies.remove(proxy_str)
                    logger.info(
                        f"Removed bad proxy: {proxy_str}. Remaining proxies: {len(DataFetcher._proxies)}"
                    )
                except ValueError:
                    pass

    def get(self, max_retries: int = 50) -> dict:
        """Fetch data from the API, handling retries and rate-limiting."""
        logger.debug("Fetching %s...", self._sanitise_for_log(self.endpoint))
        if self.data is not None:
            return self.data

        if not self.url:
            logger.error("Error: No URL provided.")
            return {}

        retries = 0
        self.last_response = None
        backoff_base = 1.5

        # Flexible headers depending on target endpoint (html page vs json api)
        if self.use_class_url:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
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
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "X-Requested-With": "XMLHttpRequest",
            }

        while retries < max_retries:
            proxy = self.get_random_proxy()
            request_url = self.url
            try:
                logger.debug("Using proxy: %s", self._sanitise_for_log(proxy))
                response = requests.get(
                    request_url,
                    proxies=proxy,
                    headers=headers,
                    timeout=10,
                    impersonate="chrome146",
                )
                self.last_response = response

                if response.status_code == 429:
                    # Handle rate limiting properly, use Retry-After if available
                    logger.warning("HTTP 429 - Too Many Requests.")
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait_seconds = int(retry_after)
                        except ValueError:
                            # Retry-After may be a HTTP-date; fall back to default
                            wait_seconds = min(60, int(backoff_base**retries))
                    else:
                        wait_seconds = min(60, int(backoff_base**retries))

                    logger.warning(
                        f"Sleeping for {wait_seconds} seconds due to 429 response"
                    )
                    time.sleep(wait_seconds)
                    retries += 1
                    continue

                if response.status_code == 404:
                    logger.warning(f"HTTP 404 - Not Found: {request_url}")
                    return {}

                if response.status_code == 403:
                    logger.warning(f"HTTP 403 - Forbidden for proxy: {proxy}")
                    self.remove_proxy(proxy)
                    retries += 1
                    continue

                if response.status_code != 200:
                    # Small backoff for other HTTP errors
                    logger.error(f"HTTP {response.status_code} - {response.text[:200]}")
                    wait_seconds = min(10, int(backoff_base**retries))
                    logger.debug(f"Waiting for {wait_seconds}s before retrying")
                    time.sleep(wait_seconds)
                    retries += 1
                    continue

                # If using Funnelback (search), parse as JSON and return the response dict.
                if not self.use_class_url:
                    resp = json_repair.loads(response.text)
                    if not resp.get("response", {}).get("resultPacket"):
                        logger.error(
                            f"Funnelback API Error: {resp.get('error', 'Unknown error')}"
                        )
                        retries += 1
                        continue
                    self.data = resp.get("response", {})
                    return self.data

                # If fetching a class/course content page, just return the HTML text as {'data': <text>}.
                if self.use_class_url:
                    soup = BeautifulSoup(response.content, "html.parser")
                    # Get main content
                    main_tag = soup.find("main")
                    if main_tag:
                        text = main_tag.get_text()
                    else:
                        text = soup.get_text()
                    # Grab H1 text if present as a separate field to help parsers
                    h1_tag = soup.find("h1")
                    h1_text = h1_tag.get_text().strip() if h1_tag else ""
                    self.data = {
                        "h1": h1_text,
                        "data": re.sub(r"\n+", "\n", text),
                        "html": response.text,
                    }
                    return self.data

            except requests.exceptions.ProxyError:
                logger.error(
                    "Proxy error with proxy: %s", self._sanitise_for_log(proxy)
                )
                self.remove_proxy(proxy)
                retries += 1
                # Reduce retry flurry by sleeping a moment
                time.sleep(min(3, backoff_base**retries))
            except requests.exceptions.RequestException as e:
                logger.error("Request failed: %s", self._sanitise_for_log(e))
                self.remove_proxy(proxy)
                retries += 1
                time.sleep(min(3, backoff_base**retries))
            except Exception as e:
                logger.error("Unexpected error: %s", self._sanitise_for_log(e))
                retries += 1
                time.sleep(min(3, backoff_base**retries))

        logger.error(
            f"Failed to fetch data from {self.url} after {max_retries} retries."
        )
        return {}
