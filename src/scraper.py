import argparse
import asyncio
import atexit
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import shake_256
from queue import Queue
from threading import Lock, Thread

import httpx

try:
    import h2  # noqa: F401

    HAS_H2 = True
except ImportError:
    HAS_H2 = False
from curl_cffi import requests
from rich.progress import Progress
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import data_fetcher
import data_parser
import fetch_proxies
from db_service import (
    bulk_replace_classes_for_year,
    compute_course_hash,
    configure_fast_sqlite,
    ensure_healthy_database,
    ensure_schema_up_to_date,
    finalize_sqlite_for_s3,
    get_existing_class_count_for_year,
    get_existing_course_hashes,
    get_existing_course_ids_for_year,
    get_previous_years_course_data,
    reset_legacy_database,
)
from log import logger
from models import (
    Assessment,
    Course,
    CourseClass,
    LearningOutcome,
    Meetings,
    Subject,
)
from s3_service import S3SyncManager
from term_utils import get_term_code
from year_discovery import YearDiscoveryService

# Session and write queue for DB writer thread
Session = sessionmaker()
write_queue = Queue()


def get_short_hash(content: str, even_length=12) -> str:
    """Generates a short hash from the given content using the shake_256 algorithm."""
    return shake_256(content.encode("utf8")).hexdigest(even_length // 2)


def _model_to_dict(model):
    """Convert an ORM model instance to a dict of column names and values."""
    return {col.name: getattr(model, col.name, None) for col in model.__table__.columns}


def db_writer(engine, batch_size: int = 500):
    """Dedicated DB writer thread to serialize all DB operations, batching commits for high performance."""
    pending_items = []
    while True:
        try:
            try:
                item = write_queue.get(timeout=1.0)
            except Exception:
                item = "TIMEOUT"

            if item == "TIMEOUT":
                if pending_items:
                    _flush_batch(engine, pending_items)
                    pending_items.clear()
                continue

            if item is None:
                if pending_items:
                    _flush_batch(engine, pending_items)
                break

            pending_items.append(item)
            if len(pending_items) >= batch_size:
                _flush_batch(engine, pending_items)
                pending_items.clear()
        except Exception as e:
            logger.error(f"[DB WRITER LOOP ERROR] {e}")


def _flush_batch(engine, items: list):
    """Flush a batch of items (ORM models or raw dict operations) to the database with high-speed bulk inserts."""
    if not items:
        return
    session = Session(bind=engine)
    try:
        classes = []
        courses = []
        outcomes = []
        assessments = []
        subjects = []
        meetings = []
        fallbacks = []

        for item in items:
            if isinstance(item, tuple) and item[0] == "upsert_class":
                classes.append(item[1])
            elif isinstance(item, Course):
                courses.append(_model_to_dict(item))
            elif isinstance(item, LearningOutcome):
                outcomes.append(_model_to_dict(item))
            elif isinstance(item, Assessment):
                assessments.append(_model_to_dict(item))
            elif isinstance(item, Subject):
                subjects.append(_model_to_dict(item))
            elif isinstance(item, Meetings):
                meetings.append(_model_to_dict(item))
            else:
                fallbacks.append(item)

        # Bulk upsert subjects
        if subjects:
            stmt = sqlite_insert(Subject).values(subjects)
            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
            session.execute(stmt)

        # Bulk upsert courses
        if courses:
            stmt = sqlite_insert(Course).values(courses)
            update_cols = {
                col.name: getattr(stmt.excluded, col.name)
                for col in Course.__table__.columns
                if col.name != "id"
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_=update_cols,
            )
            session.execute(stmt)

        # Bulk insert learning outcomes
        if outcomes:
            stmt = sqlite_insert(LearningOutcome).values(outcomes)
            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
            session.execute(stmt)

        # Bulk insert assessments
        if assessments:
            stmt = sqlite_insert(Assessment).values(assessments)
            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
            session.execute(stmt)

        # Bulk upsert classes (before meetings so foreign key exists)
        if classes:
            dedup = {}
            for c in classes:
                key = (
                    c["year"],
                    c["course_id"],
                    str(c.get("class_nbr")),
                    str(c.get("section")),
                    str(c.get("group", "") or ""),
                )
                dedup[key] = c
            clean_classes = list(dedup.values())
            stmt = sqlite_insert(CourseClass).values(clean_classes)
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    "year",
                    "course_id",
                    "class_nbr",
                    "section",
                    "group",
                ],
                set_={
                    "size": stmt.excluded.size,
                    "available": stmt.excluded.available,
                    "component": stmt.excluded.component,
                },
            )
            session.execute(stmt)

        # Bulk insert meetings (after classes exist)
        if meetings:
            dedup_m = {m["id"]: m for m in meetings}
            stmt = sqlite_insert(Meetings).values(list(dedup_m.values()))
            stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
            session.execute(stmt)

        # Any fallback items
        for item in fallbacks:
            session.merge(item)

        session.commit()
    except Exception as e:
        session.rollback()
        logger.warning(f"[DB BULK BATCH ERROR, falling back to merge] {e}")
        try:
            for item in items:
                if isinstance(item, tuple) and item[0] == "upsert_class":
                    stmt = sqlite_insert(CourseClass).values(item[1])
                    stmt = stmt.on_conflict_do_update(
                        index_elements=[
                            "year",
                            "course_id",
                            "class_nbr",
                            "section",
                            "group",
                        ],
                        set_={
                            "size": stmt.excluded.size,
                            "available": stmt.excluded.available,
                            "component": stmt.excluded.component,
                        },
                    )
                    session.execute(stmt)
                else:
                    session.merge(item)
            session.commit()
        except Exception as fallback_e:
            session.rollback()
            logger.error(f"[DB FALLBACK BATCH ERROR] {fallback_e}")
    finally:
        session.close()


