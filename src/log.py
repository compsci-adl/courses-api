import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values


def setup_logger() -> logging.Logger:
    """
    Sets up a logger that writes logs to a file in the form {timestamp}.log
    and streams to standard output for console and CI visibility.
    The level of logging that is written depends on the environment
    variable "DEFAULT_LOGGING_LEVEL".
    Returns:
        logging.Logger = a customised logger object
    """
    env_config = dotenv_values()
    raw_level = (
        env_config.get("DEFAULT_LOGGING_LEVEL")
        or os.getenv("DEFAULT_LOGGING_LEVEL")
        or "INFO"
    )
    logging_level = getattr(logging, str(raw_level).upper(), logging.INFO)

    # Initialise logger
    logger = logging.getLogger("courseAPICallLogger")
    logger.setLevel(logging_level)

    if not logger.hasHandlers():
        file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

        # Stream handler for stdout (visible in CI runner and console)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(logging_level)
        stream_handler.setFormatter(file_formatter)
        logger.addHandler(stream_handler)

        # Initialise log dir path
        logs_dir = Path(__file__).resolve().parent.parent / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Each run sends logs into a separate file
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_file_path = logs_dir / f"{timestamp}.log"

        # Setup log file handler
        log_file_handler = logging.FileHandler(log_file_path, mode="w", delay=True)
        log_file_handler.setLevel(logging_level)
        log_file_handler.setFormatter(file_formatter)
        # Add handler to logger
        logger.addHandler(log_file_handler)

    return logger


logger = setup_logger()
