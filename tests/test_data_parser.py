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

    @patch(
        "data_parser.parse_course_class_list", return_value=[{"class_number": "1234"}]
    )
    @patch("data_parser.data_fetcher.DataFetcher")
    def test_get_course_class_list_with_year(
        self, mock_fetcher_cls, mock_parse_classes
    ):
        mock_fetcher = MagicMock()
        mock_fetcher_cls.return_value = mock_fetcher
        mock_fetcher.get.return_value = {
            "h1": "Operating Systems",
            "data": "",
            "html": "<div>dummy class table</div>",
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
        self.assertIsNone(res)

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
        self.assertIsNone(res)

    @patch("data_parser.parse_course_class_list", return_value=[])
    @patch("data_parser.data_fetcher.DataFetcher")
    def test_get_course_class_list_no_classes_does_not_retry(
        self, mock_fetcher_cls, mock_parse_classes
    ):
        """A valid course page with no scheduled classes should return immediately on attempt 1."""
        mock_fetcher = MagicMock()
        mock_fetcher_cls.return_value = mock_fetcher
        mock_fetcher.get.return_value = {
            "h1": "Archaeology of Classical Greece",
            "data": "",
            "html": "<html><head><meta property='courseCode' content='ARCY 2004'></head><body><div class='cmp-herobanner'><h1>Archaeology of Classical Greece</h1></div><div class='courseavailability'></div></body></html>",
        }
        mock_fetcher.last_response.status_code = 200

        res = data_parser.get_course_class_list("ARCY2004", year=2026)

        # Must NOT retry across multiple attempts
        mock_fetcher_cls.assert_called_once_with(
            "/study/courses/2026/arcy-2004/", use_class_url=True
        )
        self.assertEqual(res["classes"], [])
        self.assertTrue(res.get("is_valid_page"))

    @patch("data_parser.parse_course_class_list", return_value=[])
    @patch("data_parser.data_fetcher.DataFetcher")
    def test_get_course_class_list_blocked_retries(
        self, mock_fetcher_cls, mock_parse_classes
    ):
        """A blocked or malformed page (e.g. Cloudflare / 403) must retry across alternate proxies."""
        mock_fetcher = MagicMock()
        mock_fetcher_cls.return_value = mock_fetcher
        mock_fetcher.get.return_value = {
            "h1": "403 Forbidden",
            "data": "",
            "html": "<html><head><title>403 Forbidden</title></head><body><h1>403 Forbidden</h1></body></html>",
        }
        mock_fetcher.last_response.status_code = 200

        res = data_parser.get_course_class_list("ARCY2004", year=2026)

        # Should retry 4 times
        self.assertEqual(mock_fetcher_cls.call_count, 4)
        self.assertEqual(res["classes"], [])
        self.assertFalse(res.get("is_valid_page"))

    @patch("data_parser.data_fetcher.DataFetcher")
    def test_get_all_courses_bulk(self, mock_fetcher_cls):
        """Verify get_all_courses_bulk parses paginated Funnelback JSON into course records."""
        mock_fetcher = MagicMock()
        mock_fetcher_cls.return_value = mock_fetcher
        mock_fetcher.get.side_effect = [
            {
                "resultPacket": {
                    "resultsSummary": {"totalMatching": 2},
                    "results": [
                        {
                            "title": "Financial Accounting 1",
                            "liveUrl": "https://adelaide.edu.au/study/courses/acct-1001/",
                            "listMetadata": {
                                "courseCode": ["ACCT1001"],
                                "studyArea": ["Accounting"],
                                "term": ["Semester 1"],
                                "dateModified": ["2026-10-27"],
                                "unitValue": ["6"],
                            },
                        },
                        {
                            "title": "Operating Systems",
                            "liveUrl": "https://adelaide.edu.au/study/courses/comp-2002/",
                            "listMetadata": {
                                "courseCode": ["COMP2002"],
                                "studyArea": ["Computer Science"],
                                "term": ["Semester 2"],
                                "dateModified": ["2026-10-28"],
                                "unitValue": ["3"],
                            },
                        },
                    ],
                }
            }
        ]

        courses = data_parser.get_all_courses_bulk(2027)
        self.assertEqual(len(courses), 2)
        self.assertEqual(courses[0]["code"], "ACCT1001")
        self.assertEqual(courses[0]["subject"], "Accounting")
        self.assertEqual(courses[1]["code"], "COMP2002")

    def test_parse_course_document_full_syllabus(self):
        """Verify parse_course_document extracts overview, outcomes, requisites, textbooks, assessments."""
        sample_html = """
        <html>
        <head>
            <meta property="courseCode" content="ACCT1001">
            <meta property="unitValue" content="6">
            <meta property="dateModified" content="2026-10-27">
        </head>
        <body>
            <div class="cmp-herobanner">
                <h1 class="cmp-herobanner__title">Financial Accounting 1</h1>
            </div>
            <section id="overview">
                <h2>Course overview</h2>
                <p>An introduction to the principles and practice of accounting.</p>
            </section>
            <section id="outcomes">
                <h2>Course learning outcomes</h2>
                <ul>
                    <li>Understand accounting concepts</li>
                    <li>Prepare financial statements</li>
                </ul>
            </section>
            <section id="requisites">
                <div class="study-page-section__content">
                    <h2>Prerequisite(s)</h2>
                    <div>Must have completed ACCT1000</div>
                    <h2>Corequisite(s)</h2>
                    <div>N/A</div>
                    <h2>Antirequisite(s)</h2>
                    <div>Must not have completed ACCT1012</div>
                </div>
                <div class="study-page-section__sidebar">
                    <h3>Textbook(s)</h3>
                    <p>Accounting Principles 10th Ed</p>
                    <h3>Assessment</h3>
                    <ul>
                        <li>Problem Solving Task – 20%</li>
                        <li>Exam – 50%</li>
                    </ul>
                </div>
            </section>
        </body>
        </html>
        """
        doc = data_parser.parse_course_document(sample_html)
        self.assertEqual(doc["title"], "Financial Accounting 1")
        self.assertEqual(doc["course_code"], "ACCT1001")
        self.assertEqual(doc["units"], "6")
        self.assertEqual(doc["date_modified"], "2026-10-27")
        self.assertIn("An introduction to the principles", doc["course_overview"])
        self.assertEqual(len(doc["learning_outcomes"]), 2)
        self.assertIn("Understand accounting concepts", doc["learning_outcomes"])
        self.assertIn("ACCT1000", doc["prerequisites"])
        self.assertEqual(doc["textbooks"], "Accounting Principles 10th Ed")
        self.assertEqual(len(doc["assessments"]), 2)
        self.assertEqual(doc["assessments"][0]["title"], "Problem Solving Task")
        self.assertEqual(doc["assessments"][0]["weighting"], "20%")
        self.assertEqual(doc["assessments"][1]["title"], "Exam")
        self.assertEqual(doc["assessments"][1]["weighting"], "50%")


if __name__ == "__main__":
    unittest.main()
