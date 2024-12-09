import random
import time

import json_repair
import requests

from log import logger


class DataFetcher:
    """Fetch data from the course planner API using a proxy."""

    BASE_URL = "https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/"
    PROXY_FILE = "src/working_proxies.txt"

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
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

                resp = json_repair.loads(response.text)

                if resp.get("status") != "success":
                    logger.error(f"API Error: {resp.get('error', 'Unknown error')}")
                    retries += 1
                    continue

                data_field = resp.get("data")
                self.data = {"data": data_field.get("query", {}).get("rows", [])}
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
