import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import fetch_proxies
import scraper
from data_fetcher import DataFetcher
from data_parser import fingerprint_page_content


class TestBackgroundRefresher(unittest.TestCase):
    def setUp(self):
        DataFetcher.stop_background_refresher()
        DataFetcher._proxies = [f"1.1.1.{i}:80" for i in range(120)]
        DataFetcher._proxy_failures.clear()
        DataFetcher._last_refill_start_time = 0.0
        # Wait briefly for previous thread to terminate if any
        if DataFetcher._refill_thread and DataFetcher._refill_thread.is_alive():
            DataFetcher._refill_thread.join(timeout=1.0)

    def tearDown(self):
        DataFetcher.stop_background_refresher()

    def test_save_working_proxies_atomic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target_path = os.path.join(tmpdir, "proxies.txt")
            proxies = ["10.0.0.1:80", "10.0.0.2:80"]
            fetch_proxies.save_working_proxies(proxies, filename=target_path)
            self.assertTrue(os.path.exists(target_path))
            with open(target_path, "r", encoding="utf-8") as f:
                content = f.read().splitlines()
            self.assertEqual(content, proxies)

    @patch("fetch_proxies.test_proxy")
    @patch("fetch_proxies.fetch_proxies")
    def test_discover_working_proxies_target_and_exclude(self, mock_fetch, mock_test):
        mock_fetch.return_value = [f"10.0.0.{i}:80" for i in range(1, 20)]
        mock_test.side_effect = lambda p: p  # All succeed
        exclude = {"10.0.0.1:80", "10.0.0.2:80"}

        result = fetch_proxies.discover_working_proxies(
            target=5, exclude=exclude, show_progress=False
        )
        self.assertEqual(len(result), 5)
        for p in result:
            self.assertNotIn(p, exclude)

    @patch("fetch_proxies.test_proxy")
    @patch("fetch_proxies.fetch_proxies")
    def test_discover_working_proxies_stop_event(self, mock_fetch, mock_test):
        mock_fetch.return_value = [f"10.0.0.{i}:80" for i in range(1, 50)]
        mock_test.side_effect = lambda p: p
        stop_event = threading.Event()
        stop_event.set()

        result = fetch_proxies.discover_working_proxies(
            target=10, stop_event=stop_event
        )
        self.assertEqual(result, [])

    @patch("fetch_proxies.discover_working_proxies")
    @patch("fetch_proxies.save_working_proxies")
    def test_background_refresher_triggers_when_low(self, mock_save, mock_discover):
        mock_discover.return_value = ["192.168.1.1:80", "192.168.1.2:80"]
        DataFetcher._proxies = [f"1.1.1.{i}:80" for i in range(50)]  # < 100
        DataFetcher.start_background_refresher()
        DataFetcher.trigger_refill_if_low()

        # Refresher runs in daemon thread, wait briefly
        for _ in range(20):
            time.sleep(0.05)
            if mock_discover.called:
                break
        self.assertTrue(mock_discover.called)
        DataFetcher.stop_background_refresher()

    def test_get_random_proxy_does_not_block_calling_thread(self):
        # Calling get_random_proxy with low count should return immediately and not block
        DataFetcher._proxies = ["1.1.1.1:80", "1.1.1.2:80"]
        fetcher = DataFetcher("?test=1", use_proxy=True)

        start = time.time()
        proxy = fetcher.get_random_proxy()
        duration = time.time() - start

        self.assertIsNotNone(proxy)
        self.assertLess(duration, 0.5)


