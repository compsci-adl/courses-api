import hashlib
import os
import random
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from bs4 import BeautifulSoup

import data_fetcher
from log import logger


def fingerprint_page_content(html: str) -> tuple[int, str]:
    """Compute a resilient fingerprint of HTML content.

    Returns (content_len, content_hash) ignoring volatile scripts, styles, comments, and whitespace.
    """
    if not html:
        return (0, "")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "svg", "noscript"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda t: isinstance(t, type(soup.comment))):
        comment.extract()
    text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    content_len = len(text)
    content_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    return (content_len, content_hash)


class ScraperFetchError(Exception):
    """Raised when fetching fails due to network, proxy, or server errors."""

    pass


class WAFBlockedError(ScraperFetchError):
    """Raised when request is blocked or challenged by WAF / rate limiting."""

    pass


class FetchStatus(str, Enum):
    SUCCESS = "success"
    NOT_OFFERED = (
        "not_offered"  # Verified 404 or explicit "not offered" university page
    )
    BLOCKED_OR_ERROR = (
        "blocked_or_error"  # 403, 429, WAF captcha/challenge, timeout, connection reset
    )
    NO_TIMETABLE = (
        "no_timetable"  # Stable page with active terms but no timetable published
    )


@dataclass
class ParseResult:
    status: FetchStatus
    data: Optional[Any] = None
    error_message: Optional[str] = None

    def __bool__(self) -> bool:
        """Allow backwards compatibility with boolean checks (e.g. if not res:)."""
        return bool(self.data)

    def __getitem__(self, item):
        """Allow backwards compatibility with dict-style item access (e.g. res['title'])."""
        if isinstance(self.data, dict):
            return self.data[item]
        raise TypeError(f"ParseResult data is not subscriptable: {type(self.data)}")

    def __contains__(self, item):
        """Allow backwards compatibility with 'in' checks (e.g. 'courses' in res)."""
        if isinstance(self.data, dict):
            return item in self.data
        return False

    def get(self, key, default=None):
        """Allow backwards compatibility with dict-style .get()."""
        if isinstance(self.data, dict):
            return self.data.get(key, default)
        return default


def _extract_subjects_from_facet_data(data) -> list[dict[str, str]]:
    """Helper to extract Area of study subject names from Funnelback response data."""
    if not data or not isinstance(data, dict):
        return []
    facets = data.get("facets", [])
    area_facet = None
    for facet in facets:
        if (
            isinstance(facet, dict)
            and str(facet.get("name", "")).lower() == "area of study"
        ):
            area_facet = facet
            break
    if not area_facet and len(facets) > 5 and isinstance(facets[5], dict):
        area_facet = facets[5]

    if area_facet and isinstance(area_facet, dict) and "allValues" in area_facet:
        values = area_facet.get("allValues", [])
        subject_list = []
        for subject in values:
            if isinstance(subject, dict):
                subj = subject.get("data")
                if subj:
                    subject_list.append({"subject": subj})
        return subject_list
    return []


def get_subjects(year: int, timeout: float = 3.0) -> dict[str, list[dict[str, str]]]:
    """Return a list of subjects for a given year by querying Funnelback search facets dynamically.

    Tries year-filtered query first, then generic study-area facet query, with proxy retries.
    """
    query_endpoints = [
        f"?f.Tabs|type=Degrees+%26+Courses&form=json&num_ranks=10&profile=site-search&query=&f.Year|year={year}&collection=uosa~sp-aem-prod&f.Study+type|studyType=Course&start_rank=1",
        "?f.Tabs|type=Degrees+%26+Courses&form=json&num_ranks=10&profile=site-search&query=&collection=uosa~sp-aem-prod&f.Study+type|studyType=Course&start_rank=1",
    ]

    fetcher = None
    for attempt in range(4):
        for endpoint in query_endpoints:
            fetcher = data_fetcher.DataFetcher(
                endpoint,
                timeout=timeout + attempt * 1.0,
            )
            try:
                data = fetcher.get()
                if (
                    fetcher.last_response is not None
                    and fetcher.last_response.status_code == 200
                    and data
                    and isinstance(data, dict)
                ):
                    subject_list = _extract_subjects_from_facet_data(data)
                    if subject_list:
                        logger.debug(
                            f"Retrieved {len(subject_list)} subjects dynamically for year {year}"
                        )
                        return {"subjects": subject_list}
            except Exception as e:
                logger.debug(
                    f"Attempt {attempt + 1} to fetch subjects for year {year} ({endpoint}) failed: {e}"
                )

        time.sleep(0.5 + attempt * 0.5)

    status = (
        fetcher.last_response.status_code
        if fetcher and fetcher.last_response
        else "NO_RESPONSE"
    )
    logger.error(
        f"Failed to dynamically retrieve subjects for year {year} after 4 attempts (HTTP {status})."
    )
    return {"subjects": []}


