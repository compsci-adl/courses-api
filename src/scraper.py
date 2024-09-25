from datetime import datetime
from tinydb import TinyDB
import data_parser


def main():
    """Scrape data from the API and store it in a local database"""
    db = TinyDB("db.json")
    year = datetime.now().year

    subjects = data_parser.get_subjects(year)
    db.insert(subjects)

    for subject in subjects["subjects"]:
        courses = data_parser.get_course_ids(subject, year)
        db.insert({"subject": subject, "courses": courses})

        for course in courses["courses"]:
            course_id = course["course_id"]
            term = course["term"]

            course_details = data_parser.get_course_details(
                course_id, term, year)
            db.insert({"course_id": course_id, "term": term,
                      "year": year, "details": course_details})

            if isinstance(course_details, list) and len(course_details) > 0:
                session = course_details[0].get("SESSION_CD", "N/A")
                offer = course_details[0].get("COURSE_OFFER_NBR", "N/A")

                course_class_list = data_parser.get_course_class_list(
                    course_id, offer, term, session)["data"]
                db.insert({"course_id": course_id, "term": term,
                          "year": year, "class_list": course_class_list})
            else:
                print(f"No data found for course {course_id}, term {term}")


if __name__ == "__main__":
    main()
