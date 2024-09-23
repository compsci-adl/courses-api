from fastapi import FastAPI
from src.api.course_scraper import CourseScraper
from tinydb import TinyDB
import time

app = FastAPI()
scraper = CourseScraper() 
db = TinyDB("db.json")

@app.get("/scrape_all_data/")
async def scrape_all_data():
    """
    Scrapes all data: subjects, courses, and course details, and stores them in the database.
    """
    year = scraper.year
        
    subjects = scraper.get_subjects(year)
    
    db.insert(subjects)

    # Keeps track of the number of requests made to the API for rate limiting
    requests = 1 
    
    # TODO: Mitigate what happens if a request fails and returns an empty list
    for subject in subjects["subjects"]:
        courses = scraper.get_course_ids(subject, year)
        
        db.insert({"subject" : subject, "courses" : courses})
        
        requests += 1
        
        for course in courses["courses"]:
            requests += 1
        
            if requests > 50:
                time.sleep(90)
                requests = 0
                
            
            course_id = course["course_id"]
            term = course["term"]
            course_details = scraper.get_course_details(course_id, term, year)
            requests += 1
    
            db.insert({"course_id": course_id, "term": term, "year":year, "deatils": course_details})
            
            data = course_details["data"]
            session = data[0]["SESSION_CD"]
            offer = data[0]["COURSE_OFFER_NBR"] 
                
            course_class_list = scraper.get_course_class_list(course_id, offer, term, session)["data"]
            
            db.insert({"course_id": course_id, "term": term, "year":year, "class_list": course_class_list})

        return {"status": "All data scraped and stored."}


@app.get("/courses/")
async def courses_route(year: int = None, term: str = None):
    """
    Fetches a list of courses given (optionally) the year and term.
    """
    year = year or scraper.year
    term = term or scraper.get_current_sem()

    return scraper.get_courses(year, term)

@app.get("/terms")
async def terms_route():
    """
    Fetches the list of terms for the current year.
    """
    return scraper.get_terms()

@app.get("/course/{course_id}")
async def course_info_route(course_id: int, year: int = None, term: str = None):
    """
    Fetches the details of a course based on course_id, term, and year.
    """
    year = year or scraper.year
    term = term or scraper.get_current_sem()

    return scraper.get_course_info(course_id, year, term)

# @app.get("/class-list/{course_id}")
# async def class_list_route(course_id: int, term: int):
#     """
#     Fetches the class list for a given course ID and term.
#     """
#     term = term or scraper.get_current_sem()
#     print(term)
#     return scraper.get_course_class_list(course_id, term)


# @app.get("/scrape_subjects")
# async def scrape_subjects():
#     """
#     Scrapes all subjects and stores them in the database.
#     """
#     subjects = scraper.get_subjects()

#     if "subjects" in subjects:
#         db.insert(subjects)
#         return {"status": "Success", "subjects": subjects}
#     return {"status": "Failed", "message": "No subjects found"}

