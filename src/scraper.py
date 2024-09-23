from datetime import datetime
import time
import requests
from tinydb import TinyDB
from tqdm import tqdm
import data_parser


def safe_api_call(api_func, *args, **kwargs):
    """Helper function to safely make API calls with retry logic on JSONDecodeError or request failures."""
    max_retries = 3
    retries = 0

    while retries < max_retries:
        try:
            response = api_func(*args, **kwargs)
            return response
        except requests.exceptions.JSONDecodeError as e:
            print(f"JSONDecodeError: {e}. Retrying after waiting 15 seconds.")
        except requests.exceptions.RequestException as e:
            print(f"RequestException: {e}. Retrying after waiting 15 seconds.")
        except Exception as e:
            print(f"Unexpected error: {e}. Retrying after waiting 15 seconds.")

        # Wait for 15 seconds before retrying
        for i in tqdm(range(15), desc="Waiting for rate limit reset"):
            time.sleep(1)

        retries += 1

    # If max retries reached, raise an exception
    raise Exception(f"Failed to retrieve data after {max_retries} attempts.")


def main():
    db = TinyDB("db.json")
    year = datetime.now().year

    subjects = safe_api_call(data_parser.get_subjects, year)

    db.insert(subjects)

    print(f"Fetched {len(subjects['subjects'])} subjects")

    for subject in subjects["subjects"]:
        courses = safe_api_call(data_parser.get_course_ids, subject, year)
        db.insert({"subject": subject, "courses": courses})

        print(f"Fetched {len(courses['courses'])
                         } courses for subject {subject}")

        for course in courses["courses"]:
            course_id = course["course_id"]
            term = course["term"]

            course_details = safe_api_call(
                data_parser.get_course_details, course_id, term, year)
            db.insert({"course_id": course_id, "term": term,
                      "year": year, "details": course_details})
            print(f"Fetched course details for course {
                  course_id}, term {term}")

            if isinstance(course_details, list) and len(course_details) > 0:
                session = course_details[0].get("SESSION_CD", "N/A")
                offer = course_details[0].get("COURSE_OFFER_NBR", "N/A")

                course_class_list = safe_api_call(
                    data_parser.get_course_class_list, course_id, offer, term, session)["data"]
                db.insert({"course_id": course_id, "term": term,
                          "year": year, "class_list": course_class_list})
                print(f"Fetched {len(course_class_list)
                                 } class entries for course {course_id}")
            else:
                print(f"No data found for course {course_id}, term {term}")


if __name__ == "__main__":
    main()
