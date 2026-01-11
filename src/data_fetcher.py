import random
import re
import time
from typing import Any

import json_repair
import requests
from bs4 import BeautifulSoup

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

    def __init__(self, endpoint: str, use_class_url: bool = False) -> None:
        self.endpoint = endpoint
        self.use_class_url = use_class_url
        if self.use_class_url:
            # Build a full URL for course page content. Ensure endpoint is a path.
            path = (
                self.endpoint if self.endpoint.startswith("/") else f"/{self.endpoint}"
            )
            self.url = self.BASE_INFO_URL.rstrip("/") + path
        else:
            self.url = self.BASE_URL + endpoint
        self.data = None
        self.last_response = None
        self.proxies = self.load_proxies()

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
        if not self.proxies:
            logger.warning("No proxies available. Proceeding without a proxy.")
            return None
        proxy = random.choice(self.proxies)
        return {
            "http": f"http://{proxy}",
            "https": f"http://{proxy}",
        }

    def get(self) -> dict:
        """Fetch data from the API, handling retries and rate-limiting."""
        logger.debug("Fetching %s...", self._sanitise_for_log(self.endpoint))
        if self.data is not None:
            return self.data

        if not self.url:
            logger.error("Error: No URL provided.")
            return {}

        max_retries = 50  # Maximum number of retries
        retries = 0
        # Clear previous last_response to avoid stale values in callers
        self.last_response = None

        # Exponential backoff base, increase gently, capped to avoid huge sleeps.
        backoff_base = 1.5

        # Avoid cached responses from intermediary proxies
        headers = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        }
        while retries < max_retries:
            proxy = self.get_random_proxy()
            # When fetching course pages, add a cache-buster
            request_url = self.url
            if self.use_class_url:
                cache_buster = f"_={int(time.time() * 1000)}"
                sep = "&" if "?" in request_url else "?"
                request_url = f"{request_url}{sep}{cache_buster}"
            try:
                logger.debug("Using proxy: %s", self._sanitise_for_log(proxy))
                response = requests.get(
                    request_url, proxies=proxy, headers=headers, timeout=10
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
                    # Try another proxy for the next attempt
                    proxy = self.get_random_proxy()
                    retries += 1
                    continue

                if response.status_code != 200:
                    logger.error(f"HTTP {response.status_code} - {response.text}")
                    # Small backoff for other HTTP errors
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
                    self.data = {"h1": h1_text, "data": re.sub(r"\n+", "\n", text)}
                    return self.data

            except requests.exceptions.ProxyError:
                logger.error(
                    "Proxy error with proxy: %s", self._sanitise_for_log(proxy)
                )
                retries += 1
                # Reduce retry flurry by sleeping a moment
                time.sleep(min(3, backoff_base**retries))
            except requests.exceptions.RequestException as e:
                logger.error("Request failed: %s", self._sanitise_for_log(e))
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
