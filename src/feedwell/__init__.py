"""feedwell package."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth: the version in pyproject.toml, as installed.
    __version__ = version("feedwell")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0.0.0"
