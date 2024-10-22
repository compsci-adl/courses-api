import logging


def setup_logger():
    """Create a logger"""
    logger = logging.getLogger("dataFetcherLogger")
    logger.setLevel(logging.DEBUG)

    if not logger.hasHandlers():
        """Console handler"""
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)

        """Error file handler"""
        error_file_handler = logging.FileHandler("../logs/error.log", mode="w")
        error_file_handler.setLevel(logging.ERROR)

        """Log formatters"""
        console_formatter = logging.Formatter(
            "\n%(asctime)s - %(levelname)s - %(message)s\n"
        )
        error_formatter = logging.Formatter(
            "\n%(asctime)s - %(levelname)s - %(message)s\n"
        )

        """Set the handlers to use the formatter"""
        console_handler.setFormatter(console_formatter)
        error_file_handler.setFormatter(error_formatter)

        """Set the loggers to use the handlers"""
        logger.addHandler(console_handler)
        logger.addHandler(error_file_handler)

    return logger


logger = setup_logger()
