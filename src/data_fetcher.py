import requests
import time
from tqdm import tqdm


class DataFetcher:
    """
    Fetch data from the course planner API
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.data = None
        self.last_response = None

    def get(self) -> dict:
        """
        Get data from the API
        """
        if self.data is not None:
            return self.data

        if not self.url:
            print("Error: No URL provided.")
            return {}

        try:
            response = requests.get(self.url)
            self.last_response = response
            print(f"Fetching data from: {self.url}")

            if response.status_code == 429:
                print("Error: HTTP 429 - Too Many Requests. Waiting for 30 seconds...")
                for i in tqdm(range(30), desc="Waiting for rate limit reset"):
                    time.sleep(1)
                return self.get() 

            if response.status_code != 200:
                print(f"Error: HTTP {response.status_code} - {response.text}")
                return {}

            resp = response.json()

            if resp.get("status") != "success":
                print(f"API Error: {resp.get('error', 'Unknown error')}")
                return {}

            self.data = {"data": resp.get("data", {}).get(
                "query", {}).get("rows", [])}
            return self.data

        except requests.exceptions.RequestException as e:
            print(f"RequestException: {e}")
            return {}
        except ValueError as e:
            print(f"JSONDecodeError: {e}")
            return {}
        
        
# As an example
if __name__ == "__main__":
    YEAR = 2024
    SUBJECT = "COMP SCI"
    COURSE_ID = 107592
    TERM = 4410

    subjects = DataFetcher(
        f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/SUBJECTS_BY_YEAR/queryx&virtual=Y&year_from={
            YEAR}&year_to={YEAR}"
    )
    print(subjects.get()["data"])

    courses = DataFetcher(
        f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_SEARCH/queryx&virtual=Y&year={
            YEAR}&subject={SUBJECT}&pagenbr=1&pagesize=500"
    )
    print(courses.get()["data"])

    course_detail = DataFetcher(
        f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_DTL/queryx&virtual=Y&year={
            YEAR}&courseid={COURSE_ID}&term={TERM}"
    )
    print(course_detail.get()["data"][0])

    course_class_list = DataFetcher(
        f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_CLASS_LIST/queryx&virtual=Y&crseid={
            COURSE_ID}&offer=1&term={TERM}&session=1"
    )
    print(course_class_list.get()["data"][0])

    terms = DataFetcher(
        f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/systemTERMS/queryx&virtual=Y&year_from={
            YEAR}&year_to={YEAR}"
    )
    print(terms.get()["data"])
