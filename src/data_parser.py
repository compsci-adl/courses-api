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
        f"?f.Tabs%7Ctype=Degrees+%26+Courses&form=json&f.Year%7Cyear={year}&num_ranks=100&profile=site-search&query=&f.Area+of+study%7CstudyArea={subject}&collection=uosa%7Esp-aem-prod&f.Study+type%7CstudyType=Course"
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
    current_class = None
    # Current component context for classes within a "Class details" block.
    current_component = "unknown"
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

        # Start parsing class details: reset the context for a new set of classes and components
        if line.startswith("Class details"):
            # Commit any prior orphaned class (if it has a class number)
            if current_class and current_class.get("class_number"):
                parsed_classes.append(current_class)
            current_class = None
            # Reset the current component and attempt a lookahead for the first component
            current_component = "unknown"
            lookahead = 1
            max_look = 24
            while i + lookahead < len(lines) and lookahead <= max_look:
                candidate = lines[i + lookahead].strip()
                if candidate.lower().startswith(
                    "enrolment class"
                ) or candidate.lower().startswith("related class"):
                    parts = candidate.split(":", 1)
                    if len(parts) > 1 and parts[1].strip():
                        current_component = parts[0].strip() + ": " + parts[1].strip()
                    else:
                        # If value is next line and not a class number, use it
                        next_val = (
                            lines[i + lookahead + 1].strip()
                            if i + lookahead + 1 < len(lines)
                            else ""
                        )
                        if next_val and not next_val.lower().startswith("class number"):
                            current_component = parts[0].strip() + ": " + next_val
                    break
                # If the block restarts, stop searching
                if candidate.startswith("Class details"):
                    break
                lookahead += 1
            i += 1
            continue

        # Parse other class attributes
        # Detect inline component labels anywhere in the Class details block
        if line.lower().startswith("enrolment class") or line.lower().startswith(
            "related class"
        ):
            parts = line.split(":", 1)
            if len(parts) > 1 and parts[1].strip():
                current_component = parts[0].strip() + ": " + parts[1].strip()
            else:
                next_val = lines[i + 1].strip() if i + 1 < len(lines) else ""
                if next_val and not next_val.lower().startswith("class number"):
                    current_component = parts[0].strip() + ": " + next_val
                    i += 1
            if current_class and not current_class.get("class_number"):
                current_class["component"] = current_component
            i += 1
            continue

        if line.startswith("Class number"):
            # If we already have a class with a class_number, start a new class and append the old one
            if current_class and current_class.get("class_number"):
                parsed_classes.append(current_class)
                # Carry over campus/header values
                current_class = {
                    "meetings": [],
                    "campus": current_class.get("campus"),
                    "component": current_component or current_class.get("component"),
                }
            elif not current_class:
                current_class = {"meetings": []}
            current_class["class_number"] = line.split("Class number")[-1].strip()
            # Ensure the class has a component set (inherit from block context)
            if "component" not in current_class or not current_class.get("component"):
                current_class["component"] = current_component or "unknown"
            i += 1
            continue

        if line.startswith("Section"):
            if not current_class:
                current_class = {"meetings": []}
                current_class["component"] = current_component or "unknown"
            current_class["section"] = line.split("Section")[-1].strip()
            i += 1
            continue
        if line.startswith("Size"):
            if not current_class:
                current_class = {"meetings": []}
                current_class["component"] = current_component or "unknown"
            current_class["size"] = line.split("Size")[-1].strip()
            i += 1
            continue
        if line.startswith("Available"):
            if not current_class:
                current_class = {"meetings": []}
                current_class["component"] = current_component or "unknown"
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
                if not current_class:
                    current_class = {"meetings": []}
                    current_class["component"] = current_component or "unknown"
                current_class.setdefault("meetings", []).append(meeting)
                i = next_i
            continue

        # Fallback: advance
        i += 1

    # Append the last class if it contains a class number
    if current_class and current_class.get("class_number"):
        parsed_classes.append(current_class)
    return parsed_classes
