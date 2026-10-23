"""Like/boost/reply tests (no network or database required).

Platform calls are faked, and Post.save is a no-op, so these check what
each action sends and how it writes the result back onto the Post.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string

from feedwell.feeds import interactions
from feedwell.feeds.adapters import bluesky, mastodon
from feedwell.feeds.models import Account, Post


@pytest.fixture(autouse=True)
def no_db_save(monkeypatch):
    monkeypatch.setattr(Post, "save", lambda self, **kwargs: None)


def _post(platform: str, raw: dict, **fields) -> Post:
    account = Account(platform=platform, metadata={"instance_domain": "example.social"})
    return Post(
        account=account,
        platform=platform,
        posted_at=datetime.now(tz=UTC),
        raw=raw,
        **fields,
    )


def test_mastodon_like_targets_boosted_status(monkeypatch) -> None:
    calls = []

    def fake_set_favourite(account, status_id, on):
        calls.append((status_id, on))
        return {"id": status_id, "favourited": on, "favourites_count": 3}

    monkeypatch.setattr(mastodon, "set_favourite", fake_set_favourite)
    post = _post(
        "mastodon",
        {"id": "wrapper", "reblog": {"id": "s1", "favourited": False, "favourites_count": 2}},
    )
    assert not post.is_liked

    interactions.toggle_like(post)

    assert calls == [("s1", True)]
    assert post.raw["id"] == "wrapper"  # boost wrapper kept, inner status updated
    assert post.is_liked
    assert post.metrics.likes == 3


def test_mastodon_reblog_unwraps_response(monkeypatch) -> None:
    def fake_api(account, path, **kwargs):
        return {"id": "boost-of-s1", "reblog": {"id": "s1", "reblogged": True}}

    monkeypatch.setattr(mastodon, "_post_status_api", fake_api)
    assert mastodon.set_reblog(SimpleNamespace(), "s1", True) == {"id": "s1", "reblogged": True}


def test_bluesky_like_then_unlike(monkeypatch) -> None:
    created, deleted = [], []

    def fake_create(account, collection, record):
        created.append((collection, record))
        return "at://did:me/app.bsky.feed.like/abc"

    monkeypatch.setattr(bluesky, "create_record", fake_create)
    monkeypatch.setattr(bluesky, "delete_record", lambda account, uri: deleted.append(uri))
    post = _post("bluesky", {"post": {"uri": "at://did:them/p/1", "cid": "c1", "likeCount": 5}})

    interactions.toggle_like(post)
    assert created == [
        ("app.bsky.feed.like", {"subject": {"uri": "at://did:them/p/1", "cid": "c1"}})
    ]
    assert post.is_liked
    assert post.metrics.likes == 6

    interactions.toggle_like(post)
    assert deleted == ["at://did:me/app.bsky.feed.like/abc"]
    assert not post.is_liked
    assert post.metrics.likes == 5


def test_bluesky_reply_keeps_thread_root(monkeypatch) -> None:
    created = []
    monkeypatch.setattr(
        bluesky, "create_record", lambda account, collection, record: created.append(record)
    )
    root = {"uri": "at://did:op/p/0", "cid": "c0"}
    post = _post(
        "bluesky",
        {"post": {"uri": "at://did:them/p/1", "cid": "c1", "record": {"reply": {"root": root}}}},
    )

    interactions.reply(post, "hi")

    assert created == [
        {"text": "hi", "reply": {"root": root, "parent": {"uri": "at://did:them/p/1", "cid": "c1"}}}
    ]
    assert post.metrics.replies == 1


def test_x_has_no_interactions() -> None:
    post = _post("x", {})
    assert not post.supports_interactions
    with pytest.raises(interactions.InteractionError):
        interactions.toggle_like(post)


def test_bluesky_session_is_reused(monkeypatch) -> None:
    monkeypatch.setattr(bluesky, "_sessions", {})
    logins = []

    def fake_create_session(identifier, app_password):
        logins.append(identifier)
        return SimpleNamespace(access_token=f"jwt{len(logins)}", did="did:me")

    monkeypatch.setattr(bluesky, "create_session", fake_create_session)
    account = SimpleNamespace(metadata={"identifier": "me", "app_password": "pw"}, external_id="")
    assert bluesky._authenticate(account) == ("jwt1", "did:me")
    assert bluesky._authenticate(account) == ("jwt1", "did:me")
    assert bluesky._authenticate(account, fresh=True) == ("jwt2", "did:me")
    assert logins == ["me", "me"]


def test_actions_bar_renders_state_and_reply_prefill() -> None:
    post = _post(
        "mastodon",
        {"id": "s1", "favourited": True, "reblogged": False},
        author_handle="alice@other.social",
    )
    post.pk = "0123456789abcdef01234567"
    html = render_to_string("feeds/_post_actions.html", {"post": post})
    assert 'aria-pressed="true"' in html  # liked
    assert "♥" in html
    assert ">@alice@other.social </textarea>" in html
