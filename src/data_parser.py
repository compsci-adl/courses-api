import re
import time
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

import data_fetcher
from log import logger


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


def get_courses_from_sitemap(timeout: float = 10.0) -> list[dict]:
    """Fetch all published course slugs from https://adelaide.edu.au/sitemap.xml.

    Catches courses that exist in the AEM publication tree but may be missing or
    omitted from the Funnelback search index.
    """
    logger.info("Fetching course list from sitemap.xml...")
    try:
        fetcher = data_fetcher.DataFetcher(
            "/sitemap.xml", use_class_url=True, timeout=timeout
        )
        data = fetcher.get()
        xml_text = ""
        if isinstance(data, dict):
            xml_text = data.get("html", "") or data.get("data", "")
        if not xml_text and fetcher.last_response is not None:
            xml_text = getattr(fetcher.last_response, "text", "")
        if not xml_text:
            return []

        xml_lower = xml_text.lower()
        if "<urlset" not in xml_lower and "<sitemapindex" not in xml_lower:
            logger.debug("Sitemap XML does not contain <urlset> or <sitemapindex>.")
            return []

        root = ET.fromstring(xml_text)
        sitemap_courses = []
        for elem in root.iter():
            if elem.tag.endswith("loc") and elem.text:
                loc = elem.text.strip()
                match = re.search(r"/study/courses/([^/]+)/?$", loc)
                if not match:
                    continue
                slug = match.group(1).strip()
                if slug.isdigit() or slug in ("search", "degrees"):
                    continue
                code = slug.replace("-", "").upper()
                parts = slug.split("-")
                subj = parts[0].upper() if parts else "General"
                sitemap_courses.append(
                    {
                        "code": code,
                        "slug": slug,
                        "title": "",
                        "subject": subj,
                        "terms": None,
                        "date_modified": None,
                        "url": loc,
                        "units": None,
                        "campus": None,
                        "level_of_study": None,
                        "course_level": None,
                        "course_availability": None,
                    }
                )

        logger.info(f"Retrieved {len(sitemap_courses)} course slugs from sitemap.xml.")
        return sitemap_courses
    except Exception as e:
        logger.debug(f"Failed to fetch or parse sitemap.xml: {e}")
        return []


