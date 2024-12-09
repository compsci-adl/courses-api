from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.exceptions import RequestException, Timeout
from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
)


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
    test_url="https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/SUBJECTS_BY_YEAR/queryx&virtual=Y&year_from=2025&year_to=2025",
    timeout=5,
    retries=2,  # Number of retries
):
    """Test if the given proxy is working by making a request."""
    proxies = {
        "http": f"http://{proxy}",
        "https": f"http://{proxy}",
    }
    for attempt in range(retries + 1):  # Retry logic
        try:
            response = requests.get(test_url, proxies=proxies, timeout=timeout)
            if response.status_code == 200:
                return proxy  # Return the working proxy
        except (RequestException, Timeout):
            if attempt < retries:
                continue  # Retry on failure
            else:
                return None  # Skip the proxy if all retries fail


def save_working_proxies(proxies, filename="src/working_proxies.txt"):
    """Save working proxies to a text file."""
    with open(filename, "w") as file:
        for proxy in proxies:
            file.write(f"{proxy}\n")


def main():
    proxy_url = "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/refs/heads/master/http.txt"
    proxies = fetch_proxies(proxy_url)

    working_proxies = []

    # Rich Progress Bar
    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeRemainingColumn(),
    ) as progress:
        task = progress.add_task("Testing Proxies...", total=len(proxies))

        # Use ThreadPoolExecutor for concurrency
        with ThreadPoolExecutor(max_workers=1000) as executor:
            future_to_proxy = {
                executor.submit(test_proxy, proxy): proxy for proxy in proxies
            }
            for future in as_completed(future_to_proxy):
                progress.update(task, advance=1)
                try:
                    result = future.result()
                    if result:
                        working_proxies.append(result)
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
