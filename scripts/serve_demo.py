"""Single-instance free demo supervisor. Full deployments use separate Celery services."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent


def main():
    if os.getenv("HOSTED_DEMO") != "1":
        raise RuntimeError("This entry point requires HOSTED_DEMO=1")
    children = []
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for command in (["manage.py", "migrate", "--noinput"], ["manage.py", "seed_showcase"]):
            subprocess.run([sys.executable, *command], cwd=ROOT, check=True)
        for command in (
            ["manage.py", "process_jobs"],
            ["-m", "daphne", "-b", "0.0.0.0", "-p", os.getenv("PORT", "8000"), "config.asgi:application"],
        ):
            children.append(subprocess.Popen([sys.executable, *command], cwd=ROOT))
        while not stopping:
            if any(child.poll() is not None for child in children):
                return 1
            time.sleep(1)
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    sys.exit(main())
