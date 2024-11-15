import logging
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

"""
Sets up a logger that writes error messages by default to a file (error_{timestamp}.log).
Depending on the logging level, if it is set to DEBUG, it will also setup a separate debug
file (debug_{timestamp}.log), which will contain all debug level outputs (debug, info, error, etc.)

Returns:
    logging.Logger = a customised logger object
"""


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("courseAPICallLogger")
    default_logging_level = dotenv_values().get("DEFAULT_LOGGING_LEVEL")
    logger.setLevel(default_logging_level)

    if not logger.hasHandlers():
        # Initialise log dir path
        logs_dir = Path(__file__).resolve().parent.parent / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Each error log sent into separate file
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

        # Error logs file path
        error_log_file_path = logs_dir / f"error_{timestamp}.log"

        # Setup error file handler
        error_file_handler = logging.FileHandler(
            error_log_file_path, mode="w", delay=True
        )
        error_file_handler.setLevel(
            dotenv_values().get("ERROR_FILE_HANDLER_LOGGING_LEVEL")
        )
        # Initialise error formatter
        file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        error_file_handler.setFormatter(file_formatter)
        # Add error handler to logger
        logger.addHandler(error_file_handler)

        # Debug file only gets created if logging level is DEBUG
        if default_logging_level == "DEBUG":
            # Debug logs file path
            debug_log_file_path = logs_dir / f"debug_{timestamp}.log"
            # Setup debug file handler
            debug_file_handler = logging.FileHandler(
                debug_log_file_path, mode="w", delay=True
            )
            # Initialise debug formatter
            debug_file_handler.setLevel(default_logging_level)
            debug_file_handler.setFormatter(file_formatter)
            # Add debug handler to logger
            logger.addHandler(debug_file_handler)

    return logger


logger = setup_logger()
