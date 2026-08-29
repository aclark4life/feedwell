"""Basic smoke tests for feedwell."""

from feedwell import __version__


def test_version() -> None:
    assert __version__
