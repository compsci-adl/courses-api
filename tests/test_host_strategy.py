import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from data_fetcher import DataFetcher
from host_strategy import (
    BreakerState,
    CircuitBreaker,
    Strategy,
    host_strategy,
)


class TestHostStrategy(unittest.TestCase):
    def setUp(self):
        # Reset host_strategy manager state before each test
        host_strategy._breakers.clear()
        host_strategy._metrics.clear()
        host_strategy._semaphores.clear()
        if "FORCE_CONNECTION_MODE" in os.environ:
            del os.environ["FORCE_CONNECTION_MODE"]
        if "DIRECT_FIRST_HOSTS" in os.environ:
            del os.environ["DIRECT_FIRST_HOSTS"]
        if "DIRECT_MAX_CONCURRENCY" in os.environ:
            del os.environ["DIRECT_MAX_CONCURRENCY"]

    def test_default_policy_table(self):
        fb_host = "uosa-search.funnelback.squiz.cloud"
        adl_host = "adelaide.edu.au"
        apps_host = "apps.adelaide.edu.au"
        other_host = "example.com"

        self.assertEqual(host_strategy.get_strategy(fb_host), Strategy.DIRECT_FIRST)
        self.assertEqual(host_strategy.get_strategy(adl_host), Strategy.PROXY_FIRST)
        self.assertEqual(host_strategy.get_strategy(apps_host), Strategy.PROXY_FIRST)
        self.assertEqual(host_strategy.get_strategy(other_host), Strategy.PROXY_FIRST)

    def test_force_connection_mode_overrides(self):
        host = "uosa-search.funnelback.squiz.cloud"
        with patch.dict(os.environ, {"FORCE_CONNECTION_MODE": "proxy"}):
            self.assertEqual(host_strategy.get_strategy(host), Strategy.PROXY_ONLY)

        with patch.dict(os.environ, {"FORCE_CONNECTION_MODE": "direct"}):
            self.assertEqual(host_strategy.get_strategy(host), Strategy.DIRECT_ONLY)

    def test_direct_first_hosts_override(self):
        adl_host = "adelaide.edu.au"
        with patch.dict(
            os.environ, {"DIRECT_FIRST_HOSTS": "adelaide.edu.au,example.com"}
        ):
            self.assertEqual(
                host_strategy.get_strategy(adl_host), Strategy.DIRECT_FIRST
            )
            self.assertEqual(
                host_strategy.get_strategy("example.com"), Strategy.DIRECT_FIRST
            )

    def test_circuit_breaker_sliding_window_and_cooldown(self):
        breaker = CircuitBreaker(
            "test-host",
            window_size=10,
            min_samples=4,
            failure_threshold=0.5,
            base_cooldown=0.2,
        )
        # 3 successes
        for _ in range(3):
            breaker.record_success()
        self.assertTrue(breaker.is_direct_allowed())
        self.assertEqual(breaker.state, BreakerState.CLOSED)

        # 4 failures: 4/7 > 50%
        for _ in range(4):
            breaker.record_failure(status_code=500)

        # Breaker should now be OPEN
        self.assertFalse(breaker.is_direct_allowed())
        self.assertEqual(breaker.state, BreakerState.OPEN)

        # Wait for cooldown to expire
        time.sleep(0.25)
        # Should now be HALF_OPEN and allow probe
        self.assertTrue(breaker.is_direct_allowed())
        self.assertEqual(breaker.state, BreakerState.HALF_OPEN)

        # Successful probe closes breaker
        breaker.record_success()
        breaker.record_success()
        self.assertEqual(breaker.state, BreakerState.CLOSED)

    def test_circuit_breaker_immediate_waf_open(self):
        breaker = CircuitBreaker("waf-host", base_cooldown=0.5)
        self.assertTrue(breaker.is_direct_allowed())

        # HTTP 403 immediately opens breaker
        breaker.record_failure(status_code=403)
        self.assertFalse(breaker.is_direct_allowed())
        self.assertEqual(breaker.state, BreakerState.OPEN)

    def test_retry_after_pauses_direct(self):
        breaker = CircuitBreaker("retry-after-host", base_cooldown=0.3)
        breaker.record_failure(status_code=429, retry_after=0.3)

        self.assertFalse(breaker.is_direct_allowed())
        time.sleep(0.35)
        # After retry_after and cooldown expire, half-open allows probe
        self.assertTrue(breaker.is_direct_allowed())

    def test_direct_max_concurrency_limit(self):
        host = "concurrency-host"
        with patch.dict(
            os.environ,
            {
                "DIRECT_MAX_CONCURRENCY": "2",
                "DIRECT_MIN_JITTER_MS": "10",
                "DIRECT_MAX_JITTER_MS": "20",
            },
        ):
            host_strategy.acquire_direct_slot(host)
            host_strategy.acquire_direct_slot(host)

            acquired_third = [False]

            def try_acquire():
                host_strategy.acquire_direct_slot(host)
                acquired_third[0] = True
                host_strategy.release_direct_slot(host)

            t = threading.Thread(target=try_acquire)
            t.start()
            t.join(timeout=0.1)
            self.assertFalse(
                acquired_third[0], "Third slot should block while concurrency=2"
            )

            host_strategy.release_direct_slot(host)
            t.join(timeout=0.5)
            self.assertTrue(
                acquired_third[0], "Third slot should acquire after one released"
            )
            host_strategy.release_direct_slot(host)