def get_all_courses_bulk(
    year: int | None = None,
    max_per_page: int = 1000,
    filter_by_year: bool = False,
    include_sitemap: bool = True,
) -> list[dict]:
    """Enumerate all courses via bulk paginated Funnelback queries, optionally augmented by sitemap.xml.

    By default (filter_by_year=False), queries without f.Year|year to get the master list of all
    course slugs across all academic periods (catching courses whose search metadata still says 2026
    or lacks year tags). Merges with sitemap.xml to achieve 100% catalog coverage.
    """
    all_courses = []
    seen_codes = set()
    start_rank = 1
    year_filter = f"&f.Year%7Cyear={year}" if (filter_by_year and year) else ""

    while True:
        endpoint = (
            f"?f.Tabs%7Ctype=Degrees+%26+Courses&form=json{year_filter}"
            f"&num_ranks={max_per_page}&profile=site-search&query="
            f"&collection=uosa%7Esp-aem-prod&f.Study+type%7CstudyType=Course"
            f"&start_rank={start_rank}"
        )
        fetcher = data_fetcher.DataFetcher(endpoint, timeout=15.0)
        data = fetcher.get()

        if not data or not isinstance(data, dict):
            break

        results = data.get("resultPacket", {}).get("results", [])
        if not results and "response" in data:
            results = (
                data.get("response", {}).get("resultPacket", {}).get("results", [])
            )

        if not results:
            break

        for item in results:
            meta = item.get("listMetadata", {})
            code_list = meta.get("courseCode")
            code = (
                code_list[0] if isinstance(code_list, list) and code_list else code_list
            )
            if not code:
                continue

            norm_code = str(code).replace(" ", "").upper()
            seen_codes.add(norm_code)

            subj_list = meta.get("studyArea")
            subj = (
                subj_list[0]
                if isinstance(subj_list, list) and subj_list
                else (subj_list or "")
            )

            term_list = meta.get("term")
            date_mod_list = meta.get("dateModified")
            date_mod = (
                date_mod_list[0]
                if isinstance(date_mod_list, list) and date_mod_list
                else date_mod_list
            )

            unit_list = meta.get("unitValue")
            unit_val = (
                unit_list[0] if isinstance(unit_list, list) and unit_list else unit_list
            )

            live_url = item.get("liveUrl") or ""
            slug = None
            slug_match = re.search(r"/study/courses/([^/]+)/?$", live_url)
            if slug_match and not slug_match.group(1).isdigit():
                slug = slug_match.group(1)

            all_courses.append(
                {
                    "code": code,
                    "slug": slug,
                    "title": item.get("title", "").strip(),
                    "subject": subj,
                    "terms": term_list,
                    "date_modified": date_mod,
                    "url": live_url,
                    "units": unit_val,
                    "campus": meta.get("location"),
                    "level_of_study": meta.get("studyLevel"),
                    "course_level": meta.get("courseLevel"),
                    "course_availability": meta.get("courseAvailability"),
                }
            )

        rp = data.get("resultPacket") or data.get("response", {}).get(
            "resultPacket", {}
        )
        total_matching = rp.get("resultsSummary", {}).get("totalMatching", 0)
        start_rank += len(results)
        if start_rank > total_matching or len(results) < max_per_page:
            break

    logger.info(
        f"Retrieved {len(all_courses)} courses from Funnelback (year filter: {filter_by_year})."
    )

    if include_sitemap:
        try:
            sitemap_courses = get_courses_from_sitemap()
            added_from_sitemap = 0
            for sc in sitemap_courses:
                sc_norm = sc["code"].replace(" ", "").upper()
                if sc_norm not in seen_codes:
                    seen_codes.add(sc_norm)
                    all_courses.append(sc)
                    added_from_sitemap += 1
            if added_from_sitemap > 0:
                logger.info(
                    f"Augmented course catalog with {added_from_sitemap} courses from sitemap.xml."
                )
        except Exception as e:
            logger.debug(f"Failed to merge sitemap courses: {e}")

    logger.info(
        f"Total catalog courses for processing: {len(all_courses)} (target year: {year})."
    )
    return all_courses


def get_course_codes(subject: str, year: int, timeout: float = 12.0):
    """Return a list of course codes for a given subject code and year."""
    # Ensure spaces and & are safely encoded for query string
    safe_subject = str(subject).replace(" ", "%20").replace("&", "%26")
    endpoint = (
        f"?f.Tabs%7Ctype=Degrees+%26+Courses&form=json&f.Year%7Cyear={year}"
        f"&num_ranks=1000&profile=site-search&query=&f.Area+of+study%7CstudyArea={safe_subject}"
        f"&collection=uosa%7Esp-aem-prod&f.Study+type%7CstudyType=Course"
    )

    for attempt in range(3):
        courses = data_fetcher.DataFetcher(
            endpoint,
            timeout=timeout + attempt * 2.0,
        )
        try:
            data = courses.get()
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
                return {"courses": course_codes}
        except Exception as e:
            logger.debug(
                f"Attempt {attempt + 1} for course codes {subject} failed: {e}"
            )

        time.sleep(0.5 + attempt * 0.5)

    status = (
        courses.last_response.status_code
        if "courses" in locals() and courses.last_response
        else "NO_RESPONSE"
    )
    logger.warning(
        f"Error fetching course codes for {subject} after 3 attempts: HTTP {status}"
    )
    return None


