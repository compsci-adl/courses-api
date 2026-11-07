import os
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional, Union

from dotenv import dotenv_values
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

try:
    from .db_service import ensure_schema_up_to_date
    from .models import Base, Course, CourseClass
    from .schemas import CourseSchema
except ImportError:
    from db_service import ensure_schema_up_to_date
    from models import Base, Course, CourseClass  # noqa: F401
    from schemas import CourseSchema  # noqa: F401

# Check if the application is running in development mode
is_dev_mode = "dev" in sys.argv

# Configure FastAPI based on the mode
app = FastAPI(
    docs_url="/docs" if is_dev_mode else None,
    redoc_url="/redoc" if is_dev_mode else None,
)

# Determine the database type
DB_TYPE = os.environ.get("DB_TYPE") or dotenv_values().get("DB_TYPE")


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
ensure_schema_up_to_date(engine)

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


def get_active_year(db: Session) -> int:
    """Finds the latest year that contains class records in the DB.

    Falls back to the latest course year or current calendar year.
    """
    result = (
        db.query(CourseClass.year).distinct().order_by(CourseClass.year.desc()).first()
    )
    if result and result[0]:
        return int(result[0])

    course_year = db.query(Course.year).distinct().order_by(Course.year.desc()).first()
    if course_year and course_year[0]:
        return int(course_year[0])

    env_year = dotenv_values().get("YEAR")
    if env_year and env_year.isdigit():
        return int(env_year)

    return datetime.now().year


def get_available_years_from_db(db: Session) -> list[int]:
    """Queries SQLite directly for all distinct course years."""
    years = db.query(Course.year).distinct().order_by(Course.year.asc()).all()
    avail = [int(y[0]) for y in years if y[0] is not None]
    if avail:
        return avail
    # Fallback to current year span
    cur = datetime.now().year
    return [cur, cur + 1, cur + 2]


def current_year(db: Optional[Session] = None) -> int:
    """Gets the current active year."""
    if db:
        return get_active_year(db)
    return datetime.now().year


def validate_and_resolve_year(year: Optional[int], db: Session) -> int:
    """Validates year against available_years in DB or defaults to active_year.

    Raises HTTP 400 Bad Request if year is invalid.
    """
    available_years = get_available_years_from_db(db)
    if year is None:
        return get_active_year(db)
    if year not in available_years:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid year: {year}. Available years are: {available_years}",
        )
    return year


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
    if not raw_date or raw_date.strip() == "":
        return {"start": None, "end": None}

    try:
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
    except Exception:
        return {"start": None, "end": None}


def meeting_time_convert(raw_time: str) -> str:
    """Converts the time given in meetings to "HH:mm"
    Args:
        raw_time (str): The given meeting time in the format of "H{am/pm}"
    Returns:
        formatted_time (str): The formatted meeting time in the format of "HH:mm"
    """
    if not raw_time or raw_time.strip() == "":
        return None

    try:
        raw_time = raw_time.strip()
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
    except Exception:
        return None


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


def get_years_with_classes_from_db(db: Session) -> list[int]:
    """Queries SQLite for all distinct years that have course classes."""
    years = db.query(CourseClass.year).distinct().order_by(CourseClass.year.asc()).all()
    return [int(y[0]) for y in years if y[0] is not None]


@app.get("/")
@app.get("/status")
@app.get("/years")
def get_years_metadata(db: Session = Depends(get_db)):
    """Returns active academic year, available years, and years with classes directly from SQLite."""
    available_years = get_available_years_from_db(db)
    years_with_classes = get_years_with_classes_from_db(db)
    active_year = get_active_year(db)
    return {
        "active_year": active_year,
        "available_years": available_years,
        "years_with_classes": years_with_classes,
    }