def join_str_if_iterable(value):
    """Return a comma-separated string if value is a list/tuple, otherwise return the value as str or empty string for None."""
    if isinstance(value, (list, tuple)):
        return ",".join([str(x) for x in value])
    if value is None:
        return ""
    return str(value)


def parse_units(value, default: int = 6) -> int:
    """Parse a scraped unit value into an int.

    The scraped text can contain non-digit characters (e.g. "3 units"
    instead of a bare "3"), so only the leading run of digits is used.
    Falls back to `default` if `value` is missing or contains no digits.
    """
    if value is None:
        return default
    match = re.search(r"\d+", str(value))
    if not match:
        return default
    return int(match.group())


def _fetch_single_outline_candidate(outline_url: str) -> tuple[str, str] | None:
    """Fetch and check a single outline candidate URL. Returns (outline_url, html) if valid, else None."""
    try:
        fetcher = data_fetcher.DataFetcher(
            "", full_url=outline_url, use_class_url=True, timeout=6.0
        )
        parsed_res = fetcher.get()
        resp_html = ""
        if parsed_res and isinstance(parsed_res, dict):
            resp_html = parsed_res.get("html", "")
        elif fetcher.last_response and fetcher.last_response.status_code == 200:
            resp_html = fetcher.last_response.text

        if not resp_html:
            proxy_dict = fetcher.get_random_proxy()
            cb_url = (
                f"{outline_url}&_cb={int(time.time() * 1000)}"
                if "?" in outline_url
                else f"{outline_url}?_cb={int(time.time() * 1000)}"
            )
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
                "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            }
            resp = requests.get(
                cb_url,
                proxies=proxy_dict,
                headers=headers,
                timeout=6,
                impersonate="chrome146",
            )
            if resp.status_code == 200:
                resp_html = resp.text

        if resp_html:
            text = resp_html.lower()
            if "course overview" in text or "subject area" in text:
                return (outline_url, resp_html)
    except Exception as e:
        logger.debug(f"Failed check for {outline_url}: {e}")
    return None


