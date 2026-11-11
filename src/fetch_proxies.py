from concurrent.futures import ThreadPoolExecutor, as_completed

from curl_cffi import requests as cffi_requests
from curl_cffi.requests.exceptions import RequestException
from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
)


def fetch_proxies(url):
    """Fetch the list of proxies from the URL."""
    import requests as std_requests

    response = std_requests.get(url, timeout=15)
    if response.status_code == 200:
        # Split the response content by newline to get each proxy
        return response.text.splitlines()
    else:
        print("Failed to retrieve proxies.")
        return []


def test_proxy(
    proxy,
    test_url="https://adelaide.edu.au/study/courses/",
    timeout=5,
    retries=1,
):
    """Test if the given proxy is working by making a request using Chrome TLS impersonation.

    Uses curl_cffi so the WAF fingerprint check passes (same as the scraper itself).
    """
    proxies = {
        "http": f"http://{proxy}",
        "https": f"http://{proxy}",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    for attempt in range(retries + 1):
        try:
            response = cffi_requests.get(
                test_url,
                proxies=proxies,
                headers=headers,
                timeout=timeout,
                impersonate="chrome146",
            )
            if response.status_code == 200:
                return proxy
            elif response.status_code == 403:
                # WAF block even with impersonation — proxy IP is blacklisted
                return None
        except (RequestException, Exception):
            if attempt < retries:
                continue
            return None
    return None


def save_working_proxies(proxies, filename="src/working_proxies.txt"):
    """Save working proxies to a text file."""
    with open(filename, "w") as file:
        for proxy in proxies:
            file.write(f"{proxy}\n")


def main(target_working: int = 500):
    proxy_urls = [
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/refs/heads/master/http.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
    ]
    proxies = []
    for url in proxy_urls:
        try:
            proxies.extend(fetch_proxies(url))
        except Exception:
            pass
    # Deduplicate proxies while preserving order
    proxies = list(dict.fromkeys(proxies))

    working_proxies = []

    # Rich Progress Bar
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task("Testing Proxies...", total=len(proxies))

        # Use ThreadPoolExecutor for concurrency
        with ThreadPoolExecutor(max_workers=100) as executor:
            future_to_proxy = {
                executor.submit(test_proxy, proxy): proxy for proxy in proxies
            }
            for future in as_completed(future_to_proxy):
                progress.update(task, advance=1)
                try:
                    result = future.result()
                    if result:
                        working_proxies.append(result)
                        if len(working_proxies) >= target_working:
                            for f in future_to_proxy:
                                f.cancel()
                            break
                except Exception:
                    pass

    # Save working proxies to file
    if working_proxies:
        save_working_proxies(working_proxies)
        print(
            f"\n[+] Saved {len(working_proxies)} working proxies to 'working_proxies.txt'."
        )
    else:
        print("\n[-] No working proxies found.")


if __name__ == "__main__":
    main()