WAF_SIGNATURES = [
    "attention required",
    "cloudflare",
    "cf-browser-verification",
    "access denied",
    "just a moment",
    "security check",
    "captcha",
    "challenge-running",
]


def _is_waf_blocked(response_obj, data_dict: dict | None = None) -> bool:
    """Check if the response indicates a WAF block or challenge."""
    if response_obj is not None:
        status_code = getattr(response_obj, "status_code", None)
        if status_code in (403, 429):
            return True
        resp_text = (getattr(response_obj, "text", "") or "").lower()
        if any(sig in resp_text for sig in WAF_SIGNATURES):
            return True
    if data_dict and isinstance(data_dict, dict):
        html_val = str(data_dict.get("html", "")).lower()
        data_val = str(data_dict.get("data", "")).lower()
        h1_val = str(data_dict.get("h1", "")).lower()
        combined = f"{h1_val} {data_val} {html_val}"
        if any(sig in combined for sig in WAF_SIGNATURES):
            return True
    return False


def get_course_codes(subject: str, year: int, timeout: float = 12.0) -> ParseResult:
    """Return a ParseResult with course codes for a given subject code and year."""
    # Ensure spaces and & are safely encoded for query string
    safe_subject = str(subject).replace(" ", "%20").replace("&", "%26")
    endpoint = (
        f"?f.Tabs%7Ctype=Degrees+%26+Courses&form=json&f.Year%7Cyear={year}"
        f"&num_ranks=1000&profile=site-search&query=&f.Area+of+study%7CstudyArea={safe_subject}"
        f"&collection=uosa%7Esp-aem-prod&f.Study+type%7CstudyType=Course"
    )

    max_attempts = int(os.environ.get("LISTING_MAX_ATTEMPTS", "12"))
    last_error = None
    courses = None

    for attempt in range(1, max_attempts + 1):
        t0 = time.time()
        courses = data_fetcher.DataFetcher(
            endpoint,
            timeout=timeout,
        )
        try:
            data = courses.get()
            elapsed = time.time() - t0
            status = getattr(courses.last_response, "status_code", "ERR")
            path_used = getattr(courses, "last_used_proxy", "unknown")
            body_bytes = len(getattr(courses.last_response, "content", b"") or b"")
            logger.debug(
                f"Listing {subject} page=1: path={path_used}, attempt={attempt}, status={status}, bytes={body_bytes}, elapsed={elapsed:.2f}s"
            )

            if (
                courses.last_response is not None
                and courses.last_response.status_code == 200
                and data
                and isinstance(data, dict)
            ):
                results = data.get("resultPacket", {}).get("results", [])
                if not results and "response" in data:
                    results = (
                        data.get("response", {})
                        .get("resultPacket", {})
                        .get("results", [])
                    )

                course_codes = [
                    {
                        "code": course.get("listMetadata", {}).get("courseCode"),
                        "terms": course.get("listMetadata", {}).get("term"),
                    }
                    for course in results
                    if course.get("listMetadata", {}).get("courseCode")
                ]
                logger.debug(
                    f"Course codes extracted successfully for {subject}: {len(course_codes)}"
                )
                return ParseResult(
                    status=FetchStatus.SUCCESS,
                    data={"courses": course_codes},
                )
            elif (
                courses.last_response is not None
                and courses.last_response.status_code == 404
            ):
                return ParseResult(
                    status=FetchStatus.NOT_OFFERED,
                    data={"courses": []},
                    error_message="HTTP 404",
                )
            elif _is_waf_blocked(
                courses.last_response, data if isinstance(data, dict) else None
            ):
                last_error = f"WAF block (HTTP {getattr(courses.last_response, 'status_code', 'unknown')})"
        except Exception as e:
            last_error = str(e)
            logger.debug(f"Attempt {attempt} for course codes {subject} failed: {e}")

        # Exponential backoff with jitter (base 1.5s, factor 1.7, cap 30s) if more attempts remain
        if attempt < max_attempts:
            backoff = min(1.5 * (1.7 ** (attempt - 1)), 30.0) + random.uniform(0.1, 0.5)
            # Check Retry-After if 429
            if (
                courses
                and courses.last_response
                and courses.last_response.status_code == 429
            ):
                retry_hdr = courses.last_response.headers.get("Retry-After")
                if retry_hdr:
                    try:
                        backoff = max(backoff, float(retry_hdr))
                    except (ValueError, TypeError):
                        pass
            time.sleep(backoff)

    status = (
        courses.last_response.status_code
        if courses and courses.last_response
        else "NO_RESPONSE"
    )
    logger.warning(
        f"Error fetching course codes for {subject} after {max_attempts} attempts: HTTP {status}"
    )
    return ParseResult(
        status=FetchStatus.BLOCKED_OR_ERROR,
        data=None,
        error_message=last_error or f"HTTP {status}",
    )


