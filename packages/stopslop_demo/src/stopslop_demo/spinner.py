"""One terminal-width progress line shared by policy checks and generation."""
import logging
import shutil
import sys
import threading
import time

console_lock = threading.RLock()
active = False
status = "Checking policies"


def set_status(value):
    global status
    with console_lock:
        status = value


def clear_line(stream):
    width = max(10, shutil.get_terminal_size((80, 24)).columns - 1)
    stream.write("\r" + " " * width + "\r")


class SpinnerHandler(logging.StreamHandler):
    def emit(self, record):
        if getattr(record, "progress", None):
            set_status(record.progress)
            return
        if getattr(record, "rule_check", False):
            return
        text = record.getMessage().removeprefix("[cached] ")
        if text.startswith("cache=miss"):
            set_status("Checking policies")
            return
        with console_lock:
            if active:
                clear_line(self.stream)
            super().emit(record)


class Spinner:
    def __init__(self, enabled=None):
        self.enabled = sys.stderr.isatty() if enabled is None else enabled
        self.stop = threading.Event()
        self.thread = None
        self.started = None

    def __enter__(self):
        global active
        set_status("Checking policies")
        self.started = time.monotonic()
        if self.enabled:
            with console_lock:
                active = True
            self.thread = threading.Thread(target=self._animate, daemon=True)
            self.thread.start()
        return self

    def _animate(self):
        index = 0
        while not self.stop.is_set():
            with console_lock:
                width = max(10, shutil.get_terminal_size((80, 24)).columns - 1)
                elapsed = int(time.monotonic() - self.started)
                label = "|/-\\"[index % 4] + " " + status + f" ({elapsed}s)"
                # Fit to one physical line, including narrow Windows terminals.
                label = label.replace("\n", " ").replace("\r", " ")
                if len(label) > width:
                    label = label[:width - 3] + "..."
                sys.stderr.write("\r" + label.ljust(width) + "\r")
                sys.stderr.flush()
            index += 1
            self.stop.wait(.12)

    def __exit__(self, *exc):
        global active
        self.stop.set()
        if self.thread:
            self.thread.join()
            with console_lock:
                clear_line(sys.stderr)
                sys.stderr.flush()
                active = False
