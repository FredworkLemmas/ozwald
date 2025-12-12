import logging
import sys


def get_logger(name: str = __name__) -> logging.Logger:
    """
    Returns a configured logger that logs to console.

    Args:
        name: The name of the logger (typically __name__ from the
              calling module)

    Returns:
        A configured Logger instance
    """
    logger = logging.getLogger(name)

    # Only add handler if logger doesn't have any handlers yet
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    return logger
