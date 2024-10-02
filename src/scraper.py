from datetime import datetime
from tinydb import TinyDB
import data_parser
from nanoid import generate


def main():
    """Scrape data from the API and store it in a local database"""
    db = TinyDB("db.json")
    year = datetime.now().year

    subjects = data_parser.get_subjects(year)
    db.insert(subjects)

    for subject in subjects["subjects"]:
        code = subject["SUBJECT"]
        courses = data_parser.get_course_ids(code, year)
        db.insert({"subject": code, "courses": courses})

        for course in courses["courses"]:
            course_id = course["course_id"]
            term = course["term"]
            offer = course["offer"]

            course_details = data_parser.get_course_details(
                course_id, term, year, offer
            )
            db.insert(
                {
                    "id": generate(),
                    "course_id": course_id,
                    "term": term,
                    "year": year,
                    "details": course_details,
                }
            )

            if isinstance(course_details, list) and len(course_details) > 0:
                session = course_details[0].get("SESSION_CD", "N/A")

                course_class_list = data_parser.get_course_class_list(
                    course_id, offer, term, session
                )["data"]

                for cls in course_class_list:
                    # Restructure each group's dict to be type, id, and then classes
                    for group in cls.get("groups", []):
                        group_id = generate()
                        group_type = group.get("type", "")
                        group_classes = group.get("classes", [])

                        group.clear()
                        group["type"] = group_type
                        group["id"] = group_id
                        group["classes"] = group_classes

                db.insert(
                    {
                        "course_id": course_id,
                        "term": term,
                        "year": year,
                        "class_list": course_class_list,
                    }
                )
            else:
                print(f"No data found for course {course_id}, term {term}")


if __name__ == "__main__":
    main()
