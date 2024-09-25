from typing import Union
from fastapi import FastAPI, HTTPException
from tinydb import TinyDB, Query

app = FastAPI()
db = TinyDB('src/db.json')

current_year = 2024
current_term = "4410"

Course = Query()


@app.get("/courses/", response_model=Union[dict, list])
def list_courses(year: int = current_year, term: str = current_term):
    """List courses for the specified year and term."""
    results = db.search((Course.year == year) & (Course.term == term))

    if not results:
        raise HTTPException(
            status_code=404, detail="No courses found for the specified year and term")

    transformed_courses = {
        "courses": []
    }

    for entry in results:
        course_id = entry.get("course_id", "")
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
                "id": course_id
            })

    return transformed_courses


@app.get("/courses/{course_id}", response_model=Union[dict, list])
def get_course(course_id: str, year: int = current_year, term: str = current_term):
    """Get detailed information about a specific course by its ID."""
    result = db.search((Course.course_id == course_id) & (
        Course.year == year) & (Course.term == term))

    if not result:
        raise HTTPException(status_code=404, detail="Course not found")

    return result[0]
