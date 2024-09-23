import unittest
from unittest.mock import patch, Mock
from src.api.data_fetcher import DataFetcher

class TestDataFetcherIntegration(unittest.TestCase):

    show_data = False  

    @classmethod
    def setUpClass(cls):
        import os
        cls.show_data = os.getenv('SHOW_DATA', 'false').lower() == 'true'

    def print_data(self, label, data):
        """
        Prints the data with a label if the SHOW_DATA environment variable is true.
        """
        if self.show_data:
            print(f"{label}:", data)

    def assert_valid_data(self, result):
        """
        Common assertions to check if the result contains valid data
        """
        self.assertIn("data", result)
        self.assertIsInstance(result["data"], list)
        self.assertGreater(len(result["data"]), 0)

    @patch('src.api.data_fetcher.requests.get')  
    def test_fetch_subjects(self, mock_get):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "success",
            "data": {
                "query": {
                    "rows": [{"SUBJECT": "COMP SCI"}]
                }
            }
        }
        mock_get.return_value = mock_response

        YEAR = 2024
        fetcher = DataFetcher(
            f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/SUBJECTS_BY_YEAR/queryx&virtual=Y&year_from={YEAR}&year_to={YEAR}"
        )
        result = fetcher.get()
        self.print_data("Subjects Data", result)
        self.assert_valid_data(result)

if __name__ == "__main__":
    unittest.main()