def get_course_details(course_code: str, year: int | str | None = None) -> ParseResult:
    """Return ParseResult with the details for a given course with retries."""
    logger.debug(f"Fetching details for course {course_code}")
    code_str = course_code[0] if isinstance(course_code, (list, tuple)) else course_code
    encoded_course_code = re.sub(
        r"([a-zA-Z]+)([0-9]+)", r"\1-\2", str(code_str)
    ).lower()

    endpoint = (
        f"/study/courses/{year}/{encoded_course_code}/"
        if year
        else f"/study/courses/{encoded_course_code}/"
    )

    last_error = None
    course_details = None
    for attempt in range(3):
        course_details = data_fetcher.DataFetcher(endpoint, use_class_url=True)
        try:
            data = course_details.get()
            if (
                course_details.last_response is not None
                and course_details.last_response.status_code == 200
                and data
                and isinstance(data, dict)
            ):
                h1_text = (data.get("h1") or "").strip()
                if (
                    h1_text.lower() == "page not found"
                    or "not offered" in h1_text.lower()
                ):
                    logger.info(
                        f"Course {course_code} is not offered for year {year} (404 Page Not Found)."
                    )
                    return ParseResult(
                        status=FetchStatus.NOT_OFFERED,
                        data=None,
                        error_message="Page not found",
                    )

                if _is_waf_blocked(course_details.last_response, data):
                    last_error = "WAF challenge page detected in 200 OK body"
                    logger.warning(f"Course {course_code} returned WAF challenge page.")
                    time.sleep(0.5 + attempt * 0.5)
                    continue

                text = data.get("data", "")
                body_text = (
                    BeautifulSoup(text, "html.parser").get_text()
                    if ("<" in text and ">" in text)
                    else text
                )

                # Parse the plain-body text for label/value pairs
                parsed = parse_course_text(body_text)

                # Extract terms from raw HTML accordion titles if present
                raw_html = data.get("html", "")
                terms = []
                if raw_html:
                    html_soup = BeautifulSoup(raw_html, "html.parser")
                    for el in html_soup.select(".cmp-course-accordion__title"):
                        txt = el.get_text(strip=True)
                        if re.search(
                            r"^(Semester|Trimester|Term|Summer|Winter|Spring|Autumn)\b",
                            txt,
                            re.I,
                        ):
                            if txt not in terms:
                                terms.append(txt)

                # Return a dict with the parsed fields and the canonical code string
                course_info = {
                    "code": code_str,
                    "title": data.get("h1", ""),
                    "course_id": parsed.get("course_id"),
                    "campus": parsed.get("campus"),
                    "level_of_study": parsed.get("level_of_study"),
                    "units": parsed.get("units"),
                    "course_coordinator": parsed.get("course_coordinator"),
                    "course_level": parsed.get("course_level"),
                    "course_overview": parsed.get("course_overview"),
                    "prerequisites": parsed.get("prerequisites"),
                    "corequisites": parsed.get("corequisites"),
                    "antirequisites": parsed.get("antirequisites"),
                    "university_wide_elective": (
                        True
                        if parsed.get("university_wide_elective") == "Yes"
                        else False
                        if parsed.get("university_wide_elective") == "No"
                        else parsed.get("university_wide_elective")
                    ),
                    "terms": terms if terms else None,
                    "html": raw_html,
                }

                logger.debug("Course details extracted successfully.")
                return ParseResult(
                    status=FetchStatus.SUCCESS,
                    data=course_info,
                )

            elif (
                course_details.last_response is not None
                and course_details.last_response.status_code == 404
            ):
                logger.info(
                    f"Course {course_code} is not offered for year {year} (404 Not Found)."
                )
                return ParseResult(
                    status=FetchStatus.NOT_OFFERED,
                    data=None,
                    error_message="HTTP 404",
                )
            elif _is_waf_blocked(
                course_details.last_response, data if isinstance(data, dict) else None
            ):
                last_error = f"WAF block (HTTP {getattr(course_details.last_response, 'status_code', 'unknown')})"
        except Exception as e:
            last_error = str(e)
            logger.debug(
                f"Attempt {attempt + 1} for course details {course_code} failed: {e}"
            )

        time.sleep(0.5 + attempt * 0.5)

    status = (
        course_details.last_response.status_code
        if course_details and course_details.last_response
        else "NO_RESPONSE"
    )
    logger.warning(
        f"Could not retrieve details for course {course_code} after 3 attempts (HTTP {status})."
    )
    return ParseResult(
        status=FetchStatus.BLOCKED_OR_ERROR,
        data=None,
        error_message=last_error or f"HTTP {status}",
    )


