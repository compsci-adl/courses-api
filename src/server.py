import re
from datetime import datetime
from typing import Union

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from tinydb import Query, TinyDB

from .schemas import CourseSchema

app = FastAPI()
db = TinyDB("src/db.json")
Course = Query()

# Configure CORS for local development and production
origins = [
    "http://localhost:5173",
    "http://localhost:8000",
    "https://mytimetable.csclub.org.au",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def current_year() -> int:
    """Gets the current year."""
    return datetime.now().year


def current_sem() -> str:
    """Gets the current semester."""
    return "Semester 1" if datetime.now().month <= 6 else "Semester 2"


def get_term_number(year: int, term: str) -> int:
    """Gets the term number from the local database."""

    # Convert aliases
    term = convert_term_alias(term)

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
    months = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    start, end = raw_date.split(" - ")

    start_d, start_m = start.split()
    start_m = str(months.index(start_m) + 1).zfill(2)

    end_d, end_m = end.split()
    end_m = str(months.index(end_m) + 1).zfill(2)

    formatted_date = {
        "start": f"{start_m}-{start_d.zfill(2)}",
        "end": f"{end_m}-{end_d.zfill(2)}",
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
    pattern = r"\b([A-Z]+(?:\s+[A-Z]+)*)\s+(\d{4}\w*)\b"
    matched_subjects = [
        " ".join(match) for match in re.findall(pattern, raw_requisites)
    ]

    return matched_subjects if matched_subjects else None


def convert_term_alias(term_alias: str) -> str:
    """Takes in a term alias and returns the CoursePlanner API name for said term
    Args:
        term_alias (str): The unconverted term, this doesn't have to be an alias
        in which case no conversion will be done
    Returns:
        str: The converted or original term depending on if a conversion was made
    """

    terms_without_digits = ("summer", "winter")
    aliases = {
        "sem": "Semester",
        "elc": "ELC Term",
        "tri": "Trimester",
        "term": "Term",
        "ol": "Online Teaching Period",
        "melb": "Melb Teaching Period",
        "pce": "PCE Term",
        "summer": "Summer School",
        "winter": "Winter School",
    }

    # Convert the alias, append its digit to the end if the term needs a digit at the end
    converted_alias = aliases.get(
        term_alias[:-1] if term_alias[-1].isdigit() else term_alias, term_alias
    )
    if (
        term_alias not in terms_without_digits
        and term_alias[-1].isdigit()
        and converted_alias != term_alias
    ):
        converted_alias += " " + term_alias[-1]

    return converted_alias


@app.get("/subjects", response_model=Union[dict, list])
def get_subjects(year: int = current_year(), term: str = current_sem()):
    """Get all possible subjects for a given year and term, sorted alphabetically.

    Args:
        year (int, optional): The year to search for courses. Defaults to current year.
        term (str, optional): The term to search for courses. Defaults to current semester.

    Returns:
        dict: A dictionary containing a list of subjects.
    """
    term_number = get_term_number(year, term)

    results = db.search((Course.year == year) & (Course.term == term_number))

    if not results:
        raise HTTPException(
            status_code=404, detail="No courses found for the specified year and term"
        )

    # Extract unique subject codes from the results
    subject_info = db.all()[0]
    subjects = subject_info.get("subjects", [])
    print(subject_info)

    unique_codes = set()

    transformed_subjects = {"subjects": []}

    # Collect unique subject codes from course results
    for entry in results:
        details = entry.get("details", [])
        for detail in details:
            code = detail.get("SUBJECT", "")
            if code:  # Skip empty codes
                unique_codes.add(code)

    # Add subject descriptions for each unique code
    for code in unique_codes:
        for subject in subjects:
            if subject.get("SUBJECT") == code:
                transformed_subjects["subjects"].append(
                    {"code": code, "name": subject.get("DESCR")}
                )
                break

    # Sort the subjects alphabetically by the code
    transformed_subjects["subjects"].sort(key=lambda x: x["code"])

    return transformed_subjects


@app.get("/courses", response_model=Union[dict, list])
def get_subject_courses(
    subject: str, year: int = current_year(), term: str = current_sem()
):
    """Gets a list of courses given a subject (and optionally a year and term).

    Args:
        subject (str, required): The subject code to search for.
        year (int, optional): The year of the courses from 2006 to
        the current year. Defaults to current year.
        term (str, optional): The term of the courses. Defaults to current semester.

    Returns:
        list[dict]: A list of courses as dictionaries.
    """
    term_number = get_term_number(year, term)
    results = db.search(
        (Course.details.any(Query().SUBJECT == subject))
        & (Course.year == year)
        & (Course.term == term_number)
    )

    if not results:
        raise HTTPException(
            status_code=404, detail="No courses found for the specified year and term"
        )

    transformed_courses = {"courses": []}

    # Extract necessary information from the results
    for entry in results:
        nano_id = entry.get("id", "")
        details = entry.get("details", [])

        for detail in details:
            subject = detail.get("SUBJECT", "")
            code = detail.get("CATALOG_NBR", "")
            title = detail.get("COURSE_TITLE", "")

            transformed_courses["courses"].append(
                {
                    "id": nano_id,
                    "name": {"subject": subject, "code": code, "title": title},
                }
            )

    return transformed_courses


def split_class_type_category(original_type: str):
    CATEGORIES = {"enrolment", "related"}
    [full_category, class_type] = original_type.split(": ")
    class_category = "unknown"
    for category in CATEGORIES:
        if category in full_category.lower():
            class_category = category
            break
    return {"category": class_category, "type": class_type}


@app.get("/courses/{id}", response_model=Union[dict, list])
def get_course(id: str):
    """Course details route, takes in an id returns the courses' info and classes.

    Args:
        id (string, required): The nano id to search for.

    Returns:
        dict: A dictionary containing the course information and classes.
    """

    results = db.search((Course.id == id))

    if not results:
        raise HTTPException(status_code=404, detail="Course not found")

    course_details = results[0]
    details = course_details.get("details", [])

    # Extract necessary information from details
    if details:
        detail = details[0]
        name = {
            "subject": detail.get("SUBJECT", ""),
            "code": detail.get("CATALOG_NBR", ""),
            "title": detail.get("COURSE_TITLE", ""),
        }
        requirements = {
            "restriction": parse_requisites(detail.get("RESTRICTION_TXT", "")),
            "prerequisite": parse_requisites(detail.get("PRE_REQUISITE", "")),
            "corequisite": parse_requisites(detail.get("CO_REQUISITE", "")),
            "assumed_knowledge": parse_requisites(detail.get("ASSUMED_KNOWLEDGE", "")),
            "incompatible": parse_requisites(detail.get("INCOMPATIBLE", "")),
        }
    else:
        name = {"subject": "", "code": "", "title": ""}
        requirements = {}

    course_id = course_details.get("course_id", "")
    year = course_details.get("year", "")
    term = course_details.get("term", "")

    # Construct the response
    response = {
        "id": id,
        "course_id": course_id,
        "name": name,
        "class_number": detail.get("CLASS_NBR", ""),
        "year": year,
        "term": detail.get("TERM_DESCR", ""),
        "campus": detail.get("CAMPUS", ""),
        "units": detail.get("UNITS", 0),
        "requirements": requirements,
        "class_list": [],
    }

    # Fetch classes info and process to match the required structure
    classes = db.search(
        (Course.course_id == course_id) & (Course.year == year) & (Course.term == term)
    )
    if classes:
        class_details = classes[1]
        class_list = class_details.get("class_list", [])
        for class_group in class_list:
            for group in class_group.get("groups", []):
                class_list_entry = {
                    **split_class_type_category(group["type"]),
                    "id": group["id"],
                    "classes": [],
                }
                for class_info in group.get("classes", []):
                    class_entry = {
                        "number": str(class_info["class_nbr"]),
                        "meetings": [],
                    }
                    for meeting in class_info.get("meetings", []):
                        meeting_entry = {
                            "day": meeting.get("days", ""),
                            "location": meeting.get("location", ""),
                            "date": meeting_date_convert(meeting.get("dates", "")),
                            "time": {
                                "start": meeting_time_convert(
                                    meeting.get("start_time", "")
                                ),
                                "end": meeting_time_convert(
                                    meeting.get("end_time", "")
                                ),
                            },
                        }
                        class_entry["meetings"].append(meeting_entry)

                    class_list_entry["classes"].append(class_entry)

                response["class_list"].append(class_list_entry)

    try:
        CourseSchema.model_validate(response)
        return response
    except ValidationError as e:
        raise HTTPException(status_code=501, detail=e.errors())

    # return response
