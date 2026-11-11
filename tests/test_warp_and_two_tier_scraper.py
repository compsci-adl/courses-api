import os
import unittest
from threading import Lock
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import data_fetcher
from models import Base, Course
from scraper import process_course, scrape_single_year


class TestWarpAndTwoTierScraper(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

    def tearDown(self):
        self.session.close()

    def test_warp_active_bypasses_proxies_and_routes_direct(self):
        with patch.dict(os.environ, {"USE_WARP": "true"}):
            fetcher = data_fetcher.DataFetcher(
                "COMP 1102", year=2027, use_class_url=True
            )
            self.assertTrue(fetcher._is_warp_active())

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = (
                "<html><h1>COMP 1102</h1><main>Course details</main></html>"
            )
            mock_resp.content = mock_resp.text.encode("utf-8")

            with patch.object(
                fetcher, "_execute_http_get", return_value=mock_resp
            ) as mock_get:
                with patch.object(fetcher, "get_random_proxy") as mock_proxy:
                    res = fetcher.get()
                    self.assertIsNotNone(res)
                    self.assertEqual(res.get("h1"), "COMP 1102")
                    mock_get.assert_called_once()
                    args, kwargs = mock_get.call_args
                    # When WARP is active, requests route via SOCKS5 proxy
                    self.assertEqual(
                        kwargs.get("proxy"),
                        {
                            "http": "socks5h://127.0.0.1:40000",
                            "https": "socks5h://127.0.0.1:40000",
                        },
                    )
                    # Must NOT use or rotate through public proxies when WARP is active
                    mock_proxy.assert_not_called()

    @patch("data_parser.get_course_details")
    @patch("data_parser.get_course_class_list")
    @patch("scraper.write_queue")
    def test_metadata_ingestion_eliminates_get_course_details(
        self, mock_queue, mock_get_classes, mock_get_details
    ):
        progress = MagicMock()
        subject_task = MagicMock()
        lock = Lock()

        # Funnelback rich course item
        course = {
            "code": "COMP 1102",
            "title": "Object Oriented Programming",
            "units": 3,
            "terms": ["Semester 1", "Semester 2"],
            "campus": ["North Terrace"],
            "level_of_study": "Undergraduate",
            "course_coordinator": "Dr. Coordinator",
            "course_overview": "Comprehensive OOP in C++ and Java.",
            "prerequisites": "COMP 1101",
            "corequisites": "N/A",
            "antirequisites": "N/A",
            "raw_meta": {"courseCode": "COMP 1102"},
        }
        subject = {"subject": "Computer Science"}

        mock_get_classes.return_value = {
            "status": "success",
            "success": True,
            "classes": [],
        }

        process_course(
            course=course,
            year=2027,
            subject=subject,
            engine=self.engine,
            progress=progress,
            subject_task=subject_task,
            lock=lock,
            scrape_classes=True,
            existing_courses_for_year={},
            harvested_course_ids=set(),
        )

        # Static course details GET call must be eliminated
        mock_get_details.assert_not_called()

        # Class list should be fetched for active terms
        mock_get_classes.assert_called_once()

        # Course was placed on the write queue with correct metadata
        queued_items = [call[0][0] for call in mock_queue.put.call_args_list]
        queued_courses = [c for c in queued_items if isinstance(c, Course)]
        self.assertEqual(len(queued_courses), 1)
        self.assertEqual(queued_courses[0].title, "Object Oriented Programming")
        self.assertEqual(queued_courses[0].course_coordinator, "Dr. Coordinator")

    @patch("data_parser.get_course_details")
    @patch("data_parser.get_course_class_list")
    def test_course_without_active_terms_skips_class_crawling(
        self, mock_get_classes, mock_get_details
    ):
        progress = MagicMock()
        subject_task = MagicMock()
        lock = Lock()
        harvested = set()

        # Course with explicitly empty terms (not offered / research placeholder)
        course = {
            "code": "RESEARCH 9999",
            "title": "Placeholder Research",
            "units": 6,
            "terms": [],
            "raw_meta": {},
        }
        subject = {"subject": "Research"}

        process_course(
            course=course,
            year=2027,
            subject=subject,
            engine=self.engine,
            progress=progress,
            subject_task=subject_task,
            lock=lock,
            scrape_classes=True,
            existing_courses_for_year={},
            harvested_course_ids=harvested,
        )

        mock_get_details.assert_not_called()
        mock_get_classes.assert_not_called()
        self.assertGreater(len(harvested), 0)

    @patch("scraper.bulk_replace_classes_for_year")
    @patch("scraper.data_parser.get_subjects")
    @patch("scraper.get_existing_class_count_for_year")
    @patch("scraper.get_existing_course_ids_for_year")
    @patch("scraper.get_previous_years_course_data")
    def test_bulk_replacement_proceeds_without_circuit_breaker(
        self,
        mock_prev,
        mock_course_ids,
        mock_class_count,
        mock_get_subjects,
        mock_bulk_replace,
    ):
        mock_class_count.return_value = 100
        mock_course_ids.return_value = {}
        mock_prev.return_value = {}
        mock_get_subjects.return_value = {"subjects": [{"subject": "Computer Science"}]}

        def fake_process_subject(*args, **kwargs):
            collected = kwargs.get("collected_classes")
            if collected is None and len(args) > 10:
                collected = args[10]
            if collected is not None:
                for i in range(20):
                    collected.append(
                        {
                            "id": f"cls_{i}",
                            "course_id": "c1",
                            "class_nbr": i,
                            "year": 2027,
                        }
                    )

        with patch("scraper.process_subject", side_effect=fake_process_subject):
            scrape_single_year(
                year=2027,
                engine=self.engine,
                is_active=True,
                scrape_classes_override=True,
            )

        # Bulk replace is invoked directly without being blocked by a circuit breaker
        mock_bulk_replace.assert_called_once()
