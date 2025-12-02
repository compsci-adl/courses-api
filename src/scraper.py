import os
import re
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
from log import logger
from models import Base, Course, CourseClass, Meetings, Subject

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


def join_str_if_iterable(value):
    """Return a comma-separated string if value is a list/tuple, otherwise return the value as str or empty string for None."""
    if isinstance(value, (list, tuple)):
        return ",".join([str(x) for x in value])
    if value is None:
        return ""
    return str(value)


def process_course(course, year, subject, engine, progress, subject_task, lock):
    """Process a single course and insert data into the database."""
    try:
        logger.debug(f"Processing course {course['code']}...")
        course_code = course["code"]
        course_details = data_parser.get_course_details(course_code)

        name = subject["subject"]
        title = course_details["title"]
        terms = course["terms"]
        campus = course_details["campus"]

        # Course Custom ID
        course_cid = get_short_hash(f"{name}{course_code}{title}{year}{terms}{campus}")

        # Encode course code to match URL format
        code_str = (
            course_code[0] if isinstance(course_code, (list, tuple)) else course_code
        )
        encoded_course_code = re.sub(
            r"([a-zA-Z]+)([0-9]+)", r"\1-\2", str(code_str)
        ).lower()

        try:
            db_course = Course(
                id=course_cid,
                course_id=course_details.get("course_id", 0),
                year=year,
                terms=join_str_if_iterable(terms),
                subject=name,
                course_code=course_code[0],
                title=title,
                campus=join_str_if_iterable(campus),
                level_of_study=course_details.get("level_of_study", "N/A"),
                units=int(course_details.get("unit_value", "6")),
                course_coordinator=course_details.get("course_coordinator", "N/A"),
                course_level=course_details.get("course_level", "N/A"),
                course_overview=course_details.get("course_overview", "N/A"),
                prerequisites=course_details.get("prerequisites", "N/A"),
                corequisites=course_details.get("corequisites", "N/A"),
                antirequisites=course_details.get("antirequisites", "N/A"),
                url="https://adelaideuni.edu.au/study/courses/" + encoded_course_code,
            )
            write_queue.put(db_course)
        except Exception as e:
            print(f"Error inserting course {course_code}: {e}")
            progress.update(subject_task, advance=1)
            return

        if terms:
            class_list = data_parser.get_course_class_list(course_code)
            class_items = (
                class_list.get("classes", []) if isinstance(class_list, dict) else []
            )

            for individual_class in class_items:
                class_type = individual_class.get("component")
                class_nbr = individual_class.get("class_number")
                section = individual_class.get("section")
                class_cid = get_short_hash(
                    f"{course_cid}{class_type}{class_nbr}{section}"
                )
                try:
                    db_course_class = CourseClass(
                        id=class_cid,
                        class_nbr=class_nbr,
                        section=section,
                        size=int(individual_class.get("size", 0)),
                        available=int(individual_class.get("available", 0)),
                        component=class_type,
                        course_id=course_cid,
                    )
                    write_queue.put(db_course_class)
                except Exception as e:
                    print(f"Error inserting class for course {course_code}: {e}")
                    print(individual_class)

                meetings = individual_class.get("meetings", [])
                for meeting in meetings:
                    try:
                        meeting_cid = get_short_hash(
                            f"{class_cid}{meeting.get('dates')}{meeting.get('days')}{meeting.get('time')}{meeting.get('campus')}{meeting.get('location')}"
                        )
                        # Extract start and end time from time string
                        time_str = meeting.get("time", "")
                        start_time = (
                            time_str.split("-")[0].strip() if "-" in time_str else "N/A"
                        )
                        end_time = (
                            time_str.split("-")[1].strip() if "-" in time_str else "N/A"
                        )
                        db_meeting = Meetings(
                            id=meeting_cid,
                            dates=meeting.get("dates", "N/A"),
                            days=meeting.get("days", "N/A"),
                            start_time=start_time,
                            end_time=end_time,
                            campus=meeting.get("campus", "N/A"),
                            location=meeting.get("location", "N/A"),
                            course_class_id=class_cid,
                        )
                        write_queue.put(db_meeting)
                    except Exception as e:
                        print(
                            f"Error inserting meeting for class {class_nbr} of course {course_code}: {e}"
                        )

        progress.update(subject_task, advance=1)

    except Exception as e:
        print(f"Error processing course {course['code']}: {e}")


def process_subject(subject, year, engine, progress, all_task, lock):
    """Process a single subject and insert data into the database."""
    try:
        name = subject["subject"]
        subject_task = progress.add_task(f"[cyan]{name}", total=None)

        # Subject Custom ID
        subject_cid = get_short_hash(f"{name}")

        # Open a new session just before inserting data
        session = Session(bind=engine)

        # Insert subject into the queue
        db_subject = Subject(id=subject_cid, name=name)
        session.close()
        write_queue.put(db_subject)

        # Encode & in subject name
        encoded_name = name.replace("&", "%26")
        courses = data_parser.get_course_codes(encoded_name, year)
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
        print(f"Error processing subject {subject['subject']}: {e}")


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

    year_str = dotenv_values().get("YEAR")
    if year_str is None:
        raise ValueError("YEAR environment variable is not set")
    year = int(year_str)

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
