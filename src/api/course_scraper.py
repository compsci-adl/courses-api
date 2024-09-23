import datetime
from src.api.data_fetcher import DataFetcher
from src.api.data_fetcher import DataFetcher
from datetime import datetime
from typing import List, Dict
from src.api.utils import parse_requisites, meeting_date_convert, meeting_time_convert, convert_term_alias

"""
Course Scraping logic 
"""
class CourseScraper:
    def __init__(self, year=None):
        self.year = year if year else datetime.now().year

    def get_year(self):
        return self.year
    
    @staticmethod
    def get_current_sem() -> str:
        """Gets the current semester."""
        return "Semester 1" if datetime.now().month <= 6 else "Semester 2"

    def get_subjects(self):
        url = f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/SUBJECTS_BY_YEAR/queryx&virtual=Y&year_from={self.year}&year_to={self.year}"
        fetcher = DataFetcher(url)
        return fetcher.get()

    def get_courses_by_subject(self, subject: str):
        url = f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_SEARCH/queryx&virtual=Y&year={self.year}&subject={subject}&pagenbr=1&pagesize=500"
        fetcher = DataFetcher(url)
        return fetcher.get()

    def get_course_details(self, course_id: int, term: int):
        url = f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_DTL/queryx&virtual=Y&year={self.year}&courseid={course_id}&term={term}"
        fetcher = DataFetcher(url)
        return fetcher.get()

    def get_course_class_list(self, course_id: int, term: int, session=1):
        url = f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_CLASS_LIST/queryx&virtual=Y&crseid={course_id}&offer=1&term={term}&session={session}"
        fetcher = DataFetcher(url)
        return fetcher.get()

    def get_terms(self):
        url = f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/TERMS/queryx&virtual=Y&year_from={self.year}&year_to={self.year}"
        fetcher = DataFetcher(url)
        return fetcher.get()
    
    # TODO: add test for this method
    def get_course_ids(self, subject_code: str):
        url = f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_SEARCH/queryx&virtual=Y&year={self.year}&pagenbr=1&pagesize=500&subject={subject_code}"
        fetcher = DataFetcher(url)
        return fetcher.get()
    
    # TODO: add test for this method
    def get_term_number(self, year: int, term: str) -> int:
        """Converts a year and term string to the courseplanner term number."""
        
        year_str = str(year)
        term_alias = convert_term_alias(term)
        
        terms_raw = DataFetcher(
            f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/TERMS/queryx&virtual=Y&year_from=0&year_to=9999"
        ).get()["data"]

        for cur_term in terms_raw:
            if year_str + " " + term_alias == cur_term["DESCR"]:
                return cur_term["TERM"]

        raise Exception(f"Invalid term: {year_str} {term_alias}")
    
    # TODO: add test for this method
    def get_course_info(self, course_id: int, year: int, term: str) -> dict:
        """Fetches the course information and class list."""
        
        term_number = self.get_term_number(year, term)
        details = DataFetcher(
            f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_DTL/queryx&virtual=Y&year={year}&courseid={course_id}&term={term_number}"
        ).get()["data"][0]
        classes = DataFetcher(
            f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_CLASS_LIST/queryx&virtual=Y&crseid={course_id}&term={term_number}&offer=1&session=1"
        ).get()["data"][0]

        classes_formatted = []
        for class_detail in classes["groups"]:
            cur_class_details = {
                "type": class_detail["type"],
                "classes": []
            }
            for _class in class_detail["classes"]:
                cur_class = {
                    "number": _class["class_nbr"],
                    "section": _class["section"],
                    "capacity": {
                        "size": _class["size"],
                        "enrolled": _class["enrolled"],
                        "available": _class["available"] if _class["available"] != "FULL" else 0,
                    },
                    "notes": _class.get("notes", []),
                    "meetings": [
                        {
                            "day": meeting["days"],
                            "date": meeting_date_convert(meeting["dates"]),
                            "time": {
                                "start": meeting_time_convert(meeting["start_time"]),
                                "end": meeting_time_convert(meeting["end_time"])
                            },
                            "location": meeting["location"]
                        }
                        for meeting in _class["meetings"]
                    ]
                }
                cur_class_details["classes"].append(cur_class)
            classes_formatted.append(cur_class_details)

        return {
            "course_id": course_id,
            "name": {
                "subject": details["SUBJECT"],
                "code": details["CATALOG_NBR"],
                "title": details["COURSE_TITLE"]
            },
            "class_number": details["CLASS_NBR"],
            "year": details["YEAR"],
            "term": details["TERM_DESCR"],
            "campus": details["CAMPUS"],
            "career": details["ACAD_CAREER_DESCR"],
            "units": details["UNITS"],
            "requirement": {
                "restriction": details.get("RESTRICTION_TXT"),
                "prerequisite": parse_requisites(details["PRE_REQUISITE"]),
                "corequisite": parse_requisites(details["CO_REQUISITE"]),
                "assumed_knowledge": parse_requisites(details["ASSUMED_KNOWLEDGE"]),
                "incompatible": parse_requisites(details["INCOMPATIBLE"])
            },
            "description": details["SYLLABUS"],
            "assessment": details["ASSESSMENT"],
            "contact": details["CONTACT"],
            "critical_dates": {
                "last_day_add_online": details["CRITICAL_DATES"]["LAST_DAY"],
                "census_date": details["CRITICAL_DATES"]["CENSUS_DT"],
                "last_day_wnf": details["CRITICAL_DATES"]["LAST_DAY_TO_WFN"],
                "last_day_wf": details["CRITICAL_DATES"]["LAST_DAY_TO_WF"]
            },
            "outline_url": details["URL"],
            "class_list": classes_formatted
        }

    # TODO: add test for this method
    def get_courses(self, year: int, term: str) -> List[Dict]:
        """Returns a list of courses for the specified year and term."""
    
        if year < 2006 or year > self.year:
            raise Exception(f"Invalid year: {year}")
        
        term_url = "&term=" + str(self.get_term_number(year, term)) if term else ""
        url = f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_SEARCH/queryx&virtual=Y&year={year}&pagenbr=1&pagesize=5000" + term_url
        courses_raw = DataFetcher(url).get()["data"]
        courses = [
            {
                "course_id": course["COURSE_ID"],
                "name": course["SUBJECT"] + " " + course["CATALOG_NBR"],
                "title": course["COURSE_TITLE"],
                "subject": course["SUBJECT"],
                "number": course["CATALOG_NBR"],
                "career": course["ACAD_CAREER_DESCR"],
                "year": course["YEAR"],
                "term": term,
                "units": course["UNITS"],
                "campus": course["CAMPUS"]
            }
            for course in courses_raw
        ]
        return courses