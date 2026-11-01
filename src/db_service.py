import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

try:
    from .log import logger
    from .models import (
        Assessment,
        Base,
        Course,
        CourseClass,
        LearningOutcome,
        Meetings,
    )
except ImportError:
    from log import logger
    from models import (
        Assessment,
        Base,
        Course,
        CourseClass,
        LearningOutcome,
        Meetings,
    )


def compute_course_hash(course_dict: dict) -> str:
    """Computes a lightweight content hash of course outline payload.

    Ignores year-specific timestamps/links, only hashes main content fields.
    """
    core_content = {
        "title": course_dict.get("title", ""),
        "units": course_dict.get("units", 3),
        "desc": course_dict.get("course_overview", "")
        or course_dict.get("description", ""),
        "prereqs": course_dict.get("prerequisites", ""),
        "coreqs": course_dict.get("corequisites", ""),
        "antireqs": course_dict.get("antirequisites", ""),
        "coordinator": course_dict.get("course_coordinator", ""),
        "outcomes": course_dict.get("learning_outcomes", []),
        "assessments": course_dict.get("assessments", []),
    }
    raw = json.dumps(core_content, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def get_existing_course_hashes(session: Session) -> dict[tuple[str, int], str]:
    """Preloads existing course hashes from the database into a memory lookup table.

    Returns dict mapping (course_id/course_cid, year) -> content_hash.
    """
    try:
        rows = session.query(Course.id, Course.year, Course.content_hash).all()
        return {(row[0], row[1]): row[2] for row in rows if row[2] is not None}
    except Exception as e:
        logger.warning(
            f"Could not load existing course hashes (will populate fresh): {e}"
        )
        return {}


def get_existing_course_ids_for_year(session: Session, year: int) -> dict[str, str]:
    """Preloads existing course identifiers for a specific year from DB.

    Maps raw course_code, uppercase course_code, and code to course id.
    """
    rows = (
        session.query(Course.course_code, Course.id).filter(Course.year == year).all()
    )
    mapping: dict[str, str] = {}
    for code, cid in rows:
        if code:
            mapping[code] = cid
            mapping[code.replace(" ", "").upper()] = cid
            mapping[code.strip()] = cid
    return mapping


def get_previous_years_course_data(
    session_or_engine, target_year: int
) -> dict[str, dict]:
    """Preloads course info, learning outcomes, and assessments from previous years into memory."""
    if hasattr(session_or_engine, "connection"):
        try:
            dbapi_conn = session_or_engine.connection().connection
            return _get_previous_years_course_data_raw(dbapi_conn, target_year)
        except Exception:
            pass

    if hasattr(session_or_engine, "raw_connection"):
        try:
            dbapi_conn = session_or_engine.raw_connection().connection
            return _get_previous_years_course_data_raw(dbapi_conn, target_year)
        except Exception:
            pass

    return _get_previous_years_course_data_orm(session_or_engine, target_year)


def _get_previous_years_course_data_raw(
    dbapi_conn, target_year: int
) -> dict[str, dict]:
    """Fast raw SQLite extraction of previous year course details, outcomes, and assessments."""
    cur = dbapi_conn.cursor()
    cur.execute(
        """
        SELECT id, course_id, year, terms, subject, course_code, title, campus,
               level_of_study, units, course_coordinator, course_level,
               course_overview, prerequisites, corequisites, antirequisites,
               university_wide_elective, url, course_outline_url, textbooks, content_hash
        FROM courses
        WHERE year != ?
        ORDER BY year ASC
        """,
        (target_year,),
    )
    courses_rows = cur.fetchall()

    cur.execute(
        "SELECT course_id, description, outcome_index FROM learning_outcomes WHERE year != ?",
        (target_year,),
    )
    lo_map: dict[str, list] = {}
    for cid, desc, idx in cur.fetchall():
        lo_map.setdefault(cid, []).append({"description": desc, "outcome_index": idx})

    cur.execute(
        "SELECT course_id, title, weighting, hurdle, learning_outcomes FROM assessments WHERE year != ?",
        (target_year,),
    )
    ass_map: dict[str, list] = {}
    for cid, title, weight, hurdle, los in cur.fetchall():
        ass_map.setdefault(cid, []).append(
            {
                "title": title,
                "weighting": weight,
                "hurdle": hurdle,
                "learning_outcomes": los,
            }
        )
    cur.close()

    course_data_map: dict[str, dict] = {}
    for row in courses_rows:
        cid = row[0]
        code = row[5]
        if not code:
            continue
        norm = code.replace(" ", "").upper()
        data = {
            "course_id": row[1],
            "year": row[2],
            "terms": row[3],
            "subject": row[4],
            "course_code": code,
            "title": row[6],
            "campus": row[7],
            "level_of_study": row[8],
            "units": row[9],
            "course_coordinator": row[10],
            "course_level": row[11],
            "course_overview": row[12],
            "prerequisites": row[13],
            "corequisites": row[14],
            "antirequisites": row[15],
            "university_wide_elective": bool(row[16]),
            "course_outline_url": row[18],
            "textbooks": row[19],
            "content_hash": row[20],
            "learning_outcomes": lo_map.get(cid, []),
            "assessments": ass_map.get(cid, []),
        }
        course_data_map[code] = data
        course_data_map[norm] = data
        course_data_map[code.strip()] = data

    return course_data_map


def _get_previous_years_course_data_orm(
    session: Session, target_year: int
) -> dict[str, dict]:
    """ORM fallback for preloading course details from previous years."""
    courses = (
        session.query(Course)
        .filter(Course.year != target_year)
        .order_by(Course.year.asc())
        .all()
    )
    course_data_map: dict[str, dict] = {}
    for c in courses:
        if not c.course_code:
            continue
        code = c.course_code
        norm = code.replace(" ", "").upper()
        data = {
            "course_id": c.course_id,
            "year": c.year,
            "terms": c.terms,
            "subject": c.subject,
            "course_code": code,
            "title": c.title,
            "campus": c.campus,
            "level_of_study": c.level_of_study,
            "units": c.units,
            "course_coordinator": c.course_coordinator,
            "course_level": c.course_level,
            "course_overview": c.course_overview,
            "prerequisites": c.prerequisites,
            "corequisites": c.corequisites,
            "antirequisites": c.antirequisites,
            "university_wide_elective": c.university_wide_elective,
            "course_outline_url": c.course_outline_url,
            "textbooks": c.textbooks,
            "content_hash": c.content_hash,
            "learning_outcomes": [
                {"description": lo.description, "outcome_index": lo.outcome_index}
                for lo in getattr(c, "learning_outcomes", [])
            ],
            "assessments": [
                {
                    "title": a.title,
                    "weighting": a.weighting,
                    "hurdle": a.hurdle,
                    "learning_outcomes": a.learning_outcomes,
                }
                for a in getattr(c, "assessments", [])
            ],
        }
        course_data_map[code] = data
        course_data_map[norm] = data
        course_data_map[code.strip()] = data
    return course_data_map


def bulk_upsert_courses(session: Session, courses_data: list[dict]):
    """Batched bulk upsert for courses.

    Only updates when content_hash changes to avoid unnecessary WAL growth.
    """
    if not courses_data:
        return

    # Ensure unique index exists on SQLite for ON CONFLICT target
    try:
        session.connection().exec_driver_sql(
            'CREATE UNIQUE INDEX IF NOT EXISTS "uq_courses_year_course_id" '
            'ON "courses" ("year", "course_id")'
        )
    except Exception:
        pass

    stmt = sqlite_insert(Course).values(courses_data)
    stmt = stmt.on_conflict_do_update(
        index_elements=["year", "course_id"],
        set_={
            "title": stmt.excluded.title,
            "terms": stmt.excluded.terms,
            "campus": stmt.excluded.campus,
            "level_of_study": stmt.excluded.level_of_study,
            "units": stmt.excluded.units,
            "course_coordinator": stmt.excluded.course_coordinator,
            "course_level": stmt.excluded.course_level,
            "course_overview": stmt.excluded.course_overview,
            "prerequisites": stmt.excluded.prerequisites,
            "corequisites": stmt.excluded.corequisites,
            "antirequisites": stmt.excluded.antirequisites,
            "university_wide_elective": stmt.excluded.university_wide_elective,
            "course_outline_url": stmt.excluded.course_outline_url,
            "textbooks": stmt.excluded.textbooks,
            "content_hash": stmt.excluded.content_hash,
        },
        where=(Course.content_hash != stmt.excluded.content_hash),
    )
    session.execute(stmt)
    session.commit()


def bulk_replace_classes_for_year(
    session: Session,
    year: int,
    classes_data: list[dict],
    meetings_data: list[dict] | None = None,
):
    """Since class availability is dynamic and ephemeral, replace the entire year's

    classes and meetings in atomic operations rather than checking row by row.
    Deduplicates in memory on the unique constraint and chunks inserts using
    SQLite ON CONFLICT DO UPDATE.
    """
    if not classes_data:
        return

    # In-memory deduplication on the unique key constraint
    # Prevents collisions when the scraper extracts the same class/section/group twice
    deduped_classes = {}
    for item in classes_data:
        key = (
            item["year"],
            item["course_id"],
            str(item["class_nbr"]),
            str(item["section"]),
            str(item.get("group", "1")),
        )
        deduped_classes[key] = item

    clean_classes = list(deduped_classes.values())

    # Delete existing meetings and classes for this year
    session.query(Meetings).filter(Meetings.year == year).delete(
        synchronize_session=False
    )
    session.query(CourseClass).filter(CourseClass.year == year).delete(
        synchronize_session=False
    )

    # Ensure unique index exists on SQLite for ON CONFLICT target
    try:
        session.connection().exec_driver_sql(
            'CREATE UNIQUE INDEX IF NOT EXISTS "uq_course_classes_year_course_class_section_group" '
            'ON "course_classes" ("year", "course_id", "class_nbr", "section", "group")'
        )
    except Exception:
        pass

    # Batch insert using SQLite ON CONFLICT
    # Chunking by 2,000 prevents exceeding SQLite's bound parameter limits
    chunk_size = 2000
    for i in range(0, len(clean_classes), chunk_size):
        chunk = clean_classes[i : i + chunk_size]
        stmt = sqlite_insert(CourseClass).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["year", "course_id", "class_nbr", "section", "group"],
            set_={
                "size": stmt.excluded.size,
                "available": stmt.excluded.available,
                "component": stmt.excluded.component,
            },
        )
        try:
            session.execute(stmt)
        except Exception as e:
            # Fallback for legacy database tables if constraint matching fails
            logger.warning(
                f"Falling back to on_conflict_do_nothing on primary key 'id' for course_classes: {e}"
            )
            fallback_stmt = sqlite_insert(CourseClass).values(chunk)
            fallback_stmt = fallback_stmt.on_conflict_do_nothing(index_elements=["id"])
            session.execute(fallback_stmt)

    if meetings_data:
        # Deduplicate meetings by ID
        deduped_meetings = {m["id"]: m for m in meetings_data}
        clean_meetings = list(deduped_meetings.values())
        for i in range(0, len(clean_meetings), chunk_size):
            m_chunk = clean_meetings[i : i + chunk_size]
            stmt_m = sqlite_insert(Meetings).values(m_chunk)
            stmt_m = stmt_m.on_conflict_do_nothing(index_elements=["id"])
            session.execute(stmt_m)

    session.commit()


