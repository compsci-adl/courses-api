import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from curl_cffi.requests.errors import RequestsError

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

    @patch("data_fetcher.fetch_proxies.main")
    @patch.object(DataFetcher, "load_proxies")
    def test_get_random_proxy_reloads_when_empty(self, mock_load, mock_fetch):
        mock_load.return_value = ["4.4.4.4:80"] * 40
        DataFetcher._proxies = []
        fetcher = DataFetcher("?test=1", use_proxy=True)

        proxy = fetcher.get_random_proxy()
        self.assertEqual(
            proxy, {"http": "http://4.4.4.4:80", "https": "http://4.4.4.4:80"}
        )
        mock_load.assert_called()

    @patch.object(DataFetcher, "trigger_refill_if_low")
    def test_proxy_replenishment_trigger(self, mock_trigger):
        DataFetcher._proxies = [f"1.1.1.{i}:80" for i in range(20)]
        fetcher = DataFetcher("?test=1", use_proxy=True)

        fetcher.get_random_proxy()
        mock_trigger.assert_called_once()
        self.assertEqual(len(DataFetcher._proxies), 20)

    @patch.object(DataFetcher, "_execute_http_get")
    def test_timeout_and_retry_ceiling(self, mock_http_get):
        DataFetcher._proxies = [f"3.3.3.{i}:80" for i in range(40)]
        fetcher = DataFetcher("?test=1", use_proxy=True)

        # Force direct to fail with 403 to trigger direct block
        mock_resp_403 = MagicMock()
        mock_resp_403.status_code = 403
        mock_http_get.side_effect = [
            mock_resp_403,
            RequestsError("Fail 1"),
            RequestsError("Fail 2"),
            RequestsError("Fail 3"),
        ]

        DataFetcher._direct_blocked = False
        DataFetcher._direct_blocked_permanently = False
        res = fetcher.get(max_retries=10)

        # Must return empty dict after max 3 proxy retries
        self.assertEqual(res, {})
        # Direct attempt + 3 proxy retries = 4 calls total
        self.assertEqual(mock_http_get.call_count, 4)
        # Verify proxy call timeout was (3.0, 5.0)
        proxy_call_kwargs = mock_http_get.call_args_list[-1].kwargs
        self.assertEqual(proxy_call_kwargs["timeout"], (3.0, 5.0))


if __name__ == "__main__":
    unittest.main()
