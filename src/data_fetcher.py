import random
import re
import time

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
        logger.debug(f"Fetching {self.endpoint}...")
        if self.data is not None:
            return self.data

        if not self.url:
            logger.error("Error: No URL provided.")
            return {}

        max_retries = 50  # Maximum number of retries
        retries = 0

        while retries < max_retries:
            proxy = self.get_random_proxy()
            try:
                logger.debug(f"Using proxy: {proxy}")
                response = requests.get(self.url, proxies=proxy, timeout=5)
                time.sleep(5)  # Sleep to avoid rate-limiting
                self.last_response = response

                if response.status_code == 429:
                    logger.warning(
                        "HTTP 429 - Too Many Requests. Trying another proxy..."
                    )
                    proxy = self.get_random_proxy()  # Try another proxy
                    if retries % 3 == 0:  # Wait after every 3 attempts
                        logger.warning(
                            "Waiting for 60 seconds due to repeated 429 errors..."
                        )
                        time.sleep(60)
                    retries += 1
                    continue

                if response.status_code != 200:
                    logger.error(f"HTTP {response.status_code} - {response.text}")
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
                logger.error(f"Proxy error with proxy: {proxy}")
                retries += 1
            except requests.exceptions.RequestException as e:
                logger.error(f"Request failed: {e}")
                retries += 1
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                retries += 1

        logger.error(
            f"Failed to fetch data from {self.url} after {max_retries} retries."
        )
        return {}
