"""Stop the sidecar when the desktop app that started it is gone, however the app ended."""

import os
import threading


def _exit_when_pipe_closes() -> None:
    try:
        while os.read(0, 1024):
            pass
    except OSError:
        return  # no usable pipe, so there is nothing to watch
    os._exit(0)


def exit_with_app() -> None:
    """The app holds the other end of stdin, and the OS closes it even after a crash or a stopped dev run."""
    if os.environ.get("TESSERACT_APP_PIPE"):
        threading.Thread(target=_exit_when_pipe_closes, daemon=True).start()