def _extract_and_queue_classes(
    course_code,
    year: int,
    course_cid: str,
    raw_html: str | None,
    collected_classes: list[dict] | None,
    collected_meetings: list[dict] | None,
    lock: Lock,
):
    """Harvest classes and meetings for a course and either append to batch lists or put to write_queue."""
    class_list = data_parser.get_course_class_list(
        course_code, year=year, raw_html=raw_html
    )
    class_items = class_list.get("classes", []) if isinstance(class_list, dict) else []

    for individual_class in class_items:
        class_type = individual_class.get("component")
        class_nbr = individual_class.get("class_number")
        section = individual_class.get("section")
        group_name = individual_class.get("group")
        class_cid = get_short_hash(
            f"{course_cid}{class_type}{class_nbr}{section}{group_name or ''}"
        )
        try:
            class_data = {
                "id": class_cid,
                "year": year,
                "class_nbr": class_nbr,
                "section": section,
                "size": int(individual_class.get("size", 0)),
                "available": int(individual_class.get("available", 0)),
                "component": class_type,
                "group": group_name,
                "course_id": course_cid,
            }
            if collected_classes is not None:
                with lock:
                    collected_classes.append(class_data)
            # Always stream class into write_queue for real-time SQLite persistence
            write_queue.put(("upsert_class", class_data))
        except Exception as e:
            logger.error(f"Error preparing class for course {course_code}: {e}")

        meetings = individual_class.get("meetings", [])
        for meeting in meetings:
            try:
                meeting_cid = get_short_hash(
                    f"{class_cid}{meeting.get('dates')}{meeting.get('days')}{meeting.get('time')}{meeting.get('campus')}{meeting.get('location')}"
                )
                time_str = meeting.get("time", "")
                start_time = time_str.split("-")[0].strip() if "-" in time_str else ""
                end_time = time_str.split("-")[1].strip() if "-" in time_str else ""
                meeting_data = {
                    "id": meeting_cid,
                    "year": year,
                    "dates": meeting.get("dates", ""),
                    "days": meeting.get("days", ""),
                    "start_time": start_time,
                    "end_time": end_time,
                    "campus": meeting.get("campus", ""),
                    "location": meeting.get("location", ""),
                    "instructor": meeting.get("instructor"),
                    "course_class_id": class_cid,
                }
                if collected_meetings is not None:
                    with lock:
                        collected_meetings.append(meeting_data)
                # Always stream meeting into write_queue for real-time SQLite persistence
                db_meeting = Meetings(**meeting_data)
                write_queue.put(db_meeting)
            except Exception as e:
                logger.error(
                    f"Error inserting meeting for class {class_nbr} of course {course_code}: {e}"
                )


