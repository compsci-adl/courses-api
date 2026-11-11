import asyncio
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from threading import Lock
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import data_fetcher
import data_parser
from db_service import (
    bulk_replace_classes_for_year,
    compute_course_hash,
    ensure_healthy_database,
    ensure_schema_up_to_date,
    get_existing_course_ids_for_year,
    get_previous_years_course_data,
    is_legacy_database,
    purge_year_data,
    reset_legacy_database,
)
from models import (
    Assessment,
    Base,
    Course,
    CourseClass,
    LearningOutcome,
    Meetings,
    Subject,
)
from s3_service import S3SyncManager
from scraper import (
    _extract_and_queue_classes,
    process_course,
    run_pipeline,
    scrape_single_year,
)
from server import app, get_db
from year_discovery import YearDiscoveryService


class TestMultiYearPipeline(unittest.TestCase):
    def setUp(self):
        # Set up an in-memory SQLite database
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

        # Seed subjects
        subj = Subject(id="subj_cs", name="Computer Science")
        self.session.add(subj)
        self.session.commit()

    def tearDown(self):
        self.session.close()

    def test_purge_year_data_atomic(self):
        # Add courses for 2026 and 2027
        c2026 = Course(
            id="c2026_1",
            course_id=101,
            year=2026,
            terms="Semester 1",
            subject="Computer Science",
            course_code="COMP101",
            title="Intro CS",
            campus="City East",
            units=3,
            course_level="1",
            prerequisites="N/A",
            corequisites="N/A",
            antirequisites="N/A",
            university_wide_elective=False,
            url="https://adelaide.edu.au/course/comp101",
        )
        c2027 = Course(
            id="c2027_1",
            course_id=101,  # Same course_id, different year
            year=2027,
            terms="Semester 1",
            subject="Computer Science",
            course_code="COMP101",
            title="Intro CS",
            campus="City East",
            units=3,
            course_level="1",
            prerequisites="N/A",
            corequisites="N/A",
            antirequisites="N/A",
            university_wide_elective=False,
            url="https://adelaide.edu.au/course/comp101",
        )
        cls2026 = CourseClass(
            id="cls2026_1",
            year=2026,
            class_nbr=1111,
            section="01",
            size=30,
            available=10,
            component="Lecture",
            course_id="c2026_1",
        )
        cls2027 = CourseClass(
            id="cls2027_1",
            year=2027,
            class_nbr=1111,
            section="01",
            size=30,
            available=10,
            component="Lecture",
            course_id="c2027_1",
        )
        self.session.add_all([c2026, c2027, cls2026, cls2027])
        self.session.commit()

        # Purge only 2026
        purged = purge_year_data(self.session, 2026)
        self.assertEqual(purged["courses"], 1)
        self.assertEqual(purged["classes"], 1)

        # Check 2027 remains intact
        remaining_courses = self.session.query(Course).all()
        self.assertEqual(len(remaining_courses), 1)
        self.assertEqual(remaining_courses[0].year, 2027)

        remaining_classes = self.session.query(CourseClass).all()
        self.assertEqual(len(remaining_classes), 1)
        self.assertEqual(remaining_classes[0].year, 2027)

    def test_cross_listed_classes_share_class_nbr_across_courses(self):
        """Cross-listed courses often share class_nbr, section, and group.

        Verifies that inserting identical class_nbr across different course_ids
        succeeds without IntegrityError.
        """
        c1 = Course(
            id="c_undergrad",
            course_id=3001,
            year=2026,
            terms="Semester 1",
            subject="Computer Science",
            course_code="COMP3001",
            title="Operating Systems UG",
            campus="City East",
            units=3,
            course_level="3",
            prerequisites="N/A",
            corequisites="N/A",
            antirequisites="N/A",
            university_wide_elective=False,
            url="https://adelaide.edu.au/course/comp3001",
        )
        c2 = Course(
            id="c_postgrad",
            course_id=7001,
            year=2026,
            terms="Semester 1",
            subject="Computer Science",
            course_code="COMP7001",
            title="Operating Systems PG",
            campus="City East",
            units=3,
            course_level="7",
            prerequisites="N/A",
            corequisites="N/A",
            antirequisites="N/A",
            university_wide_elective=False,
            url="https://adelaide.edu.au/course/comp7001",
        )
        self.session.add_all([c1, c2])
        self.session.commit()

        # Cross-listed class with identical year, class_nbr, section, group
        cls1 = CourseClass(
            id="cls_ug",
            year=2026,
            class_nbr=30041,
            section="SE01",
            group="1",
            size=50,
            available=20,
            component="Enrolment class: Lecture",
            course_id="c_undergrad",
        )
        cls2 = CourseClass(
            id="cls_pg",
            year=2026,
            class_nbr=30041,
            section="SE01",
            group="1",
            size=50,
            available=20,
            component="Enrolment class: Lecture",
            course_id="c_postgrad",
        )
        # Should not raise IntegrityError
        self.session.add_all([cls1, cls2])
        self.session.commit()

        classes = (
            self.session.query(CourseClass).filter(CourseClass.class_nbr == 30041).all()
        )
        self.assertEqual(len(classes), 2)
        course_ids = {c.course_id for c in classes}
        self.assertEqual(course_ids, {"c_undergrad", "c_postgrad"})

    def test_class_upsert_idempotent(self):
        """Test on_conflict upsert on CourseClass."""
        c = Course(
            id="c_os",
            course_id=3002,
            year=2026,
            terms="Semester 1",
            subject="Computer Science",
            course_code="COMP3002",
            title="Operating Systems",
            campus="City East",
            units=3,
            course_level="3",
            prerequisites="N/A",
            corequisites="N/A",
            antirequisites="N/A",
            university_wide_elective=False,
            url="https://adelaide.edu.au/course/comp3002",
        )
        self.session.add(c)
        self.session.commit()

        # Initial insert
        class_data = {
            "id": "cls_os_1",
            "year": 2026,
            "class_nbr": 44444,
            "section": "01",
            "group": "A",
            "size": 30,
            "available": 15,
            "component": "Lecture",
            "course_id": "c_os",
        }
        stmt = sqlite_insert(CourseClass).values(class_data)
        self.session.execute(stmt)
        self.session.commit()

        # Update available seats via ON CONFLICT DO UPDATE
        updated_data = dict(class_data)
        updated_data["available"] = 5
        stmt2 = sqlite_insert(CourseClass).values(updated_data)
        stmt2 = stmt2.on_conflict_do_update(
            index_elements=["year", "course_id", "class_nbr", "section", "group"],
            set_={"available": stmt2.excluded.available},
        )
        self.session.execute(stmt2)
        self.session.commit()

        db_cls = (
            self.session.query(CourseClass).filter(CourseClass.class_nbr == 44444).one()
        )
        self.assertEqual(db_cls.available, 5)

    def test_get_existing_course_ids_for_year(self):
        c2026 = Course(
            id="cid_2026_comp",
            course_id=3003,
            year=2026,
            terms="Semester 1",
            subject="Computer Science",
            course_code="COMP 3003",
            title="Parallel Programming",
            campus="City East",
            units=3,
            course_level="3",
            prerequisites="N/A",
            corequisites="N/A",
            antirequisites="N/A",
            university_wide_elective=False,
            url="https://adelaide.edu.au/course/comp3003",
        )
        c2027 = Course(
            id="cid_2027_comp",
            course_id=3003,
            year=2027,
            terms="Semester 1",
            subject="Computer Science",
            course_code="COMP 3003",
            title="Parallel Programming",
            campus="City East",
            units=3,
            course_level="3",
            prerequisites="N/A",
            corequisites="N/A",
            antirequisites="N/A",
            university_wide_elective=False,
            url="https://adelaide.edu.au/course/comp3003",
        )
        self.session.add_all([c2026, c2027])
        self.session.commit()

        lookup_2026 = get_existing_course_ids_for_year(self.session, 2026)
        # Should match raw code, normalized uppercase code without spaces, and stripped code
        self.assertEqual(lookup_2026.get("COMP 3003"), "cid_2026_comp")
        self.assertEqual(lookup_2026.get("COMP3003"), "cid_2026_comp")
        # Should NOT include 2027 cid
        self.assertNotIn("cid_2027_comp", lookup_2026.values())

    @patch("data_parser.get_course_details")
    @patch("data_parser.get_course_class_list")
    def test_process_course_skips_course_details_if_already_in_db(
        self, mock_get_classes, mock_get_details
    ):
        progress = MagicMock()
        subject_task = MagicMock()
        lock = Lock()
        collected_classes = []
        collected_meetings = []

        mock_get_classes.return_value = {
            "classes": [
                {
                    "component": "Lecture",
                    "class_number": 9999,
                    "section": "01",
                    "group": "A",
                    "size": 40,
                    "available": 20,
                    "meetings": [],
                }
            ]
        }

        existing_courses_map = {"COMP3001": "cid_existing_123"}
        course = {"code": "COMP3001"}
        subject = {"subject": "Computer Science"}

        process_course(
            course=course,
            year=2026,
            subject=subject,
            engine=self.engine,
            progress=progress,
            subject_task=subject_task,
            lock=lock,
            scrape_classes=True,
            existing_courses_for_year=existing_courses_map,
            collected_classes=collected_classes,
            collected_meetings=collected_meetings,
        )

        # Must NOT fetch course details or outlines since course is already in DB
        mock_get_details.assert_not_called()
        # Must fetch classes since scrape_classes=True
        mock_get_classes.assert_called_once_with("COMP3001", year=2026, raw_html=None)
        # Collected class should have the existing course_id
        self.assertEqual(len(collected_classes), 1)
        self.assertEqual(collected_classes[0]["course_id"], "cid_existing_123")
        self.assertEqual(collected_classes[0]["class_nbr"], 9999)

    @patch("data_parser.get_course_details")
    @patch("data_parser.get_course_class_list")
    def test_process_course_static_year_skips_all_network(
        self, mock_get_classes, mock_get_details
    ):
        progress = MagicMock()
        subject_task = MagicMock()
        lock = Lock()

        existing_courses_map = {"COMP3001": "cid_existing_123"}
        course = {"code": "COMP3001"}
        subject = {"subject": "Computer Science"}

        process_course(
            course=course,
            year=2027,
            subject=subject,
            engine=self.engine,
            progress=progress,
            subject_task=subject_task,
            lock=lock,
            scrape_classes=False,  # static future/historical year
            existing_courses_for_year=existing_courses_map,
        )

        # Neither course details nor class list should be fetched
        mock_get_details.assert_not_called()
        mock_get_classes.assert_not_called()

    def test_get_previous_years_course_data(self):
        # Add course, outcome, assessment for 2026
        c2026 = Course(
            id="cid_arcy1000",
            course_id=10101,
            year=2026,
            terms="Semester 1",
            subject="Archaeology",
            course_code="ARCY1000",
            title="Intro Archaeology",
            campus="City East",
            units=3,
            course_level="1",
            prerequisites="N/A",
            corequisites="N/A",
            antirequisites="N/A",
            university_wide_elective=False,
            url="https://adelaide.edu.au/course/arcy1000",
            content_hash="hash123",
        )
        self.session.add(c2026)
        self.session.commit()

        lo = LearningOutcome(
            id="lo_1",
            year=2026,
            course_id="cid_arcy1000",
            description="Understand archaeological theory",
            outcome_index=1,
        )
        ass = Assessment(
            id="ass_1",
            year=2026,
            course_id="cid_arcy1000",
            title="Final Essay",
            weighting="50%",
            hurdle="",
            learning_outcomes="1",
        )
        self.session.add_all([lo, ass])
        self.session.commit()

        # Query for previous years relative to 2027
        prev_data = get_previous_years_course_data(self.session, 2027)
        self.assertIn("ARCY1000", prev_data)
        item = prev_data["ARCY1000"]
        self.assertEqual(item["title"], "Intro Archaeology")
        self.assertEqual(item["year"], 2026)
        self.assertEqual(len(item["learning_outcomes"]), 1)
        self.assertEqual(
            item["learning_outcomes"][0]["description"],
            "Understand archaeological theory",
        )
        self.assertEqual(len(item["assessments"]), 1)
        self.assertEqual(item["assessments"][0]["title"], "Final Essay")

    @patch("scraper.write_queue")
    @patch("data_parser.get_course_details")
    @patch("data_parser.get_course_class_list")
    def test_process_course_reuses_previous_year_course_info(
        self, mock_get_classes, mock_get_details, mock_queue
    ):
        progress = MagicMock()
        subject_task = MagicMock()
        lock = Lock()

        course_details_web = {
            "code": "CHEM1001",
            "title": "Foundations of Chemistry",
            "course_id": 99901,
            "terms": "Semester 1",
            "campus": "North Terrace",
            "level_of_study": "Undergraduate",
            "units": 3,
            "course_coordinator": "Dr. Mole",
            "course_level": "1",
            "course_overview": "Basic chemical principles.",
            "prerequisites": "None",
            "corequisites": "None",
            "antirequisites": "None",
            "university_wide_elective": True,
            "html": "<html></html>",
        }
        mock_get_details.return_value = course_details_web

        matching_hash = compute_course_hash(course_details_web)
        previous_courses_map = {
            "CHEM1001": {
                "course_id": 99901,
                "year": 2026,
                "title": "Foundations of Chemistry",
                "subject": "Chemistry",
                "terms": "Semester 1",
                "campus": "North Terrace",
                "level_of_study": "Undergraduate",
                "units": 3,
                "course_coordinator": "Dr. Mole",
                "course_level": "1",
                "course_overview": "Basic chemical principles.",
                "prerequisites": "None",
                "corequisites": "None",
                "antirequisites": "None",
                "university_wide_elective": True,
                "course_outline_url": "https://apps.adelaide.edu.au/outline?id=123",
                "textbooks": "Chemistry 101",
                "content_hash": matching_hash,
                "learning_outcomes": [
                    {"description": "Balance reactions", "outcome_index": 1}
                ],
                "assessments": [
                    {
                        "title": "Lab Exam",
                        "weighting": "40%",
                        "hurdle": "Y",
                        "learning_outcomes": "1",
                    }
                ],
            }
        }

        course = {"code": "CHEM1001"}
        subject = {"subject": "Chemistry"}

        process_course(
            course=course,
            year=2027,
            subject=subject,
            engine=self.engine,
            progress=progress,
            subject_task=subject_task,
            lock=lock,
            scrape_classes=False,
            existing_courses_for_year={},  # Not yet in 2027
            previous_courses=previous_courses_map,  # Exists in 2026
        )

        # Web details fetched to verify hash
        mock_get_details.assert_called_once()
        mock_get_classes.assert_not_called()

        # write_queue must have received the cloned Course, LearningOutcome, and Assessment
        queued_items = [call[0][0] for call in mock_queue.put.call_args_list]
        queued_courses = [item for item in queued_items if isinstance(item, Course)]
        queued_los = [
            item for item in queued_items if isinstance(item, LearningOutcome)
        ]
        queued_assessments = [
            item for item in queued_items if isinstance(item, Assessment)
        ]

        self.assertEqual(len(queued_courses), 1)
        cloned_course = queued_courses[0]
        self.assertEqual(cloned_course.year, 2027)
        self.assertEqual(cloned_course.title, "Foundations of Chemistry")
        self.assertEqual(cloned_course.course_code, "CHEM1001")
        self.assertEqual(cloned_course.course_coordinator, "Dr. Mole")

        self.assertEqual(len(queued_los), 1)
        self.assertEqual(queued_los[0].year, 2027)
        self.assertEqual(queued_los[0].description, "Balance reactions")

        self.assertEqual(len(queued_assessments), 1)
        self.assertEqual(queued_assessments[0].year, 2027)
        self.assertEqual(queued_assessments[0].title, "Lab Exam")

    @patch("scraper.write_queue")
    @patch("data_parser.get_course_details")
    @patch("scraper.requests.get")
    def test_process_course_scrapes_fresh_outline_when_hash_differs(
        self, mock_requests_get, mock_get_details, mock_queue
    ):
        progress = MagicMock()
        subject_task = MagicMock()
        lock = Lock()

        # Web returns updated course details (prerequisites changed!)
        course_details_web = {
            "code": "CHEM1001",
            "title": "Foundations of Chemistry",
            "course_id": 99901,
            "terms": "Semester 1",
            "campus": "North Terrace",
            "level_of_study": "Undergraduate",
            "units": 3,
            "course_coordinator": "Dr. NewProf",
            "course_level": "1",
            "course_overview": "Updated advanced chemical principles.",
            "prerequisites": "Advanced Maths",
            "corequisites": "None",
            "antirequisites": "None",
            "university_wide_elective": True,
            "html": "<html></html>",
        }
        mock_get_details.return_value = course_details_web

        # Outline request returns 200 with new syllabus
        mock_outline_resp = MagicMock()
        mock_outline_resp.status_code = 200
        mock_outline_resp.text = (
            "<html><h1>Course Overview</h1><p>Aim: Master modern chemistry</p></html>"
        )
        mock_requests_get.return_value = mock_outline_resp

        # Previous year had different hash
        previous_courses_map = {
            "CHEM1001": {
                "course_id": 99901,
                "year": 2026,
                "title": "Foundations of Chemistry",
                "subject": "Chemistry",
                "content_hash": "different_old_hash_123",
                "course_outline_url": "https://apps.adelaide.edu.au/outline?id=old",
                "learning_outcomes": [],
                "assessments": [],
            }
        }

        course = {"code": "CHEM1001"}
        subject = {"subject": "Chemistry"}

        process_course(
            course=course,
            year=2027,
            subject=subject,
            engine=self.engine,
            progress=progress,
            subject_task=subject_task,
            lock=lock,
            scrape_classes=False,
            existing_courses_for_year={},
            previous_courses=previous_courses_map,
        )

        mock_get_details.assert_called_once()
        # Because hash differed, outline URL was probed
        mock_requests_get.assert_called()

    def test_ensure_healthy_database_recovers_malformed_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            good_db = base / "dev.sqlite3"
            corrupt_db = base / "src" / "dev.sqlite3"
            corrupt_db.parent.mkdir(parents=True, exist_ok=True)

            # Create a good SQLite database
            conn = sqlite3.connect(str(good_db))
            conn.execute("CREATE TABLE test (id INT);")
            conn.execute("INSERT INTO test VALUES (1);")
            conn.commit()
            conn.close()

            # Corrupt the target database by writing garbage
            with open(corrupt_db, "wb") as f:
                f.write(b"GARBAGE_NOT_A_VALID_SQLITE_DATABASE_HEADER_1234567890")

            # ensure_healthy_database should detect corruption and recover from good_db
            healthy = ensure_healthy_database(str(corrupt_db), base_dir=str(base))
            self.assertTrue(healthy)

            # Check that recovered database passes integrity check
            conn = sqlite3.connect(str(corrupt_db))
            res = conn.execute("PRAGMA integrity_check;").fetchall()
            conn.close()
            self.assertEqual(res, [("ok",)])

    @patch("data_fetcher.DataFetcher")
    def test_get_subjects_uses_reduced_timeout(self, mock_fetcher_cls):
        mock_instance = MagicMock()
        mock_instance.get.return_value = {
            "facets": [
                None,
                None,
                None,
                None,
                None,
                {"allValues": [{"data": "Biology"}]},
            ]
        }
        mock_instance.last_response.status_code = 200
        mock_fetcher_cls.return_value = mock_instance

        res = data_parser.get_subjects(2026)
        self.assertEqual(res["subjects"], [{"subject": "Biology"}])
        # Verify reduced timeout (3.0s) was passed
        mock_fetcher_cls.assert_called_once()
        _, kwargs = mock_fetcher_cls.call_args
        self.assertEqual(kwargs.get("timeout"), 3.0)

    def test_data_fetcher_timeout_configuration(self):
        fetcher_default = data_fetcher.DataFetcher("?test=1")
        self.assertEqual(fetcher_default.timeout, 3.0)

        fetcher_fast = data_fetcher.DataFetcher("?test=2", timeout=2.5)
        self.assertEqual(fetcher_fast.timeout, 2.5)

    @patch("year_discovery.httpx.Client")
    def test_discover_available_years_api_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "facets": [
                {
                    "name": "Academic Year",
                    "allValues": [{"data": "2026"}, {"data": "2027"}, {"data": "2028"}],
                }
            ]
        }
        mock_client.get.return_value = mock_resp

        years = YearDiscoveryService.discover_available_years(mock_client)
        self.assertEqual(years, [2026, 2027, 2028])

    @patch("year_discovery.httpx.Client")
    def test_discover_available_years_fallback(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.get.side_effect = Exception("Network timeout")

        years = YearDiscoveryService.discover_available_years(mock_client)
        self.assertEqual(len(years), 3)
        self.assertTrue(all(isinstance(y, int) for y in years))

    async def async_test_determine_active_year_probe(self):
        mock_client = AsyncMock()

        # Simulate 2028: no classes, 2027: no classes, 2026: classes published
        def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "2028" in url or "2027" in url:
                resp.text = "<html><body>Course Overview only</body></html>"
            else:
                resp.text = '<html><body><div class="cmp-course-accordion">Enrolment class: Lecture</div></body></html>'
            return resp

        mock_client.get.side_effect = mock_get

        active_year = await YearDiscoveryService.determine_active_year(
            mock_client, [2026, 2027, 2028]
        )
        self.assertEqual(active_year, 2026)

    def test_determine_active_year(self):
        asyncio.run(self.async_test_determine_active_year_probe())

    def test_get_already_scraped_years(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp:
            db_path = tmp.name

        engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        sess = Session()
        subj = Subject(id="subj_test", name="Test Subject")
        c2026 = Course(
            id="c_2026",
            course_id=501,
            year=2026,
            terms="Semester 1",
            subject="Test Subject",
            course_code="TEST101",
            title="Test Course 2026",
            campus="North Terrace",
            units=3,
            course_level="1",
            prerequisites="N/A",
            corequisites="N/A",
            antirequisites="N/A",
            university_wide_elective=False,
            url="https://adelaide.edu.au/course/test101",
        )
        c2027 = Course(
            id="c_2027",
            course_id=502,
            year=2027,
            terms="Semester 1",
            subject="Test Subject",
            course_code="TEST102",
            title="Test Course 2027",
            campus="North Terrace",
            units=3,
            course_level="1",
            prerequisites="N/A",
            corequisites="N/A",
            antirequisites="N/A",
            university_wide_elective=False,
            url="https://adelaide.edu.au/course/test102",
        )
        sess.add_all([subj, c2026, c2027])
        sess.commit()
        sess.close()

        stored_years = S3SyncManager.get_already_scraped_years(db_path)
        self.assertEqual(stored_years, {2026, 2027})


class TestFastAPIRouting(unittest.TestCase):
    def setUp(self):
        # Use StaticPool and check_same_thread=False for in-memory SQLite with FastAPI TestClient
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db_session = self.Session()

        # Seed data
        subj = Subject(id="subj_math", name="Mathematics")
        c2026 = Course(
            id="math_2026",
            course_id=201,
            year=2026,
            terms="Semester 1",
            subject="Mathematics",
            course_code="MATH1001",
            title="Calculus 2026",
            campus="North Terrace",
            units=3,
            course_level="1",
            prerequisites="N/A",
            corequisites="N/A",
            antirequisites="N/A",
            university_wide_elective=False,
            url="https://adelaide.edu.au/course/math1001",
        )
        c2027 = Course(
            id="math_2027",
            course_id=201,
            year=2027,
            terms="Semester 1",
            subject="Mathematics",
            course_code="MATH1001",
            title="Calculus 2027",
            campus="North Terrace",
            units=3,
            course_level="1",
            prerequisites="N/A",
            corequisites="N/A",
            antirequisites="N/A",
            university_wide_elective=False,
            url="https://adelaide.edu.au/course/math1001",
        )
        self.db_session.add_all([subj, c2026, c2027])
        self.db_session.commit()

        def override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db_session.close()

    def test_get_years_metadata(self):
        resp = self.client.get("/years")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["active_year"], 2027)  # latest year in seeded courses
        self.assertEqual(data["available_years"], [2026, 2027])

    def test_courses_defaults_to_active_year(self):
        resp = self.client.get("/courses?term=Semester 1")
        self.assertEqual(resp.status_code, 200)
        courses = resp.json().get("courses", [])
        self.assertEqual(len(courses), 1)
        self.assertEqual(courses[0]["name"]["title"], "Calculus 2027")

    def test_courses_explicit_year(self):
        resp = self.client.get("/courses?year=2026&term=Semester 1")
        self.assertEqual(resp.status_code, 200)
        courses = resp.json().get("courses", [])
        self.assertEqual(len(courses), 1)
        self.assertEqual(courses[0]["name"]["title"], "Calculus 2026")

    def test_courses_invalid_year_returns_400(self):
        resp = self.client.get("/courses?year=1999&term=Semester 1")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid year: 1999", resp.json()["detail"])

    def test_compute_course_hash_and_delta(self):
        course_a = {"title": "Intro to CS", "units": 3, "description": "CS Basics"}
        course_b = {"title": "Intro to CS", "units": 3, "description": "CS Basics"}
        course_c = {"title": "Intro to CS", "units": 6, "description": "CS Advanced"}
        self.assertEqual(compute_course_hash(course_a), compute_course_hash(course_b))
        self.assertNotEqual(
            compute_course_hash(course_a), compute_course_hash(course_c)
        )

    def test_bulk_replace_classes_for_year(self):
        # Include duplicate records in input list with identical (year, course_id, class_nbr, section, group)
        classes = [
            {
                "id": "cls_101",
                "year": 2026,
                "class_nbr": 101,
                "section": "01",
                "group": "A",
                "size": 30,
                "available": 10,
                "component": "Lecture",
                "course_id": "math_2026",
            },
            {
                # Duplicate entry with updated availability
                "id": "cls_101_dup",
                "year": 2026,
                "class_nbr": 101,
                "section": "01",
                "group": "A",
                "size": 30,
                "available": 5,
                "component": "Lecture",
                "course_id": "math_2026",
            },
        ]
        meetings = [
            {
                "id": "meet_101",
                "year": 2026,
                "dates": "01 Jan - 01 Feb",
                "days": "Monday",
                "start_time": "09:00",
                "end_time": "10:00",
                "campus": "North Terrace",
                "location": "Ingkarni Wardli",
                "instructor": "Dr. Smith",
                "course_class_id": "cls_101",
            },
            {
                # Duplicate meeting id
                "id": "meet_101",
                "year": 2026,
                "dates": "01 Jan - 01 Feb",
                "days": "Monday",
                "start_time": "09:00",
                "end_time": "10:00",
                "campus": "North Terrace",
                "location": "Ingkarni Wardli",
                "instructor": "Dr. Smith",
                "course_class_id": "cls_101",
            },
        ]
        # Should not raise IntegrityError despite duplicates in list
        bulk_replace_classes_for_year(self.db_session, 2026, classes, meetings)

        db_classes = (
            self.db_session.query(CourseClass).filter(CourseClass.year == 2026).all()
        )
        db_meetings = (
            self.db_session.query(Meetings).filter(Meetings.year == 2026).all()
        )
        self.assertEqual(len(db_classes), 1)
        self.assertEqual(len(db_meetings), 1)
        self.assertEqual(db_classes[0].available, 5)

    def test_bulk_replace_classes_for_year_scoped_course_ids(self):
        # Seed 2 courses with classes and meetings
        initial_classes = [
            {
                "id": "cls_course_a",
                "year": 2027,
                "class_nbr": 100,
                "section": "01",
                "group": "A",
                "size": 50,
                "available": 20,
                "component": "Lecture",
                "course_id": "course_a",
            },
            {
                "id": "cls_course_b",
                "year": 2027,
                "class_nbr": 200,
                "section": "01",
                "group": "A",
                "size": 40,
                "available": 15,
                "component": "Workshop",
                "course_id": "course_b",
            },
        ]
        initial_meetings = [
            {
                "id": "meet_a",
                "year": 2027,
                "dates": "01 Jan - 01 Feb",
                "days": "Monday",
                "start_time": "09:00",
                "end_time": "10:00",
                "campus": "North Terrace",
                "location": "Ligertwood",
                "instructor": "Dr. A",
                "course_class_id": "cls_course_a",
            },
            {
                "id": "meet_b",
                "year": 2027,
                "dates": "01 Jan - 01 Feb",
                "days": "Tuesday",
                "start_time": "10:00",
                "end_time": "11:00",
                "campus": "North Terrace",
                "location": "Ligertwood",
                "instructor": "Dr. B",
                "course_class_id": "cls_course_b",
            },
        ]
        bulk_replace_classes_for_year(
            self.db_session, 2027, initial_classes, initial_meetings, course_ids=None
        )

        # Replace only course_a with updated class data
        updated_classes = [
            {
                "id": "cls_course_a_new",
                "year": 2027,
                "class_nbr": 100,
                "section": "01",
                "group": "A",
                "size": 50,
                "available": 5,  # Changed availability
                "component": "Lecture",
                "course_id": "course_a",
            }
        ]
        updated_meetings = [
            {
                "id": "meet_a_new",
                "year": 2027,
                "dates": "01 Jan - 01 Feb",
                "days": "Monday",
                "start_time": "09:00",
                "end_time": "10:00",
                "campus": "North Terrace",
                "location": "Ligertwood 333",
                "instructor": "Dr. A New",
                "course_class_id": "cls_course_a_new",
            }
        ]

        bulk_replace_classes_for_year(
            self.db_session,
            2027,
            updated_classes,
            updated_meetings,
            course_ids={"course_a"},
        )

        # Verify course_a was updated
        ca = (
            self.db_session.query(CourseClass)
            .filter(CourseClass.year == 2027, CourseClass.course_id == "course_a")
            .all()
        )
        self.assertEqual(len(ca), 1)
        self.assertEqual(ca[0].available, 5)

        ma = (
            self.db_session.query(Meetings)
            .filter(
                Meetings.year == 2027, Meetings.course_class_id == "cls_course_a_new"
            )
            .all()
        )
        self.assertEqual(len(ma), 1)
        self.assertEqual(ma[0].location, "Ligertwood 333")

        # Verify course_b is completely intact (rows untouched)
        cb = (
            self.db_session.query(CourseClass)
            .filter(CourseClass.year == 2027, CourseClass.course_id == "course_b")
            .all()
        )
        self.assertEqual(len(cb), 1)
        self.assertEqual(cb[0].available, 15)

        mb = (
            self.db_session.query(Meetings)
            .filter(Meetings.year == 2027, Meetings.course_class_id == "cls_course_b")
            .all()
        )
        self.assertEqual(len(mb), 1)
        self.assertEqual(mb[0].instructor, "Dr. B")

    def test_determine_active_year_override_and_env(self):
        async def run():
            mock_client = AsyncMock()
            # Override takes highest priority
            yr = await YearDiscoveryService.determine_active_year(
                mock_client, [2026, 2027, 2028], active_year_override=2027
            )
            self.assertEqual(yr, 2027)

            # Env variable YEAR
            with patch.dict("os.environ", {"YEAR": "2027"}):
                yr = await YearDiscoveryService.determine_active_year(
                    mock_client, [2026, 2027, 2028]
                )
                self.assertEqual(yr, 2027)

            # Env variable ACTIVE_YEAR
            with patch.dict("os.environ", {"ACTIVE_YEAR": "2028"}):
                yr = await YearDiscoveryService.determine_active_year(
                    mock_client, [2026, 2027, 2028]
                )
                self.assertEqual(yr, 2028)

        asyncio.run(run())

    def test_determine_active_year_probing(self):
        async def run():
            mock_client = AsyncMock()

            # Mock probing: 2028 returns 404, 2027 returns 200 with class accordion
            def mock_get(url, **kwargs):
                resp = MagicMock()
                if "2028" in str(url):
                    resp.status_code = 404
                    resp.text = "Not Found"
                elif "2027" in str(url):
                    resp.status_code = 200
                    resp.text = '<html><div class="cmp-course-accordion">Class details</div></html>'
                else:
                    resp.status_code = 200
                    resp.text = "Old"
                return resp

            mock_client.get = AsyncMock(side_effect=mock_get)

            with patch.dict("os.environ", {}, clear=True):
                yr = await YearDiscoveryService.determine_active_year(
                    mock_client, [2026, 2027, 2028]
                )
                self.assertEqual(yr, 2027)

        asyncio.run(run())

    @patch("fetch_proxies.main")
    @patch("scraper.scrape_single_year")
    @patch("scraper.S3SyncManager")
    @patch("scraper.ensure_healthy_database")
    @patch("scraper.finalize_sqlite_for_s3")
    def test_pipeline_skips_historical_2026_and_scrapes_active_2027(
        self, mock_finalize, mock_ensure, mock_s3_class, mock_scrape_year, mock_proxies
    ):
        async def run():
            mock_s3 = MagicMock()
            mock_s3.download_database.return_value = False
            mock_s3.upload_database.return_value = True
            # Simulate 2026 already stored in DB
            mock_s3_class.get_synced_years_from_db.return_value = [2026]
            mock_s3_class.return_value = mock_s3

            with (
                patch.object(
                    YearDiscoveryService,
                    "discover_available_years",
                    return_value=[2026, 2027, 2028],
                ),
                patch.object(
                    YearDiscoveryService,
                    "determine_active_year",
                    new_callable=AsyncMock,
                ) as mock_det,
            ):
                mock_det.return_value = 2027

                with tempfile.NamedTemporaryFile(suffix=".sqlite3") as tmp_db:
                    await run_pipeline(
                        force_all_years=False,
                        db_path=tmp_db.name,
                        active_year=2027,
                    )

            # Check which years were called for scrape_single_year
            called_years = [call.args[0] for call in mock_scrape_year.call_args_list]
            # 2026 must NEVER be scraped because it's a historical year already in DB!
            self.assertNotIn(2026, called_years)
            # 2027 must be scraped with is_active=True
            self.assertIn(2027, called_years)
            for call in mock_scrape_year.call_args_list:
                yr = call.args[0]
                if yr == 2027:
                    self.assertTrue(call.kwargs["is_active"])

        asyncio.run(run())

    def test_schema_mismatch_resets_database(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = Path(tmpdir) / "test_legacy.sqlite3"
            engine = create_engine(f"sqlite:///{db_file}")
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    """
                    CREATE TABLE courses (
                        id VARCHAR PRIMARY KEY,
                        course_id INTEGER NOT NULL,
                        year INTEGER NOT NULL,
                        terms VARCHAR NOT NULL,
                        subject VARCHAR NOT NULL,
                        course_code VARCHAR NOT NULL,
                        title VARCHAR NOT NULL,
                        campus VARCHAR NOT NULL,
                        level_of_study VARCHAR,
                        units INTEGER NOT NULL,
                        course_coordinator VARCHAR,
                        course_level VARCHAR NOT NULL,
                        course_overview VARCHAR,
                        prerequisites VARCHAR NOT NULL,
                        corequisites VARCHAR NOT NULL,
                        antirequisites VARCHAR NOT NULL,
                        university_wide_elective BOOLEAN NOT NULL,
                        url VARCHAR NOT NULL,
                        course_outline_url VARCHAR,
                        textbooks VARCHAR
                    )
                    """
                )
                conn.exec_driver_sql(
                    """
                    CREATE TABLE learning_outcomes (
                        id VARCHAR PRIMARY KEY,
                        course_id VARCHAR NOT NULL,
                        description VARCHAR NOT NULL,
                        outcome_index INTEGER NOT NULL
                    )
                    """
                )
            engine.dispose()

            # Schema lacks content_hash and year in learning_outcomes -> invalid
            self.assertFalse(is_legacy_database(str(db_file)) is False)

            # Resetting legacy DB cleanly wipes it
            did_reset = reset_legacy_database(str(db_file))
            self.assertTrue(did_reset)
            self.assertFalse(db_file.exists())

            # Fresh init creates full schema
            new_engine = create_engine(f"sqlite:///{db_file}")
            ensure_schema_up_to_date(new_engine)
            new_engine.dispose()

            # Now schema matches perfectly
            self.assertFalse(is_legacy_database(str(db_file)))

    def test_bulk_replace_classes_on_legacy_table_without_initial_unique_constraint(
        self,
    ):
        # Create a database where course_classes has NO initial unique constraint on (year, course_id, class_nbr, section, group)
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.exec_driver_sql(
                """
                CREATE TABLE course_classes (
                    id VARCHAR PRIMARY KEY,
                    year INTEGER NOT NULL,
                    class_nbr INTEGER NOT NULL,
                    section VARCHAR NOT NULL,
                    size INTEGER NOT NULL,
                    available INTEGER NOT NULL,
                    component VARCHAR,
                    "group" VARCHAR,
                    course_id VARCHAR NOT NULL
                )
                """
            )
            conn.exec_driver_sql(
                """
                CREATE TABLE meetings (
                    id VARCHAR PRIMARY KEY,
                    year INTEGER NOT NULL,
                    dates VARCHAR NOT NULL,
                    days VARCHAR NOT NULL,
                    start_time VARCHAR NOT NULL,
                    end_time VARCHAR NOT NULL,
                    campus VARCHAR NOT NULL,
                    location VARCHAR NOT NULL,
                    instructor VARCHAR,
                    course_class_id VARCHAR NOT NULL
                )
                """
            )

        Session = sessionmaker(bind=engine)
        sess = Session()
        try:
            classes_data = [
                {
                    "id": "cls_1",
                    "year": 2027,
                    "class_nbr": 1001,
                    "section": "LE01",
                    "size": 50,
                    "available": 10,
                    "component": "Lecture",
                    "group": "1",
                    "course_id": "c1",
                }
            ]
            meetings_data = [
                {
                    "id": "m_1",
                    "year": 2027,
                    "dates": "1 Mar - 1 Jun",
                    "days": "Mon",
                    "start_time": "9am",
                    "end_time": "10am",
                    "campus": "North Terrace",
                    "location": "Room 1",
                    "instructor": "Staff",
                    "course_class_id": "cls_1",
                }
            ]
            # Must succeed without sqlite3.OperationalError: ON CONFLICT clause does not match...
            bulk_replace_classes_for_year(sess, 2027, classes_data, meetings_data)

            # Verify rows were inserted
            inserted_cls = sess.query(CourseClass).all()
            self.assertEqual(len(inserted_cls), 1)
            self.assertEqual(inserted_cls[0].class_nbr, 1001)
        finally:
            sess.close()

    def test_is_legacy_database_and_reset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy_db = Path(tmpdir) / "legacy.sqlite3"
            modern_db = Path(tmpdir) / "modern.sqlite3"

            # Create legacy DB (missing content_hash)
            l_conn = sqlite3.connect(str(legacy_db))
            l_conn.execute(
                """
                CREATE TABLE courses (
                    id VARCHAR PRIMARY KEY,
                    course_id INTEGER NOT NULL,
                    year INTEGER NOT NULL,
                    terms VARCHAR NOT NULL,
                    subject VARCHAR NOT NULL,
                    course_code VARCHAR NOT NULL,
                    title VARCHAR NOT NULL,
                    campus VARCHAR NOT NULL,
                    units INTEGER NOT NULL,
                    course_level VARCHAR NOT NULL,
                    prerequisites VARCHAR NOT NULL,
                    corequisites VARCHAR NOT NULL,
                    antirequisites VARCHAR NOT NULL,
                    university_wide_elective BOOLEAN NOT NULL,
                    url VARCHAR NOT NULL
                )
                """
            )
            l_conn.close()

            # Create modern DB
            m_engine = create_engine(f"sqlite:///{modern_db}")
            Base.metadata.create_all(m_engine)
            m_engine.dispose()

            # Test detection
            self.assertTrue(is_legacy_database(str(legacy_db)))
            self.assertFalse(is_legacy_database(str(modern_db)))

            # Test reset
            reset_result = reset_legacy_database(str(legacy_db))
            self.assertTrue(reset_result)
            self.assertFalse(legacy_db.exists())

            # Resetting modern DB should return False and keep file
            modern_reset = reset_legacy_database(str(modern_db))
            self.assertFalse(modern_reset)
            self.assertTrue(modern_db.exists())

    def test_get_course_codes_returns_none_on_403_so_subject_is_not_skipped(self):
        # When Funnelback returns 403, get_course_codes must return ParseResult with BLOCKED_OR_ERROR
        mock_fetcher = MagicMock()
        mock_fetcher.get.return_value = {}
        mock_fetcher.last_response = MagicMock(status_code=403)

        with (
            patch("data_fetcher.DataFetcher", return_value=mock_fetcher),
            patch("time.sleep"),
        ):
            res = data_parser.get_course_codes("Accounting", 2027)
            self.assertIsInstance(res, data_parser.ParseResult)
            self.assertEqual(res.status, data_parser.FetchStatus.BLOCKED_OR_ERROR)
            self.assertIsNone(res.data)

    def test_data_fetcher_direct_blocked_flag_on_403(self):
        # When direct connection gets 403, DataFetcher sets _direct_blocked = True
        data_fetcher.DataFetcher._direct_blocked = False
        data_fetcher.DataFetcher._direct_blocked_permanently = False
        data_fetcher.DataFetcher._direct_blocked_until = 0.0

        mock_resp = MagicMock(status_code=403)
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "", "CI": ""}, clear=False):
            with patch("curl_cffi.requests.get", return_value=mock_resp):
                fetcher = data_fetcher.DataFetcher("?test", use_proxy=False)
                fetcher.get()
                self.assertTrue(data_fetcher.DataFetcher._direct_blocked)

        # Reset flag
        data_fetcher.DataFetcher._direct_blocked = False
        data_fetcher.DataFetcher._direct_blocked_permanently = False

    @patch("scraper.fetch_proxies")
    @patch("scraper.scrape_single_year")
    @patch("scraper.S3SyncManager")
    @patch("scraper.ensure_healthy_database")
    @patch("scraper.finalize_sqlite_for_s3")
    def test_pipeline_empty_db_scrapes_2026_and_all_target_years(
        self, mock_finalize, mock_ensure, mock_s3_class, mock_scrape_year, mock_proxies
    ):
        async def run():
            mock_s3 = MagicMock()
            mock_s3.download_database.return_value = False
            mock_s3.upload_database.return_value = True
            # Simulate completely empty DB (no synced years)
            mock_s3_class.get_synced_years_from_db.return_value = set()
            mock_s3_class.return_value = mock_s3

            with (
                patch.object(
                    YearDiscoveryService,
                    "discover_available_years",
                    return_value=[2027, 2028],  # Even if 2026 missing from discovery
                ),
                patch.object(
                    YearDiscoveryService,
                    "determine_active_year",
                    new_callable=AsyncMock,
                ) as mock_det,
            ):
                mock_det.return_value = 2027

                with tempfile.NamedTemporaryFile(suffix=".sqlite3") as tmp_db:
                    await run_pipeline(
                        force_all_years=False,
                        db_path=tmp_db.name,
                        active_year=2027,
                    )

            # Check which years were called for scrape_single_year
            called_years = [call.args[0] for call in mock_scrape_year.call_args_list]
            # 2026 MUST be scraped when DB is empty!
            self.assertIn(2026, called_years)
            self.assertIn(2027, called_years)
            self.assertIn(2028, called_years)

            # 2027 is active (is_active=True), 2026 and 2028 are static (is_active=False)
            for call in mock_scrape_year.call_args_list:
                yr = call.args[0]
                if yr == 2027:
                    self.assertTrue(call.kwargs["is_active"])
                else:
                    self.assertFalse(call.kwargs["is_active"])

        asyncio.run(run())

    def test_get_subjects_dynamic_extraction_and_fallback_to_alternate_query(self):
        # Verify get_subjects extracts subjects dynamically without hardcoded constants
        # 1st call fails (403), 2nd call (unfiltered query) succeeds dynamically
        mock_fail = MagicMock()
        mock_fail.get.return_value = {}
        mock_fail.last_response = MagicMock(status_code=403)

        mock_succ = MagicMock()
        mock_succ.get.return_value = {
            "facets": [
                {
                    "name": "Area of study",
                    "allValues": [{"data": "Accounting"}, {"data": "Computer Science"}],
                }
            ]
        }
        mock_succ.last_response = MagicMock(status_code=200)

        with patch("data_fetcher.DataFetcher", side_effect=[mock_fail, mock_succ]):
            res = data_parser.get_subjects(2026, timeout=0.1)
            self.assertIn("subjects", res)
            self.assertEqual(
                res["subjects"],
                [{"subject": "Accounting"}, {"subject": "Computer Science"}],
            )

    def test_get_subjects_returns_empty_when_all_network_attempts_fail(self):
        # When all queries fail, returns empty subjects list without raising or using hardcoded defaults
        mock_fail = MagicMock()
        mock_fail.get.return_value = {}
        mock_fail.last_response = MagicMock(status_code=403)

        with patch("data_fetcher.DataFetcher", return_value=mock_fail):
            res = data_parser.get_subjects(2026, timeout=0.1)
            self.assertEqual(res, {"subjects": []})

    def test_year_discovery_always_includes_2026_to_2028(self):
        # Fallback years must always include 2026 through 2028
        fallback = YearDiscoveryService.get_fallback_years(2027)
        self.assertIn(2026, fallback)
        self.assertIn(2027, fallback)
        self.assertIn(2028, fallback)

        # discover_available_years must ensure 2026 is present even if API returns higher years
        mock_client = MagicMock()
        mock_client.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "facets": [
                    {
                        "name": "Year",
                        "allValues": [{"data": "2027"}, {"data": "2028"}],
                    }
                ]
            },
        )
        years = YearDiscoveryService.discover_available_years(mock_client)
        self.assertIn(2026, years)
        self.assertIn(2027, years)
        self.assertIn(2028, years)

    @patch("scraper.bulk_replace_classes_for_year")
    @patch("scraper.data_parser.get_subjects")
    @patch("scraper.get_existing_class_count_for_year")
    @patch("scraper.get_existing_course_ids_for_year")
    @patch("scraper.get_previous_years_course_data")
    def test_scrape_single_year_circuit_breaker_aborts_destructive_replace(
        self,
        mock_prev,
        mock_course_ids,
        mock_class_count,
        mock_get_subjects,
        mock_bulk_replace,
    ):
        # DB currently has 100 classes
        mock_class_count.return_value = 100
        mock_course_ids.return_value = {}
        mock_prev.return_value = {}
        # Subject discovery returns no subjects or empty courses
        mock_get_subjects.return_value = {"subjects": []}

        # Simulate scrape where only 10 classes were collected (less than 90% threshold)
        # We test scrape_single_year behavior
        scrape_single_year(
            year=2026,
            engine=self.engine,
            is_active=True,
            scrape_classes_override=True,
        )

        # Because 0 collected classes < 90, bulk_replace_classes_for_year must NOT be invoked
        mock_bulk_replace.assert_not_called()

    def test_extract_and_queue_classes_returns_false_on_error(self):
        with patch(
            "data_parser.get_course_class_list",
            return_value={"status": "error", "classes": []},
        ):
            res = _extract_and_queue_classes(
                course_code="COMP2002",
                year=2027,
                course_cid="cid123",
                raw_html=None,
                collected_classes=[],
                collected_meetings=[],
                lock=Lock(),
            )
            self.assertFalse(res)

    def test_process_course_puts_course_into_failed_courses_when_class_fetch_fails(
        self,
    ):
        mock_progress = MagicMock()
        mock_task = "task1"
        lock = Lock()
        failed_courses = []
        harvested_course_ids = set()

        course = {"code": "ENGE5010", "terms": "Semester 1"}
        subject = {"subject": "Engineering"}

        with patch(
            "scraper._extract_and_queue_classes",
            return_value=False,
        ):
            process_course(
                course=course,
                year=2027,
                subject=subject,
                engine=self.engine,
                progress=mock_progress,
                subject_task=mock_task,
                lock=lock,
                scrape_classes=True,
                existing_courses_for_year={"ENGE5010": "cid_enge5010"},
                failed_courses=failed_courses,
                harvested_course_ids=harvested_course_ids,
            )

        self.assertEqual(len(failed_courses), 1)
        self.assertEqual(failed_courses[0][0]["code"], "ENGE5010")
        self.assertNotIn("cid_enge5010", harvested_course_ids)
        # progress.update should not have been called for failed course
        mock_progress.update.assert_not_called()

    def test_data_fetcher_direct_blocked_immediately_in_ci(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            self.assertFalse(data_fetcher.DataFetcher._should_attempt_direct())
            self.assertTrue(data_fetcher.DataFetcher.is_direct_blocked())

    def test_data_fetcher_record_direct_failure_permanent(self):
        data_fetcher.DataFetcher._direct_blocked_permanently = False
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "", "CI": ""}, clear=False):
            data_fetcher.DataFetcher._record_direct_failure(403)
            self.assertTrue(data_fetcher.DataFetcher._direct_blocked_permanently)
            self.assertFalse(data_fetcher.DataFetcher._should_attempt_direct())
        # Reset for subsequent tests
        data_fetcher.DataFetcher._direct_blocked_permanently = False


if __name__ == "__main__":
    unittest.main()
