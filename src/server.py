import re
import sys
from datetime import datetime
from typing import Dict, List, Optional, Union

from dotenv import dotenv_values
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, Course, CourseClass, Subject
from .schemas import CourseSchema

# Check if the application is running in development mode
is_dev_mode = "dev" in sys.argv

# Configure FastAPI based on the mode
app = FastAPI(
    docs_url="/docs" if is_dev_mode else None,
    redoc_url="/redoc" if is_dev_mode else None,
)

# Determine the database type
DB_TYPE = dotenv_values().get("DB_TYPE")


if DB_TYPE == "dev":
    # Use dev db
    DATABASE_URL = "sqlite:///src/dev.sqlite3"
    engine = create_engine(DATABASE_URL)
else:
    # Use completed courses db
    DATABASE_URL = "sqlite:///src/local.sqlite3"
    engine = create_engine(DATABASE_URL)

print("DB_TYPE:", DB_TYPE)
print("DATABASE_URL:", DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)

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


def get_db():
    """Get a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_year() -> int:
    """Gets the current year."""
    year_str = dotenv_values().get("YEAR")
    if year_str is None:
        return datetime.now().year
    return int(year_str)


def current_sem() -> str:
    """Gets the current semester."""
    return "Semester 1" if datetime.now().month <= 6 else "Semester 2"


def get_term_number(db, year: int, term: str) -> str:
    """Gets the term number from the local database."""

    # Convert aliases
    term = convert_term_alias(term)
    courses = db.query(Course).filter(Course.year == year).all()

    if not courses:
        raise HTTPException(
            status_code=404, detail=f"No courses found for year: {year}"
        )

    for course in courses:
        if term in (course.terms or ""):
            return term

    raise HTTPException(
        status_code=404, detail=f"Invalid term: {term} for year: {year}"
    )


def meeting_date_convert(raw_date: str) -> dict[str, str]:
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

    return {
        "start": f"{start_m}-{start_d.zfill(2)}",
        "end": f"{end_m}-{end_d.zfill(2)}",
    }


def meeting_time_convert(raw_time: str) -> str:
    """Converts the time given in meetings to "HH:mm"
    Args:
        raw_time (str): The given meeting time in the format of "H{am/pm}"
    Returns:
        formatted_time (str): The formatted meeting time in the format of "HH:mm"
    """
    if ":" in raw_time:
        time_part, period = raw_time[:-2], raw_time[-2:].lower()
        hour, minute = map(int, time_part.split(":"))
    else:
        period = raw_time[-2:].lower()
        hour = int(raw_time[:-2])
        minute = 0

    if period == "pm" and hour != 12:
        hour += 12
    elif period == "am" and hour == 12:
        hour = 0

    return f"{str(hour).zfill(2)}:{str(minute).zfill(2)}"


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
        "summer": "summer",
        "winter": "Winter",
        "online": "Online Term",
        "term": "Term",
        "uao": "UAO Teaching Period",
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


@app.get("/subjects", response_model=List[str])
def get_subjects(
    year: int = current_year(), term: str = current_sem(), db: Session = Depends(get_db)
):
    """Get all possible subjects for a given year and term, sorted alphabetically.

    Args:
        year (int, optional): The year to search for courses. Defaults to current year.
        term (str, optional): The term to search for courses. Defaults to current semester.

    Returns:
        dict: A dictionary containing a list of subjects.
    """
    term_number = get_term_number(db, year, term)

    results = (
        db.query(Course)
        .filter(Course.year == year, Course.terms.contains(term_number))
        .all()
    )

    if not results:
        raise HTTPException(
            status_code=404, detail="No courses found for the specified year and term"
        )

    # Extract unique subject codes from the results
    subjects = db.query(Subject).all()
    unique_names = set()

    subjects: list[str] = []

    # Collect unique subject codes from course results
    for entry in results:
        name = entry.subject
        if name:  # Skip empty names
            unique_names.add(name)

    # Add subject name for each unique name
    for name in unique_names:
        subjects.append(name)

    # Sort the subjects alphabetically
    subjects.sort()
    return subjects


@app.get("/courses", response_model=Union[Dict, List])
def get_subject_courses(
    subject: str,
    year: int = current_year(),
    term: str = current_sem(),
    university_wide_elective: Optional[bool] = None,
    level_of_study: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Gets a list of courses, optionally filtered by subject, year, term, university_wide_elective and level_of_study.

    Examples:
        /courses?year=2026&term=online1&subject=Accounting&university_wide_elective=false&level_of_study=Undergraduate

    Args:
        subject (str, optional): The subject code to search for. If omitted, all subjects are included.
        year (int, optional): The year of the courses. Defaults to current year.
        term (str, optional): The term of the courses. Defaults to current semester.
        university_wide_elective (bool, optional): Filter courses by whether they're an university-wide elective.
        level_of_study (str, optional): Filter courses by level of study (e.g., 'Undergraduate').

    Returns:
        list[dict]: A list of courses as dictionaries.
    """
    term_number = get_term_number(db, year, term)
    filters = [Course.year == year, Course.terms.contains(term_number)]
    if subject:
        filters.append(Course.subject == subject)
    if university_wide_elective is not None:
        filters.append(Course.university_wide_elective == university_wide_elective)
    if level_of_study:
        filters.append(Course.level_of_study == level_of_study)

    results = db.query(Course).filter(*filters).order_by(Course.course_code).all()

    if not results:
        raise HTTPException(
            status_code=404, detail="No courses found for the specified year and term"
        )

    transformed_courses = {"courses": []}

    # Extract necessary information from the results
    for entry in results:
        transformed_courses["courses"].append(
            {
                "id": entry.id,
                "name": {
                    "subject": entry.subject,
                    "code": entry.course_code,
                    "title": entry.title,
                },
                "university_wide_elective": entry.university_wide_elective,
                "level_of_study": entry.level_of_study,
            }
        )

    # Sort courses by course code alphabetically
    transformed_courses["courses"].sort(
        key=lambda x: x["name"]["code"].lower() if x["name"]["code"] else ""
    )
    return transformed_courses