def process_course(
    course,
    year: int,
    subject,
    engine,
    progress,
    subject_task,
    lock,
    scrape_classes: bool = True,
    existing_hashes: dict[tuple[str, int], str] | None = None,
    existing_courses_for_year: dict[str, str] | None = None,
    previous_courses: dict[str, dict] | None = None,
    collected_classes: list[dict] | None = None,
    collected_meetings: list[dict] | None = None,
):
    """Process a single course and insert/upsert data into the database directly.

    1. If the course already exists in DB for this year, skip outline scraping.
    2. If the course exists in previous years, reuse static course info, outcomes,
       and assessments without making redundant network crawls.
    3. Only new courses not in any previous year are fetched from the web.
    """
    try:
        course_code = course.get("code")
        if not course_code:
            print(f"Skipping course with missing code: {course}")
            progress.update(subject_task, advance=1)
            return

        code_str = (
            course_code[0] if isinstance(course_code, (list, tuple)) else course_code
        )
        norm_code = str(code_str).replace(" ", "").upper() if code_str else ""

        # Check if this course is already present in DB for this exact year
        existing_cid = None
        if existing_courses_for_year:
            existing_cid = existing_courses_for_year.get(
                code_str
            ) or existing_courses_for_year.get(norm_code)

        if existing_cid:
            logger.debug(
                f"Course {code_str} already in DB for year {year} (id: {existing_cid}). Skipping course outline fetch."
            )
            course_cid = existing_cid

            if scrape_classes:
                _extract_and_queue_classes(
                    code_str,
                    year,
                    course_cid,
                    raw_html=None,
                    collected_classes=collected_classes,
                    collected_meetings=collected_meetings,
                    lock=lock,
                )
            progress.update(subject_task, advance=1)
            return

        # Fetch course details for this year from the web
        course_details = data_parser.get_course_details(course_code, year=year)
        if not course_details:
            logger.info(
                f"Course {course_code} is not offered for year {year}. Skipping course."
            )
            progress.update(subject_task, advance=1)
            return

        name = subject["subject"] if isinstance(subject, dict) else str(subject)
        title = course_details.get("title", "")
        terms = course.get("terms") or course_details.get("terms")
        campus = course_details.get("campus")

        # Course Custom ID
        course_cid = get_short_hash(f"{name}{code_str}{title}{year}{terms}{campus}")
        encoded_course_code = re.sub(
            r"([a-zA-Z]+)([0-9]+)", r"\1-\2", str(code_str)
        ).lower()

        # Compute content hash for the course outline
        outline_hash = compute_course_hash(
            {
                "title": title,
                "units": parse_units(course_details.get("units")),
                "course_overview": course_details.get("course_overview", "N/A"),
                "prerequisites": course_details.get("prerequisites", "N/A"),
                "corequisites": course_details.get("corequisites", "N/A"),
                "antirequisites": course_details.get("antirequisites", "N/A"),
                "course_coordinator": course_details.get("course_coordinator", "N/A"),
            }
        )

        # Check if previous year data exists
        prev_data = None
        if previous_courses:
            prev_data = previous_courses.get(code_str) or previous_courses.get(
                norm_code
            )

        prev_hash = None
        if prev_data:
            prev_hash = prev_data.get("content_hash")
            if not prev_hash:
                prev_hash = compute_course_hash(prev_data)

        # If hash matches previous year, reuse previous year outline and assessments
        if prev_data and prev_hash == outline_hash:
            logger.debug(
                f"Course {code_str} outline hash matches previous year ({prev_data.get('year')}). Reusing outline & assessments."
            )
            db_course = Course(
                id=course_cid,
                course_id=course_details.get("course_id")
                or prev_data.get("course_id", 0),
                year=year,
                terms=join_str_if_iterable(terms),
                subject=name,
                course_code=code_str,
                title=title,
                campus=join_str_if_iterable(campus),
                level_of_study=course_details.get("level_of_study")
                or prev_data.get("level_of_study", "N/A"),
                units=parse_units(course_details.get("units")),
                course_coordinator=course_details.get("course_coordinator")
                or prev_data.get("course_coordinator", "N/A"),
                course_level=course_details.get("course_level")
                or prev_data.get("course_level", "N/A"),
                course_overview=course_details.get("course_overview")
                or prev_data.get("course_overview", "N/A"),
                prerequisites=course_details.get("prerequisites")
                or prev_data.get("prerequisites", "N/A"),
                corequisites=course_details.get("corequisites")
                or prev_data.get("corequisites", "N/A"),
                antirequisites=course_details.get("antirequisites")
                or prev_data.get("antirequisites", "N/A"),
                university_wide_elective=course_details.get(
                    "university_wide_elective", False
                ),
                url=f"https://adelaide.edu.au/study/courses/{year}/{encoded_course_code}/",
                course_outline_url=prev_data.get("course_outline_url"),
                textbooks=prev_data.get("textbooks"),
                content_hash=outline_hash,
            )

            # Replicate learning outcomes for new year
            for lo in prev_data.get("learning_outcomes", []):
                lo_desc = lo.get("description", "")
                lo_id = get_short_hash(f"{course_cid}lo{lo_desc}")
                db_lo = LearningOutcome(
                    id=lo_id,
                    year=year,
                    course_id=course_cid,
                    description=lo_desc,
                    outcome_index=lo.get("outcome_index", 1),
                )
                write_queue.put(db_lo)

            # Replicate assessments for new year
            for assess in prev_data.get("assessments", []):
                assess_title = assess.get("title", "")
                assess_id = get_short_hash(f"{course_cid}assess{assess_title}")
                db_assess = Assessment(
                    id=assess_id,
                    year=year,
                    course_id=course_cid,
                    title=assess_title,
                    weighting=assess.get("weighting"),
                    hurdle=assess.get("hurdle"),
                    learning_outcomes=assess.get("learning_outcomes"),
                )
                write_queue.put(db_assess)

            write_queue.put(db_course)

        else:
            # Hash is different or brand new course: scrape fresh outline & assessments
            if prev_data:
                logger.info(
                    f"Course {code_str} outline changed from previous year (hash delta). Scraping fresh outline for year {year}..."
                )
            else:
                logger.info(
                    f"Course {code_str} is new for year {year}. Scraping fresh outline..."
                )

            db_course = Course(
                id=course_cid,
                course_id=course_details.get("course_id", 0),
                year=year,
                terms=join_str_if_iterable(terms),
                subject=name,
                course_code=code_str,
                title=title,
                campus=join_str_if_iterable(campus),
                level_of_study=course_details.get("level_of_study", "N/A"),
                units=parse_units(course_details.get("units")),
                course_coordinator=course_details.get("course_coordinator", "N/A"),
                course_level=course_details.get("course_level", "N/A"),
                course_overview=course_details.get("course_overview", "N/A"),
                prerequisites=course_details.get("prerequisites", "N/A"),
                corequisites=course_details.get("corequisites", "N/A"),
                antirequisites=course_details.get("antirequisites", "N/A"),
                university_wide_elective=course_details.get(
                    "university_wide_elective", False
                ),
                url=f"https://adelaide.edu.au/study/courses/{year}/{encoded_course_code}/",
                course_outline_url=None,
                content_hash=outline_hash,
            )

            # Generate course outline URL if term and year are valid
            term_str = join_str_if_iterable(terms)
            term_code = get_term_code(term_str)

            if not term_code and term_str:
                first_term = term_str.split(",")[0].strip()
                term_code = get_term_code(first_term)

            if term_code:
                year_short = str(year)[-2:]
                formatted_code = re.sub(
                    r"([a-zA-Z]+)\s*(\d+)", r"\1_\2", str(code_str)
                ).replace(" ", "_")

                candidates = [
                    (
                        suffix,
                        f"https://apps.adelaide.edu.au/public/courseoutline?courseInstanceId={year_short}{term_code}_{formatted_code}_{suffix}",
                    )
                    for suffix in range(1, 4)
                ]

                found_match = None
                with ThreadPoolExecutor(max_workers=3) as outline_executor:
                    future_to_suffix = {
                        outline_executor.submit(
                            _fetch_single_outline_candidate, url
                        ): suffix
                        for suffix, url in candidates
                    }
                    results = {}
                    for future in as_completed(future_to_suffix):
                        suffix = future_to_suffix[future]
                        res = future.result()
                        if res:
                            results[suffix] = res

                    for suffix in range(1, 4):
                        if suffix in results:
                            found_match = results[suffix]
                            break

                if found_match:
                    outline_url, resp_html = found_match
                    db_course.course_outline_url = outline_url
                    parsed_outline = data_parser.parse_course_outline(resp_html)

                    if parsed_outline.get("aim"):
                        db_course.course_overview = parsed_outline["aim"]

                    for index, lo_text in enumerate(
                        parsed_outline.get("learning_outcomes", []),
                        start=1,
                    ):
                        lo_id = get_short_hash(f"{course_cid}lo{lo_text}")
                        db_lo = LearningOutcome(
                            id=lo_id,
                            year=year,
                            course_id=course_cid,
                            description=lo_text,
                            outcome_index=index,
                        )
                        write_queue.put(db_lo)

                    db_course.textbooks = parsed_outline.get("textbooks")

                    for assess in parsed_outline.get("assessments", []):
                        assess_title = assess.get("title")
                        assess_id = get_short_hash(f"{course_cid}assess{assess_title}")
                        db_assess = Assessment(
                            id=assess_id,
                            year=year,
                            course_id=course_cid,
                            title=assess_title,
                            weighting=assess.get("weighting"),
                            hurdle=assess.get("hurdle"),
                            learning_outcomes=assess.get("learning_outcomes"),
                        )
                        write_queue.put(db_assess)

            write_queue.put(db_course)

        # Track in existing_courses_for_year so duplicates in same run are skipped
        if existing_courses_for_year is not None:
            with lock:
                existing_courses_for_year[code_str] = course_cid
                existing_courses_for_year[norm_code] = course_cid

        # Scrape classes ONLY if scrape_classes is True (active year)
        # Pass raw_html from course_details so 0 extra network calls are made for classes!
        if scrape_classes:
            _extract_and_queue_classes(
                code_str,
                year,
                course_cid,
                raw_html=course_details.get("html"),
                collected_classes=collected_classes,
                collected_meetings=collected_meetings,
                lock=lock,
            )

        progress.update(subject_task, advance=1)

    except Exception as e:
        print(f"Error processing course {course.get('code')}: {e}")


