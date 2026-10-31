"""Tesseract sidecar entry point - runs the FastAPI server."""

import uvicorn
from parent_link import exit_with_app
from server import app


def main():
    exit_with_app()
    # The access log would print the code from the OAuth redirect.
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info", access_log=False)


if __name__ == "__main__":
    main()