def split_class_type_category(original_type: str):
    CATEGORIES = {"enrolment", "related"}
    full_category, class_type = original_type.split(": ")
    class_category = "unknown"
    for category in CATEGORIES:
        if category in full_category.lower():
            class_category = category
            break
    return {"category": class_category, "type": class_type}


@app.get("/courses/{course_cid}", response_model=Union[Dict, List])
def get_course(course_cid: str, db: Session = Depends(get_db)):
    """Course details route, takes in an id returns the courses' info and classes.

    Args:
        course_cid (string, required): The id to search for.

    Returns:
        dict: A dictionary containing the course information and classes.
    """
    course = db.query(Course).filter(Course.id == course_cid).first()

    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    course_id = course.course_id

    course_details = db.query(Course).filter(Course.course_id == course_id).first()

    # Extract necessary information from details
    if course_details:
        name = {
            "subject": course.subject,
            "code": course.course_code,
            "title": course.title,
        }
        requirements = {
            "prerequisites": parse_requisites(course_details.prerequisites),
            "corequisites": parse_requisites(course_details.corequisites),
            "antirequisites": parse_requisites(course_details.antirequisites),
        }
    else:
        name = {"subject": "", "code": "", "title": ""}
        requirements = {}

    # Construct the response
    response = {
        "id": course_cid,
        "course_id": course.course_id,
        "name": name,
        "year": course.year,
        "term": course.terms,
        "campus": course.campus,
        "units": course.units,
        "university_wide_elective": course.university_wide_elective,
        "course_coordinator": course.course_coordinator,
        "course_overview": course.course_overview,
        "level_of_study": course.level_of_study,
        "requirements": requirements,
        "class_list": [],
    }

    # Fetch classes info and process to match the required structure
    classes = db.query(CourseClass).filter(CourseClass.course_id == course_cid).all()
    if classes:
        class_groups = {}
        for class_group in classes:
            class_type = split_class_type_category(class_group.component)["type"]
            if class_type not in class_groups:
                class_groups[class_type] = {
                    **split_class_type_category(class_group.component),
                    "id": class_group.id,
                    "classes": [],
                }
            class_list_entry = class_groups[class_type]
            class_entry = {
                "number": str(class_group.class_nbr),
                "section": class_group.section,  # Returns class section
                "size": str(class_group.size),
                "available_seats": str(class_group.available),
                "meetings": [],
            }
            for meeting in class_group.meetings:
                # Split the meeting days by commas, and handle multiple same-day entries
                meeting_days = [
                    day.strip() for day in meeting.days.split(",") if day.strip()
                ]

                # Flatten the list if the days appear multiple times (e.g., "Monday, Monday")
                flattened_meeting_days = []
                for day in meeting_days:
                    # Append each day individually
                    flattened_meeting_days.append(day)

                # Skip weekend meetings
                if any(day in flattened_meeting_days for day in ["Saturday", "Sunday"]):
                    continue

                # Create meeting entry
                for day in flattened_meeting_days:
                    meeting_entry = {
                        "day": day,
                        "location": meeting.location,
                        "campus": meeting.campus,
                        "date": meeting_date_convert(meeting.dates),
                        "time": {
                            "start": meeting_time_convert(meeting.start_time),
                            "end": meeting_time_convert(meeting.end_time),
                        },
                    }
                    class_entry["meetings"].append(meeting_entry)

            class_list_entry["classes"].append(class_entry)

        response["class_list"] = list(class_groups.values())

    try:
        CourseSchema.model_validate(response)
    except ValidationError as e:
        raise HTTPException(status_code=501, detail=e.errors())

    return response
