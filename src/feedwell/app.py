"""feedwell - All social media clients suck. This one just sucks less.

Console entry point (Typer CLI). Running `feedwell` with no subcommand:

  1. Makes sure a MongoDB instance is reachable, auto-starting a local,
     throwaway one via `mongodb-runner` if nothing else is available.
  2. Applies database migrations.
  3. Creates a default local admin account if none exists yet.
  4. Starts the dev server and opens it in your browser.

`feedwell django <command>` is an escape hatch that forwards to Django's
manage.py commands (`migrate`, `createsuperuser`, `makemigrations`, ...) for
advanced use. Pass `--debug` to see full tracebacks and verbose output
instead of feedwell's plain-English error messages.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import webbrowser

import typer

HOST = "127.0.0.1"
PORT = "8000"
DEFAULT_MONGODB_URI = "mongodb://localhost:27017/"
RUNNER_ID = "feedwell-dev"

app = typer.Typer(
    name="feedwell",
    help="All social media clients suck. This one just sucks less.",
    add_completion=False,
)


class FriendlyError(Exception):
    """An error whose message alone (no traceback) should be shown to users."""


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    debug: bool = typer.Option(False, "--debug", help="Show full tracebacks and verbose output."),
) -> None:
    ctx.obj = {"debug": debug}
    if ctx.invoked_subcommand is None:
        _safe_run(_serve, debug=debug)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help="Forward to a Django manage.py command, e.g. `feedwell django createsuperuser`.",
)
def django(ctx: typer.Context) -> None:
    debug = ctx.obj["debug"]
    _safe_run(_run_django_command, ctx.args, debug=debug)


@app.command(
    "config-set",
    help="Save a platform credential in feedwell.toml, e.g. "
    "`feedwell config-set x.client_id abc123`.",
)
def config_set(ctx: typer.Context, key: str, value: str) -> None:
    debug = ctx.obj["debug"]
    _safe_run(_config_set, key, value, debug=debug)


def _config_set(key: str, value: str, *, debug: bool) -> None:
    from feedwell import config as feedwell_config

    if "." not in key:
        raise FriendlyError(
            f"key must be in 'section.name' form, e.g. 'x.client_id' (got {key!r})."
        )
    section, name = key.split(".", 1)
    path = feedwell_config.set_value(section, name, value)
    print(f"feedwell: saved {key} to {path}")


def _safe_run(func, *args, debug: bool, **kwargs) -> None:
    try:
        func(*args, debug=debug, **kwargs)
    except KeyboardInterrupt:
        print()
    except FriendlyError as exc:
        _fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - intentionally broad at the top level
        if debug:
            raise
        _fail(f"unexpected error ({exc}). Re-run with --debug for details.")


def _run_django_command(args: list[str], *, debug: bool) -> None:
    _ensure_mongodb(["django", *args], debug=debug)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "feedwell.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line([sys.argv[0], *args])


def _serve(*, debug: bool) -> None:
    _ensure_mongodb([], debug=debug)

    from feedwell import config as feedwell_config

    config_path = feedwell_config.ensure_config_file()

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "feedwell.settings")
    import django

    django.setup()
    from django.core.management import call_command

    verbosity = 1 if debug else 0

    # Django's autoreloader works via an outer "watcher" process that spawns
    # a fresh inner subprocess (RUN_MAIN=true) each time it restarts the
    # server after a code change -- so RUN_MAIN alone can't tell "first
    # launch" from "just reloaded after an edit". The outer watcher process,
    # however, runs this exact line exactly once per `feedwell` invocation,
    # before the reloader loop ever starts, so do one-time setup there.
    is_reloader_child = os.environ.get("RUN_MAIN") == "true"
    if not is_reloader_child:
        call_command("migrate", interactive=False, verbosity=verbosity)
        _ensure_default_admin()
        print(f"feedwell: platform credentials can be set in {config_path}")

        url = f"http://{HOST}:{PORT}/"
        print(f"feedwell running at {url} (Ctrl+C to stop)")
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()


    call_command("runserver", f"{HOST}:{PORT}", use_reloader=True, verbosity=verbosity)


def _ensure_default_admin() -> None:
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    if user_model.objects.filter(is_superuser=True).exists():
        return
    user_model.objects.create_superuser(username="admin", email="", password="admin")
    print("feedwell: created local admin account -> username: admin / password: admin (change this at /admin/)")


def _mongo_reachable(uri: str, timeout: float = 1.0) -> bool:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    client = None
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=int(timeout * 1000))
        client.admin.command("ping")
        return True
    except PyMongoError:
        return False
    finally:
        if client is not None:
            client.close()


def _ensure_mongodb(reexec_args: list[str], *, debug: bool) -> None:
    """Make sure MONGODB_URI points at a reachable MongoDB instance.

    If none is configured or running, try to auto-start an ephemeral local
    one via `mongodb-runner` and re-run this exact command under it (that
    tool sets MONGODB_URI for the child process and tears the instance down
    when it exits).
    """
    uri = os.environ.get("MONGODB_URI")
    if uri:
        if _mongo_reachable(uri):
            return
        raise FriendlyError(
            f"could not reach MongoDB at {uri!r}. Check that it's running and that MONGODB_URI is correct."
        )

    if _mongo_reachable(DEFAULT_MONGODB_URI):
        os.environ["MONGODB_URI"] = DEFAULT_MONGODB_URI
        return

    npx = shutil.which("npx")
    if not npx:
        raise FriendlyError(
            "no MongoDB found running locally, and Node.js/npx isn't installed "
            "so feedwell can't auto-start one.\n"
            "  Either start MongoDB yourself and set MONGODB_URI, or\n"
            "  install Node.js (https://nodejs.org) so feedwell can manage a "
            "local instance automatically."
        )

    print("feedwell: no MongoDB found, starting a local instance (mongodb-runner)...")
    extra = ["--debug"] if debug else []
    cmd = [
        npx,
        "--yes",
        "mongodb-runner",
        "exec",
        "--id",
        RUNNER_ID,
        "--",
        sys.executable,
        "-m",
        "feedwell.app",
        *extra,
        *reexec_args,
    ]
    os.execvp(cmd[0], cmd)


def _fail(message: str) -> None:
    print(f"feedwell: {message}", file=sys.stderr)
    sys.exit(1)


def main_entry() -> None:
    app()


if __name__ == "__main__":
    main_entry()