def parse_course_text(text: str) -> dict:
    """Parse a course details plain text and return a dict of fields."""
    if not isinstance(text, str):
        return {}

    # Ensure text is plain and normalised
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    labels = {
        "course id": "course_id",
        "campus": "campus",
        "level of study": "level_of_study",
        "unit value": "units",
        "course coordinator": "course_coordinator",
        "course level": "course_level",
        "course overview": "course_overview",
        "prerequisite(s)": "prerequisites",
        "corequisite(s)": "corequisites",
        "antirequisite(s)": "antirequisites",
        "university-wide elective course": "university_wide_elective",
    }

    parsed = {v: None for v in labels.values()}
    i = 0
    # Update the parse_course_text function to handle the case where campus is "Location"
    while i < len(lines):
        key = lines[i].lower()
        if key in labels and i + 1 < len(lines):
            parsed_key = labels[key]
            value = lines[i + 1].strip()
            # Skip if the value for campus is "Location"
            if parsed_key == "campus" and value.lower() == "location":
                i += 2
                continue
            parsed[parsed_key] = value
            i += 2
            continue
        i += 1
    return parsed


def get_course_class_list(
    course_code: int | str,
    year: int | str | None = None,
    raw_html: str | None = None,
    terms: Any = None,
) -> dict:
    """Extract class timetable with explicit network outcome status.

    Returns:
        dict: {"status": "success" | "error", "classes": list[dict], ...}
    """
    code_str = course_code[0] if isinstance(course_code, (list, tuple)) else course_code
    fetcher_status = 200
    used_proxy = None

    if raw_html is None:
        # Encode course code to match URL format
        encoded_course_code = re.sub(
            r"([a-zA-Z]+)([0-9]+)", r"\1-\2", str(code_str)
        ).lower()

        endpoint = (
            f"/study/courses/{year}/{encoded_course_code}/"
            if year
            else f"/study/courses/{encoded_course_code}/"
        )
        fetcher = data_fetcher.DataFetcher(endpoint, use_class_url=True)
        try:
            parsed_res = fetcher.get()
            html_content = ""
            if fetcher.last_response:
                fetcher_status = fetcher.last_response.status_code
            used_proxy = getattr(fetcher, "last_used_proxy", None)

            if parsed_res and isinstance(parsed_res, dict):
                # If page is explicitly not offered (e.g. 404 Page Not Found)
                if parsed_res.get("h1", "").lower() == "page not found":
                    return {
                        "status": "success",
                        "success": True,
                        "code": code_str,
                        "title": parsed_res.get("h1", ""),
                        "course_id": None,
                        "classes": [],
                        "http_status": fetcher_status,
                        "raw_html": "",
                        "proxy": used_proxy,
                    }
                html_content = parsed_res.get("html", "") or parsed_res.get("data", "")
            elif fetcher.last_response and fetcher.last_response.status_code == 200:
                html_content = fetcher.last_response.text
            elif fetcher.last_response and fetcher.last_response.status_code == 404:
                return {
                    "status": "success",
                    "success": True,
                    "code": code_str,
                    "title": "",
                    "course_id": None,
                    "classes": [],
                    "http_status": 404,
                    "raw_html": "",
                    "proxy": used_proxy,
                }

            if not html_content or not str(html_content).strip():
                status = (
                    fetcher.last_response.status_code
                    if fetcher.last_response
                    else "NO_RESPONSE"
                )
                logger.error(
                    f"Error fetching class list for {course_code}: Status {status}"
                )
                return {
                    "status": "error",
                    "success": False,
                    "code": code_str,
                    "title": "",
                    "course_id": None,
                    "classes": [],
                    "error": f"HTTP {status}",
                    "http_status": status,
                    "raw_html": "",
                    "proxy": used_proxy,
                }
            raw_html = html_content
        except Exception as e:
            logger.error(f"An error occurred while fetching course class list: {e}")
            return {
                "status": "error",
                "success": False,
                "code": code_str,
                "title": "",
                "course_id": None,
                "classes": [],
                "error": str(e),
                "http_status": fetcher_status,
                "raw_html": "",
                "proxy": used_proxy,
            }

    if not raw_html or not str(raw_html).strip():
        return {
            "status": "error",
            "success": False,
            "code": code_str,
            "title": "",
            "course_id": None,
            "classes": [],
            "error": "Empty HTML content",
            "http_status": fetcher_status,
            "raw_html": "",
            "proxy": used_proxy,
        }

    raw_html_str = str(raw_html)
    # Check minimum sensible body length (e.g. 500 characters)
    if len(raw_html_str.strip()) < 500:
        logger.warning(
            f"Suspiciously short HTML body ({len(raw_html_str)} chars) for {code_str}."
        )
        return {
            "status": "error",
            "success": False,
            "code": code_str,
            "title": "",
            "course_id": None,
            "classes": [],
            "error": f"HTML body too short ({len(raw_html_str)} bytes)",
            "http_status": fetcher_status,
            "raw_html": raw_html_str,
            "proxy": used_proxy,
        }

    # If course is offered in terms, verify expected page markers are present
    has_terms = bool(terms)
    has_markers = ("Class details" in raw_html_str) or ("Enrol by date" in raw_html_str)
    fingerprint = fingerprint_page_content(raw_html_str)
    if has_terms and not has_markers:
        logger.warning(
            f"Course {code_str} has terms '{terms}' but HTML lacks class markers ('Class details' / 'Enrol by date')."
        )
        return {
            "status": "error",
            "success": False,
            "code": code_str,
            "title": "",
            "course_id": None,
            "classes": [],
            "error": "Missing class timetable markers on page with active terms",
            "http_status": fetcher_status,
            "raw_html": raw_html_str,
            "proxy": used_proxy,
            "markers_found": False,
            "fingerprint": fingerprint,
        }

    parsed_classes = parse_course_class_list(raw_html_str)
    return {
        "status": "success",
        "success": True,
        "code": code_str,
        "classes": parsed_classes,
        "http_status": fetcher_status,
        "raw_html": raw_html_str,
        "proxy": used_proxy,
        "markers_found": has_markers,
        "fingerprint": fingerprint,
    }


