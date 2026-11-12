import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from data_fetcher import DataFetcher


class TestDataFetcher(unittest.TestCase):
    def setUp(self):
        DataFetcher._proxies = ["1.1.1.1:80", "2.2.2.2:80", "3.3.3.3:80"]
        DataFetcher._proxy_failures = {}

    def test_proxy_failure_threshold(self):
        fetcher = DataFetcher("?test=1", use_proxy=True)
        proxy = {"http": "http://1.1.1.1:80", "https": "http://1.1.1.1:80"}

        # Failures before MAX_PROXY_FAILURES: should NOT be removed
        for i in range(1, DataFetcher.MAX_PROXY_FAILURES):
            fetcher.report_proxy_failure(proxy)
            self.assertIn("1.1.1.1:80", DataFetcher._proxies)
            self.assertEqual(DataFetcher._proxy_failures.get("1.1.1.1:80"), i)

        # Threshold reached (MAX_PROXY_FAILURES), should be removed
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
        DataFetcher._proxies = ["3.3.3.3:80"]

        fetcher.report_proxy_failure(proxy, fatal=True)
        self.assertNotIn("3.3.3.3:80", DataFetcher._proxies)

    @patch("data_fetcher.requests.get")
    def test_funnelback_allows_direct_even_when_direct_blocked(self, mock_get):
        """Funnelback (squiz.cloud) search must attempt direct connection even if course pages are direct-blocked."""
        DataFetcher._direct_blocked = True
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"resultPacket": {"results": []}}'
        mock_get.return_value = mock_resp

        fetcher = DataFetcher("?query=test", use_class_url=False, use_proxy=False)
        data = fetcher.get()

        mock_get.assert_called()
        self.assertIsInstance(data, dict)
        DataFetcher._direct_blocked = False

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

    @patch("data_fetcher.requests.get")
    def test_conditional_request_304(self, mock_get):
        """Verify that HTTP 304 response is handled cleanly and marked not_modified."""
        mock_resp = MagicMock()
        mock_resp.status_code = 304
        mock_resp.headers = {"date": "Thu, 12 Nov 2026 07:00:00 GMT"}
        mock_get.return_value = mock_resp

        fetcher = DataFetcher(
            "/study/courses/2027/acct-1001/",
            use_class_url=True,
            use_proxy=False,
            if_modified_since="Thu, 12 Nov 2026 06:00:00 GMT",
        )
        data = fetcher.get()

        self.assertEqual(data.get("status_code"), 304)
        self.assertTrue(data.get("not_modified"))
        self.assertEqual(data.get("edge_date"), "Thu, 12 Nov 2026 07:00:00 GMT")


if __name__ == "__main__":
    unittest.main()
