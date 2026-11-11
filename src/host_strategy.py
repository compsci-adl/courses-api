"""src/host_strategy.py

Per-host connection strategy and circuit breaker.
Controls whether requests to a host attempt direct connection first or route through proxies.

Policy defaults:
- uosa-search.funnelback.squiz.cloud: direct_first
- adelaide.edu.au: proxy_first (by default, opt-in via DIRECT_FIRST_HOSTS)
- apps.adelaide.edu.au: proxy_first (by default, opt-in via DIRECT_FIRST_HOSTS)
- others: proxy_first

Global overrides:
- FORCE_CONNECTION_MODE: auto (default), direct, proxy
- DIRECT_FIRST_HOSTS: comma-separated list of hostnames to set to direct_first
- DIRECT_MAX_CONCURRENCY: max concurrent direct requests per host (default 3)
- DIRECT_MIN_JITTER_MS / DIRECT_MAX_JITTER_MS: delay between direct requests (default 200ms - 600ms)
"""

import os
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

from log import logger


class Strategy(str, Enum):
    DIRECT_FIRST = "direct_first"
    PROXY_FIRST = "proxy_first"
    DIRECT_ONLY = "direct_only"
    PROXY_ONLY = "proxy_only"


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class HostMetrics:
    direct_attempts: int = 0
    direct_successes: int = 0
    direct_failures: int = 0
    proxy_attempts: int = 0
    proxy_successes: int = 0
    proxy_failures: int = 0
    breaker_open_count: int = 0
    time_spent_breaker_open: float = 0.0