def process_subject(
    subject,
    year: int,
    engine,
    progress,
    all_task,
    lock,
    scrape_classes: bool = True,
    existing_hashes: dict[tuple[str, int], str] | None = None,
    existing_courses_for_year: dict[str, str] | None = None,
    previous_courses: dict[str, dict] | None = None,
    collected_classes: list[dict] | None = None,
    collected_meetings: list[dict] | None = None,
    course_executor: ThreadPoolExecutor | None = None,
    course_workers: int = 24,
):
    """Process a single subject and insert data into the database."""
    try:
        name = subject["subject"]
        subject_task = progress.add_task(f"[cyan]{name}", total=None)

        # Subject Custom ID
        subject_cid = get_short_hash(f"{name}")

        # Insert subject into the queue
        db_subject = Subject(id=subject_cid, name=name)
        write_queue.put(db_subject)

        # Encode & in subject name
        encoded_name = name.replace("&", "%26")
        course_list = None
        for attempt in range(5):
            courses = data_parser.get_course_codes(encoded_name, year)
            if (
                courses is not None
                and isinstance(courses, dict)
                and "courses" in courses
            ):
                course_list = courses["courses"]
                break
            logger.warning(
                f"Subject '{name}' ({year}) course fetch attempt {attempt + 1}/5 failed or blocked by WAF. Retrying with alternate proxy..."
            )
            time.sleep(0.5 + attempt * 0.3)

        if course_list is None:
            logger.error(
                f"Subject '{name}' ({year}) could not be retrieved after 5 attempts. Skipping."
            )
            course_list = []

        progress.update(subject_task, total=len(course_list))

        # Process each course concurrently
        executor_to_use = course_executor
        managed_executor = None
        if executor_to_use is None:
            managed_executor = ThreadPoolExecutor(
                max_workers=course_workers, thread_name_prefix="course_worker"
            )
            executor_to_use = managed_executor

        try:
            futures = []
            for course in course_list:
                future = executor_to_use.submit(
                    process_course,
                    course,
                    year,
                    subject,
                    engine,
                    progress,
                    subject_task,
                    lock,
                    scrape_classes,
                    existing_hashes,
                    existing_courses_for_year,
                    previous_courses,
                    collected_classes,
                    collected_meetings,
                )
                futures.append(future)

            # Wait for all threads to complete
            for future in as_completed(futures):
                future.result()
        finally:
            if managed_executor is not None:
                managed_executor.shutdown(wait=True)

        progress.update(subject_task, advance=1)
        progress.update(all_task, advance=1)

    except Exception as e:
        print(f"Error processing subject {subject['subject']}: {e}")


