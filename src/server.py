from typing import Union, List
from fastapi import FastAPI, HTTPException
from tinydb import TinyDB, Query
import re
from datetime import datetime

app = FastAPI()
db = TinyDB('src/db.json')
Course = Query()

def current_year() -> int:
    """Gets the current year."""
    return datetime.now().year


def current_sem() -> str:
    """Gets the current semester."""
    return "Semester 1" if datetime.now().month <= 6 else "Semester 2"


def get_term_number(year: int, term: str) -> int:
    """Gets the term number from the local database."""
    course_details_results = db.search(Course.year == year)

    if not course_details_results:
        raise Exception(f"No courses found for year: {year}")

    for course_details in course_details_results:
        details = course_details.get("details", [])

        for detail in details:
            if detail.get("TERM_DESCR") == term:
                return detail.get("TERM", None)

    raise Exception(f"Invalid term: {term} for year: {year}")



def meeting_date_convert(raw_date: str) -> dict[str]:
    """Converts the date format given in the meetings to "MM-DD"
    Args:
        raw_date (str): The given meeting date in the format of "DD {3-char weekday}
        - DD {3-char weekday}"
    Returns:
        formatted_date (dict[str]): The formatted meeting date in the format of "MM-DD"
    """
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    start, end = raw_date.split(" - ")

    start_d, start_m = start.split()
    start_m = str(months.index(start_m) + 1).zfill(2)

    end_d, end_m = end.split()
    end_m = str(months.index(end_m) + 1).zfill(2)

    formatted_date = {
        "start": f"{start_m}-{start_d.zfill(2)}",
        "end": f"{end_m}-{end_d.zfill(2)}"
    }
    return formatted_date


def meeting_time_convert(raw_time: str) -> str:
    """Converts the time given in meetings to "HH:mm"
    Args:
        raw_time (str): The given meeting time in the format of "H{am/pm}"
    Returns:
        formatted_time (str): The formatted meeting time in the format of "HH:mm"
    """
    period = raw_time[-2:]
    hour = int(raw_time.replace(period, "").strip())

    if period.lower() == "pm" and hour != 12:
        hour += 12
    elif period.lower() == "am" and hour == 12:
        hour = 0

    formatted_time = f"{str(hour).zfill(2)}:00"
    return formatted_time


def parse_requisites(raw_requisites: str) -> Union[list[str], None]:
    """Takes in a string of -requisites and returns a list of the parsed-out subjects
    Args:
        raw_requisites (str): The raw string containing a list of -requisites, usually
        in the format of "COMP SCI 1103, COMP SCI 2202, COMP SCI 2202B" as an example
    Returns:
        parsed_requisites (Union[list[str], None]): A list of the parsed -requisites,
        or None if raw_requisites is None
    """

    if not raw_requisites:
        return None

    # Regex pattern to match subjects and course numbers
    pattern = r'\b([A-Z]+(?:\s+[A-Z]+)*)\s+(\d{4}\w*)\b'
    matched_subjects = [" ".join(match)
                        for match in re.findall(pattern, raw_requisites)]

    return matched_subjects if matched_subjects else None

@app.get("/courses/", response_model=Union[dict, list])
def list_courses(year: int = current_year(), term: str = current_sem()):
    """Gets a list of courses given (optionally) the year and term
    Args:
        year (int, optional): The year of the courses from 2006 to
        the current year. Defaults to None.
        term (str, optional): The term of the courses. Defaults to None.

    Returns:
        list[dict]: A list of courses as dictionaries
    """
    term_number = get_term_number(year, term)
    results = db.search((Course.year == year) & (Course.term == term_number))

    if not results:
        raise HTTPException(
            status_code=404, detail="No courses found for the specified year and term")

    transformed_courses = {
        "courses": []
    }

    # Extract necessary information from the results
    for entry in results:
        nano_id = entry.get("id", "")
        details = entry.get("details", [])

        for detail in details:
            subject = detail.get("SUBJECT", "")
            code = detail.get("CATALOG_NBR", "")
            title = detail.get("COURSE_TITLE", "")

            transformed_courses["courses"].append({
                "name": {
                    "subject": subject,
                    "code": code,
                    "title": title
                },
                "id": nano_id
            })

    return transformed_courses


@app.get("/courses/{course_id}", response_model=Union[dict, list])
def get_course(course_id: str, year: int = current_year(), term: str = current_sem()):
    """Course details route, takes in a course ID (and optionally a year and term) and returns the courses' info and classes
    Args:
        year (int, optional): The year the course takes place in. Defaults to None.
        term (str, optional): The term the course takes place in. Defaults to None.
    Returns:
        dict: A dictionary containing the course information and classes
    """
    # Search for the course details using course_id, year, and term
    term_number = get_term_number(year, term)
    course_details_result = db.search((Course.course_id == course_id) &
                                      (Course.year == year) &
                                      (Course.term == term_number))

    if not course_details_result:
        raise HTTPException(status_code=404, detail="Course not found")

    course_details = course_details_result[0]
    class_details = course_details_result[1]
    details = course_details.get("details", [])
    classes = class_details.get("class_list", [])

    # Extract necessary information from details
    if details:
        detail = details[0]
        name = {
            "subject": detail.get("SUBJECT", ""),
            "code": detail.get("CATALOG_NBR", ""),
            "title": detail.get("COURSE_TITLE", ""),
        }
        requirement = {
            "restriction": parse_requisites(detail.get("RESTRICTION_TXT", "")),
            "prerequisite": parse_requisites(detail.get("PRE_REQUISITE", "")),
            "corequisite": parse_requisites(detail.get("CO_REQUISITE", "")),
            "assumed_knowledge": parse_requisites(detail.get("ASSUMED_KNOWLEDGE", "")),
            "incompatible": parse_requisites(detail.get("INCOMPATIBLE", ""))
        }
    else:
        name = {"subject": "", "code": "", "title": ""}
        requirement = {}

    # Construct the response
    response = {
        "id": course_details.get("id", ""),
        "course_id": course_id,
        "name": name,
        "class_number": detail.get("CLASS_NBR", ""),
        "year": year,
        "term": detail.get("TERM_DESCR", ""),
        "campus": detail.get("CAMPUS", ""),
        "units": detail.get("UNITS", 0),
        "requirement": requirement,
        "class_list": []
    }

    # Process the classes to match the required structure
    for class_group in classes:
        for group in class_group.get("groups", []):
            class_list_entry = {
                "type": group["type"],
                "id": group["id"],
                "classes": []
            }
            for class_info in group.get("classes", []):
                class_entry = {
                    "number": class_info["class_nbr"],
                    "meetings": []
                }
                for meeting in class_info.get("meetings", []):
                    meeting_entry = {
                        "day": meeting.get("days", ""),
                        "location": meeting.get("location", ""),
                        "date": meeting_date_convert(meeting.get("dates", "")),
                        "time": {
                            "start": meeting_time_convert(meeting.get("start_time", "")),
                            "end": meeting_time_convert(meeting.get("end_time", ""))
                        }
                    }
                    class_entry["meetings"].append(meeting_entry)

                class_list_entry["classes"].append(class_entry)

            response["class_list"].append(class_list_entry)

    return response