class TestFingerprintingAndNoTimetable(unittest.TestCase):
    def test_fingerprint_page_content_identical_despite_scripts_comments(self):
        html1 = """
        <html>
        <head><script>var token = 'abc123';</script><style>.box { color: red; }</style></head>
        <body>
            <!-- Random comment -->
            <h1>Course Heading</h1>
            <p>Some actual text content</p>
        </body>
        </html>
        """
        html2 = """
        <html>
        <head><script>var token = 'xyz999'; console.log('diff');</script><style>.box { color: blue; }</style></head>
        <body>
            <!-- Another different comment -->
            <h1>Course Heading</h1>
            <p>Some actual text content</p>
        </body>
        </html>
        """
        fp1 = fingerprint_page_content(html1)
        fp2 = fingerprint_page_content(html2)
        self.assertEqual(fp1, fp2)

    def test_fingerprint_page_content_different_text(self):
        html1 = "<html><body><h1>Course Heading 1</h1><p>Content A</p></body></html>"
        html2 = "<html><body><h1>Course Heading 2</h1><p>Content B</p></body></html>"
        self.assertNotEqual(
            fingerprint_page_content(html1), fingerprint_page_content(html2)
        )

    def test_no_timetable_confirmation_logic(self):
        """Test _extract_and_queue_classes with marker-missing pages:
        1st attempt records fingerprint and returns False (triggering retry)
        2nd attempt with identical fingerprint on different proxy returns NO_TIMETABLE
        """
        fake_html = (
            "<html><body>" + ("<p>No timetable here</p>" * 100) + "</body></html>"
        )
        collected_classes = []
        collected_meetings = []
        marker_missing_attempts = {}
        lock = threading.Lock()

        with patch("data_parser.data_fetcher.DataFetcher") as mock_fetcher_cls:
            mock_fetcher = MagicMock()
            mock_fetcher_cls.return_value = mock_fetcher
            mock_fetcher.last_response.status_code = 200
            mock_fetcher.last_response.text = fake_html
            mock_fetcher.get.return_value = {"html": fake_html}
            mock_fetcher.last_used_proxy = {
                "http": "http://1.1.1.1:80",
                "https": "http://1.1.1.1:80",
            }

            # 1st attempt with proxy 1
            res1 = scraper._extract_and_queue_classes(
                course_code="TEST1001",
                year=2027,
                course_cid="cid_1001",
                raw_html=None,
                collected_classes=collected_classes,
                collected_meetings=collected_meetings,
                lock=lock,
                terms="Semester 1",
                marker_missing_attempts=marker_missing_attempts,
            )
            self.assertFalse(res1)
            code_key = (2027, "TEST1001")
            self.assertIn(code_key, marker_missing_attempts)
            self.assertEqual(len(marker_missing_attempts[code_key]), 1)

            # 2nd attempt with same proxy (e.g. proxy couldn't be distinguished) -> still False
            res2_same_proxy = scraper._extract_and_queue_classes(
                course_code="TEST1001",
                year=2027,
                course_cid="cid_1001",
                raw_html=None,
                collected_classes=collected_classes,
                collected_meetings=collected_meetings,
                lock=lock,
                terms="Semester 1",
                marker_missing_attempts=marker_missing_attempts,
            )
            self.assertFalse(res2_same_proxy)

            # 2nd attempt with different proxy -> reaches confirmation threshold!
            mock_fetcher.last_used_proxy = {
                "http": "http://2.2.2.2:80",
                "https": "http://2.2.2.2:80",
            }
            res2_diff_proxy = scraper._extract_and_queue_classes(
                course_code="TEST1001",
                year=2027,
                course_cid="cid_1001",
                raw_html=None,
                collected_classes=collected_classes,
                collected_meetings=collected_meetings,
                lock=lock,
                terms="Semester 1",
                marker_missing_attempts=marker_missing_attempts,
            )
            self.assertEqual(res2_diff_proxy, "NO_TIMETABLE")
            # No classes or meetings queued
            self.assertEqual(len(collected_classes), 0)
            self.assertEqual(len(collected_meetings), 0)

    def test_marker_missing_different_fingerprints_keeps_retrying(self):
        """If HTML changes between attempts (different fingerprint), it shouldn't confirm NO_TIMETABLE."""
        fake_html_1 = (
            "<html><body>" + ("<p>Text version 1</p>" * 100) + "</body></html>"
        )
        fake_html_2 = (
            "<html><body>" + ("<p>Text version 2</p>" * 100) + "</body></html>"
        )
        collected_classes = []
        collected_meetings = []
        marker_missing_attempts = {}
        lock = threading.Lock()

        with patch("data_parser.data_fetcher.DataFetcher") as mock_fetcher_cls:
            mock_fetcher = MagicMock()
            mock_fetcher_cls.return_value = mock_fetcher
            mock_fetcher.last_response.status_code = 200
            mock_fetcher.last_response.text = fake_html_1
            mock_fetcher.get.return_value = {"html": fake_html_1}
            mock_fetcher.last_used_proxy = {
                "http": "http://1.1.1.1:80",
                "https": "http://1.1.1.1:80",
            }

            # 1st attempt
            res1 = scraper._extract_and_queue_classes(
                course_code="TEST1002",
                year=2027,
                course_cid="cid_1002",
                raw_html=None,
                collected_classes=collected_classes,
                collected_meetings=collected_meetings,
                lock=lock,
                terms="Semester 1",
                marker_missing_attempts=marker_missing_attempts,
            )
            self.assertFalse(res1)

            # 2nd attempt with different fingerprint
            mock_fetcher.last_response.text = fake_html_2
            mock_fetcher.get.return_value = {"html": fake_html_2}
            mock_fetcher.last_used_proxy = {
                "http": "http://2.2.2.2:80",
                "https": "http://2.2.2.2:80",
            }
            res2 = scraper._extract_and_queue_classes(
                course_code="TEST1002",
                year=2027,
                course_cid="cid_1002",
                raw_html=None,
                collected_classes=collected_classes,
                collected_meetings=collected_meetings,
                lock=lock,
                terms="Semester 1",
                marker_missing_attempts=marker_missing_attempts,
            )
            # Should NOT be NO_TIMETABLE because fingerprints differed
            self.assertFalse(res2)

    def test_process_course_no_timetable_handling(self):
        """When _extract_and_queue_classes returns NO_TIMETABLE, process_course should NOT queue to failed_courses."""
        course = {
            "code": "TEST9999",
            "year": 2027,
            "terms": "Semester 1",
            "subject": "TEST",
            "catalogue_number": "9999",
            "course_title": "Test Course",
        }
        collected_classes = []
        collected_meetings = []
        failed_courses = []
        harvested_course_ids = set()
        marker_missing_attempts = {}
        no_timetable_codes = set()
        progress = MagicMock()
        lock = threading.Lock()

        # Mock existing_courses_for_year so it goes straight to class extraction
        existing_courses_for_year = {"TEST9999": "cid_9999"}

        with patch.object(
            scraper, "_extract_and_queue_classes", return_value="NO_TIMETABLE"
        ):
            scraper.process_course(
                course=course,
                year=2027,
                subject={"code": "TEST"},
                engine=MagicMock(),
                progress=progress,
                subject_task=1,
                lock=lock,
                existing_courses_for_year=existing_courses_for_year,
                collected_classes=collected_classes,
                collected_meetings=collected_meetings,
                failed_courses=failed_courses,
                harvested_course_ids=harvested_course_ids,
                marker_missing_attempts=marker_missing_attempts,
                no_timetable_codes=no_timetable_codes,
            )

        # Should be recorded in no_timetable_codes
        self.assertIn("TEST9999", no_timetable_codes)
        # Should NOT be recorded in failed_courses (no retries!)
        self.assertEqual(len(failed_courses), 0)
        # Should NOT be in harvested_course_ids so existing DB rows remain completely untouched
        self.assertNotIn("cid_9999", harvested_course_ids)

    def test_process_course_empty_or_none_terms_skips_class_crawl(self):
        """When course terms is None or [], class crawl must be skipped without network calls."""
        for empty_terms in [None, [], ""]:
            course = {
                "code": "TEST8888",
                "year": 2027,
                "terms": empty_terms,
                "subject": "TEST",
            }
            harvested_course_ids = set()
            progress = MagicMock()
            lock = threading.Lock()
            existing_courses_for_year = {"TEST8888": "cid_8888"}

            with patch.object(scraper, "_extract_and_queue_classes") as mock_extract:
                scraper.process_course(
                    course=course,
                    year=2027,
                    subject={"code": "TEST"},
                    engine=MagicMock(),
                    progress=progress,
                    subject_task=1,
                    lock=lock,
                    existing_courses_for_year=existing_courses_for_year,
                    harvested_course_ids=harvested_course_ids,
                )
                mock_extract.assert_not_called()
                self.assertIn("cid_8888", harvested_course_ids)

    def test_process_course_active_terms_crawls_classes(self):
        """When course has active terms (e.g. BIOL1032, AERO1009), class crawl must be executed."""
        course = {
            "code": "AERO1009",
            "year": 2027,
            "terms": ["Semester 1"],
            "subject": "AERO",
        }
        harvested_course_ids = set()
        progress = MagicMock()
        lock = threading.Lock()
        existing_courses_for_year = {"AERO1009": "cid_aero"}

        with patch.object(
            scraper, "_extract_and_queue_classes", return_value=True
        ) as mock_extract:
            scraper.process_course(
                course=course,
                year=2027,
                subject={"code": "AERO"},
                engine=MagicMock(),
                progress=progress,
                subject_task=1,
                lock=lock,
                existing_courses_for_year=existing_courses_for_year,
                harvested_course_ids=harvested_course_ids,
            )
            mock_extract.assert_called_once()
            self.assertIn("cid_aero", harvested_course_ids)


if __name__ == "__main__":
    unittest.main()
