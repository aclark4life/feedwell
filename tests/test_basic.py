"""Basic smoke tests for feedwell."""

from feedwell import __version__


def test_version() -> None:
    assert __version__


def test_version_matches_package_metadata() -> None:
    from importlib.metadata import version

    assert __version__ == version("feedwell")
