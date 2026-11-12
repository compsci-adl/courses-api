"""Tests for scraper.py's unit-value parsing.

Regression test for the "units" scraping bug: `process_course` looked
up `course_details["unit_value"]`, but `data_parser.get_course_details`
returns the field under the key "units", so the lookup always fell
back to the default of 6. Additionally, the raw scraped value could
contain non-digit text (e.g. "3 units"), which crashed a bare
`int(...)` conversion.
"""

import os
import sys
import unittest
from threading import Lock
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from scraper import parse_units, process_course  # noqa: E402


def test_parse_units_plain_digits():
    assert parse_units("12") == 12


def test_parse_units_with_trailing_text():
    """Scraped text like "3 units" should still parse to 3, not crash."""
    assert parse_units("3 units") == 3


def test_parse_units_missing_falls_back_to_default():
    assert parse_units(None) == 6


def test_parse_units_unparseable_falls_back_to_default():
    assert parse_units("N/A") == 6


def test_parse_units_custom_default():
    assert parse_units(None, default=0) == 0


class TestScraper(unittest.TestCase):
    def test_parse_units_plain_digits(self):
        test_parse_units_plain_digits()

    def test_parse_units_with_trailing_text(self):
        test_parse_units_with_trailing_text()

    def test_parse_units_missing_falls_back_to_default(self):
        test_parse_units_missing_falls_back_to_default()

    def test_parse_units_unparseable_falls_back_to_default(self):
        test_parse_units_unparseable_falls_back_to_default()

    def test_parse_units_custom_default(self):
        test_parse_units_custom_default()


