"""feedwell.toml config file: local storage for third-party API credentials.

Environment variables (FEEDWELL_X_CLIENT_ID, etc.) always take priority so
existing setups keep working -- feedwell.toml is just a friendlier way to
store the same values persistently without exporting env vars every time.

Only a small, flat set of string keys is supported, so this hand-rolls a
minimal TOML writer instead of adding a dependency just for that.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

CONFIG_FILENAME = "feedwell.toml"

# (section, key, env var name, help comment)
CONFIG_FIELDS = [
    ("x", "client_id", "FEEDWELL_X_CLIENT_ID", "X (Twitter) OAuth2 app client ID"),
    ("x", "client_secret", "FEEDWELL_X_CLIENT_SECRET", "X (Twitter) OAuth2 app client secret"),
]

_TEMPLATE = """\
# feedwell.toml -- local credentials for connecting social media platforms.
#
# Environment variables (e.g. FEEDWELL_X_CLIENT_ID) always take priority over
# this file if both are set. This file just saves you from exporting them
# every time you run `feedwell`.
#
# Get X API credentials by creating an app at https://developer.x.com/
# (free signup) with OAuth 2.0 enabled, then paste them below.

[x]
# client_id = "your-x-client-id"
# client_secret = "your-x-client-secret"
"""


def config_path(base_dir: Path | None = None) -> Path:
    return (base_dir or Path.cwd()) / CONFIG_FILENAME


def ensure_config_file(base_dir: Path | None = None) -> Path:
    """Create feedwell.toml with a commented-out template if it doesn't exist yet."""
    path = config_path(base_dir)
    if not path.exists():
        path.write_text(_TEMPLATE)
    return path


def load_config(base_dir: Path | None = None) -> dict:
    """Return the parsed contents of feedwell.toml, or {} if missing/invalid."""
    path = config_path(base_dir)
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError:
        return {}


def get(section: str, key: str, base_dir: Path | None = None) -> str:
    """Look up a single value from feedwell.toml, or "" if missing."""
    return str(load_config(base_dir).get(section, {}).get(key, "") or "")


def set_value(section: str, key: str, value: str, base_dir: Path | None = None) -> Path:
    """Write a single key under [section] in feedwell.toml, creating the file
    or the section as needed, preserving other existing values."""
    path = ensure_config_file(base_dir)
    data = load_config(base_dir)
    data.setdefault(section, {})[key] = value
    path.write_text(_render(data))
    return path


def _render(data: dict) -> str:
    lines = [
        "# feedwell.toml -- local credentials for connecting social media platforms.",
        "# Environment variables (e.g. FEEDWELL_X_CLIENT_ID) always take priority.",
        "",
    ]
    for section, values in data.items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f'{key} = "{_escape(str(value))}"')
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
