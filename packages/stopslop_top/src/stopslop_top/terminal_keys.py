"""Small nonblocking keyboard adapter with terminal restoration on every exit."""
from contextlib import contextmanager
import os
import sys


@contextmanager
def keyboard():
    if os.name == "nt":
        import msvcrt

        def read():
            if not msvcrt.kbhit():
                return None
            key = msvcrt.getwch()
            if key in ("\x00", "\xe0"):
                return {"H": "up", "P": "down", "I": "pageup", "Q": "pagedown",
                        "G": "home", "O": "end", "K": "left", "M": "right"}.get(msvcrt.getwch())
            return normalize(key)
        yield read
    else:
        import select
        import termios
        import tty
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)

            def read():
                if not select.select([sys.stdin], [], [], 0)[0]:
                    return None
                value = os.read(fd, 32).decode("utf-8", errors="replace")
                return {"\x1b[A": "up", "\x1b[B": "down", "\x1b[5~": "pageup", "\x1b[6~": "pagedown",
                        "\x1b[H": "home", "\x1b[F": "end", "\x1b[C": "right", "\x1b[D": "left",
                        "\x1b[Z": "backtab"}.get(value, normalize(value))
            yield read
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def normalize(key):
    return {"\t": "tab", "\r": "enter", "\n": "enter", "\x1b": "escape", "\x03": "q"}.get(key, key)
