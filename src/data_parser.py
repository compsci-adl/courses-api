import re

from bs4 import BeautifulSoup

import data_fetcher
from log import logger


def get_subjects(year: int) -> dict[str, list[dict[str, str]]]:
    """Return a list of subjects for a given year."""
    subjects = data_fetcher.DataFetcher(
        f"?f.Tabs|type=Degrees+%26+Courses&form=json&num_ranks=10&profile=site-search&query=&f.Year|year={year}&collection=uosa~sp-aem-prod&f.Study+type|studyType=Course&start_rank=1"
    )

    try:
        data = subjects.get()
        if (
            subjects.last_response is None
            or subjects.last_response.status_code != 200
            or data is None
        ):
            status = (
                subjects.last_response.status_code
                if subjects.last_response
                else "NO_RESPONSE"
            )
            print(f"Error: {status} - {data}")
            return {"subjects": []}

        subject_list = []

        data = data.get("facets")[5].get("allValues", [])
        for subject in data:
            subj = subject.get("data")
            subject_list.append({"subject": subj})
        logger.debug(f"Subjects: {subject_list}")
        return {"subjects": subject_list}

    except Exception as e:
        print(f"An error occurred while fetching subjects: {e}")
        return {"subjects": []}


def get_course_codes(subject: str, year: int):
    """Return a list of course codes for a given subject code and year."""
    courses = data_fetcher.DataFetcher(
        f"?f.Tabs%7Ctype=Degrees+%26+Courses&form=json&f.Year%7Cyear={year}&num_ranks=1000&profile=site-search&query=&f.Area+of+study%7CstudyArea={subject}&collection=uosa%7Esp-aem-prod&f.Study+type%7CstudyType=Course"
    )

    try:
        data = courses.get()
        logger.debug(f"Course data: {data}")
        if (
            courses.last_response is None
            or courses.last_response.status_code != 200
            or data is None
        ):
            status = (
                courses.last_response.status_code
                if courses.last_response
                else "NO_RESPONSE"
            )
            print(f"Error: {status} - {data}")
            return {"courses": []}
        results = data.get("resultPacket", []).get("results", [])
        logger.debug(f"Number of courses found: {len(results)}")

        if not results:
            logger.debug("No results found in course codes.")
            return {}

        course_codes = [
            {
                "code": course.get("listMetadata", {}).get("courseCode"),
                "terms": course.get("listMetadata", {}).get("term"),
            }
            for course in results
        ]
        logger.debug("Course codes extracted successfully.")
        return {"courses": course_codes}

    except Exception as e:
        print(f"An error occurred while fetching course codes: {e}")
        return {"courses": []}


def get_course_details(course_code: str, max_retries=3):
    """Return the details for a given course."""
    logger.debug(f"Fetching details for course {course_code}")
    for _ in range(max_retries):
        # Encode course code to match URL format
        code_str = (
            course_code[0] if isinstance(course_code, (list, tuple)) else course_code
        )
        encoded_course_code = re.sub(
            r"([a-zA-Z]+)([0-9]+)", r"\1-\2", str(code_str)
        ).lower()

        course_details = data_fetcher.DataFetcher(
            f"/study/courses/{encoded_course_code}/", use_class_url=True
        )
        try:
            data = course_details.get()
            if course_details.last_response is None:
                logger.error(
                    f"No HTTP response available for {course_code}. Data: {data}"
                )
                # Make sure to return a dictionary with expected keys so caller won't crash
                return {
                    "code": code_str,
                    "title": data.get("h1", "") if isinstance(data, dict) else "",
                    "course_id": None,
                }
            if course_details.last_response.status_code != 200:
                print(
                    f"Error: {course_details.last_response.status_code} - "
                    f"{course_details.last_response.text}"
                )
                return {}
            # Return plain text string without extra newlines
            text = data.get("data", "")

            # Strip HTML tags
            soup = BeautifulSoup(text, "html.parser")
            body_text = soup.get_text() if soup else text

            # Parse the plain-body text for label/value pairs
            parsed = parse_course_text(body_text)

            # Return a dict with the parsed fields and the canonical code string
            course_details = {
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
            }

            logger.debug("Course details extracted successfully.")
            return course_details

        except Exception as e:
            print(f"An error occurred while fetching course details: {e}")
            return {}

    print(
        f"Failed to retrieve course details for course {course_code} after {max_retries} attempts."
    )
    return {}


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


def get_course_class_list(course_code: int):
    """Return the class list of a course for a given course code."""

    # Encode course code to match URL format
    code_str = course_code[0] if isinstance(course_code, (list, tuple)) else course_code
    encoded_course_code = re.sub(
        r"([a-zA-Z]+)([0-9]+)", r"\1-\2", str(code_str)
    ).lower()

    course_details = data_fetcher.DataFetcher(
        f"/study/courses/{encoded_course_code}/", use_class_url=True
    )

    try:
        data = course_details.get()
        if (
            course_details.last_response is None
            or course_details.last_response.status_code != 200
        ):
            status = (
                course_details.last_response.status_code
                if course_details.last_response
                else "NO_RESPONSE"
            )
            text = (
                course_details.last_response.text
                if course_details.last_response
                else ""
            )
            print(f"Error: {status} - {text}")
            # Return a minimal dict so callers don't KeyError when accessing title/course_id
            return {
                "code": code_str,
                "title": data.get("h1", "") if isinstance(data, dict) else "",
                "course_id": None,
            }
        # Use raw HTML for BeautifulSoup parsing
        text = data.get("html", "") or data.get("data", "")

        # Parse the plain-body text for class list details
        parsed_classes = parse_course_class_list(text)
        return {"classes": parsed_classes}

    except Exception as e:
        print(f"An error occurred while fetching course class list: {e}")
        return {}


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
            "class_number": None,
            "section": None,
            "size": None,
            "available": None,
        }

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
            lambda tag: tag.name in ["h2", "h3", "h4"]
            and header_text.lower() in tag.get_text().lower()
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
        lambda tag: tag.name in ["h2", "h3"]
        and "Assessment Descriptions" in tag.get_text()
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
