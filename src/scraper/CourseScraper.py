import datetime
from src.api.DataFetcher import DataFetcher

class CourseScraper:
    def __init__(self, year=None):
        self.year = year if year is not None else datetime.now().year
        
    def get_subjects(self):
        url = f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/SUBJECTS_BY_YEAR/queryx&virtual=Y&year_from={self.year}&year_to={self.year}"
        fetcher = DataFetcher(url)
        return fetcher.get()

    def get_courses_by_subject(self, subject):
        url = f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_SEARCH/queryx&virtual=Y&year={self.year}&subject={subject}&pagenbr=1&pagesize=500"
        fetcher = DataFetcher(url)
        return fetcher.get()

    def get_course_details(self, course_id, term):
        url = f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_DTL/queryx&virtual=Y&year={self.year}&courseid={course_id}&term={term}"
        fetcher = DataFetcher(url)
        return fetcher.get()

    def get_course_class_list(self, course_id, term, session=1):
        url = f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_CLASS_LIST/queryx&virtual=Y&crseid={course_id}&offer=1&term={term}&session={session}"
        fetcher = DataFetcher(url)
        return fetcher.get()

    def get_terms(self):
        """
        Fetches the list of terms for the specified year
        Example:
        "query" : {
        "num_rows" : 21,
        "queryname" : "TERMS",
        "rows" : [
         {
                "TERM" : "4410",
                "DESCR" : "2024 Semester 1",
                "ACAD_YEAR" : "2024",
                "CURRENT" : false
        }]
        }}
        """
        url = f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/TERMS/queryx&virtual=Y&year_from={self.year}&year_to={self.year}"
        fetcher = DataFetcher(url)
        return fetcher.get()

    """ 
    # Not feasible due to rate limiting 
    def scrape_all_data(self):
        print("Scraping data...")
        subjects = self.get_subjects()["data"]
        for subject in subjects:
            subject_code = subject["SUBJECT"]
            courses = self.get_courses_by_subject(subject_code)["data"]
            for course in courses:
                course_id = course["COURSE_ID"]
                course_details = self.get_course_details(course_id, term=4410)["data"]
                course_class_list = self.get_course_class_list(course_id, term=4410)["data"] """
                


# Example usage
if __name__ == "__main__":
    YEAR = 2024
    SUBJECT = "COMP SCI"
    COURSE_ID = 107592
    TERM = 4410

    scraper = CourseScraper(year=YEAR)

    # Test get_subjects
    print("Testing get_subjects:")
    subjects = scraper.get_subjects()
    print(subjects)

    # Test get_courses_by_subject
    print("\nTesting get_courses_by_subject:")
    courses = scraper.get_courses_by_subject(SUBJECT)
    print(courses)

    # Test get_course_details
    print("\nTesting get_course_details:")
    course_details = scraper.get_course_details(COURSE_ID, TERM)
    print(course_details)

    # Test get_course_class_list
    print("\nTesting get_course_class_list:")
    course_class_list = scraper.get_course_class_list(COURSE_ID, TERM)
    print(course_class_list)

    # Test get_terms
    print("\nTesting get_terms:")
    terms = scraper.get_terms()
    print(terms)