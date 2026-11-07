import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values


def setup_logger() -> logging.Logger:
    """Sets up a logger that writes all logs (DEBUG, INFO, WARNING, ERROR) to a file

    in logs/{timestamp}.log, while only streaming ERROR (and CRITICAL) logs to the
    console (sys.stdout).
    """
    env_config = dotenv_values()
    raw_level = (
        os.getenv("FILE_LOGGING_LEVEL")
        or env_config.get("FILE_LOGGING_LEVEL")
        or env_config.get("DEFAULT_LOGGING_LEVEL")
        or os.getenv("DEFAULT_LOGGING_LEVEL")
        or "DEBUG"
    )
    # File receives all info, debug, warnings, and errors by default
    file_logging_level = (
        getattr(logging, str(raw_level).upper(), logging.DEBUG)
        if str(raw_level).upper() != "ERROR"
        else logging.DEBUG
    )

    # Initialise logger with DEBUG so all records reach handlers
    logger = logging.getLogger("courseAPICallLogger")
    logger.setLevel(logging.DEBUG)

    file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    # Console level: only ERROR printed to console
    console_level = getattr(
        logging,
        os.getenv("CONSOLE_LOGGING_LEVEL", "ERROR").upper(),
        logging.ERROR,
    )

    if not logger.hasHandlers():
        # Stream handler for stdout: ONLY print errors to console
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(console_level)
        stream_handler.setFormatter(file_formatter)
        logger.addHandler(stream_handler)

        # Initialise log dir path
        logs_dir = Path(__file__).resolve().parent.parent / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Each run sends logs into a separate file
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_file_path = logs_dir / f"{timestamp}.log"

        # Setup log file handler: ALL info, debug, warnings, and errors go to file
        log_file_handler = logging.FileHandler(log_file_path, mode="w", delay=True)
        log_file_handler.setLevel(file_logging_level)
        log_file_handler.setFormatter(file_formatter)
        logger.addHandler(log_file_handler)

        # Optional latest.log symlink for convenience
        try:
            latest_log_path = logs_dir / "latest.log"
            if latest_log_path.is_symlink() or latest_log_path.exists():
                latest_log_path.unlink()
            latest_log_path.symlink_to(log_file_path.name)
        except Exception:
            pass
    else:
        for handler in logger.handlers:
            if isinstance(handler, logging.FileHandler):
                handler.setLevel(file_logging_level)
            elif isinstance(handler, logging.StreamHandler):
                handler.setLevel(console_level)

    return logger


logger = setup_logger()
