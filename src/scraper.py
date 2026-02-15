import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import shake_256
from queue import Queue
from threading import Lock, Thread

import requests
from dotenv import dotenv_values
from rich.progress import Progress
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import data_parser
import fetch_proxies
from log import logger
from models import (
    Assessment,
    Base,
    Course,
    CourseClass,
    LearningOutcome,
    Meetings,
    Subject,
)
from term_utils import get_term_code

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
        course_code = course.get("code")
        if not course_code:
            print(f"Skipping course with missing code: {course}")
            progress.update(subject_task, advance=1)
            return
        course_details = data_parser.get_course_details(course_code)

        name = subject["subject"]
        title = course_details.get("title", "")
        terms = course.get("terms")
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
                course_code=course_code[0]
                if isinstance(course_code, (list, tuple))
                else course_code,
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
                university_wide_elective=course_details.get(
                    "university_wide_elective", False
                ),
                url="https://adelaideuni.edu.au/study/courses/" + encoded_course_code,
                course_outline_url=None,
            )

            # Generate course outline URL if term and year are valid
            # Format: https://apps.adelaide.edu.au/public/courseoutline?courseInstanceId={year_short}{term_code}_{subject}_{code}_2
            # Example: 2620_MATH_X311_2
            term_str = join_str_if_iterable(terms)
            term_code = get_term_code(term_str)

            if not term_code and term_str:
                # Try first term if multiple (e.g. "Semester 1, Semester 2")
                first_term = term_str.split(",")[0].strip()
                term_code = get_term_code(first_term)

            if term_code:
                year_short = str(year)[-2:]
                formatted_code = re.sub(r"([a-zA-Z]+)\s*(\d+)", r"\1_\2", str(code_str))
                formatted_code = formatted_code.replace(" ", "_")

                # Brute-force suffixes _1 to _6 to find the valid course outline
                found_valid_outline = False
                for suffix in range(1, 7):
                    course_instance_id = (
                        f"{year_short}{term_code}_{formatted_code}_{suffix}"
                    )
                    outline_url = f"https://apps.adelaide.edu.au/public/courseoutline?courseInstanceId={course_instance_id}"

                    try:
                        headers = {
                            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                        }
                        resp = requests.get(outline_url, headers=headers, timeout=5)

                        if resp.status_code == 200:
                            text = resp.text.lower()
                            if "course overview" in text or "subject area" in text:
                                logger.debug(
                                    f"Found valid course outline at {outline_url}"
                                )
                                db_course.course_outline_url = outline_url

                                parsed_outline = data_parser.parse_course_outline(
                                    resp.text
                                )

                                if parsed_outline.get("aim"):
                                    db_course.course_overview = parsed_outline["aim"]

                                # Populate Learning Outcomes
                                for index, lo_text in enumerate(
                                    parsed_outline.get("learning_outcomes", []), start=1
                                ):
                                    lo_id = get_short_hash(f"{course_cid}lo{lo_text}")
                                    db_lo = LearningOutcome(
                                        id=lo_id,
                                        course_id=course_cid,
                                        description=lo_text,
                                        outcome_index=index,
                                    )
                                    write_queue.put(db_lo)

                                db_course.textbooks = parsed_outline.get("textbooks")

                                # Populate Assessments
                                for assess in parsed_outline.get("assessments", []):
                                    assess_title = assess.get("title")
                                    assess_id = get_short_hash(
                                        f"{course_cid}assess{assess_title}"
                                    )
                                    db_assess = Assessment(
                                        id=assess_id,
                                        course_id=course_cid,
                                        title=assess_title,
                                        weighting=assess.get("weighting"),
                                        hurdle=assess.get("hurdle"),
                                        learning_outcomes=assess.get(
                                            "learning_outcomes"
                                        ),
                                    )
                                    write_queue.put(db_assess)

                                found_valid_outline = True
                                break
                    except Exception as e:
                        logger.debug(f"Failed check for {outline_url}: {e}")
                        pass

                if not found_valid_outline:
                    logger.debug(
                        f"No valid course outline found for {course_code} (suffixes 1-6)"
                    )

            write_queue.put(db_course)

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
                            time_str.split("-")[0].strip() if "-" in time_str else ""
                        )
                        end_time = (
                            time_str.split("-")[1].strip() if "-" in time_str else ""
                        )
                        db_meeting = Meetings(
                            id=meeting_cid,
                            dates=meeting.get("dates", ""),
                            days=meeting.get("days", ""),
                            start_time=start_time,
                            end_time=end_time,
                            campus=meeting.get("campus", ""),
                            location=meeting.get("location", ""),
                            instructor=meeting.get("instructor"),
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
        course_list = courses.get("courses", []) if isinstance(courses, dict) else []
        progress.update(subject_task, total=len(course_list))

        # Process each course concurrently
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = []
            for course in course_list:
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