def scrape_single_year(
    year: int,
    engine,
    is_active: bool,
    existing_hashes: dict[tuple[str, int], str] | None = None,
    scrape_classes_override: bool | None = None,
):
    """Scrape data directly into SQLite for a given academic year.

    Preserves existing courses in DB to prevent redundant course outline re-scraping.
    For active or unpopulated years: harvests classes and meetings.
    For static/future years with classes already present: scrapes courses only if missing from DB.
    """
    logger.info(f"=== Scraping Academic Year {year} (Active: {is_active}) ===")

    lock = Lock()
    collected_classes: list[dict] = []
    collected_meetings: list[dict] = []

    # Preload existing course identifiers for this year and reusable previous years
    check_session = Session(bind=engine)
    try:
        existing_courses_for_year = get_existing_course_ids_for_year(
            check_session, year
        )
        existing_class_count = get_existing_class_count_for_year(check_session, year)
        logger.info(
            f"Preloaded {len(existing_courses_for_year)} existing course records and "
            f"{existing_class_count} class records for year {year} from DB."
        )
        previous_courses = get_previous_years_course_data(check_session, year)
        logger.info(
            f"Preloaded {len(previous_courses)} reusable course records from previous years."
        )
    finally:
        check_session.close()

    # Harvest classes if active year, or if year has 0 classes in DB (e.g. initial clean scrape)
    should_scrape_classes = (
        scrape_classes_override
        if scrape_classes_override is not None
        else (is_active or existing_class_count == 0)
    )

    subject_workers = int(os.environ.get("SUBJECT_WORKERS", "8"))
    course_workers = int(os.environ.get("COURSE_WORKERS", "24"))

    try:
        with Progress() as progress:
            subj_list = []
            for attempt in range(4):
                subjects = data_parser.get_subjects(year)
                subj_list = subjects.get("subjects", [])
                if subj_list:
                    break
                logger.warning(
                    f"Subject discovery attempt {attempt + 1}/4 for year {year} returned 0 subjects. Retrying..."
                )
                time.sleep(1.0 + attempt * 1.0)

            if not subj_list:
                logger.error(
                    f"Could not retrieve subjects for year {year}. Skipping scrape for {year}."
                )
                return

            all_task = progress.add_task(
                f"[cyan bold]{year} Courses", total=len(subj_list)
            )

            # Create shared course worker pool and subject worker pool for high parallelism without GIL thrashing
            with ThreadPoolExecutor(
                max_workers=course_workers, thread_name_prefix="course_worker"
            ) as course_executor:
                with ThreadPoolExecutor(
                    max_workers=subject_workers, thread_name_prefix="subject_worker"
                ) as executor:
                    futures = []
                    for subject in subj_list:
                        future = executor.submit(
                            process_subject,
                            subject,
                            year,
                            engine,
                            progress,
                            all_task,
                            lock,
                            should_scrape_classes,
                            existing_hashes,
                            existing_courses_for_year,
                            previous_courses,
                            collected_classes if should_scrape_classes else None,
                            collected_meetings if should_scrape_classes else None,
                            course_executor=course_executor,
                            course_workers=course_workers,
                        )
                        futures.append(future)

                    # Wait for all threads to complete
                    for future in as_completed(futures):
                        future.result()

    finally:
        # Bulk atomic replace for active/harvested classes and meetings (persists even if interrupted)
        if should_scrape_classes and collected_classes:
            if existing_class_count > 1000 and len(collected_classes) < (
                existing_class_count * 0.3
            ):
                logger.error(
                    f"SAFETY GUARD: Harvested only {len(collected_classes)} classes for year {year}, "
                    f"but DB already has {existing_class_count}. Aborting bulk replace to prevent data loss."
                )
            else:
                logger.info(
                    f"Atomic bulk replacing {len(collected_classes)} classes and "
                    f"{len(collected_meetings)} meetings for year {year}..."
                )
                db_session = Session(bind=engine)
                try:
                    bulk_replace_classes_for_year(
                        db_session,
                        year,
                        collected_classes,
                        collected_meetings,
                    )
                finally:
                    db_session.close()
        else:
            if not should_scrape_classes:
                logger.info(
                    f"Year {year} already has {existing_class_count} classes in DB: skipped class harvesting."
                )
            else:
                logger.info(f"Year {year} has no classes published yet.")

    logger.info(f"{year} scraping completed directly to SQLite database.")


