#!/usr/bin/env python3
"""scripts/probe_hosts.py

Measure direct (no-proxy) request outcomes from the current environment (e.g. GitHub Actions runner)
across key hosts:
1. Funnelback search: small subject query
2. Funnelback search: large subject query (e.g. Biological Sciences with num_ranks=1000)
3. University course page: adelaide.edu.au comparison

Prints:
- Success count / total attempts
- Status code histogram
- Median / min / max latency
- Response byte size
- Error types / WAF signatures
"""

import argparse
import statistics
import time
from collections import Counter

from curl_cffi import requests

TARGET_URLS = [
    {
        "name": "Funnelback (Small Subject: ACCT)",
        "host": "uosa-search.funnelback.squiz.cloud",
        "url": (
            "https://uosa-search.funnelback.squiz.cloud/s/search.html"
            "?f.Tabs%7Ctype=Degrees+%26+Courses&form=json&f.Year%7Cyear=2026"
            "&num_ranks=1000&profile=site-search&query=&f.Area+of+study%7CstudyArea=Accounting"
            "&collection=uosa%7Esp-aem-prod&f.Study+type%7CstudyType=Course"
        ),
        "headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://www.adelaide.edu.au/",
            "Origin": "https://www.adelaide.edu.au",
        },
    },
    {
        "name": "Funnelback (Large Subject: Biological Sciences)",
        "host": "uosa-search.funnelback.squiz.cloud",
        "url": (
            "https://uosa-search.funnelback.squiz.cloud/s/search.html"
            "?f.Tabs%7Ctype=Degrees+%26+Courses&form=json&f.Year%7Cyear=2026"
            "&num_ranks=1000&profile=site-search&query=&f.Area+of+study%7CstudyArea=Biological%20Sciences"
            "&collection=uosa%7Esp-aem-prod&f.Study+type%7CstudyType=Course"
        ),
        "headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://www.adelaide.edu.au/",
            "Origin": "https://www.adelaide.edu.au",
        },
    },
    {
        "name": "Course Page (adelaide.edu.au)",
        "host": "adelaide.edu.au",
        "url": "https://adelaide.edu.au/study/courses/2026/biol-1101/",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.adelaide.edu.au/",
        },
    },
]


def probe_target(target: dict, count: int = 10, timeout: float = 15.0) -> dict:
    name = target["name"]
    url = target["url"]
    headers = target["headers"]

    status_codes = Counter()
    latencies = []
    response_sizes = []
    errors = Counter()
    success_count = 0

    session = requests.Session(impersonate="chrome146")

    print(f"\nProbing {name} ({count} direct requests)...")
    for i in range(1, count + 1):
        t0 = time.time()
        try:
            resp = session.get(url, headers=headers, timeout=timeout)
            elapsed = time.time() - t0
            latencies.append(elapsed)
            status_codes[resp.status_code] += 1
            body_len = len(resp.content)
            response_sizes.append(body_len)

            if resp.status_code == 200:
                success_count += 1
            elif resp.status_code in (403, 429):
                errors[f"HTTP_{resp.status_code}"] += 1
            else:
                errors[f"HTTP_{resp.status_code}"] += 1
        except Exception as e:
            elapsed = time.time() - t0
            latencies.append(elapsed)
            err_name = type(e).__name__
            errors[err_name] += 1
            status_codes["ERR"] += 1

        # Small delay between probes
        time.sleep(0.3)

    median_lat = statistics.median(latencies) if latencies else 0.0
    min_lat = min(latencies) if latencies else 0.0
    max_lat = max(latencies) if latencies else 0.0
    avg_size = statistics.mean(response_sizes) if response_sizes else 0

    return {
        "name": name,
        "host": target["host"],
        "count": count,
        "success_count": success_count,
        "status_codes": dict(status_codes),
        "median_lat": median_lat,
        "min_lat": min_lat,
        "max_lat": max_lat,
        "avg_size": avg_size,
        "errors": dict(errors),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Probe direct connectivity to scraper hosts."
    )
    parser.add_argument(
        "-n", "--count", type=int, default=10, help="Number of requests per target"
    )
    parser.add_argument(
        "--timeout", type=float, default=15.0, help="Request timeout in seconds"
    )
    args = parser.parse_args()

    print(f"=== Direct Host Connectivity Probe (count={args.count}) ===")
    results = []
    for target in TARGET_URLS:
        res = probe_target(target, count=args.count, timeout=args.timeout)
        results.append(res)

    print("\n" + "=" * 80)
    print("PROBE RESULTS SUMMARY")
    print("=" * 80)
    print(
        f"{'Target Name':<42} | {'Success':<8} | {'Codes':<14} | {'Med Lat (s)':<11} | {'Avg Bytes':<10}"
    )
    print("-" * 80)
    for r in results:
        success_str = f"{r['success_count']}/{r['count']}"
        codes_str = str(r["status_codes"])
        lat_str = f"{r['median_lat']:.2f}s"
        bytes_str = f"{int(r['avg_size']):,}"
        print(
            f"{r['name']:<42} | {success_str:<8} | {codes_str:<14} | {lat_str:<11} | {bytes_str:<10}"
        )

    print("=" * 80)
    for r in results:
        if r["errors"]:
            print(f"Errors for {r['name']}: {r['errors']}")


if __name__ == "__main__":
    main()
