import data_fetcher

def get_subjects(year: int):
    """Return a list of subjects for a given year """
    
    subjects = data_fetcher.DataFetcher(
        f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/SUBJECTS_BY_YEAR/queryx&virtual=Y&year_from={year}&year_to={year}"
    )
    data = subjects.get()["data"]
    
    if data is None:
        return {"subjects": []}
    
    subject_list = []
    for subject in data:
        subject_list.append(subject["SUBJECT"])
        
    return {"subjects": subject_list} 
   
def get_course_ids(subject_code: int, year: int):
    """Return a list of course ids for a given subject code and year"""
    
    courses = data_fetcher.DataFetcher(
        f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_SEARCH/queryx&virtual=Y&year={year}&pagenbr=1&pagesize=500&subject={subject_code}"
    )
    data = courses.get()["data"]
    
    if data is None:
        return {"courses": []}
    
    course_ids = []
    for course in data:
        course_ids.append({"course_id" : course["COURSE_ID"], "term" : course["TERM"]})
    
    return {"courses": course_ids}

def get_course_details(course_id: int, term: int, year: int):
    """Return the details of a course for a given course id, term and year"""
    
    course_details = data_fetcher.DataFetcher(
        f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_DTL/queryx&virtual=Y&year={year}&courseid={course_id}&term={term}"
    )
    return course_details.get()

def get_course_class_list(course_id: int, offer: int, term: int, session: int):
    """Return the class list of a course for a given course id, offer, term and session"""
    
    course_class_list = data_fetcher.DataFetcher(
        f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_CLASS_LIST/queryx&virtual=Y&crseid={course_id}&offer={offer}&term={term}&session={session}"
    )
    
    return course_class_list.get()


# TODO: Merge get_course_details and get_course_class_list into one function in the format specified in issue #3 in the GitHub
