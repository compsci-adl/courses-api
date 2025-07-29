import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import shake_256
from queue import Queue
from threading import Lock, Thread

from dotenv import dotenv_values
from rich.progress import Progress
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import data_parser
import fetch_proxies
from models import Base, Course, CourseClass, CourseDetail, Meetings, Subject

# Session and write queue for DB writer thread
Session = sessionmaker()
write_queue = Queue()


def get_short_hash(content: str, even_length=12) -> str:
    """Generates a short hash from the given content using the shake_256 algorithm."""
    return shake_256(content.encode("utf8")).hexdigest(even_length // 2)


def db_writer(engine):
    """Dedicated DB writer thread to serialize all DB operations and prevent locking."""
    while True:
        obj = write_queue.get()
        if obj is None:
            break  # Stop signal

        session = Session(bind=engine)

        try:
            session.merge(obj)
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"[DB ERROR] {e} on {obj}")
        finally:
            session.close()


def process_course(course, year, subject, engine, progress, subject_task, lock):
    """Process a single course and insert data into the database."""
    try:
        course_id = course["course_id"]
        term = course["term"]
        offer = course["offer"]

        course_details = data_parser.get_course_details(course_id, term, year, offer)

        if not isinstance(course_details, list) or len(course_details) == 0:
            progress.update(subject_task, advance=1)
            return

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

        # Insert course into the queue
        db_course = Course(
            id=course_cid,
            course_id=course_id,
            course_offer_nbr=offer,
            year=year,
            term=term,
            term_descr=term_descr,
            subject=subject["SUBJECT"],
            catalog_nbr=catalog_nbr,
            course_title=course_title,
            campus=campus,
            units=units,
            class_nbr=class_nbr,
        )
        write_queue.put(db_course)

        # Process course details
        for detail in course_details:
            subject_name = detail["SUBJECT"]
            detail_catalog_nbr = detail["CATALOG_NBR"]
            detail_cid = get_short_hash(
                f"{subject_name}{detail_catalog_nbr}{year}{term}{course_id}"
            )

            db_course_detail = CourseDetail(
                id=detail_cid,
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
            write_queue.put(db_course_detail)

        # Process course classes and meetings
        session_code = course_details[0].get("SESSION_CD", "N/A")
        course_class_list = data_parser.get_course_class_list(
            course_id, offer, term, session_code
        )["data"]

        for cls in course_class_list:
            for group in cls["groups"]:
                for class_info in group["classes"]:
                    class_nbr = class_info["class_nbr"]
                    section = class_info["section"]
                    component = class_info["component"]

                    course_class_cid = get_short_hash(
                        f"{class_nbr}{section}{component}"
                    )

                    db_course_class = CourseClass(
                        id=course_class_cid,
                        class_nbr=class_nbr,
                        section=section,
                        size=class_info["size"],
                        enrolled=class_info["enrolled"],
                        available=class_info["available"],
                        component=component,
                        course_id=course_cid,
                    )
                    write_queue.put(db_course_class)

                    for meeting in class_info.get("meetings", []):
                        meeting_cid = get_short_hash(
                            f"{class_nbr}{meeting['dates']}{meeting['days']}{meeting['start_time']}{meeting['end_time']}{meeting['location']}{course_class_cid}"
                        )

                        db_meeting = Meetings(
                            id=meeting_cid,
                            dates=meeting["dates"],
                            days=meeting["days"],
                            start_time=meeting["start_time"],
                            end_time=meeting["end_time"],
                            location=meeting["location"],
                            course_class_id=course_class_cid,
                        )
                        write_queue.put(db_meeting)

        progress.update(subject_task, advance=1)

    except Exception as e:
        print(f"Error processing course {course['course_id']}: {e}")


def process_subject(subject, year, engine, progress, all_task, lock):
    """Process a single subject and insert data into the database."""
    try:
        code = subject["SUBJECT"]
        description = subject.get("DESCR", "")
        subject_task = progress.add_task(f"[cyan]{code}", total=None)

        # Subject Custom ID
        subject_cid = get_short_hash(f"{code}{description}")

        # Open a new session just before inserting data
        session = Session(bind=engine)

        # Insert subject into the queue
        db_subject = Subject(id=subject_cid, subject_code=code, description=description)
        session.close()
        write_queue.put(db_subject)

        courses = data_parser.get_course_ids(code, year)
        progress.update(subject_task, total=len(courses["courses"]))

        # Process each course concurrently
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = []
            for course in courses["courses"]:
                future = executor.submit(
                    process_course,
                    course,
                    year,
                    subject,
                    engine,
                    progress,
                    subject_task,
                    lock,
                )
                futures.append(future)

            # Wait for all threads to complete
            for future in as_completed(futures):
                future.result()

        progress.update(subject_task, advance=1)
        progress.update(all_task, advance=1)

    except Exception as e:
        print(f"Error processing subject {subject['SUBJECT']}: {e}")


def main():
    """Scrape data from the API and store it in a local database"""

    # Run proxy fetching and testing
    fetch_proxies.main()

    # If db already exists, delete it
    if os.path.exists("src/dev.sqlite3"):
        os.remove("src/dev.sqlite3")

    engine = create_engine(
        "sqlite:///src/dev.sqlite3",
        pool_size=1000,  # Increase the pool size to allow for more connections
        max_overflow=1000,  # Allow overflow connections
        pool_timeout=30,  # Set the pool timeout to 30 seconds
    )
    Base.metadata.create_all(engine)
    Session.configure(bind=engine)

    year = dotenv_values().get("YEAR")

    # Create lock for thread-safe operations
    lock = Lock()

    # Start DB writer thread
    writer_thread = Thread(target=db_writer, args=(engine,))
    writer_thread.start()

    with Progress() as progress:
        subjects = data_parser.get_subjects(year)

        all_task = progress.add_task(
            "[cyan bold]All Courses", total=len(subjects["subjects"])
        )

        # Create a thread pool with multiple threads
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = []
            for subject in subjects["subjects"]:
                future = executor.submit(
                    process_subject, subject, year, engine, progress, all_task, lock
                )
                futures.append(future)

            # Wait for all threads to complete
            for future in as_completed(futures):
                future.result()

    # Signal DB writer to stop and wait
    write_queue.put(None)
    writer_thread.join()


if __name__ == "__main__":
    main()
