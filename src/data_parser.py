import data_fetcher
import time


def get_subjects(year: int):
    """Return a list of subjects for a given year."""
    subjects = data_fetcher.DataFetcher(
        f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/SUBJECTS_BY_YEAR/queryx&virtual=Y&year_from={
            year}&year_to={year}"
    )

    try:
        data = subjects.get()
        print(f"HTTP Response Code: {subjects.last_response.status_code}")
        if subjects.last_response.status_code != 200 or data is None:
            print(f"Error: {subjects.last_response.status_code} - {data}")
            return {"subjects": []}

        subject_list = [subject["SUBJECT"] for subject in data["data"]]
        return {"subjects": subject_list}

    except Exception as e:
        print(f"An error occurred while fetching subjects: {e}")
        return {"subjects": []}


def get_course_ids(subject_code: str, year: int):
    """Return a list of course ids for a given subject code and year."""
    courses = data_fetcher.DataFetcher(
        f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_SEARCH/queryx&virtual=Y&year={
            year}&pagenbr=1&pagesize=500&subject={subject_code}"
    )

    try:
        data = courses.get()
        print(f"HTTP Response Code: {courses.last_response.status_code}")
        if courses.last_response.status_code != 200 or data is None:
            print(f"Error: {courses.last_response.status_code} - {data}")
            return {"courses": []}

        course_ids = [{"course_id": course["COURSE_ID"],
                       "term": course["TERM"]} for course in data["data"]]
        return {"courses": course_ids}

    except Exception as e:
        print(f"An error occurred while fetching course IDs: {e}")
        return {"courses": []}


def get_course_details(course_id: int, term: int, year: int, max_retries=3):
    """Return the details of a course for a given course id, term and year."""
    for attempt in range(max_retries):
        course_details = data_fetcher.DataFetcher(
            f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_DTL/queryx&virtual=Y&year={
                year}&courseid={course_id}&term={term}"
        )

        try:
            data = course_details.get()
            print(f"HTTP Response Code: {
                  course_details.last_response.status_code}")
            if course_details.last_response.status_code != 200:
                print(f"Error: {
                      course_details.last_response.status_code} - {course_details.last_response.text}")
                return {}

            # Try to fetch data again if no data is found
            if "data" not in data or len(data["data"]) == 0:
                print(f"No data found for course {
                      course_id}, term {term}. Retrying...")
                time.sleep(2)
                continue

            return data["data"]

        except Exception as e:
            print(f"An error occurred while fetching course details: {e}")
            return {}

    print(f"Failed to retrieve course details for course {
          course_id} after {max_retries} attempts.")
    return {}


def get_course_class_list(course_id: int, offer: int, term: int, session: int):
    """Return the class list of a course for a given course id, offer, term and session."""

    # If session is empty, set value to 1
    if session is None or session == "":  
        print("Session is missing, defaulting to '1'.")
        session = "1"  

    course_class_list = data_fetcher.DataFetcher(
        f"https://courseplanner-api.adelaide.edu.au/api/course-planner-query/v1/?target=/system/COURSE_CLASS_LIST/queryx&virtual=Y&crseid={
            course_id}&offer={offer}&term={term}&session={session}"
    )

    try:
        data = course_class_list.get()
        print(f"HTTP Response Code: {
              course_class_list.last_response.status_code}")
        if course_class_list.last_response.status_code != 200:
            print(f"Error: {
                  course_class_list.last_response.status_code} - {course_class_list.last_response.text}")
            return {}

        return data

    except Exception as e:
        print(f"An error occurred while fetching course class list: {e}")
        return {}
