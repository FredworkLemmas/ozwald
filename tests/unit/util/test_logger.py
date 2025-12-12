"""Test suite for the `get_logger` helper in `src/util/logger.py`.

These tests verify that `get_logger`:
- creates/returns a named logger configured to log to stdout,
- configures the formatter and level as expected,
- is idempotent (does not add duplicate handlers on subsequent calls), and
- respects the current `sys.stdout` stream (using the `mocker` fixture).
"""

import io
import logging
import sys
from typing import Callable
from uuid import uuid4

import pytest

from src.util.logger import get_logger


@pytest.fixture
def logger_name() -> str:
    """Provide a unique logger name for each test.

    Using a unique name avoids interference between tests that rely on the
    logging module's global logger registry.
    """

    return f"tests.util.logger.{uuid4()}"


@pytest.fixture
def prepare_clean_logger() -> Callable[[str], logging.Logger]:
    """Factory that ensures a clean logger for a given name and cleans up after.

    The returned callable accepts a logger name, clears any pre-existing
    handlers for that logger so tests start from a known state, and registers
    the logger for cleanup once the test completes.
    """

    created: list[logging.Logger] = []

    def _factory(name: str) -> logging.Logger:
        logger = logging.getLogger(name)
        # Ensure no pre-existing handlers remain
        for h in list(logger.handlers):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
        created.append(logger)
        return logger

    yield _factory

    # Cleanup handlers added during the test
    for logger in created:
        for h in list(logger.handlers):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass


def _get_format_string(formatter: logging.Formatter) -> str:
    """Return the effective format string from a `logging.Formatter`.

    Supports both the legacy `._fmt` and the newer `. _style._fmt` attribute
    used by Python's logging implementation, for cross-version robustness.
    """

    if hasattr(formatter, "_style") and hasattr(formatter._style, "_fmt"):
        return formatter._style._fmt  # type: ignore[attr-defined]
    if hasattr(formatter, "_fmt"):
        return formatter._fmt  # type: ignore[attr-defined]
    # Fallback: stringify (should not happen in supported versions)
    return str(formatter)


class TestGetLogger:
    """Tests covering the behavior of `get_logger` configuration helper."""

    def test_creates_named_logger_with_stdout_handler(
        self,
        logger_name: str,
        prepare_clean_logger: Callable[[str], logging.Logger],
    ) -> None:
        """
        It returns a logger with the given name, a StreamHandler to stdout,
        and INFO level.
        """

        prepare_clean_logger(logger_name)
        logger = get_logger(logger_name)

        # Name
        assert logger.name == logger_name

        # One stream handler attached
        assert len(logger.handlers) == 1
        handler = logger.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert getattr(handler, "stream", None) is sys.stdout

        # Level is set to INFO
        assert logger.level == logging.INFO

    def test_is_idempotent_no_duplicate_handlers(
        self,
        logger_name: str,
        prepare_clean_logger: Callable[[str], logging.Logger],
    ) -> None:
        """
        Calling `get_logger` multiple times does not add duplicate handlers.
        """

        prepare_clean_logger(logger_name)
        logger1 = get_logger(logger_name)
        handlers_after_first = list(logger1.handlers)

        logger2 = get_logger(logger_name)
        handlers_after_second = list(logger2.handlers)

        assert logger1 is logger2  # same logger instance for the same name
        assert len(handlers_after_first) == 1
        assert len(handlers_after_second) == 1
        assert handlers_after_first[0] is handlers_after_second[0]

    def test_formatter_configuration(
        self,
        logger_name: str,
        prepare_clean_logger: Callable[[str], logging.Logger],
    ) -> None:
        """
        The attached handler uses the expected format and date format.
        """

        prepare_clean_logger(logger_name)
        logger = get_logger(logger_name)
        handler = logger.handlers[0]
        formatter = handler.formatter
        assert isinstance(formatter, logging.Formatter)

        fmt = _get_format_string(formatter)
        # From src/util/logger.py
        assert fmt == "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        assert formatter.datefmt == "%Y-%m-%d %H:%M:%S"

    def test_respects_current_stdout_stream(
        self,
        logger_name: str,
        prepare_clean_logger: Callable[[str], logging.Logger],
        mocker,
    ) -> None:
        """
        The StreamHandler binds to the current `sys.stdout` (verified using
        the mocker fixture).
        """

        prepare_clean_logger(logger_name)

        # Replace sys.stdout with a StringIO using pytest-mock's `mocker`
        # fixture
        fake_stdout = io.StringIO()
        mocker.patch("sys.stdout", new=fake_stdout)

        logger = get_logger(logger_name)
        handler = logger.handlers[0]

        # The handler should be writing to the (patched) sys.stdout
        assert getattr(handler, "stream", None) is fake_stdout