def _extract_terms_from_soup(soup: BeautifulSoup | None) -> list[str]:
    """Extract academic terms from course accordion titles and HTML meta tags."""
    terms = []
    if not soup:
        return terms
    for el in soup.select(".cmp-course-accordion__title"):
        txt = el.get_text(strip=True)
        if re.search(
            r"^(Semester|Trimester|Term|Summer|Winter|Spring|Autumn)\b",
            txt,
            re.I,
        ):
            if txt not in terms:
                terms.append(txt)
    for meta_tag in soup.find_all("meta"):
        if (
            meta_tag.get("property") == "term" or meta_tag.get("name") == "term"
        ) and meta_tag.get("content"):
            term_val = meta_tag.get("content").strip()
            if term_val and term_val not in terms:
                terms.append(term_val)
    return terms


def parse_course_document(html: str) -> dict:
    """Extract complete course metadata, syllabus, and class availability from raw HTML in a single pass."""
    if not html or not isinstance(html, str):
        return {}

    soup = BeautifulSoup(html, "html.parser")

    # 1. Title & Meta tags
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""
    meta = {
        m.get("property") or m.get("name"): m.get("content")
        for m in soup.find_all("meta")
    }

    # 2. Overview
    overview_sec = soup.find("section", id="overview")
    overview = (
        re.sub(r"^Course overview\s*", "", overview_sec.get_text("\n", strip=True))
        if overview_sec
        else ""
    )

    # 3. Learning outcomes
    outcomes_sec = soup.find("section", id="outcomes")
    outcomes = (
        [
            li.get_text(strip=True)
            for li in outcomes_sec.find_all("li")
            if li.get_text(strip=True)
        ]
        if outcomes_sec
        else []
    )

    # 4. Requisites, Textbooks, Assessments
    reqs_sec = soup.find("section", id="requisites")
    prereqs, coreqs, antireqs, textbooks = "N/A", "N/A", "N/A", "N/A"
    assessments = []

    if reqs_sec:
        for h2 in reqs_sec.find_all("h2"):
            h_text = h2.get_text(strip=True).lower()
            nxt = h2.find_next_sibling(["div", "p", "ul"])
            txt = nxt.get_text(" ", strip=True) if nxt else "N/A"
            if "prerequisite" in h_text:
                prereqs = txt
            elif "corequisite" in h_text:
                coreqs = txt
            elif "antirequisite" in h_text:
                antireqs = txt

        for h3 in reqs_sec.find_all("h3"):
            h_text = h3.get_text(strip=True).lower()
            nxt = h3.find_next_sibling(["div", "p", "ul"])
            if "textbook" in h_text:
                textbooks = nxt.get_text(" ", strip=True) if nxt else "N/A"
            elif "assessment" in h_text and nxt:
                raw_assessments = (
                    [li.get_text(strip=True) for li in nxt.find_all("li")]
                    if nxt.name == "ul"
                    else [nxt.get_text(strip=True)]
                )
                for item in raw_assessments:
                    if not item or item == "N/A":
                        continue
                    match = re.match(r"^(.*?)\s*[\u2013\u2014\-]\s*(\d+%\s*.*)$", item)
                    if match:
                        assessments.append(
                            {
                                "title": match.group(1).strip(),
                                "weighting": match.group(2).strip(),
                            }
                        )
                    else:
                        assessments.append(
                            {
                                "title": item.strip(),
                                "weighting": None,
                            }
                        )

    # 5. Terms from accordion titles and meta tags
    terms = _extract_terms_from_soup(soup)

    # 6. Live class timetable
    classes = parse_course_class_list(html)

    return {
        "title": title,
        "course_code": meta.get("courseCode"),
        "units": meta.get("unitValue"),
        "date_modified": meta.get("dateModified"),
        "course_overview": overview if overview else None,
        "learning_outcomes": outcomes,
        "prerequisites": prereqs,
        "corequisites": coreqs,
        "antirequisites": antireqs,
        "textbooks": textbooks if textbooks != "N/A" else None,
        "assessments": assessments,
        "terms": terms if terms else None,
        "classes": classes,
        "has_classes": len(classes) > 0,
    }


