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

    @patch("data_fetcher.requests.get")
    def test_direct_retry_on_transient_error_succeeds(self, mock_get):
        """Verify direct request retries after transient failure and recovers without proxies."""
        success_resp = MagicMock()
        success_resp.status_code = 200
        success_resp.text = "<html><body><h1>Direct Success</h1></html>"
        success_resp.content = success_resp.text.encode("utf-8")
        success_resp.headers = {"date": "Fri, 13 Nov 2026 00:00:00 GMT"}

        # Attempt 1 fails with Exception, Attempt 2 succeeds
        mock_get.side_effect = [Exception("Connection reset by peer"), success_resp]

        fetcher = DataFetcher(
            "/study/courses/2027/test-1001/",
            use_class_url=True,
            use_proxy=True,
        )
        data = fetcher.get()

        self.assertEqual(data.get("h1"), "Direct Success")
        self.assertEqual(mock_get.call_count, 2)

    @patch("data_fetcher.requests.get")
    def test_direct_404_does_not_retry(self, mock_get):
        """Verify HTTP 404 on direct request returns immediately without retrying."""
        not_found_resp = MagicMock()
        not_found_resp.status_code = 404

        mock_get.return_value = not_found_resp

        fetcher = DataFetcher(
            "/study/courses/2027/none-9999/",
            use_class_url=True,
            use_proxy=True,
        )
        data = fetcher.get()

        self.assertEqual(data, {})
        self.assertEqual(mock_get.call_count, 1)

    @patch("data_fetcher.requests.get")
    def test_direct_403_pauses_direct_and_falls_back_to_proxy(self, mock_get):
        """Verify direct 403 sets direct_blocked and falls back to proxy without direct retry loop."""
        DataFetcher._direct_blocked = False

        waf_resp = MagicMock()
        waf_resp.status_code = 403

        proxy_resp = MagicMock()
        proxy_resp.status_code = 200
        proxy_resp.text = "<html><body><h1>Proxy Content</h1></html>"
        proxy_resp.content = proxy_resp.text.encode("utf-8")
        proxy_resp.headers = {"date": "Fri, 13 Nov 2026 00:00:00 GMT"}

        # First call is direct (403), second call is via proxy (200)
        mock_get.side_effect = [waf_resp, proxy_resp]

        fetcher = DataFetcher(
            "/study/courses/2027/test-1001/",
            use_class_url=True,
            use_proxy=True,
        )
        data = fetcher.get()

        self.assertTrue(DataFetcher.is_direct_blocked())
        self.assertEqual(data.get("h1"), "Proxy Content")
        DataFetcher._direct_blocked = False

    def test_xml_endpoint_headers_and_validation(self):
        """XML endpoints should use XML accept headers and validate closing XML tags."""
        fetcher = DataFetcher("/sitemap.xml", use_class_url=True, use_proxy=False)
        headers = fetcher._get_headers()
        self.assertIn("application/xml", headers.get("Accept", ""))

        # Truncated XML should be rejected
        truncated_resp = MagicMock()
        truncated_resp.status_code = 200
        truncated_resp.text = "<urlset>" + (
            "<url><loc>https://adelaide.edu.au</loc></url>" * 10
        )
        truncated_resp.headers = {"date": "Fri, 13 Nov 2026 00:00:00 GMT"}
        self.assertIsNone(fetcher._process_response(truncated_resp))

        # Complete XML should be accepted
        valid_resp = MagicMock()
        valid_resp.status_code = 200
        valid_resp.text = (
            "<urlset><url><loc>https://adelaide.edu.au</loc></url></urlset>"
        )
        valid_resp.headers = {"date": "Fri, 13 Nov 2026 00:00:00 GMT"}
        processed = fetcher._process_response(valid_resp)
        self.assertIsNotNone(processed)
        self.assertIn("</urlset>", processed.get("html", ""))

    @patch("data_fetcher.requests.get")
    def test_proxy_406_not_marked_fatal(self, mock_get):
        """HTTP 406 returned by proxy should not fatally remove the proxy from the pool."""
        DataFetcher._proxies = ["9.9.9.9:80"]
        DataFetcher._direct_blocked = True

        not_acceptable_resp = MagicMock()
        not_acceptable_resp.status_code = 406

        success_resp = MagicMock()
        success_resp.status_code = 200
        success_resp.text = "<urlset><url><loc>test</loc></url></urlset>"
        success_resp.headers = {"date": "Fri, 13 Nov 2026 00:00:00 GMT"}

        mock_get.side_effect = [not_acceptable_resp, success_resp]

        fetcher = DataFetcher("/sitemap.xml", use_class_url=True, use_proxy=True)
        fetcher.get(max_retries=2)

        self.assertIn("9.9.9.9:80", DataFetcher._proxies)
        DataFetcher._direct_blocked = False


if __name__ == "__main__":
    unittest.main()
