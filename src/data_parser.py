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
        if subjects.last_response.status_code != 200 or data is None:
            print(f"Error: {subjects.last_response.status_code} - {data}")
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
        f"?f.Tabs%7Ctype=Degrees+%26+Courses&form=json&f.Year%7Cyear={year}&num_ranks=100&profile=site-search&query=&f.Area+of+study%7CstudyArea={subject}&collection=uosa%7Esp-aem-prod&f.Study+type%7CstudyType=Course"
    )

    try:
        data = courses.get()
        logger.debug(f"Course data: {data}")
        if courses.last_response.status_code != 200 or data is None:
            print(f"Error: {courses.last_response.status_code} - {data}")
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
        if course_details.last_response.status_code != 200:
            print(
                f"Error: {course_details.last_response.status_code} - "
                f"{course_details.last_response.text}"
            )
            return {}
        # Return plain text string without extra newlines
        text = data.get("data", "")

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

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    parsed_classes = []
    current_class = {}
    i = 0

    def _is_date_line(s: str) -> bool:
        # matches things like '3 Aug - 21 Sep' or '12 Oct - 9 Nov'
        return bool(re.search(r"^\d{1,2} [A-Za-z]+\s*-\s*\d{1,2} [A-Za-z]+$", s))

    while i < len(lines):
        line = lines[i]

        # Look for "Availability" and skip until "Class details" – content prior to class details is not needed
        if "Availability" in line:
            while i < len(lines) and not lines[i].startswith("Class details"):
                i += 1
            continue

        # Start parsing class details: initialise a new current_class dict and accept optional campus/enrolment header
        if line.startswith("Class details"):
            if current_class:
                parsed_classes.append(current_class)
            current_class = {"meetings": []}
            i += 1
            # Optional campus header (e.g., 'Adelaide City Campus West') and component (Enrolment/Related)
            # Sometimes the campus appears before the component and sometimes after; handle both orders.
            if i < len(lines):
                # If next line is the component, capture it first
                if lines[i].lower().startswith("enrolment class") or lines[
                    i
                ].lower().startswith("related class"):
                    parts = lines[i].split(":", 1)
                    current_class["component"] = (
                        parts[1].strip() if len(parts) > 1 else parts[0].strip()
                    )
                    i += 1
                    # If campus follows, capture it
                    if i < len(lines) and "Campus" in lines[i]:
                        current_class["campus"] = lines[i]
                        i += 1
                else:
                    # If next line is campus, capture it first
                    if "Campus" in lines[i]:
                        current_class["campus"] = lines[i]
                        i += 1
                        # Try to capture a component immediately after campus
                        if i < len(lines) and (
                            lines[i].lower().startswith("enrolment class")
                            or lines[i].lower().startswith("related class")
                        ):
                            parts = lines[i].split(":", 1)
                            current_class["component"] = (
                                parts[1].strip() if len(parts) > 1 else parts[0].strip()
                            )
                            i += 1
                    else:
                        # Neither campus nor component on the next line; try to look ahead one line for component
                        if i + 1 < len(lines) and (
                            lines[i + 1].lower().startswith("enrolment class")
                            or lines[i + 1].lower().startswith("related class")
                        ):
                            parts = lines[i + 1].split(":", 1)
                            current_class["component"] = (
                                parts[1].strip() if len(parts) > 1 else parts[0].strip()
                            )
                            i += 2
            continue

        # Parse other class attributes
        if line.startswith("Class number"):
            # If we already have a class with a class_number, start a new class and append the old one
            if current_class and current_class.get("class_number"):
                parsed_classes.append(current_class)
                # Carry over campus/header values
                current_class = {
                    "meetings": [],
                    "campus": current_class.get("campus"),
                    "component": current_class.get("component"),
                }
            elif not current_class:
                current_class = {"meetings": []}
            current_class["class_number"] = line.split("Class number")[-1].strip()
            i += 1
            continue
        if line.startswith("Section"):
            current_class["section"] = line.split("Section")[-1].strip()
            i += 1
            continue
        if line.startswith("Size"):
            current_class["size"] = line.split("Size")[-1].strip()
            i += 1
            continue
        if line.startswith("Available"):
            if line.split("Available")[-1].strip().isdigit():
                current_class["available"] = line.split("Available")[-1].strip()
            i += 1
            continue

        # Detect meeting table header
        headers = ["Dates", "Days", "Time", "Campus", "Location", "Instructor"]
        if all(
            i + j < len(lines) and lines[i + j] == headers[j]
            for j in range(len(headers))
        ):
            # Skip header row
            i += len(headers)
            # Read meeting rows until next class starts
            while (
                i < len(lines)
                and not lines[i].startswith("Class number")
                and not lines[i].startswith("Class details")
            ):
                # Need at least a date, day, time, campus, location, instructor to be a valid row
                if not _is_date_line(lines[i]):
                    break
                # Attempt to parse row segments
                dates_val = lines[i]
                days_val = lines[i + 1] if i + 1 < len(lines) else ""
                time_val = lines[i + 2] if i + 2 < len(lines) else ""
                campus_val = lines[i + 3] if i + 3 < len(lines) else ""
                location_val = lines[i + 4] if i + 4 < len(lines) else ""
                next_i = i + 5

                meeting = {
                    "dates": dates_val,
                    "days": days_val,
                    "time": time_val,
                    "campus": campus_val,
                    "location": location_val,
                }
                current_class.setdefault("meetings", []).append(meeting)
                i = next_i
            continue

        # Fallback: advance
        i += 1

    # Append the last class if it contains a class number
    if current_class and current_class.get("class_number"):
        parsed_classes.append(current_class)
    return parsed_classes
