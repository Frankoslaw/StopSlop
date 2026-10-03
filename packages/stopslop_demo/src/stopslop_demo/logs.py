"""Readable audit output without prompts, response content, or credentials."""
import logging
import os
import sys
from .spinner import SpinnerHandler


def configure_console():
    # Hosted replies can contain Unicode punctuation unsupported by Windows CP1250.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and getattr(stream, "encoding", "").lower().replace("-", "") != "utf8":
            stream.reconfigure(encoding="utf-8", errors="replace")


class ColoredFormatter(logging.Formatter):
    def __init__(self, color=False):
        super().__init__()
        self.color = color

    def format(self, record):
        label = "ERROR" if record.levelno >= logging.ERROR else "WARN" if record.levelno >= logging.WARNING else "INFO"
        text = f"[{label}] {record.getMessage()}"
        if self.color:
            code = "31" if label == "ERROR" else "33" if label == "WARN" else "36"
            return f"\033[{code}m{text}\033[0m"
        return text


def configure_logs(mode="auto"):
    logger = logging.getLogger("stopslop.audit")
    for handler in list(logger.handlers):
        if getattr(handler, "stopslop_demo", False):
            logger.removeHandler(handler)
            handler.close()
    handler = SpinnerHandler(sys.stderr)
    handler.stopslop_demo = True
    color = mode == "always" or (mode == "auto" and sys.stderr.isatty() and "NO_COLOR" not in os.environ)
    handler.setFormatter(ColoredFormatter(color))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
