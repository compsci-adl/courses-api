from datetime import datetime
import time

from tinydb import TinyDB

import data_parser


def main():
    db = TinyDB("db.json")
    year = datetime.now().year
    
    subjects = data_parser.get_subjects(year)
    
    db.insert(subjects)
    
    # Keeps track of the number of requests made to the API for rate limiting
    requests = 1
    
    #TODO: Mitigate what happens if a request fails and returns an empty list
    for subject in subjects["subjects"]:
        courses = data_parser.get_course_ids(subject, year)
        
        db.insert({"subject" : subject, "courses" : courses})
        
        requests += 1
          
        for course in courses["courses"]:
            requests += 1
          
            if requests > 50:
                time.sleep(90)
                requests = 0
                
            
            course_id = course["course_id"]
            term = course["term"]
            course_details = data_parser.get_course_details(course_id, term, year)
            requests += 1
    
            db.insert({"course_id": course_id, "term": term, "year":year, "deatils": course_details})
            
            data = course_details["data"]
            session = data[0]["SESSION_CD"]
            offer = data[0]["COURSE_OFFER_NBR"] 
                
            course_class_list = data_parser.get_course_class_list(course_id, offer, term, session)["data"]
            
            db.insert({"course_id": course_id, "term": term, "year":year, "class_list": course_class_list})


if __name__ == "__main__":
    main()