class TestSelectiveClassReplace(unittest.TestCase):
    """Verify that bulk_replace_classes_for_year preserves classes for unreached courses."""

    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from models import Base, Course, CourseClass, Subject

        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(bind=self.engine)

        s = Subject(id="subj_1", name="Computer Science")
        self.session.add(s)
        self.session.commit()

        # Seed 2 courses for 2026
        c1 = Course(
            id="cid_1",
            course_id=101,
            year=2026,
            terms="Semester 1",
            subject="Computer Science",
            course_code="COMP 1001",
            title="Intro CS",
            campus="North Terrace",
            units=3,
            course_level="Undergraduate",
            prerequisites="None",
            corequisites="None",
            antirequisites="None",
            university_wide_elective=False,
            url="http://example.com/1",
        )
        c2 = Course(
            id="cid_2",
            course_id=102,
            year=2026,
            terms="Semester 2",
            subject="Computer Science",
            course_code="COMP 1002",
            title="Data Structures",
            campus="North Terrace",
            units=3,
            course_level="Undergraduate",
            prerequisites="None",
            corequisites="None",
            antirequisites="None",
            university_wide_elective=False,
            url="http://example.com/2",
        )
        self.session.add_all([c1, c2])

        # Seed initial classes for both courses
        cl1 = CourseClass(
            id="cl_1", year=2026, class_nbr=1111, section="LE01", course_id="cid_1"
        )
        cl2 = CourseClass(
            id="cl_2", year=2026, class_nbr=2222, section="LE01", course_id="cid_2"
        )
        self.session.add_all([cl1, cl2])
        self.session.commit()

    def tearDown(self):
        self.session.close()

    def test_selective_replace_preserves_unfetched_course_classes(self):
        from db_service import bulk_replace_classes_for_year
        from models import CourseClass

        # Only cid_1 was fetched this run, with an updated class number
        new_classes_data = [
            {
                "id": "cl_1_new",
                "year": 2026,
                "course_id": "cid_1",
                "class_nbr": 1112,
                "section": "LE01",
                "component": "Lecture",
                "size": 100,
                "available": 50,
            }
        ]

        # Call with fetched_course_ids = {"cid_1"}
        bulk_replace_classes_for_year(
            self.session,
            year=2026,
            classes_data=new_classes_data,
            meetings_data=[],
            fetched_course_ids={"cid_1"},
        )

        # cid_1 should have the new class
        cid_1_classes = (
            self.session.query(CourseClass).filter_by(course_id="cid_1").all()
        )
        self.assertEqual(len(cid_1_classes), 1)
        self.assertEqual(cid_1_classes[0].class_nbr, 1112)

        # cid_2 was NOT in fetched_course_ids (e.g. proxy timeout/WAF blocked), so its class MUST be preserved
        cid_2_classes = (
            self.session.query(CourseClass).filter_by(course_id="cid_2").all()
        )
        self.assertEqual(len(cid_2_classes), 1)
        self.assertEqual(cid_2_classes[0].class_nbr, 2222)

    def test_extract_and_queue_classes_tracks_fetched_ids(self):
        from threading import Lock
        from unittest.mock import patch

        from scraper import _extract_and_queue_classes

        lock = Lock()
        fetched_ids = set()
        collected_classes = []

        mock_classes = {
            "classes": [
                {
                    "component": "Lecture",
                    "class_number": "5555",
                    "section": "LE01",
                    "size": 50,
                    "available": 10,
                }
            ]
        }

        with patch("data_parser.get_course_class_list", return_value=mock_classes):
            res = _extract_and_queue_classes(
                "COMP 1001",
                2026,
                "cid_1",
                raw_html=None,
                collected_classes=collected_classes,
                collected_meetings=None,
                lock=lock,
                fetched_course_ids=fetched_ids,
            )

        self.assertTrue(res)
        self.assertIn("cid_1", fetched_ids)
        self.assertEqual(len(collected_classes), 1)

    def test_extract_and_queue_classes_no_classes_valid_page_tracks_id(self):
        """Courses confirmed to legitimately have 0 classes should be tracked and return True."""
        from threading import Lock
        from unittest.mock import patch

        from scraper import _extract_and_queue_classes

        lock = Lock()
        fetched_ids = set()
        collected_classes = []

        mock_classes = {
            "classes": [],
            "is_valid_page": True,
        }

        with patch("data_parser.get_course_class_list", return_value=mock_classes):
            res = _extract_and_queue_classes(
                "ARCY 2004",
                2026,
                "cid_arcy",
                raw_html=None,
                collected_classes=collected_classes,
                collected_meetings=None,
                lock=lock,
                fetched_course_ids=fetched_ids,
            )

        self.assertTrue(res)
        self.assertIn("cid_arcy", fetched_ids)
        self.assertEqual(len(collected_classes), 0)

    def test_extract_and_queue_classes_blocked_page_does_not_track_id(self):
        """Blocked or failed fetches must return False and NOT be added to fetched_ids."""
        from threading import Lock
        from unittest.mock import patch

        from scraper import _extract_and_queue_classes

        lock = Lock()
        fetched_ids = set()
        collected_classes = []

        mock_classes = {
            "classes": [],
            "is_valid_page": False,
        }

        with patch("data_parser.get_course_class_list", return_value=mock_classes):
            res = _extract_and_queue_classes(
                "COMP 1001",
                2026,
                "cid_blocked",
                raw_html=None,
                collected_classes=collected_classes,
                collected_meetings=None,
                lock=lock,
                fetched_course_ids=fetched_ids,
            )

        self.assertFalse(res)
        self.assertNotIn("cid_blocked", fetched_ids)
        self.assertEqual(len(collected_classes), 0)

    @patch("scraper.write_queue")
    @patch("scraper._fetch_single_outline_candidate")
    @patch("data_parser.get_course_details")
    def test_process_course_uses_page_syllabus_directly(
        self, mock_details, mock_outline, mock_queue
    ):
        """When course page contains syllabus, use it directly without calling external outline URLs."""
        from threading import Lock

        from scraper import process_course

        mock_details.return_value = {
            "title": "Financial Accounting 1",
            "course_id": 9999,
            "units": 6,
            "campus": "North Terrace",
            "terms": ["Semester 1"],
            "course_overview": "Introduction to accounting",
            "learning_outcomes": ["Understand accounting", "Prepare reports"],
            "assessments": [
                {"title": "Project", "weighting": "40%"},
                {"title": "Exam", "weighting": "60%"},
            ],
            "prerequisites": "None",
            "corequisites": "None",
            "antirequisites": "None",
            "html": "<div>dummy html</div>",
        }

        progress = MagicMock()
        task = MagicMock()
        lock = Lock()

        process_course(
            course={"code": "ACCT 1001"},
            year=2027,
            subject={"subject": "Accounting"},
            engine=self.engine,
            progress=progress,
            subject_task=task,
            lock=lock,
            scrape_classes=False,
        )

        # External outline candidate crawler should NOT have been invoked
        mock_outline.assert_not_called()

        # write_queue should have received course, 2 outcomes, and 2 assessments
        queued_items = [call_args[0][0] for call_args in mock_queue.put.call_args_list]
        from models import Assessment, Course, LearningOutcome

        courses_q = [x for x in queued_items if isinstance(x, Course)]
        outcomes_q = [x for x in queued_items if isinstance(x, LearningOutcome)]
        assessments_q = [x for x in queued_items if isinstance(x, Assessment)]

        self.assertEqual(len(courses_q), 1)
        self.assertEqual(courses_q[0].title, "Financial Accounting 1")
        self.assertEqual(len(outcomes_q), 2)
        self.assertEqual(outcomes_q[0].description, "Understand accounting")
        self.assertEqual(len(assessments_q), 2)
        self.assertEqual(assessments_q[0].title, "Project")
        self.assertEqual(assessments_q[0].weighting, "40%")

    @patch("scraper.write_queue")
    @patch("scraper.data_parser.get_course_details")
    def test_process_course_backfills_syllabus_for_existing_course(
        self, mock_details, mock_queue
    ):
        """When course exists in DB but is missing syllabus, process_course backfills outcomes/assessments."""
        from models import Assessment, LearningOutcome

        mock_details.return_value = {
            "title": "Existing Course",
            "learning_outcomes": [{"description": "Outcome 1"}],
            "assessments": [{"title": "Exam 1", "weighting": "50%"}],
            "classes": [],
            "html": "<html>...</html>",
        }

        progress = MagicMock()
        task = MagicMock()
        lock = Lock()
        existing_courses = {"TEST1001": "cid_test_1001"}
        courses_with_syllabus = set()  # cid_test_1001 not in it!

        process_course(
            course={"code": "TEST1001"},
            year=2027,
            subject={"subject": "Testing"},
            engine=self.engine,
            progress=progress,
            subject_task=task,
            lock=lock,
            scrape_classes=True,
            existing_courses_for_year=existing_courses,
            courses_with_syllabus=courses_with_syllabus,
        )

        mock_details.assert_called_once_with("TEST1001", year=2027)
        queued = [c[0][0] for c in mock_queue.put.call_args_list]
        outcomes = [x for x in queued if isinstance(x, LearningOutcome)]
        assessments = [x for x in queued if isinstance(x, Assessment)]

        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].description, "Outcome 1")
        self.assertEqual(len(assessments), 1)
        self.assertEqual(assessments[0].title, "Exam 1")
        self.assertIn("cid_test_1001", courses_with_syllabus)

    @patch("scraper.write_queue")
    @patch("scraper.data_parser.get_course_details")
    def test_process_course_updates_terms_for_existing_course(
        self, mock_details, mock_queue
    ):
        """When an existing course in DB has terms discovered from bulk or details, queue update_course."""
        mock_details.return_value = None

        progress = MagicMock()
        task = MagicMock()
        lock = Lock()
        existing_courses = {"ENGE5010": "cid_enge_5010"}
        courses_with_terms = set()  # not in DB yet!

        process_course(
            course={"code": "ENGE5010", "terms": ["Semester 1"]},
            year=2027,
            subject={"subject": "Engineering"},
            engine=self.engine,
            progress=progress,
            subject_task=task,
            lock=lock,
            scrape_classes=False,
            existing_courses_for_year=existing_courses,
            courses_with_terms=courses_with_terms,
        )

        queued = [c[0][0] for c in mock_queue.put.call_args_list]
        updates = [
            x for x in queued if isinstance(x, tuple) and x[0] == "update_course"
        ]
        self.assertTrue(len(updates) >= 1)
        update_data = updates[0][1]
        self.assertEqual(update_data["id"], "cid_enge_5010")
        self.assertEqual(update_data["terms"], "Semester 1")
        self.assertIn("cid_enge_5010", courses_with_terms)

    @patch("scraper._extract_and_queue_classes")
    @patch("scraper.write_queue")
    @patch("scraper.data_parser.get_course_details")
    def test_process_course_attempts_classes_for_course_with_terms_when_details_none(
        self, mock_details, mock_queue, mock_classes
    ):
        """If a course has terms listed, class harvesting is attempted even if course_details returns None."""
        mock_details.return_value = None

        progress = MagicMock()
        task = MagicMock()
        lock = Lock()
        existing_courses = {"ENGE5010": "cid_enge_5010"}
        courses_with_syllabus = set()  # missing syllabus

        process_course(
            course={"code": "ENGE5010", "terms": ["Semester 1"]},
            year=2027,
            subject={"subject": "Engineering"},
            engine=self.engine,
            progress=progress,
            subject_task=task,
            lock=lock,
            scrape_classes=True,
            existing_courses_for_year=existing_courses,
            courses_with_syllabus=courses_with_syllabus,
        )

        # Classes MUST be attempted with raw_html=None since course_details returned None!
        mock_classes.assert_called_once()
        call_args = mock_classes.call_args
        self.assertEqual(call_args[0][0], "ENGE5010")
        self.assertEqual(call_args[0][1], 2027)
        self.assertEqual(call_args[0][2], "cid_enge_5010")
        self.assertIsNone(call_args[1].get("raw_html"))

    @patch("scraper._extract_and_queue_classes")
    @patch("scraper.write_queue")
    @patch("scraper.data_parser.get_course_details")
    def test_process_course_skips_class_fetch_when_no_terms_and_no_syllabus_needed(
        self, mock_details, mock_queue, mock_classes
    ):
        """If a course has no terms listed anywhere and already has syllabus, skip class network crawl."""
        progress = MagicMock()
        task = MagicMock()
        lock = Lock()
        existing_courses = {"ENGE5006": "cid_enge_5006"}
        courses_with_syllabus = {"cid_enge_5006"}  # already has syllabus
        courses_with_terms = set()  # no terms in DB
        fetched_ids = set()

        process_course(
            course={"code": "ENGE5006", "terms": None},
            year=2027,
            subject={"subject": "Engineering"},
            engine=self.engine,
            progress=progress,
            subject_task=task,
            lock=lock,
            scrape_classes=True,
            existing_courses_for_year=existing_courses,
            courses_with_syllabus=courses_with_syllabus,
            courses_with_terms=courses_with_terms,
            fetched_course_ids=fetched_ids,
        )

        # No network calls to details or classes
        mock_details.assert_not_called()
        mock_classes.assert_not_called()
        # Successfully marked as fetched so it is retained/cleared correctly in DB
        self.assertIn("cid_enge_5006", fetched_ids)


if __name__ == "__main__":
    unittest.main()
