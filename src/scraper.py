import data_parser
from datetime import datetime
from tinydb import TinyDB
import json


def main():
    db = TinyDB("db.json")
    year = datetime.now().year
    
    subjects = data_parser.get_subjects(year)
    db.insert(subjects)
    
    for subject in subjects["subjects"]:
        courses = data_parser.get_course_ids(subject, year)
        
        db.insert({"subject" : subject, "courses" : courses})
            
        for course in courses["courses"]:
            course_id = course["course_id"]
            term = course["term"]
            course_details = data_parser.get_course_details(course_id, term, year)
            
            db.insert({"course_id": course_id, "term": term, "year":year, "deatils": course_details})
            
            data = course_details["data"]
            session = data[0]["SESSION_CD"]
            offer = data[0]["COURSE_OFFER_NBR"] 
                
            course_class_list = data_parser.get_course_class_list(course_id, offer, term, session)["data"]
            
            db.insert({"course_id": course_id, "term": term, "year":year, "class_list": course_class_list})


if __name__ == "__main__":
    main()