def get_course_details(course_code: str, year: int | str | None = None):
    """Return the details for a given course with retries."""
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
                if data.get("h1", "").lower() == "page not found":
                    logger.info(
                        f"Course {course_code} is not offered for year {year} (404 Page Not Found)."
                    )
                    return None

                text = data.get("data", "")
                body_text = (
                    BeautifulSoup(text, "html.parser").get_text()
                    if ("<" in text and ">" in text)
                    else text
                )

                # Parse the plain-body text for label/value pairs
                parsed = parse_course_text(body_text)

                # Extract terms and syllabus fields from raw HTML
                raw_html = data.get("html", "")
                doc = parse_course_document(raw_html) if raw_html else {}

                terms = doc.get("terms")
                if not terms and raw_html:
                    html_soup = BeautifulSoup(raw_html, "html.parser")
                    terms = []
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
                    "title": data.get("h1") or doc.get("title", ""),
                    "course_id": parsed.get("course_id"),
                    "campus": parsed.get("campus"),
                    "level_of_study": parsed.get("level_of_study"),
                    "units": parsed.get("units") or doc.get("units"),
                    "course_coordinator": parsed.get("course_coordinator"),
                    "course_level": parsed.get("course_level"),
                    "course_overview": doc.get("course_overview")
                    or parsed.get("course_overview"),
                    "prerequisites": (
                        doc.get("prerequisites")
                        if doc.get("prerequisites")
                        and doc.get("prerequisites") != "N/A"
                        else parsed.get("prerequisites")
                    ),
                    "corequisites": (
                        doc.get("corequisites")
                        if doc.get("corequisites") and doc.get("corequisites") != "N/A"
                        else parsed.get("corequisites")
                    ),
                    "antirequisites": (
                        doc.get("antirequisites")
                        if doc.get("antirequisites")
                        and doc.get("antirequisites") != "N/A"
                        else parsed.get("antirequisites")
                    ),
                    "textbooks": doc.get("textbooks"),
                    "learning_outcomes": doc.get("learning_outcomes", []),
                    "assessments": doc.get("assessments", []),
                    "date_modified": doc.get("date_modified"),
                    "university_wide_elective": (
                        True
                        if parsed.get("university_wide_elective") == "Yes"
                        else False
                        if parsed.get("university_wide_elective") == "No"
                        else parsed.get("university_wide_elective")
                    ),
                    "terms": terms if terms else None,
                    "classes": doc.get("classes", []),
                    "html": raw_html,
                }

                logger.debug("Course details extracted successfully.")
                return course_info

            elif (
                course_details.last_response is not None
                and course_details.last_response.status_code == 404
            ):
                logger.info(
                    f"Course {course_code} is not offered for year {year} (404 Not Found)."
                )
                return None
        except Exception as e:
            logger.debug(
                f"Attempt {attempt + 1} for course details {course_code} failed: {e}"
            )

        time.sleep(0.5 + attempt * 0.5)

    status = (
        course_details.last_response.status_code
        if "course_details" in locals() and course_details.last_response
        else "NO_RESPONSE"
    )
    logger.warning(
        f"Could not retrieve details for course {course_code} after 3 attempts (HTTP {status})."
    )
    return None


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


