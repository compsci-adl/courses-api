import os
from hashlib import shake_256

from dotenv import dotenv_values
from rich.progress import Progress
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import data_parser
from models import Base, Course, CourseClass, CourseDetail, Meetings, Subject


def get_short_hash(content: str, even_length=12) -> str:
    """Generates a short hash from the given content using the shake_256 algorithm."""
    return shake_256(content.encode("utf8")).hexdigest(even_length // 2)


def main():
    """Scrape data from the API and store it in a local database"""

    # If db already exists, delete it
    if os.path.exists("src/dev.sqlite3"):
        os.remove("src/dev.sqlite3")

    engine = create_engine("sqlite:///src/dev.sqlite3")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    year = dotenv_values().get("YEAR")

    with Progress() as progress:
        subjects = data_parser.get_subjects(year)

        all_task = progress.add_task(
            "[cyan bold]All Courses", total=len(subjects["subjects"])
        )

        for subject in subjects["subjects"]:
            code = subject["SUBJECT"]
            description = subject.get("DESCR", "")
            subject_task = progress.add_task(f"[cyan]{code}", total=None)

            # Subject Custom ID
            subject_cid = get_short_hash(f"{code}{description}")

            # Insert subject into the database
            db_subject = Subject(
                id=subject_cid, subject_code=code, description=description
            )
            session.add(db_subject)
            session.commit()

            courses = data_parser.get_course_ids(code, year)
            progress.update(subject_task, total=len(courses["courses"]))

            for course in courses["courses"]:
                course_id = course["course_id"]
                term = course["term"]
                offer = course["offer"]

                course_details = data_parser.get_course_details(
                    course_id, term, year, offer
                )

                if isinstance(course_details, list) and len(course_details) > 0:
                    subject_code = course_details[0]["SUBJECT"]
                    catalog_nbr = course_details[0]["CATALOG_NBR"]
                    course_title = course_details[0]["COURSE_TITLE"]
                    term_descr = course_details[0]["TERM_DESCR"]
                    campus = course_details[0]["CAMPUS"]
                    units = course_details[0]["UNITS"]
                    class_nbr = course_details[0]["CLASS_NBR"]

                # Course Custom ID
                course_cid = get_short_hash(
                    f"{subject_code}{catalog_nbr}{course_title}{year}{term}{class_nbr}"
                )

                # Check if Course Custom ID exists, if it does, skip
                if session.query(Course).filter_by(id=course_cid).first():
                    progress.update(subject_task, advance=1)
                    continue

                # Insert course into the database
                db_course = Course(
                    id=course_cid,
                    course_id=course_id,
                    course_offer_nbr=offer,
                    year=year,
                    term=term,
                    term_descr=term_descr,
                    subject=subject_code,
                    catalog_nbr=catalog_nbr,
                    course_title=course_title,
                    campus=campus,
                    units=units,
                    class_nbr=class_nbr,
                )
                session.add(db_course)
                session.commit()

                if isinstance(course_details, list) and len(course_details) > 0:
                    for detail in course_details:
                        subject_name = detail["SUBJECT"]
                        # Course Custom ID
                        course_cid = get_short_hash(
                            f"{subject_name}{catalog_nbr}{year}{term}{course_id}"
                        )

                        db_course_detail = CourseDetail(
                            id=course_cid,
                            year=detail["YEAR"],
                            course_id=course_id,
                            course_offer_nbr=offer,
                            term=term,
                            term_descr=detail["TERM_DESCR"],
                            course_title=detail["COURSE_TITLE"],
                            campus=detail["CAMPUS"],
                            campus_cd=detail["CAMPUS_CD"],
                            subject=subject_name,
                            catalog_nbr=detail["CATALOG_NBR"],
                            restriction=detail["RESTRICTION"],
                            restriction_txt=detail.get("RESTRICTION_TXT", ""),
                            pre_requisite=detail.get("PRE_REQUISITE", ""),
                            co_requisite=detail.get("CO_REQUISITE", ""),
                            assumed_knowledge=detail.get("ASSUMED_KNOWLEDGE", ""),
                            incompatible=detail.get("INCOMPATIBLE", ""),
                            syllabus=detail["SYLLABUS"],
                            url=detail["URL"],
                        )
                        session.add(db_course_detail)
                        session.commit()

                    session_code = course_details[0].get("SESSION_CD", "N/A")

                    course_class_list = data_parser.get_course_class_list(
                        course_id, offer, term, session_code
                    )["data"]

                    for cls in course_class_list:
                        for group in cls["groups"]:
                            for class_info in group["classes"]:
                                # Course Class Custom ID
                                course_class_cid = get_short_hash(
                                    f"{class_info['class_nbr']}{class_info['section']}{class_info['component']}"
                                )

                                # Check if Class Custom ID exists, if it does, skip
                                if (
                                    session.query(CourseClass)
                                    .filter_by(id=course_class_cid)
                                    .first()
                                ):
                                    progress.update(subject_task, advance=1)
                                    continue

                                db_course_class = CourseClass(
                                    id=course_class_cid,
                                    class_nbr=class_info["class_nbr"],
                                    section=class_info["section"],
                                    size=class_info["size"],
                                    enrolled=class_info["enrolled"],
                                    available=class_info["available"],
                                    component=class_info["component"],
                                    course_id=db_course.id,
                                )
                                session.add(db_course_class)
                                session.commit()
                                if class_info.get("meetings"):
                                    for meeting in class_info.get("meetings"):
                                        # Meeting Custom ID
                                        meeting_cid = get_short_hash(
                                            f"{class_info['class_nbr']}{meeting['dates']}{meeting['days']}{meeting['start_time']}{meeting['end_time']}{meeting['location']}{db_course_class.id}"
                                        )

                                        db_meeting = Meetings(
                                            id=meeting_cid,
                                            dates=meeting["dates"],
                                            days=meeting["days"],
                                            start_time=meeting["start_time"],
                                            end_time=meeting["end_time"],
                                            location=meeting["location"],
                                            course_class_id=db_course_class.id,
                                        )
                                        session.add(db_meeting)
                                        session.commit()

                else:
                    print(f"No data found for course {course_id}, term {term}")

                progress.update(subject_task, advance=1)

            progress.update(all_task, advance=1)


if __name__ == "__main__":
    main()
