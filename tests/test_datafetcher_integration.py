import unittest
from src.api.DataFetcher import DataFetcher

class TestDataFetcherIntegration(unittest.TestCase):
    
    show_data = False  

    @classmethod
    def setUpClass(cls):
        import os
        cls.show_data = os.getenv('SHOW_DATA', 'false').lower() == 'true'

        def print_data(self, label, data):
            """
            Prints the data with a label if the SHOW_DATA environment variable is true
            """
            if self.show_data:
                print(f"{label}:", data)

    def test_fetch_subjects(self):
        YEAR = 2024
        fetcher = DataFetcher(
            f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/SUBJECTS_BY_YEAR/queryx&virtual=Y&year_from={YEAR}&year_to={YEAR}"
        )
        result = fetcher.get()
        self.print_data("Subjects Data", result)  
        self.assertIn("data", result)
        self.assertIsInstance(result["data"], list)

    def test_fetch_courses(self):
        YEAR = 2024
        SUBJECT = "COMP SCI"
        fetcher = DataFetcher(
            f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_SEARCH/queryx&virtual=Y&year={YEAR}&subject={SUBJECT}&pagenbr=1&pagesize=500"
        )
        result = fetcher.get()
        self.print_data("Courses Data", result)  
        self.assertIn("data", result)
        self.assertIsInstance(result["data"], list)

    def test_fetch_course_detail(self):
        YEAR = 2024
        COURSE_ID = 107592
        TERM = 4410
        fetcher = DataFetcher(
            f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_DTL/queryx&virtual=Y&year={YEAR}&courseid={COURSE_ID}&term={TERM}"
        )
        result = fetcher.get()
        self.print_data("Course Detail Data", result)  
        self.assertIn("data", result)
        self.assertIsInstance(result["data"], list)
        self.assertGreater(len(result["data"]), 0)

    def test_fetch_course_class_list(self):
        COURSE_ID = 107592
        TERM = 4410
        fetcher = DataFetcher(
            f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_CLASS_LIST/queryx&virtual=Y&crseid={COURSE_ID}&offer=1&term={TERM}&session=1"
        )
        result = fetcher.get()
        self.print_data("Course Class List Data", result)  
        self.assertIn("data", result)
        self.assertIsInstance(result["data"], list)
        self.assertGreater(len(result["data"]), 0)

    def test_fetch_terms(self):
        YEAR = 2024
        fetcher = DataFetcher(
            f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/TERMS/queryx&virtual=Y&year_from={YEAR}&year_to={YEAR}"
        )
        result = fetcher.get()
        self.print_data("Terms Data", result)  
        self.assertIn("data", result)
        self.assertIsInstance(result["data"], list)

if __name__ == "__main__":
    unittest.main()
