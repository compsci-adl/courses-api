import time

import json_repair
import requests

from log import logger


class DataFetcher:
    """Fetch data from the course planner API"""

    BASE_URL = "https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/"

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self.url = self.BASE_URL + endpoint
        self.data = None
        self.last_response = None

    def get(self) -> dict:
        """Get data from the API"""
        logger.debug(f"Fetching {self.endpoint}...")
        if self.data is not None:
            return self.data

        if not self.url:
            print("Error: No URL provided.")
            return {}

        response = requests.get(self.url)
        self.last_response = response

        if response.status_code == 429:
            print("Error: HTTP 429 - Too Many Requests. Waiting for 30 seconds...")
            time.sleep(30)
            return self.get()

        if response.status_code != 200:
            print(f"Error: HTTP {response.status_code} - {response.text}")
            return {}

        resp = json_repair.loads(response.text)

        if resp.get("status") != "success":
            print(f"API Error: {resp.get('error', 'Unknown error')}")
            return {}

        data_field = resp.get("data")

        self.data = {"data": data_field.get("query", {}).get("rows", [])}
        return self.data


# Example usage
if __name__ == "__main__":
    YEAR = 2024
    SUBJECT = "COMP SCI"
    COURSE_ID = 107592
    TERM = 4410

    subjects = DataFetcher(
        f"SUBJECTS_BY_YEAR/queryx&virtual=Y&year_from={YEAR}&year_to={YEAR}"
    )

    courses = DataFetcher(
        f"COURSE_SEARCH/queryx&virtual=Y&year={YEAR}&subject={SUBJECT}&pagenbr=1&pagesize=500"
    )
    print(courses.get()["data"])

    course_detail = DataFetcher(
        f"COURSE_DTL/queryx&virtual=Y&year={YEAR}&courseid={COURSE_ID}&term={TERM}"
    )
    print(course_detail.get()["data"][0])

    course_class_list = DataFetcher(
        f"COURSE_CLASS_LIST/queryx&virtual=Y&crseid={COURSE_ID}&offer=1&term={TERM}&session=1"
    )
    print(course_class_list.get()["data"][0])

    terms = DataFetcher(f"TERMS/queryx&virtual=Y&year_from={YEAR}&year_to={YEAR}")
    print(terms.get()["data"])
