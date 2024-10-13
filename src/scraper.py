from datetime import datetime
from hashlib import shake_256
from tinydb import TinyDB
from rich.progress import Progress

import data_parser


def get_short_hash(content: str, even_length=12) -> str:
    """Generates a short hash from the given content using the shake_256 algorithm."""
    return shake_256(content.encode("utf8")).hexdigest(even_length // 2)


def main():
    """Scrape data from the API and store it in a local database"""

    db = TinyDB("db.json")
    year = datetime.now().year

    with Progress() as progress:
        subjects = data_parser.get_subjects(year)
        db.insert(subjects)

        all_task = progress.add_task(
            "[cyan bold]All Courses", total=len(subjects["subjects"])
        )

        for subject in subjects["subjects"]:
            # Test with a single subject
            # TEST_SUBJECT = "COMP SCI"
            # if subject["SUBJECT"] != TEST_SUBJECT:
            #     continue

            code = subject["SUBJECT"]
            subject_task = progress.add_task(f"[cyan]{code}", total=None)
            courses = data_parser.get_course_ids(code, year)
            db.insert({"subject": code, "courses": courses})
            progress.update(subject_task, total=len(courses["courses"]))

            for course in courses["courses"]:
                course_id = course["course_id"]
                term = course["term"]
                offer = course["offer"]

                course_details = data_parser.get_course_details(
                    course_id, term, year, offer
                )

                subject = course_details[0]["SUBJECT"]
                catalog_nbr = course_details[0]["CATALOG_NBR"]

                # Course Custom ID
                course_cid = get_short_hash(
                    f"{subject}{catalog_nbr}{year}{term}{course_id}"
                )
                db.insert(
                    {
                        "id": course_cid,
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
                            group_type = group.get("type", "")
                            group_id = get_short_hash(f"{course_cid}{group_type}")
                            group_classes = group.get("classes", [])

                            group.clear()
                            group["type"] = group_type
                            group["id"] = group_id
                            group["classes"] = group_classes

                    db.insert(
                        {
                            "id": course_cid,
                            "class_list": course_class_list,
                        }
                    )
                else:
                    print(f"No data found for course {course_id}, term {term}")

                progress.update(subject_task, advance=1)

            progress.update(all_task, advance=1)


if __name__ == "__main__":
    main()