class CircuitBreaker:
    """Sliding-window circuit breaker with exponential cooldown and Retry-After support."""

    def __init__(
        self,
        host: str,
        window_size: int = 20,
        min_samples: int = 8,
        failure_threshold: float = 0.50,
        base_cooldown: float = 300.0,  # 5 minutes
        max_cooldown: float = 1800.0,  # 30 minutes
        half_open_probes: int = 2,
    ):
        self.host = host
        self.window_size = window_size
        self.min_samples = min_samples
        self.failure_threshold = failure_threshold
        self.base_cooldown = base_cooldown
        self.max_cooldown = max_cooldown
        self.half_open_probes = half_open_probes

        self._state = BreakerState.CLOSED
        self._window: deque[bool] = deque(
            maxlen=window_size
        )  # True = success, False = failure
        self._open_until: float = 0.0
        self._retry_after_until: float = 0.0
        self._current_cooldown = base_cooldown
        self._half_open_successful_probes = 0
        self._lock = threading.Lock()

        # Metrics tracking
        self.open_count = 0
        self.total_open_duration = 0.0
        self._last_opened_at = 0.0

    @property
    def state(self) -> BreakerState:
        with self._lock:
            now = time.time()
            if self._state == BreakerState.OPEN:
                effective_open_until = max(self._open_until, self._retry_after_until)
                if now >= effective_open_until:
                    # Transition to half-open
                    self._state = BreakerState.HALF_OPEN
                    self._half_open_successful_probes = 0
                    if self._last_opened_at > 0:
                        self.total_open_duration += now - self._last_opened_at
                        self._last_opened_at = 0.0
                    logger.info(
                        f"Direct path for {self.host} transition: HALF_OPEN (probing direct)"
                    )
            return self._state

    def is_direct_allowed(self) -> bool:
        """Return True if direct traffic is currently permitted for this host."""
        now = time.time()
        with self._lock:
            if now < self._retry_after_until:
                return False

            if self._state == BreakerState.OPEN:
                if now >= max(self._open_until, self._retry_after_until):
                    self._state = BreakerState.HALF_OPEN
                    self._half_open_successful_probes = 0
                    if self._last_opened_at > 0:
                        self.total_open_duration += now - self._last_opened_at
                        self._last_opened_at = 0.0
                    logger.info(
                        f"Direct path for {self.host} transition: HALF_OPEN (probing direct)"
                    )
                    return True
                return False

            return True

    def record_success(self) -> None:
        with self._lock:
            self._window.append(True)
            if self._state == BreakerState.HALF_OPEN:
                self._half_open_successful_probes += 1
                if self._half_open_successful_probes >= self.half_open_probes:
                    self._state = BreakerState.CLOSED
                    self._current_cooldown = self.base_cooldown
                    self._window.clear()
                    logger.info(f"Direct path for {self.host} CLOSED (recovered).")

    def record_failure(
        self,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        now = time.time()
        with self._lock:
            self._window.append(False)

            if retry_after and retry_after > 0:
                self._retry_after_until = now + retry_after
                logger.warning(
                    f"Direct path for {self.host} received 429 with Retry-After: pausing direct for {retry_after:.1f}s"
                )

            # Trigger condition: 403 / 429 / explicit WAF response, or failure rate in sliding window exceeded
            is_waf = status_code in (403, 429)
            should_open = False
            reason = ""

            if is_waf:
                should_open = True
                reason = f"HTTP {status_code}"
            elif self._state == BreakerState.HALF_OPEN:
                should_open = True
                reason = f"probe failed in HALF_OPEN (status={status_code})"
            elif len(self._window) >= self.min_samples:
                fail_count = sum(1 for outcome in self._window if not outcome)
                fail_rate = fail_count / len(self._window)
                if fail_rate >= self.failure_threshold:
                    should_open = True
                    reason = f"fail rate {fail_rate * 100:.1f}% over {len(self._window)} attempts"

            if should_open:
                if self._state != BreakerState.OPEN:
                    self.open_count += 1
                    self._last_opened_at = now
                    cooldown = self._current_cooldown
                    self._open_until = now + cooldown
                    # Double cooldown for next time breaker re-opens
                    self._current_cooldown = min(
                        self._current_cooldown * 2, self.max_cooldown
                    )
                    self._state = BreakerState.OPEN
                    cooldown_min = cooldown / 60.0
                    logger.info(
                        f"Direct path for {self.host} OPEN ({reason}, cooldown {cooldown_min:.1f}m)"
                    )


class HostStrategyManager:
    """Manages per-host policies, circuit breakers, rate-limiting semaphores, and telemetry."""

    DEFAULT_POLICIES = {
        "uosa-search.funnelback.squiz.cloud": Strategy.DIRECT_FIRST,
        "adelaide.edu.au": Strategy.PROXY_FIRST,
        "apps.adelaide.edu.au": Strategy.PROXY_FIRST,
    }

    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._metrics: dict[str, HostMetrics] = {}
        self._semaphores: dict[str, threading.Semaphore] = {}
        self._last_direct_request_time: dict[str, float] = {}
        self._lock = threading.Lock()

    def get_host(self, url: str) -> str:
        parsed = urlparse(url)
        return parsed.netloc.split(":")[0] if parsed.netloc else ""

    def get_strategy(self, host: str) -> Strategy:
        # Check global force override
        force_mode = os.environ.get("FORCE_CONNECTION_MODE", "auto").strip().lower()
        if force_mode == "direct":
            return Strategy.DIRECT_ONLY
        elif force_mode == "proxy":
            return Strategy.PROXY_ONLY

        # Check explicit opt-ins in DIRECT_FIRST_HOSTS
        direct_first_hosts = {
            h.strip().lower()
            for h in os.environ.get("DIRECT_FIRST_HOSTS", "").split(",")
            if h.strip()
        }
        if host.lower() in direct_first_hosts:
            return Strategy.DIRECT_FIRST

        return self.DEFAULT_POLICIES.get(host.lower(), Strategy.PROXY_FIRST)

    def get_breaker(self, host: str) -> CircuitBreaker:
        with self._lock:
            if host not in self._breakers:
                self._breakers[host] = CircuitBreaker(host)
            return self._breakers[host]

    def get_metrics(self, host: str) -> HostMetrics:
        with self._lock:
            if host not in self._metrics:
                self._metrics[host] = HostMetrics()
            return self._metrics[host]

    def acquire_direct_slot(self, host: str) -> threading.Semaphore:
        """Acquires a concurrency slot and applies jittered delay before a direct request."""
        concurrency = int(os.environ.get("DIRECT_MAX_CONCURRENCY", "3"))
        with self._lock:
            if host not in self._semaphores:
                self._semaphores[host] = threading.Semaphore(concurrency)
            sem = self._semaphores[host]

        sem.acquire()
        try:
            # Jitter between direct requests to the same host
            min_jitter = float(os.environ.get("DIRECT_MIN_JITTER_MS", "200")) / 1000.0
            max_jitter = float(os.environ.get("DIRECT_MAX_JITTER_MS", "600")) / 1000.0
            delay = random.uniform(min_jitter, max_jitter)
            time.sleep(delay)
        except Exception:
            sem.release()
            raise
        return sem

    def release_direct_slot(self, host: str) -> None:
        with self._lock:
            sem = self._semaphores.get(host)
        if sem:
            sem.release()

    def record_direct_outcome(
        self,
        host: str,
        success: bool,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        breaker = self.get_breaker(host)
        metrics = self.get_metrics(host)
        with self._lock:
            metrics.direct_attempts += 1
            if success:
                metrics.direct_successes += 1
            else:
                metrics.direct_failures += 1

        if success:
            breaker.record_success()
        else:
            breaker.record_failure(status_code=status_code, retry_after=retry_after)

    def record_proxy_outcome(self, host: str, success: bool) -> None:
        metrics = self.get_metrics(host)
        with self._lock:
            metrics.proxy_attempts += 1
            if success:
                metrics.proxy_successes += 1
            else:
                metrics.proxy_failures += 1

    def get_all_metrics(self) -> dict[str, HostMetrics]:
        with self._lock:
            # Update open durations for active open breakers
            now = time.time()
            for host, breaker in self._breakers.items():
                if host in self._metrics:
                    m = self._metrics[host]
                    m.breaker_open_count = breaker.open_count
                    extra = (
                        (now - breaker._last_opened_at)
                        if breaker._last_opened_at > 0
                        else 0.0
                    )
                    m.time_spent_breaker_open = breaker.total_open_duration + extra
            return dict(self._metrics)


# Global singleton instance
host_strategy = HostStrategyManager()
