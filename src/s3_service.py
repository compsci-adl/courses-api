import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    boto3 = None
    ClientError = Exception

from log import logger


class S3SyncManager:
    """Manages pre-flight download, year inspection, checkpointing, and upload of courses.db to S3."""

    def __init__(self, bucket_name: Optional[str] = None, base_dir: str = ".") -> None:
        self.bucket_name = (
            bucket_name
            or os.environ.get("S3_BUCKET_NAME")
            or os.environ.get("AWS_S3_BUCKET")
        )
        self.base_dir = Path(base_dir)
        self.s3_client = None
        if boto3 and self.bucket_name:
            try:
                self.s3_client = boto3.client("s3")
            except Exception as e:
                logger.debug(f"Could not initialize boto3 s3 client: {e}")

    def is_s3_available(self) -> bool:
        return bool(self.bucket_name)

    def download_database(self, local_path: str = "src/dev.sqlite3") -> bool:
        """Downloads courses.db from S3 if it exists (Pre-Flight hook)."""
        local_file = Path(local_path)
        local_file.parent.mkdir(parents=True, exist_ok=True)

        if not self.is_s3_available():
            logger.info("S3_BUCKET_NAME not set. Running in local DB mode.")
            # If local_file does not exist, is empty, or has no courses, seed from existing local DB candidate
            years_in_local = self.get_synced_years_from_db(local_file)
            if (
                not local_file.exists()
                or local_file.stat().st_size == 0
                or not years_in_local
            ):
                candidates = [
                    self.base_dir / "dev.sqlite3",
                    self.base_dir / "src" / "dev.sqlite3",
                    self.base_dir / "src" / "local.sqlite3",
                    self.base_dir / "local.sqlite3",
                ]
                for candidate in candidates:
                    if (
                        candidate.exists()
                        and candidate.resolve() != local_file.resolve()
                        and candidate.stat().st_size > 0
                        and self.get_synced_years_from_db(candidate)
                    ):
                        try:
                            # Checkpoint candidate so main file is self-contained before copy
                            try:
                                cand_conn = sqlite3.connect(
                                    str(candidate), timeout=10.0
                                )
                                cand_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                                cand_conn.close()
                            except Exception:
                                pass

                            # Clean up target files
                            wal_file = Path(str(local_file) + "-wal")
                            shm_file = Path(str(local_file) + "-shm")
                            if wal_file.exists():
                                wal_file.unlink()
                            if shm_file.exists():
                                shm_file.unlink()

                            shutil.copy2(candidate, local_file)

                            # Checkpoint destination
                            try:
                                dest_conn = sqlite3.connect(
                                    str(local_file), timeout=10.0
                                )
                                dest_conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                                dest_conn.close()
                            except Exception:
                                pass

                            logger.info(
                                f"Seeded {local_path} from existing local database: {candidate}"
                            )
                            return True
                        except Exception as e:
                            logger.warning(
                                f"Failed to copy candidate {candidate} to {local_path}: {e}"
                            )
            return False

        keys_to_check = [
            "courses-api/courses.db",
            "courses-api/local.sqlite3",
            "courses.db",
            "local.sqlite3",
        ]

        # Try boto3
        if self.s3_client:
            for s3_key in keys_to_check:
                try:
                    logger.info(
                        f"Downloading s3://{self.bucket_name}/{s3_key} -> {local_path}..."
                    )
                    self.s3_client.download_file(
                        self.bucket_name, s3_key, str(local_file)
                    )
                    logger.info("Database downloaded successfully.")
                    return True
                except ClientError as e:
                    code = e.response.get("Error", {}).get("Code")
                    if code not in ("404", "NoSuchKey"):
                        logger.warning(f"S3 download error for {s3_key}: {e}")
                except Exception as e:
                    logger.debug(f"Download attempt failed for {s3_key}: {e}")

        # Fallback to AWS CLI
        for s3_key in keys_to_check:
            s3_uri = f"s3://{self.bucket_name}/{s3_key}"
            cmd = ["aws", "s3", "cp", s3_uri, str(local_file)]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if res.returncode == 0 and local_file.exists():
                    logger.info(f"Database downloaded successfully via CLI: {s3_uri}")
                    return True
            except Exception as e:
                logger.debug(f"CLI download attempt failed for {s3_uri}: {e}")

        logger.info("No existing database in S3. Starting fresh.")
        return False

    def upload_database(self, db_path: str = "src/local.sqlite3") -> bool:
        """Flushes SQLite WAL journal and uploads courses.db to S3."""
        local_file = Path(db_path)
        if not local_file.exists():
            logger.warning(f"Database file {db_path} does not exist for upload.")
            return False

        # Ensure WAL journal is flushed into main database file before uploading
        try:
            conn = sqlite3.connect(str(local_file))
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            conn.close()
            logger.info(
                "PRAGMA wal_checkpoint(TRUNCATE) and VACUUM executed successfully."
            )
        except Exception as e:
            logger.warning(
                f"Could not execute wal_checkpoint/vacuum before upload: {e}"
            )

        if not self.is_s3_available():
            logger.info("S3_BUCKET_NAME not set. Skipping upload.")
            return False

        # Try boto3
        if self.s3_client:
            try:
                logger.info(
                    f"Uploading {db_path} to s3://{self.bucket_name}/courses-api/courses.db..."
                )
                self.s3_client.upload_file(
                    str(local_file), self.bucket_name, "courses-api/courses.db"
                )
                self.s3_client.upload_file(
                    str(local_file), self.bucket_name, "courses-api/local.sqlite3"
                )
                logger.info("Database upload complete.")
                return True
            except Exception as e:
                logger.error(f"boto3 upload database error: {e}")

        # Fallback to AWS CLI
        s3_uri = f"s3://{self.bucket_name}/courses-api/courses.db"
        cmd = ["aws", "s3", "cp", str(local_file), s3_uri]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False)
            compat_uri = f"s3://{self.bucket_name}/courses-api/local.sqlite3"
            subprocess.run(
                ["aws", "s3", "cp", str(local_file), compat_uri],
                capture_output=True,
                check=False,
            )
            logger.info("Database upload complete via CLI.")
            return res.returncode == 0
        except Exception as e:
            logger.error(f"Failed to upload database to S3 via CLI: {e}")
            return False

    @staticmethod
    def get_synced_years_from_db(db_path: str | Path | Any) -> set[int]:
        """Queries SQLite directly to identify which years are already populated in courses table."""
        # Support SQLAlchemy Engine or direct path
        if hasattr(db_path, "connect"):
            with db_path.connect() as conn:
                try:
                    result = conn.execute(text("SELECT DISTINCT year FROM courses"))
                    return {
                        int(row[0]) for row in result.fetchall() if row[0] is not None
                    }
                except Exception:
                    return set()

        path = Path(db_path)
        if not path.exists():
            return set()

        conn = sqlite3.connect(str(path))
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT DISTINCT year FROM courses")
            years = {int(row[0]) for row in cursor.fetchall() if row[0] is not None}
            return years
        except sqlite3.OperationalError:
            # Table doesn't exist yet on clean DB
            return set()
        finally:
            conn.close()

    # Backward-compatible alias
    get_already_scraped_years = get_synced_years_from_db