def purge_year_data(db_session: Session, target_year: int) -> dict[str, int]:
    """Purge all academic data strictly scoped to target_year from database."""
    deleted_meetings = (
        db_session.query(Meetings)
        .filter(Meetings.year == target_year)
        .delete(synchronize_session=False)
    )

    deleted_classes = (
        db_session.query(CourseClass)
        .filter(CourseClass.year == target_year)
        .delete(synchronize_session=False)
    )

    deleted_assessments = (
        db_session.query(Assessment)
        .filter(Assessment.year == target_year)
        .delete(synchronize_session=False)
    )

    deleted_outcomes = (
        db_session.query(LearningOutcome)
        .filter(LearningOutcome.year == target_year)
        .delete(synchronize_session=False)
    )

    deleted_courses = (
        db_session.query(Course)
        .filter(Course.year == target_year)
        .delete(synchronize_session=False)
    )

    db_session.commit()

    return {
        "meetings": deleted_meetings,
        "classes": deleted_classes,
        "assessments": deleted_assessments,
        "learning_outcomes": deleted_outcomes,
        "courses": deleted_courses,
    }


def configure_fast_sqlite(dbapi_connection):
    """Configures high-speed, crash-resilient SQLite pragmas."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode = WAL;")
    cursor.execute("PRAGMA synchronous = NORMAL;")
    cursor.execute("PRAGMA wal_autocheckpoint = 1000;")
    cursor.execute(
        "PRAGMA busy_timeout = 60000;"
    )  # 60s timeout to prevent lock contention
    cursor.execute("PRAGMA temp_store = MEMORY;")
    cursor.execute("PRAGMA cache_size = -64000;")  # 64MB cache
    cursor.close()


def finalize_sqlite_for_s3(db_path: str):
    """Cleanly compact and flush DB before S3 upload or on process exit."""
    if not os.path.exists(db_path):
        return
    try:
        conn = sqlite3.connect(db_path, timeout=60.0)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.close()
    except Exception as e:
        print(f"[DB CLEANUP WARNING] Could not checkpoint WAL: {e}")


def ensure_healthy_database(db_path: str, base_dir: str = ".") -> bool:
    """Verifies that the SQLite database is healthy and not corrupted.

    If malformed or corrupted from a sudden exit, automatically recovers by checkpointing
    or restoring from a healthy backup candidate (dev.sqlite3 or src/local.sqlite3).
    Returns True if healthy/recovered, False otherwise.
    """
    path = Path(db_path)
    if not path.exists() or path.stat().st_size == 0:
        return True

    # Attempt to checkpoint any leftover WAL journal and check integrity
    try:
        conn = sqlite3.connect(str(path), timeout=30.0)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        integrity_res = conn.execute("PRAGMA integrity_check;").fetchall()
        conn.close()
        if integrity_res == [("ok",)]:
            return True
        print(f"[DB HEALTH CHECK] Database {db_path} is malformed: {integrity_res[:2]}")
    except Exception as e:
        print(f"[DB HEALTH CHECK] Error checking integrity of {db_path}: {e}")

    # Database is corrupted. Search for a healthy candidate to restore from.
    print(
        f"[DB HEALTH CHECK] Attempting recovery of {db_path} from backup candidates..."
    )
    base = Path(base_dir)
    candidates = [
        base / "dev.sqlite3",
        base / "src" / "dev.sqlite3",
        base / "src" / "local.sqlite3",
        base / "local.sqlite3",
    ]
    for cand in candidates:
        if (
            cand.exists()
            and cand.resolve() != path.resolve()
            and cand.stat().st_size > 0
        ):
            try:
                c_conn = sqlite3.connect(str(cand), timeout=10.0)
                c_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                c_res = c_conn.execute("PRAGMA integrity_check;").fetchall()
                c_conn.close()
                if c_res == [("ok",)]:
                    # Clean up corrupted main, wal, shm
                    for ext in ["", "-wal", "-shm"]:
                        f = Path(str(path) + ext)
                        if f.exists():
                            f.unlink()
                    shutil.copy2(cand, path)
                    # Verify restored file
                    r_conn = sqlite3.connect(str(path), timeout=10.0)
                    r_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                    r_conn.close()
                    print(
                        f"[DB HEALTH CHECK] Successfully recovered {db_path} from healthy {cand}."
                    )
                    return True
            except Exception as e:
                print(f"[DB HEALTH CHECK] Candidate {cand} failed integrity check: {e}")

    return False


def validate_db_schema(db_path: str) -> bool:
    """Verifies that the SQLite database at db_path exists, is valid SQLite,
    and has all tables and columns defined in Base.metadata.
    Returns False if any table or column is missing or mismatched.
    """
    path = Path(db_path)
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        conn = sqlite3.connect(str(path))
        cursor = conn.cursor()
        # Verify integrity
        integrity = cursor.execute("PRAGMA integrity_check;").fetchall()
        if integrity != [("ok",)]:
            conn.close()
            return False

        # Get existing tables in SQLite
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0].lower() for row in cursor.fetchall()}

        # Verify all tables and columns defined in Base.metadata exist
        for table_name, table in Base.metadata.tables.items():
            if table_name.lower() not in existing_tables:
                conn.close()
                return False
            cursor.execute(f"PRAGMA table_info('{table_name}')")
            existing_cols = {row[1].lower() for row in cursor.fetchall()}
            for col in table.columns:
                if col.name.lower() not in existing_cols:
                    conn.close()
                    return False

        conn.close()
        return True
    except Exception:
        return False


def reset_db_if_invalid(db_path: str) -> bool:
    """If the database at db_path does not match the expected schema or is legacy,
    cleanly removes it so the application starts fresh from scratch.
    """
    path = Path(db_path)
    if not path.exists():
        return False

    if not validate_db_schema(db_path):
        logger.warning(
            f"Database {db_path} schema does not match current models or is corrupted. "
            f"Ignoring saved database and starting from scratch."
        )
        for ext in ["", "-wal", "-shm"]:
            f = Path(str(db_path) + ext)
            if f.exists():
                try:
                    f.unlink()
                except Exception:
                    pass
        return True
    return False


# Backward compatibility aliases
def is_legacy_database(db_path: str) -> bool:
    return not validate_db_schema(db_path)


reset_legacy_database = reset_db_if_invalid


def ensure_schema_up_to_date(engine_or_conn):
    """Ensures all tables, columns, constraints, and indexes in Base.metadata exist."""
    Base.metadata.create_all(engine_or_conn)
