import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from data_fetcher import DataFetcher


class TestDataFetcher(unittest.TestCase):
    def setUp(self):
        DataFetcher._proxies = ["1.1.1.1:80", "2.2.2.2:80", "3.3.3.3:80"]
        DataFetcher._proxy_failures = {}

    def test_proxy_failure_threshold(self):
        fetcher = DataFetcher("?test=1", use_proxy=True)
        proxy = {"http": "http://1.1.1.1:80", "https": "http://1.1.1.1:80"}

        # 1st failure: should NOT be removed
        fetcher.report_proxy_failure(proxy)
        self.assertIn("1.1.1.1:80", DataFetcher._proxies)
        self.assertEqual(DataFetcher._proxy_failures.get("1.1.1.1:80"), 1)

        # 2nd failure: should NOT be removed
        fetcher.report_proxy_failure(proxy)
        self.assertIn("1.1.1.1:80", DataFetcher._proxies)
        self.assertEqual(DataFetcher._proxy_failures.get("1.1.1.1:80"), 2)

        # 3rd failure: threshold reached (MAX_PROXY_FAILURES=3), should be removed
        fetcher.report_proxy_failure(proxy)
        self.assertNotIn("1.1.1.1:80", DataFetcher._proxies)

    def test_proxy_success_resets_failures(self):
        fetcher = DataFetcher("?test=1", use_proxy=True)
        proxy = {"http": "http://2.2.2.2:80", "https": "http://2.2.2.2:80"}

        fetcher.report_proxy_failure(proxy)
        self.assertEqual(DataFetcher._proxy_failures.get("2.2.2.2:80"), 1)

        fetcher.report_proxy_success(proxy)
        self.assertNotIn("2.2.2.2:80", DataFetcher._proxy_failures)

    def test_fatal_proxy_failure_removes_immediately(self):
        fetcher = DataFetcher("?test=1", use_proxy=True)
        proxy = {"http": "http://3.3.3.3:80", "https": "http://3.3.3.3:80"}

        fetcher.report_proxy_failure(proxy, fatal=True)
        self.assertNotIn("3.3.3.3:80", DataFetcher._proxies)

    @patch.object(DataFetcher, "load_proxies")
    def test_get_random_proxy_reloads_when_empty(self, mock_load):
        mock_load.return_value = ["4.4.4.4:80"]
        DataFetcher._proxies = []
        fetcher = DataFetcher("?test=1", use_proxy=True)

        proxy = fetcher.get_random_proxy()
        self.assertEqual(
            proxy, {"http": "http://4.4.4.4:80", "https": "http://4.4.4.4:80"}
        )
        mock_load.assert_called()

    def test_clean_request_url(self):
        fetcher = DataFetcher("/study/courses/2026/acct-1001/", use_class_url=True)
        req_url = fetcher._build_request_url()
        self.assertNotIn("_cb=", req_url)
        self.assertEqual(
            req_url, "https://adelaide.edu.au/study/courses/2026/acct-1001/"
        )

    def test_clean_headers_no_proxy_rejection(self):
        fetcher = DataFetcher("/study/courses/2026/acct-1001/", use_class_url=True)
        headers = fetcher._get_headers()
        self.assertNotIn("Cache-Control", headers)
        self.assertNotIn("Pragma", headers)
        self.assertNotIn("Expires", headers)
        self.assertIn("Accept", headers)
        self.assertIn("Referer", headers)

    def test_process_response_rejects_empty_or_invalid_proxy_page(self):
        fetcher = DataFetcher("/study/courses/2026/acct-1001/", use_class_url=True)
        mock_resp = unittest.mock.MagicMock()
        # Proxy block/error page without course markers or h1
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Squid Error 504 Gateway Timeout</body></html>"
        mock_resp.content = mock_resp.text.encode("utf-8")
        parsed = fetcher._process_response(mock_resp)
        self.assertIsNone(parsed)

    def test_process_response_accepts_valid_course_page(self):
        fetcher = DataFetcher("/study/courses/2026/acct-1001/", use_class_url=True)
        mock_resp = unittest.mock.MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><head><title>Course | Adelaide</title></head><body><h1 class='cmp-title'>Accounting 1</h1><div class='cmp-course'>Course ID: 12345</div></body></html>"
        mock_resp.content = mock_resp.text.encode("utf-8")
        parsed = fetcher._process_response(mock_resp)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["h1"], "Accounting 1")


if __name__ == "__main__":
    unittest.main()
