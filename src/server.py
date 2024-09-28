from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from tinydb import Query, TinyDB
from typing import Union
from src.utils import current_sem, current_year, get_term_number, meeting_date_convert, meeting_time_convert, parse_requisites

app = FastAPI()
Course = Query()

# Configure CORS for local development
origins = [
    "http://localhost:5173",
    "http://localhost:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db(): 
    return TinyDB('src/db.json')

@app.get("/subjects", response_model=Union[dict, list])
def get_subjects(year: int = current_year(), term: str = current_sem(), db: TinyDB = Depends(get_db)):
    """Get all possible subjects for a given year and term, sorted alphabetically.
    
    Args: 
        year (int, optional): The year to search for courses. Defaults to current year.
        term (str, optional): The term to search for courses. Defaults to current semester.

    Returns:
        dict: A dictionary containing a list of subjects.
    """
    term_number = get_term_number(year, term, Course, db)

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

    transformed_subjects = {
        "subjects": []
    }

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
                transformed_subjects["subjects"].append({
                    "code": code,
                    "name": subject.get("DESCR")
                })
                break

    # Sort the subjects alphabetically by the code
    transformed_subjects["subjects"].sort(key=lambda x: x["code"])

    return transformed_subjects


@app.get("/courses", response_model=Union[dict, list])
def get_subject_courses(subject: str, year: int = current_year(), term: str = current_sem(), db: TinyDB = Depends(get_db)):
    """Gets a list of courses given a subject (and optionally a year and term).
    
    Args:
        subject (str, required): The subject code to search for.
        year (int, optional): The year of the courses from 2006 to
        the current year. Defaults to current year.
        term (str, optional): The term of the courses. Defaults to current semester.

    Returns:
        list[dict]: A list of courses as dictionaries.
    """
    term_number = get_term_number(year, term, Course, db)
    results = db.search((Course.details.any(Query().SUBJECT == subject)) &
                        (Course.year == year) &
                        (Course.term == term_number))

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
                "id": nano_id,
                "name": {
                    "subject": subject,
                    "code": code,
                    "title": title
                },
            })

    return transformed_courses


@app.get("/courses/{id}", response_model=Union[dict, list])
def get_course(id: str, db: TinyDB = Depends(get_db)):
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
        "requirement": requirement,
        "class_list": []
    }

    # Fetch classes info and process to match the required structure
    classes = db.search((Course.course_id == course_id) & (Course.year == year) &
                        (Course.term == term))
    if classes:
        class_details = classes[1]
        class_list = class_details.get("class_list", [])
        for class_group in class_list:
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
