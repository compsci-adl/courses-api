import time

import data_fetcher


def get_subjects(year: int) -> dict[str, list[dict[str, str]]]:
    """Return a list of subjects for a given year."""
    subjects = data_fetcher.DataFetcher(
        f"SUBJECTS_BY_YEAR/queryx&virtual=Y&year_from={year}&year_to={year}"
    )

    try:
        data = subjects.get()
        if subjects.last_response.status_code != 200 or data is None:
            print(f"Error: {subjects.last_response.status_code} - {data}")
            return {"subjects": []}

        subject_list = [
            {
                "SUBJECT": subject["SUBJECT"],
                "DESCR": subject["DESCR"].split(" - ", 1)[1],
            }
            for subject in data["data"]
        ]
        return {"subjects": subject_list}

    except Exception as e:
        print(f"An error occurred while fetching subjects: {e}")
        return {"subjects": []}


def get_course_ids(subject_code: str, year: int):
    """Return a list of course ids for a given subject code and year."""
    courses = data_fetcher.DataFetcher(
        f"COURSE_SEARCH/queryx&virtual=Y&year={year}&pagenbr=1&pagesize=500&subject={subject_code}"
    )

    try:
        data = courses.get()
        if courses.last_response.status_code != 200 or data is None:
            print(f"Error: {courses.last_response.status_code} - {data}")
            return {"courses": []}

        course_ids = [
            {
                "course_id": course["COURSE_ID"],
                "term": course["TERM"],
                "offer": course["COURSE_OFFER_NBR"],
            }
            for course in data["data"]
        ]
        return {"courses": course_ids}

    except Exception as e:
        print(f"An error occurred while fetching course IDs: {e}")
        return {"courses": []}


def get_course_details(course_id: str, term: int, year: int, offer: int, max_retries=3):
    """Return the details of a course for a given course id, term, and year."""
    for _ in range(max_retries):
        course_details = data_fetcher.DataFetcher(
            f"COURSE_DTL/queryx&virtual=Y&year={year}&courseid={course_id}&term={term}&course_offer_nbr={offer}"
        )

        try:
            data = course_details.get()
            if course_details.last_response.status_code != 200:
                print(
                    f"Error: {course_details.last_response.status_code} - "
                    f"{course_details.last_response.text}"
                )
                return {}

            if "data" not in data or len(data["data"]) == 0:
                print(f"No data found for course {course_id}, term {term}. Retrying...")
                time.sleep(2)
                continue

            details = data["data"][0]
            if not (
                details["CATALOG_NBR"]
                and details["COURSE_TITLE"]
                and details["TERM_DESCR"]
            ):
                # TODO: Logger
                # print(f"Course Details Not Found")
                return {}
            # TODO: Logger
            # name_with_term = f"{details['CATALOG_NBR']} {details['COURSE_TITLE']} {details['TERM_DESCR']}"
            # print(name_with_term)

            return data["data"]

        except Exception as e:
            print(f"An error occurred while fetching course details: {e}")
            return {}

    print(
        f"Failed to retrieve course details for course {course_id} after {max_retries} attempts."
    )
    return {}


def get_course_class_list(course_id: int, offer: int, term: int, session: int):
    """Return the class list of a course for a given course id, offer, term, and session."""
    # If session is empty, set value to 1
    if session is None or session == "":
        print("Session is missing, defaulting to '1'.")
        session = "1"

    course_class_list = data_fetcher.DataFetcher(
        f"COURSE_CLASS_LIST/queryx&virtual=Y&crseid={course_id}&offer={offer}&term={term}&session={session}"
    )

    try:
        data = course_class_list.get()
        if course_class_list.last_response.status_code != 200:
            print(
                f"Error: {course_class_list.last_response.status_code} - "
                f"{course_class_list.last_response.text}"
            )
            return {}

        # TODO: Logger
        # print(f"{len(data['data'][0]['groups'])} Classes Found")

        return data

    except Exception as e:
        print(f"An error occurred while fetching course class list: {e}")
        return {}
