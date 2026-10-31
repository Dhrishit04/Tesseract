"""A sidecar started by the app must not outlive it, or the next launch reuses its old code."""

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SIDECAR = Path(__file__).resolve().parent.parent
SCRIPT = "import parent_link, time; parent_link.exit_with_app(); print('up', flush=True); time.sleep(30)"


def start(env: dict) -> subprocess.Popen:
    # sys.executable is the venv launcher, which runs the real Python as a child, as the app does.
    process = subprocess.Popen(
        [sys.executable, "-c", SCRIPT], cwd=SIDECAR, env={**os.environ, **env},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    process.stdout.readline()
    return process


class ParentLinkTests(unittest.TestCase):
    def test_exits_when_the_app_is_gone(self):
        process = start({"TESSERACT_APP_PIPE": "1"})
        self.addCleanup(process.stdout.close)
        process.stdin.close()
        self.assertEqual(process.wait(timeout=10), 0)

    def test_a_sidecar_started_by_hand_keeps_running(self):
        process = start({"TESSERACT_APP_PIPE": ""})
        self.addCleanup(process.stdout.close)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        process.stdin.close()
        with self.assertRaises(subprocess.TimeoutExpired):
            process.wait(timeout=1)


class AccessLogTests(unittest.TestCase):
    def test_the_oauth_callback_code_stays_out_of_the_logs(self):
        import main

        with patch.object(main.uvicorn, "run") as run, patch.object(main, "exit_with_app"):
            main.main()
        self.assertIs(run.call_args.kwargs["access_log"], False)


if __name__ == "__main__":
    unittest.main()
