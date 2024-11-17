import logging
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values


def setup_logger() -> logging.Logger:
    """
    Sets up a logger that writes logs to a file in the form {timestamp}.log
    The level of logging that is written to the file depends on the environment
    variable "DEFAULT_LOGGING_LEVEL".
    Returns:
        logging.Logger = a customised logger object
    """

    # Initialise logger
    logger = logging.getLogger("courseAPICallLogger")
    default_logging_level = dotenv_values().get("DEFAULT_LOGGING_LEVEL")
    logger.setLevel(default_logging_level)

    if not logger.hasHandlers():
        # Initialise log dir path
        logs_dir = Path(__file__).resolve().parent.parent / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Each error log sent into separate file
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

        # Log gile path
        log_file_path = logs_dir / f"{timestamp}.log"

        # Setup log file handler
        log_file_handler = logging.FileHandler(log_file_path, mode="w", delay=True)

        # Set level of file handler
        log_file_handler.setLevel(default_logging_level)

        # Setup file formatter
        file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        log_file_handler.setFormatter(file_formatter)

        # Add handler to logger
        logger.addHandler(log_file_handler)

    return logger


logger = setup_logger()
