from concurrent.futures import ThreadPoolExecutor, as_completed

from curl_cffi import requests
from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
)


def fetch_proxies(url):
    """Fetch the list of proxies from the URL."""
    try:
        response = requests.get(url, timeout=10, impersonate="chrome124")
        if response.status_code == 200:
            return response.text.splitlines()
    except Exception:
        pass
    print("Failed to retrieve proxies.")
    return []


def test_proxy(
    proxy,
    test_url="https://adelaide.edu.au/study/courses/",
    timeout=5,
    retries=1,  # Number of retries
):
    """Test if the given proxy is genuinely working for Adelaide University."""
    proxies = {
        "http": f"http://{proxy}",
        "https": f"http://{proxy}",
    }
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://adelaide.edu.au/",
    }
    for attempt in range(retries + 1):
        try:
            response = requests.get(
                test_url,
                proxies=proxies,
                headers=headers,
                timeout=timeout,
                impersonate="chrome124",
            )
            if response.status_code == 200:
                raw_text = response.text or ""
                raw_lower = raw_text.lower()
                has_genuine_markers = "<html" in raw_lower and (
                    "university of adelaide" in raw_lower
                    or "study at adelaide" in raw_lower
                    or "course" in raw_lower
                )
                is_proxy_error = any(
                    err in raw_lower
                    for err in [
                        "squid",
                        "access denied",
                        "proxy error",
                        "gateway timeout",
                        "connection refused",
                        "not acceptable",
                        "attention required",
                        "cloudflare",
                        "406 not acceptable",
                        "502 bad gateway",
                    ]
                )
                if has_genuine_markers and not is_proxy_error:
                    return proxy
            elif response.status_code in (403, 406, 429):
                return None
        except Exception:
            if attempt < retries:
                continue
            return None
    return None


def save_working_proxies(proxies, filename="src/working_proxies.txt"):
    """Save working proxies to text file, writing to both candidate paths."""
    candidates = [filename, "src/working_proxies.txt", "working_proxies.txt"]
    for path in set(candidates):
        try:
            with open(path, "w") as file:
                for proxy in proxies:
                    file.write(f"{proxy}\n")
        except Exception:
            pass


def main(target_working: int = 30):
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
                    pass  # Handle or log specific proxy testing errors if needed

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
