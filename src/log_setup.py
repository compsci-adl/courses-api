import logging
import os
from datetime import datetime


def setup_logger():
    logger = logging.getLogger("dataFetcherLogger")
    logger.setLevel(logging.DEBUG)

    if not logger.hasHandlers():
        logs_dir = os.path.join(os.path.dirname(__file__), "../logs")
        os.makedirs(logs_dir, exist_ok=True)

        # Each error log sent into
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        error_log_file_path = os.path.join(logs_dir, f"error_{timestamp}.log")

        # All non_error outputs go into single debug file
        debug_log_file_path = os.path.join(logs_dir, f"debug_{timestamp}.log")

        # # Setup console handler
        # console_handler = logging.StreamHandler()
        # console_handler.setLevel(logging.DEBUG)

        # Setup debug file handler
        debug_file_handler = logging.FileHandler(
            debug_log_file_path, mode="w", delay=True
        )
        debug_file_handler.setLevel

        # Setup error filehandler
        error_file_handler = logging.FileHandler(
            error_log_file_path, mode="w", delay=True
        )
        error_file_handler.setLevel(logging.ERROR)

        # Initiaise formatters
        # console_formatter = logging.Formatter('\n%(asctime)s - %(levelname)s - %(message)s\n')
        file_formatter = logging.Formatter(
            "\n%(asctime)s - %(levelname)s - %(message)s\n"
        )

        # Set formatters
        # console_handler.setFormatter(console_formatter)
        error_file_handler.setFormatter(file_formatter)
        debug_file_handler.setFormatter(file_formatter)

        # Add handlers to logger
        # logger.addHandler(console_handler)
        logger.addHandler(error_file_handler)
        logger.addHandler(debug_file_handler)

    return logger


logger = setup_logger()