def is_valid_course_page(
    soup: BeautifulSoup, default_h1: str = "", raw_html: str = ""
) -> bool:
    """Check if the page successfully loaded as a valid course page (not blocked or malformed)."""
    if not soup:
        return False

    if raw_html:
        lower_html = raw_html.lower()
        if (
            len(raw_html) > 500
            and "</html>" not in lower_html
            and "</body>" not in lower_html
        ):
            return False

    h1_el = soup.find("h1")
    h1_text = (h1_el.get_text(strip=True) if h1_el else default_h1).lower()

    # Block pages / WAF / Cloudflare interstitials
    if any(
        blocked in h1_text
        for blocked in [
            "forbidden",
            "attention required",
            "access denied",
            "just a moment",
            "cloudflare",
        ]
    ):
        return False

    title_el = soup.find("title")
    title_text = title_el.get_text(strip=True).lower() if title_el else ""
    if any(
        blocked in title_text
        for blocked in [
            "attention required",
            "cloudflare",
            "access denied",
            "403 forbidden",
        ]
    ):
        return False

    has_course_meta = any(
        meta.get("property") in ("courseCode", "areaCatalogue", "og:title")
        or meta.get("name") in ("courseCode", "areaCatalogue")
        for meta in soup.find_all("meta")
    )
    has_course_structure = bool(
        soup.select_one(".cmp-herobanner")
        or soup.select_one(".courseavailability")
        or soup.select_one(".cmp-course-accordion")
        or soup.select_one(".coursedetail")
        or soup.select_one(".cmp-course-page")
    )

    return bool(h1_text and (has_course_meta or has_course_structure))


