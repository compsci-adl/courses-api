import requests


class CourseFetch:
    """
    Collection of methods to fetch courses
    """

    BASE_URL = "https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system"

    def __init__(self, year: int) -> None:
        self.year = year
        pass

    def get_subjects(self):
        """
        Get subjects for the year
        """

        url = (
            self.BASE_URL
            + f"/SUBJECTS_BY_YEAR/queryx&virtual=Y&year_from={self.year}&year_to={self.year}"
        )
        resp = requests.get(url)

        if resp["status"] != "success":
            return []

        return resp["data"]["query"]["rows"]

    def get_courses(self, subject: str):
        """
        Get all courses for a given subject
        """

        subject = subject.replace(" ", "%20")

        # NOTE: just grab everything - no pagination
        url = (
            self.BASE_URL
            + f"/COURSE_SEARCH/queryx&virtual=Y&year={self.year}&subject={subject}&pagenbr=1&pagesize=500"
        )
        resp = requests.get(url)

        if resp["status"] != "success":
            return []

        return resp["data"]["query"]["rows"]

    def get_course_detail(self, course_id: str, term: str):
        """
        Get details of a course
        """

        url = (
            self.BASE_URL
            + f"/COURSE_DTL/queryx&virtual=Y&year={self.year}&courseid={course_id}&term={term}"
        )

        resp = requests.get(url)

        if resp["status"] != "success":
            return {}

        return resp["data"]["query"]["rows"][0]

    def get_course_class_list(self, course_id: str, term: str):
        """
        Get list of classes for a course
        """

        url = (
            self.BASE_URL
            + f"/COURSE_CLASS_LIST/queryx&virtual=Y&crseid={course_id}&offer=1&term={term}&session=1"
        )

        resp = requests.get(url)

        if resp["status"] != "success":
            return {}

        return resp["data"]["query"]["rows"][0]

    def get_terms(self):
        """
        Get term codes for the year
        """

        url = (
            self.BASE_URL
            + f"/TERMS/queryx&virtual=Y&year_from={self.year}&year_to={self.year}"
        )
        resp = requests.get(url)

        if resp["status"] != "success":
            return {}

        return resp["data"]["query"]["rows"]
