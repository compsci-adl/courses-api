import unittest
from src.api.course_scraper import CourseScraper

# TODO: update test names to have the following format test_<method_name> for courseScraper
class TestCourseScraperIntegration(unittest.TestCase):
    
    show_data = False  

    @classmethod
    def setUpClass(cls):
        import os
        cls.show_data = os.getenv('SHOW_DATA', 'false').lower() == 'true'

    def setUp(self):
        """
        Set up common test variables and create a CourseScraper instance.
        """
        self.year = 2024
        self.subject = "COMP SCI"
        self.course_id = 107592
        self.term = 4410
        self.scraper = CourseScraper(year=self.year)

    def print_data(self, label, data):
        """
        Prints the data with a label if the SHOW_DATA environment variable is true.
        """
        if self.show_data:
            print(f"{label}:", data)

    def assert_valid_data(self, result):
        """
        Common assertions to check if the result contains valid data.
        """
        self.assertIn("data", result)
        self.assertIsInstance(result["data"], list)
        self.assertGreater(len(result["data"]), 0)

    def test_fetch_subjects(self):
        result = self.scraper.get_subjects()
        self.print_data("Subjects Data", result)  
        self.assert_valid_data(result)

    def test_fetch_courses(self):
        result = self.scraper.get_courses_by_subject(self.subject)
        self.print_data("Courses Data", result)  
        self.assert_valid_data(result)

    def test_fetch_course_detail(self):
        result = self.scraper.get_course_details(course_id=self.course_id, term=self.term)
        self.print_data("Course Detail Data", result)  
        self.assert_valid_data(result)

    def test_fetch_course_class_list(self):
        result = self.scraper.get_course_class_list(course_id=self.course_id, term=self.term)
        self.print_data("Course Class List Data", result)  
        self.assert_valid_data(result)

    def test_fetch_terms(self):
        result = self.scraper.get_terms()
        self.print_data("Terms Data", result)  
        self.assert_valid_data(result)

if __name__ == "__main__":
    unittest.main()
