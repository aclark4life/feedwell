"""feedwell - All social media accounts suck. This one just sucks less.

Console entry point. Behaves like Django's manage.py:

    feedwell migrate
    feedwell createsuperuser
    feedwell makemigrations feeds

With no arguments, it runs migrations and starts the dev server on
127.0.0.1:8000, opening it in the default browser.
"""

from __future__ import annotations

import os
import sys
import threading
import webbrowser

HOST = "127.0.0.1"
PORT = "8000"


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "feedwell.settings")
    from django.core.management import execute_from_command_line

    args = sys.argv[1:]
    if args:
        execute_from_command_line([sys.argv[0], *args])
        return

    execute_from_command_line([sys.argv[0], "migrate", "--noinput"])
    url = f"http://{HOST}:{PORT}/"
    print(f"feedwell running at {url} (Ctrl+C to stop)")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    execute_from_command_line([sys.argv[0], "runserver", f"{HOST}:{PORT}"])


if __name__ == "__main__":
    main()