def get_course_class_list(
    course_code: int | str,
    year: int | str | None = None,
    raw_html: str | None = None,
):
    """Return the class list of a course for a given course code."""
    if raw_html:
        parsed_classes = parse_course_class_list(raw_html)
        soup = BeautifulSoup(raw_html, "html.parser") if raw_html else None
        return {
            "classes": parsed_classes,
            "is_valid_page": True,
            "html": raw_html,
            "terms": _extract_terms_from_soup(soup),
        }

    # Encode course code to match URL format
    code_str = course_code[0] if isinstance(course_code, (list, tuple)) else course_code
    encoded_course_code = re.sub(
        r"([a-zA-Z]+)([0-9]+)", r"\1-\2", str(code_str)
    ).lower()

    endpoint = (
        f"/study/courses/{year}/{encoded_course_code}/"
        if year
        else f"/study/courses/{encoded_course_code}/"
    )

    last_status = "NO_RESPONSE"
    for attempt in range(4):
        course_details = data_fetcher.DataFetcher(endpoint, use_class_url=True)
        try:
            data = course_details.get()
            last_status = (
                course_details.last_response.status_code
                if course_details.last_response
                else "NO_RESPONSE"
            )
            if (
                course_details.last_response is None
                or course_details.last_response.status_code != 200
                or not data
                or (
                    isinstance(data, dict)
                    and data.get("h1", "").lower() == "page not found"
                )
            ):
                if last_status == 404 or (
                    isinstance(data, dict)
                    and data.get("h1", "").lower() == "page not found"
                ):
                    # If versioned course URL is 404, check canonical unversioned URL before giving up
                    if year and attempt == 0:
                        unversioned_endpoint = f"/study/courses/{encoded_course_code}/"
                        try:
                            u_fetcher = data_fetcher.DataFetcher(
                                unversioned_endpoint, use_class_url=True
                            )
                            u_data = u_fetcher.get()
                            if u_data and isinstance(u_data, dict):
                                u_html = u_data.get("html", "") or u_data.get(
                                    "data", ""
                                )
                                u_classes = parse_course_class_list(u_html)
                                if u_classes:
                                    u_soup = BeautifulSoup(u_html, "html.parser")
                                    return {
                                        "code": code_str,
                                        "title": u_data.get("h1") or "",
                                        "course_id": None,
                                        "classes": u_classes,
                                        "is_valid_page": True,
                                        "html": u_html,
                                        "terms": _extract_terms_from_soup(u_soup),
                                    }
                        except Exception as e:
                            logger.debug(
                                f"Canonical unversioned fallback for {code_str} failed: {e}"
                            )

                    # Course genuinely not available; no point retrying
                    return {
                        "code": code_str,
                        "title": data.get("h1", "") if isinstance(data, dict) else "",
                        "course_id": None,
                        "classes": [],
                        "is_valid_page": True,
                        "html": "",
                        "terms": [],
                    }
                # Transient failure — retry
                logger.debug(
                    f"Class list fetch attempt {attempt + 1}/4 for {course_code} returned "
                    f"HTTP {last_status}. Retrying..."
                )
                time.sleep(0.5 + attempt * 0.5)
                continue

            # Use raw HTML for BeautifulSoup parsing
            text = data.get("html", "") or data.get("data", "")
            soup = BeautifulSoup(text, "html.parser") if text else None
            page_terms = _extract_terms_from_soup(soup)

            # 1. Parse the plain-body text for class list details
            parsed_classes = parse_course_class_list(text)
            if parsed_classes:
                return {
                    "classes": parsed_classes,
                    "is_valid_page": True,
                    "html": text,
                    "terms": page_terms,
                }

            # Check if page has indicators of timetable availability
            has_timetable_marker = False
            has_term_meta = False
            if soup:
                has_timetable_marker = bool(
                    soup.select_one(".cmp-course-accordion__class-details")
                    or soup.select_one(".cmp-course-accordion--container-session")
                    or (
                        soup.select_one(".courseavailability")
                        and any(
                            kw
                            in soup.select_one(".courseavailability").get_text().lower()
                            for kw in [
                                "availability",
                                "semester",
                                "trimester",
                                "term",
                                "enrol by",
                            ]
                        )
                    )
                )
                has_term_meta = any(
                    (meta.get("property") == "term" or meta.get("name") == "term")
                    and bool(meta.get("content"))
                    for meta in soup.find_all("meta")
                )

            # If timetable or term markers exist on the page but parsed_classes is empty,
            # this indicates a corrupted/partial render or edge glitch -> retry!
            if (has_timetable_marker or has_term_meta) and attempt < 3:
                logger.debug(
                    f"Class list page for {course_code} had timetable/term markers but parsed 0 classes "
                    f"(attempt {attempt + 1}/4). Retrying with alternate connection..."
                )
                time.sleep(0.5 + attempt * 0.5)
                continue

            # 2. Check if the page successfully loaded as a valid course page (not blocked or malformed)
            default_h1 = data.get("h1", "") if isinstance(data, dict) else ""
            valid_page = (
                is_valid_course_page(soup, default_h1=default_h1, raw_html=text)
                if soup
                else False
            )
            if valid_page and not has_timetable_marker:
                # Valid course page, and course genuinely has no scheduled classes for this year.
                # Accept immediately and do NOT retry across proxies!
                logger.debug(
                    f"Course {course_code} (year {year}) has no scheduled classes. Proceeding without retry."
                )
                return {
                    "code": code_str,
                    "title": default_h1,
                    "course_id": None,
                    "classes": [],
                    "is_valid_page": True,
                    "html": text,
                    "terms": page_terms,
                }

            # 3. Got a 200 but page is broken, malformed, or WAF-blocked — retry with alternate proxy
            if attempt < 3:
                logger.debug(
                    f"Class list page for {course_code} was malformed or blocked "
                    f"(attempt {attempt + 1}/4). Retrying with alternate proxy..."
                )
                time.sleep(0.5 + attempt * 0.5)
                continue

            return {
                "code": code_str,
                "title": "",
                "course_id": None,
                "classes": [],
                "is_valid_page": False,
                "html": "",
                "terms": [],
            }

        except Exception as e:
            logger.debug(
                f"Attempt {attempt + 1}/4 for class list {course_code} failed: {e}"
            )
            time.sleep(0.5 + attempt * 0.5)

    logger.warning(
        f"Could not retrieve class list for {course_code} after 4 attempts (HTTP {last_status})."
    )
    return {
        "code": code_str,
        "title": "",
        "course_id": None,
        "classes": [],
        "is_valid_page": False,
        "html": "",
        "terms": [],
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