@app.get("/subjects", response_model=List[str])
def get_subjects(
    year: Optional[int] = Query(default=None),
    term: str = current_sem(),
    db: Session = Depends(get_db),
):
    """Get all possible subjects for a given year and term, sorted alphabetically.

    Args:
        year (int, optional): The year to search for courses. Defaults to active year.
        term (str, optional): The term to search for courses. Defaults to current semester.

    Returns:
        list[str]: A list of subjects.
    """
    resolved_year = validate_and_resolve_year(year, db)
    term_number = get_term_number(db, resolved_year, term)

    results = (
        db.query(Course)
        .filter(Course.year == resolved_year, Course.terms.contains(term_number))
        .all()
    )

    if not results:
        raise HTTPException(
            status_code=404, detail="No courses found for the specified year and term"
        )

    unique_names = set()
    for entry in results:
        if entry.subject:
            unique_names.add(entry.subject)

    subjects = sorted(list(unique_names))
    return subjects


@app.get("/courses", response_model=Union[Dict, List])
def get_subject_courses(
    subject: Optional[str] = None,
    year: Optional[int] = Query(default=None),
    term: str = current_sem(),
    university_wide_elective: Optional[bool] = None,
    level_of_study: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Gets a list of courses, optionally filtered by subject, year, term, university_wide_elective and level_of_study.

    Args:
        subject (str, optional): The subject code to search for. If omitted, all subjects are included.
        year (int, optional): The year of the courses. Defaults to active year.
        term (str, optional): The term of the courses. Defaults to current semester.
        university_wide_elective (bool, optional): Filter courses by whether they're a university-wide elective.
        level_of_study (str, optional): Filter courses by level of study.

    Returns:
        dict: A dictionary containing the list of courses.
    """
    resolved_year = validate_and_resolve_year(year, db)
    term_number = get_term_number(db, resolved_year, term)
    filters = [Course.year == resolved_year, Course.terms.contains(term_number)]
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
                "course_level": entry.course_level,
                "campus": entry.campus,
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
def get_course(
    course_cid: str,
    year: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    """Course details route, takes in an id returns the course's info and classes."""
    query = db.query(Course).filter(Course.id == course_cid)
    if year is not None:
        resolved_year = validate_and_resolve_year(year, db)
        query = query.filter(Course.year == resolved_year)

    course = query.first()

    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    course_id = course.course_id
    course_details = (
        db.query(Course)
        .filter(Course.course_id == course_id, Course.year == course.year)
        .first()
    )

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

    learning_outcomes = [
        {"description": lo.description, "outcome_index": lo.outcome_index}
        for lo in course.learning_outcomes
    ]

    assessments = [
        {
            "title": assess.title,
            "weighting": assess.weighting,
            "hurdle": assess.hurdle,
            "learning_outcomes": assess.learning_outcomes,
        }
        for assess in course.assessments
    ]

    # Construct the response
    response = {
        "id": course_cid,
        "course_id": course.course_id,
        "name": name,
        "year": str(course.year),
        "term": course.terms,
        "campus": course.campus,
        "units": course.units,
        "university_wide_elective": course.university_wide_elective,
        "course_coordinator": course.course_coordinator,
        "course_overview": course.course_overview,
        "level_of_study": course.level_of_study,
        "course_level": course.course_level,
        "course_url": course.url,
        "course_outline_url": course.course_outline_url,
        "learning_outcomes": learning_outcomes,
        "textbooks": course.textbooks,
        "assessments": assessments,
        "requirements": requirements,
        "class_list": [],
    }

    # Fetch classes info and process to match the required structure
    classes = (
        db.query(CourseClass)
        .filter(CourseClass.course_id == course_cid, CourseClass.year == course.year)
        .all()
    )
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
                "group": class_group.group,
                "meetings": [],
            }
            for meeting in class_group.meetings:
                # Split the meeting days by commas, and handle multiple same-day entries
                meeting_days = [
                    day.strip() for day in meeting.days.split(",") if day.strip()
                ]

                # Create meeting entry
                for day in meeting_days:
                    meeting_entry = {
                        "day": day,
                        "location": meeting.location,
                        "campus": meeting.campus,
                        "instructor": meeting.instructor,
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
