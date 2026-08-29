"""Django app-loading smoke tests (no database connection required)."""

from feedwell.feeds.models import Account, Post


def test_models_import() -> None:
    assert Account._meta.app_label == "feeds"
    assert Post._meta.app_label == "feeds"