class TestDataFetcherStrategyExecution(unittest.TestCase):
    def setUp(self):
        host_strategy._breakers.clear()
        host_strategy._metrics.clear()
        DataFetcher._proxies = ["1.1.1.1:80", "2.2.2.2:80"]

    @patch.object(DataFetcher, "_execute_http_get")
    def test_direct_first_success_touches_no_proxies(self, mock_http_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"response": {"resultPacket": {"results": []}}}'
        mock_http_get.return_value = mock_resp

        with patch.dict(
            os.environ, {"DIRECT_MIN_JITTER_MS": "0", "DIRECT_MAX_JITTER_MS": "0"}
        ):
            fetcher = DataFetcher("?query=test", use_proxy=True)
            res = fetcher.get()

        self.assertEqual(res, {"resultPacket": {"results": []}})
        self.assertEqual(mock_http_get.call_count, 1)
        # Proxy was None (direct request)
        self.assertNotIn("proxy", mock_http_get.call_args.kwargs)

    @patch.object(DataFetcher, "get_random_proxy")
    @patch.object(DataFetcher, "_execute_http_get")
    def test_direct_first_order_direct_direct_then_proxy(
        self, mock_http_get, mock_get_proxy
    ):
        mock_get_proxy.return_value = {
            "http": "http://1.1.1.1:80",
            "https": "http://1.1.1.1:80",
        }

        # Attempt 1 direct fails (500)
        # Attempt 2 direct fails (500)
        # Attempt 3 proxy succeeds (200)
        mock_resp_fail = MagicMock()
        mock_resp_fail.status_code = 500
        mock_resp_fail.text = "Internal Server Error"

        mock_resp_success = MagicMock()
        mock_resp_success.status_code = 200
        mock_resp_success.text = '{"response": {"resultPacket": {"results": [{"listMetadata": {"courseCode": ["COMP1001"]}}]}}}'

        mock_http_get.side_effect = [mock_resp_fail, mock_resp_fail, mock_resp_success]

        with patch.dict(
            os.environ, {"DIRECT_MIN_JITTER_MS": "0", "DIRECT_MAX_JITTER_MS": "0"}
        ):
            fetcher = DataFetcher("?query=test", use_proxy=True)
            res = fetcher.get()

        self.assertIn("resultPacket", res)
        # Call count should be 3: direct, direct, proxy
        self.assertEqual(mock_http_get.call_count, 3)
        self.assertNotIn("proxy", mock_http_get.call_args_list[0].kwargs)
        self.assertNotIn("proxy", mock_http_get.call_args_list[1].kwargs)
        self.assertIn("proxy", mock_http_get.call_args_list[2].kwargs)

    @patch.object(DataFetcher, "get_random_proxy")
    @patch.object(DataFetcher, "_execute_http_get")
    def test_unparseable_direct_falls_back_to_proxy(
        self, mock_http_get, mock_get_proxy
    ):
        mock_get_proxy.return_value = {
            "http": "http://1.1.1.1:80",
            "https": "http://1.1.1.1:80",
        }

        # Direct returns 200 with garbage/html instead of JSON
        mock_resp_bad = MagicMock()
        mock_resp_bad.status_code = 200
        mock_resp_bad.text = "<html><body>Some non-json page</body></html>"

        # Proxy returns valid JSON
        mock_resp_good = MagicMock()
        mock_resp_good.status_code = 200
        mock_resp_good.text = '{"response": {"resultPacket": {"results": []}}}'

        mock_http_get.side_effect = [mock_resp_bad, mock_resp_bad, mock_resp_good]

        with patch.dict(
            os.environ, {"DIRECT_MIN_JITTER_MS": "0", "DIRECT_MAX_JITTER_MS": "0"}
        ):
            fetcher = DataFetcher("?query=test", use_proxy=True)
            res = fetcher.get()

        self.assertEqual(res, {"resultPacket": {"results": []}})
        # Verified fallback to proxy happened
        self.assertIn("proxy", mock_http_get.call_args_list[2].kwargs)


if __name__ == "__main__":
    unittest.main()