def parse_course_class_list(text: str) -> list[dict]:
    """Parse course class list details from the given text."""
    if not isinstance(text, str):
        return []

    soup = BeautifulSoup(text, "html.parser")
    parsed_classes = []

    # Find all component containers (e.g. Enrolment class, Related class)
    # The structure: .cmp-course-accordion__class-details -> .cmp-course-accordion -> .cmp-course-accordion--group
    # -> .cmp-course-accordion--container (this holds the component title)
    #    -> .cmp-course-accordion--container-content
    #       -> .cmp-course-accordion--container-session (one or more, these hold the class)

    # Iterate over all .cmp-course-accordion--container-session to find classes

    sessions = soup.select(".cmp-course-accordion--container-session")
    for session in sessions:
        class_info = {
            "meetings": [],
            "component": "unknown",
            "group": None,
            "class_number": None,
            "section": None,
            "size": None,
            "available": None,
        }

        # Find group name if session is inside a group container
        group_el = session.find_parent("div", class_="cmp-course-accordion--group")
        if group_el:
            title_text_el = group_el.select_one(
                ".cmp-course-accordion--group-title-text"
            )
            raw_group = ""
            if title_text_el:
                raw_group = title_text_el.get_text(strip=True)
            else:
                group_title_el = group_el.select_one(
                    ".cmp-course-accordion--group-title"
                )
                if group_title_el:
                    raw_group = group_title_el.get_text(strip=True)

            if raw_group:
                clean_group = re.sub(r"(?i)^group\s*", "", raw_group).strip()
                class_info["group"] = clean_group if clean_group else raw_group

        # Find component name from parent container
        # session -> content -> container -> h5(title)
        container = session.find_parent("div", class_="cmp-course-accordion--container")
        if container:
            title_el = container.select_one(".cmp-course-accordion__title")
            if title_el:
                class_info["component"] = title_el.get_text(strip=True)

        # Parse cards for class details
        cards = session.select(".cmp-course-accordion--card-text")
        for card in cards:
            text = card.get_text(strip=True)
            if "Class number" in text:
                class_info["class_number"] = text.replace("Class number", "").strip()
            elif "Section" in text:
                class_info["section"] = text.replace("Section", "").strip()
            elif "Size" in text:
                class_info["size"] = text.replace("Size", "").strip()
            elif "Available" in text:
                class_info["available"] = text.replace("Available", "").strip()

        # Parse meetings table
        rows = session.select("table tbody tr")
        for row in rows:
            cols = row.select("td")
            if not cols:
                continue

            def get_val(col):
                # Attempt to find .table-content div first (used in responsive tables)
                content = col.select_one(".table-content")
                if content:
                    val = content.get_text(separator=" ", strip=True)
                else:
                    val = col.get_text(separator=" ", strip=True)

                # Clean up specific placeholders like "-", ",", "N/A"
                if val in ["-", ",", "N/A"]:
                    return ""
                return val

            # Expected order: Dates, Days, Time, Campus, Location, Instructor
            # Verify headers if possible, but structure seems consistent.
            if len(cols) >= 5:  # Some might miss Instructor
                meeting = {
                    "dates": get_val(cols[0]),
                    "days": get_val(cols[1]),
                    "time": get_val(cols[2]),
                    "campus": get_val(cols[3]),
                    "location": get_val(cols[4]),
                }
                if len(cols) >= 6:
                    meeting["instructor"] = get_val(cols[5])

                class_info["meetings"].append(meeting)

        if class_info.get("class_number"):
            parsed_classes.append(class_info)

    return parsed_classes


