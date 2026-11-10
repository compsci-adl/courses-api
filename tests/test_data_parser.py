import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import data_parser


class TestDataParser(unittest.TestCase):
    def test_parse_course_text(self):
        sample_text = """
Course ID
200078
Campus
Mawson Lakes, Adelaide City Campus East
Level of study
Undergraduate
Unit value
6
Course coordinator
Cruz Izu
Course level
2
Course overview
Introduction to Operating Systems.
Prerequisite(s)
COMP 2000
Corequisite(s)
N/A
Antirequisite(s)
N/A
University-wide elective course
Yes
"""
        parsed = data_parser.parse_course_text(sample_text)
        self.assertEqual(parsed.get("course_id"), "200078")
        self.assertEqual(parsed.get("units"), "6")
        self.assertEqual(parsed.get("course_coordinator"), "Cruz Izu")
        self.assertEqual(parsed.get("university_wide_elective"), "Yes")

    @patch("data_parser.data_fetcher.DataFetcher")
    def test_get_course_details_with_year(self, mock_fetcher_cls):
        mock_fetcher = MagicMock()
        mock_fetcher_cls.return_value = mock_fetcher
        mock_fetcher.get.return_value = {
            "h1": "Operating Systems",
            "data": "Course ID\n200078\nUnit value\n6",
            "html": '<span class="cmp-course-accordion__title">Semester 2</span>',
        }
        mock_fetcher.last_response.status_code = 200

        res = data_parser.get_course_details("COMP2002", year=2026)

        mock_fetcher_cls.assert_called_once_with(
            "/study/courses/2026/comp-2002/", use_class_url=True
        )
        self.assertEqual(res["title"], "Operating Systems")
        self.assertEqual(res["terms"], ["Semester 2"])

    @patch("data_parser.data_fetcher.DataFetcher")
    def test_get_course_details_without_year(self, mock_fetcher_cls):
        mock_fetcher = MagicMock()
        mock_fetcher_cls.return_value = mock_fetcher
        mock_fetcher.get.return_value = {
            "h1": "Operating Systems",
            "data": "Course ID\n200078\nUnit value\n6",
            "html": "",
        }
        mock_fetcher.last_response.status_code = 200

        res = data_parser.get_course_details("COMP2002")

        mock_fetcher_cls.assert_called_once_with(
            "/study/courses/comp-2002/", use_class_url=True
        )
        self.assertEqual(res["title"], "Operating Systems")

    @patch("data_parser.data_fetcher.DataFetcher")
    def test_get_course_class_list_with_year(self, mock_fetcher_cls):
        mock_fetcher = MagicMock()
        mock_fetcher_cls.return_value = mock_fetcher
        mock_fetcher.get.return_value = {
            "h1": "Operating Systems",
            "data": "",
            "html": "",
        }
        mock_fetcher.last_response.status_code = 200

        data_parser.get_course_class_list("COMP2002", year=2026)

        mock_fetcher_cls.assert_called_once_with(
            "/study/courses/2026/comp-2002/", use_class_url=True
        )

    @patch("data_parser.data_fetcher.DataFetcher")
    def test_get_course_details_not_offered_404(self, mock_fetcher_cls):
        mock_fetcher = MagicMock()
        mock_fetcher_cls.return_value = mock_fetcher
        mock_fetcher.get.return_value = {}
        mock_fetcher.last_response.status_code = 404

        res = data_parser.get_course_details("ENVI2013", year=2026)

        mock_fetcher_cls.assert_called_once_with(
            "/study/courses/2026/envi-2013/", use_class_url=True
        )
        self.assertIsInstance(res, data_parser.ParseResult)
        self.assertEqual(res.status, data_parser.FetchStatus.NOT_OFFERED)
        self.assertIsNone(res.data)

    @patch("data_parser.data_fetcher.DataFetcher")
    def test_get_course_details_page_not_found_h1(self, mock_fetcher_cls):
        mock_fetcher = MagicMock()
        mock_fetcher_cls.return_value = mock_fetcher
        mock_fetcher.get.return_value = {
            "h1": "Page not found",
            "data": "Page not found ERROR 404",
            "html": "<h1>Page not found</h1>",
        }
        mock_fetcher.last_response.status_code = 200

        res = data_parser.get_course_details("ENVI2013", year=2026)
        self.assertIsInstance(res, data_parser.ParseResult)
        self.assertEqual(res.status, data_parser.FetchStatus.NOT_OFFERED)
        self.assertIsNone(res.data)

    @patch("data_parser.data_fetcher.DataFetcher")
    def test_get_course_details_blocked_403(self, mock_fetcher_cls):
        mock_fetcher = MagicMock()
        mock_fetcher_cls.return_value = mock_fetcher
        mock_fetcher.get.return_value = {}
        mock_fetcher.last_response.status_code = 403

        res = data_parser.get_course_details("COMP2002", year=2026)
        self.assertIsInstance(res, data_parser.ParseResult)
        self.assertEqual(res.status, data_parser.FetchStatus.BLOCKED_OR_ERROR)
        self.assertIsNone(res.data)

    @patch("data_parser.data_fetcher.DataFetcher")
    def test_get_course_details_waf_challenge_in_200(self, mock_fetcher_cls):
        mock_fetcher = MagicMock()
        mock_fetcher_cls.return_value = mock_fetcher
        mock_fetcher.get.return_value = {
            "h1": "Attention Required! | Cloudflare",
            "data": "Please complete the security check to access the website",
            "html": "<html><body>Just a moment... cf-browser-verification</body></html>",
        }
        mock_fetcher.last_response.status_code = 200

        res = data_parser.get_course_details("COMP2002", year=2026)
        self.assertIsInstance(res, data_parser.ParseResult)
        self.assertEqual(res.status, data_parser.FetchStatus.BLOCKED_OR_ERROR)
        self.assertIsNone(res.data)

    @patch("data_parser.data_fetcher.DataFetcher")
    def test_get_course_class_list_403_returns_failure(self, mock_fetcher_cls):
        mock_fetcher = MagicMock()
        mock_fetcher_cls.return_value = mock_fetcher
        mock_fetcher.get.return_value = {}
        mock_fetcher.last_response.status_code = 403
        mock_fetcher.last_response.text = "Forbidden"

        res = data_parser.get_course_class_list("COMP2002", year=2026)
        self.assertFalse(res.get("success"))
        self.assertEqual(res.get("classes"), [])
        self.assertIn("HTTP 403", res.get("error", ""))

    @patch("data_parser.data_fetcher.DataFetcher")
    def test_get_course_class_list_404_treated_as_empty_not_error(
        self, mock_fetcher_cls
    ):
        mock_fetcher = MagicMock()
        mock_fetcher_cls.return_value = mock_fetcher
        mock_fetcher.get.return_value = {}
        mock_fetcher.last_response.status_code = 404

        res = data_parser.get_course_class_list("COMP2002", year=2026)
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("classes"), [])
        self.assertIsNone(res.get("error"))


if __name__ == "__main__":
    unittest.main()