def ensure_proxies_ready(min_proxies: int = 20):
    """Ensure that a valid working_proxies file exists with at least min_proxies."""
    candidate_files = [
        "src/working_proxies.txt",
        "working_proxies.txt",
        os.path.join(os.path.dirname(__file__), "working_proxies.txt"),
    ]
    valid_count = 0
    for path in candidate_files:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    lines = [line.strip() for line in f if line.strip()]
                    valid_count = max(valid_count, len(lines))
            except Exception:
                pass
    if valid_count < min_proxies:
        logger.info(
            f"Found {valid_count} working proxies (< {min_proxies} required). "
            f"Testing and fetching fresh proxies..."
        )
        try:
            fetch_proxies.main()
            with data_fetcher.DataFetcher._proxy_lock:
                data_fetcher.DataFetcher._proxies = None
                data_fetcher.DataFetcher._proxy_failures.clear()
        except Exception as e:
            logger.warning(f"Proxy discovery failed: {e}")


async def create_scraper_client() -> httpx.AsyncClient:
    """Creates persistent httpx client with connection pooling and optional HTTP/2."""
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)
    timeout = httpx.Timeout(20.0, connect=10.0)
    return httpx.AsyncClient(limits=limits, timeout=timeout, http2=HAS_H2)


async def run_pipeline(
    force_all_years: bool = False,
    db_path: str = "src/dev.sqlite3",
    active_year: int | None = None,
):
    """Orchestrates dynamic discovery, probe, selective direct-to-DB scraping, and direct S3 sync."""
    # Register an atexit hook to ensure SQLite WAL is cleanly flushed even on abrupt process exits
    atexit.register(finalize_sqlite_for_s3, db_path)

    # Ensure local database file integrity (repair if previous run crashed/malformed)
    ensure_healthy_database(db_path)

    s3_manager = S3SyncManager()

    # Ensure sufficient working proxies are available before scraping
    ensure_proxies_ready()

    # Pre-flight S3 Download
    db_downloaded = s3_manager.download_database(db_path)
    if db_downloaded:
        logger.info(f"Loaded existing database from S3 into {db_path}")

    # If database uses old single-year format, reset so it starts clean with modern schema
    if reset_legacy_database(db_path):
        logger.info(
            f"Reset legacy database at {db_path} to build clean multi-year schema from scratch."
        )

    existing_years = S3SyncManager.get_synced_years_from_db(db_path)
    logger.info(f"Years already populated in DB: {existing_years}")

    # Discover available academic years and probe active year via persistent connection pool
    async with await create_scraper_client() as http_client:
        available_years = YearDiscoveryService.discover_available_years()
        logger.info(f"Discovered available academic years: {available_years}")

        active_year = await YearDiscoveryService.determine_active_year(
            http_client, available_years, active_year_override=active_year
        )
        # Guarantee target multi-year window (2026-2028) is always present
        for y in [2026, 2027, 2028]:
            if y not in available_years:
                available_years.append(y)
        if active_year not in available_years:
            available_years.append(active_year)
        available_years.sort()

        logger.info(
            f"Available years: {available_years} | Active year (with classes): {active_year}"
        )

    # Initialize SQLite database engine with NullPool and 60s timeout to prevent locking conflicts
    engine = create_engine(
        f"sqlite:///{db_path}",
        poolclass=NullPool,
        connect_args={"timeout": 60.0},
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        configure_fast_sqlite(dbapi_connection)

    ensure_schema_up_to_date(engine)
    Session.configure(bind=engine)

    # Preload existing content hashes to skip duplicate course outline updates
    check_session = Session(bind=engine)
    try:
        existing_hashes = get_existing_course_hashes(check_session)
        logger.info(
            f"Preloaded {len(existing_hashes)} course outline hashes for delta detection."
        )
    finally:
        check_session.close()

    # Start DB writer thread
    writer_thread = Thread(target=db_writer, args=(engine,))
    writer_thread.start()

    try:
        # Selective scraping directly into DB
        for yr in available_years:
            is_active = yr == active_year

            # --- SKIP CHECK ---
            # If it's not the active year and we already have it in DB, skip with ZERO network calls
            if not is_active and yr in existing_years and not force_all_years:
                reason = (
                    "historical/past year" if yr < active_year else "static future year"
                )
                logger.info(f"Skipping year {yr} ({reason}): already present in DB.")
                continue

            # --- SCRAPE EXECUTION ---
            if is_active:
                if yr in existing_years and not force_all_years:
                    logger.info(
                        f"Active year {yr} courses already in DB. "
                        f"Harvesting live class availability only (skipping course outline rescrapes)..."
                    )
                else:
                    logger.info(
                        f"Scraping ACTIVE year {yr} (courses + class availability)..."
                    )
                scrape_single_year(
                    yr,
                    engine,
                    is_active=True,
                    existing_hashes=existing_hashes,
                )
            else:
                tag = "historical" if yr < active_year else "future"
                logger.info(
                    f"Scraping {tag} year {yr} (courses + class harvesting if unpopulated)..."
                )
                scrape_single_year(
                    yr,
                    engine,
                    is_active=False,
                    existing_hashes=existing_hashes,
                )

    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.warning(
            "Scraper received interrupt signal. Initiating clean DB shutdown..."
        )
    finally:
        logger.info("Draining write queue and closing database...")
        write_queue.put(None)
        writer_thread.join(timeout=30)
        try:
            engine.dispose()
        except Exception as e:
            logger.debug(f"Engine disposal: {e}")
        finalize_sqlite_for_s3(db_path)

    # Upload updated database directly to S3
    s3_manager.upload_database(db_path)
    logger.info("Pipeline finished successfully.")


def main():
    parser = argparse.ArgumentParser(
        description="Multi-year direct SQLite scraper for Adelaide University courses"
    )
    parser.add_argument(
        "--year",
        "--active-year",
        dest="active_year",
        type=int,
        default=None,
        help="Explicit active academic year to scrape classes for (default: auto-detected from env or web probe)",
    )
    parser.add_argument(
        "--force-all-years",
        action="store_true",
        help="Force scraping all available years, ignoring cached/S3 state",
    )
    parser.add_argument(
        "--db-path",
        default="src/dev.sqlite3",
        help="Path to SQLite database file",
    )
    parser.add_argument(
        "--course-workers",
        type=int,
        default=None,
        help="Number of concurrent workers for course processing (default: 24)",
    )
    parser.add_argument(
        "--subject-workers",
        type=int,
        default=None,
        help="Number of concurrent workers for subject discovery (default: 8)",
    )
    args = parser.parse_args()

    if args.course_workers:
        os.environ["COURSE_WORKERS"] = str(args.course_workers)
    if args.subject_workers:
        os.environ["SUBJECT_WORKERS"] = str(args.subject_workers)

    asyncio.run(
        run_pipeline(
            force_all_years=args.force_all_years,
            db_path=args.db_path,
            active_year=args.active_year,
        )
    )


if __name__ == "__main__":
    main()