def parse_course_outline(html_content: str) -> dict:
    """
    Parse the course outline.
    Extracts:
    - Aim (from Course Overview)
    - Learning Outcomes
    - Learning Resources (Textbooks)
    - Assessments
    """
    if not html_content:
        return {}

    soup = BeautifulSoup(html_content, "html.parser")
    result = {
        "aim": None,
        "learning_outcomes": [],
        "textbooks": None,
        "assessments": [],
    }

    # Helper to find section content based on header
    def get_section_text(header_text):
        header = soup.find(
            lambda tag: (
                tag.name in ["h2", "h3", "h4"]
                and header_text.lower() in tag.get_text().lower()
            )
        )
        if header:
            content = []
            curr = header.find_next_sibling()
            while curr and curr.name not in ["h2", "h3", "h4"]:
                text = curr.get_text(strip=True, separator=" ")
                if text:
                    content.append(text)
                curr = curr.find_next_sibling()
            return "\n\n".join(content)
        return None

    aim_text = get_section_text("Aim")
    if not aim_text:
        pass
    result["aim"] = aim_text

    lo_header = soup.find(
        lambda tag: tag.name in ["h2", "h3"] and "Learning Outcomes" in tag.get_text()
    )
    if lo_header:
        lo_table = lo_header.find_next("table")
        if lo_table:
            rows = lo_table.find_all("tr")
            for row in rows:
                cols = row.find_all("td")
                if len(cols) >= 2:
                    text = cols[1].get_text(strip=True)
                    if text.startswith("Course Learning Outcome"):
                        text = text[len("Course Learning Outcome") :].strip()
                    result["learning_outcomes"].append(text)
        else:
            lo_list = lo_header.find_next("ul") or lo_header.find_next("ol")
            if lo_list:
                for li in lo_list.find_all("li"):
                    text = li.get_text(strip=True)
                    if text.startswith("Course Learning Outcome"):
                        text = text[len("Course Learning Outcome") :].strip()
                    result["learning_outcomes"].append(text)

    text_res = get_section_text("Learning Resources")
    if text_res:
        pass
    result["textbooks"] = text_res

    assess_header = soup.find(
        lambda tag: (
            tag.name in ["h2", "h3"] and "Assessment Descriptions" in tag.get_text()
        )
    )
    if assess_header:
        assess_table = assess_header.find_next("table")
        if assess_table:
            headers = [
                th.get_text(strip=True).lower() for th in assess_table.find_all("th")
            ]
            idx_title = 0
            idx_weight = -1
            idx_hurdle = -1
            idx_lo = -1

            for i, h in enumerate(headers):
                if "title" in h:
                    idx_title = i
                elif "weight" in h:
                    idx_weight = i
                elif "hurdle" in h:
                    idx_hurdle = i
                elif "learning outcome" in h:
                    idx_lo = i

            rows = assess_table.find_all("tr")[1:]  # Skip header
            for row in rows:
                cols = row.find_all("td")
                if not cols:
                    continue

                # Helper to safely get cleaned text
                def get_col_text(idx, prefix=None):
                    if idx >= 0 and idx < len(cols):
                        text = cols[idx].get_text(strip=True)
                        if prefix and text.startswith(prefix):
                            text = text[len(prefix) :].strip()
                        return text
                    return None

                assessment = {
                    "title": get_col_text(idx_title, "Title"),
                    "weighting": get_col_text(idx_weight, "Weighting"),
                    "hurdle": get_col_text(idx_hurdle, "Hurdle"),
                    "learning_outcomes": get_col_text(idx_lo, "Learning Outcomes"),
                }

                if assessment["title"]:
                    result["assessments"].append(assessment)

    return result
