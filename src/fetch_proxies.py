import os
import random
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.exceptions import RequestException, Timeout


def fetch_proxies(url):
    """Fetch the list of proxies from the URL."""
    response = requests.get(url)
    if response.status_code == 200:
        # Split the response content by newline to get each proxy
        return response.text.splitlines()
    else:
        print("Failed to retrieve proxies.")
        return []


def test_proxy(
    proxy,
    test_url="https://adelaide.edu.au/study/courses/",
    timeout=2.5,
    retries=0,  # Fast single-shot candidate check without retrying dead proxies
):
    """Test if the given proxy is working by making a request."""
    proxies = {
        "http": f"http://{proxy}",
        "https": f"http://{proxy}",
    }
    for attempt in range(retries + 1):  # Retry logic
        try:
            # Use realistic User-Agent to avoid WAF block
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
            }
            response = requests.get(
                test_url, proxies=proxies, headers=headers, timeout=timeout
            )
            if response.status_code == 200:
                return proxy  # Return the working proxy
            elif response.status_code == 403:
                # WAF block, skip immediately
                return None
        except (RequestException, Timeout):
            if attempt < retries:
                continue  # Retry on failure
            else:
                return None  # Skip the proxy if all retries fail
    return None


def save_working_proxies(proxies, filename="src/working_proxies.txt"):
    """Atomically save working proxies to a text file."""
    dirname = os.path.dirname(filename) or "."
    os.makedirs(dirname, exist_ok=True)
    temp_fd, temp_path = tempfile.mkstemp(dir=dirname, prefix="proxies_", suffix=".tmp")
    try:
        with open(temp_fd, "w", encoding="utf-8") as file:
            for proxy in proxies:
                file.write(f"{proxy}\n")
        os.replace(temp_path, filename)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def discover_working_proxies(
    target: int,
    max_candidates: int | None = None,
    stop_event: threading.Event | None = None,
    exclude: set[str] | None = None,
    show_progress: bool = False,
) -> list[str]:
    """Fetch candidate lists, deduplicate, filter excluded proxies, and concurrently test

    until target working proxies are found or stop_event is set.
    Does not write to files or mutate global state.
    """
    if stop_event and stop_event.is_set():
        return []

    if os.getenv("PROXY_URL"):
        return []

    proxy_urls = [
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/refs/heads/master/http.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
    ]
    raw_proxies = []
    for url in proxy_urls:
        if stop_event and stop_event.is_set():
            return []
        try:
            raw_proxies.extend(fetch_proxies(url))
        except Exception:
            pass

    exclude_set = exclude or set()
    deduped = []
    seen = set()
    for p in raw_proxies:
        p_clean = p.strip()
        if p_clean and p_clean not in seen and p_clean not in exclude_set:
            seen.add(p_clean)
            deduped.append(p_clean)

    # Shuffle candidates to sample randomly across providers rather than getting stuck in dead IP blocks
    random.shuffle(deduped)

    if max_candidates is not None and max_candidates > 0:
        deduped = deduped[:max_candidates]

    working_proxies = []
    test_workers = int(os.environ.get("PROXY_TEST_WORKERS", "100"))
    executor = ThreadPoolExecutor(max_workers=test_workers)
    try:
        max_to_submit = min(len(deduped), max(target * 15, 500))
        future_to_proxy = {
            executor.submit(test_proxy, proxy): proxy
            for proxy in deduped[:max_to_submit]
        }
        for future in as_completed(future_to_proxy):
            if stop_event and stop_event.is_set():
                break
            try:
                result = future.result()
                if result:
                    working_proxies.append(result)
                    if len(working_proxies) >= target:
                        break
            except Exception:
                pass
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return working_proxies


def main(target_working: int = 500):
    if os.getenv("PROXY_URL"):
        print("PROXY_URL configured; skipping public proxy fetching.")
        return

    working_proxies = discover_working_proxies(
        target=target_working,
        show_progress=True,
    )
    if working_proxies:
        save_working_proxies(working_proxies)
        print(
            f"\n[+] Saved {len(working_proxies)} working proxies to 'working_proxies.txt'."
        )
    else:
        print("\n[-] No working proxies found.")


if __name__ == "__main__":
    main()
