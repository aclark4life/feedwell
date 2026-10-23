"""Home-timeline catch-up paging tests (no network or database required).

Each adapter's _request is replaced with a fake that serves canned pages,
recording the params of every call so the paging can be checked.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from feedwell.feeds import sync
from feedwell.feeds.adapters import bluesky, mastodon
from feedwell.feeds.adapters import x as x_adapter


class FakeResponse:
    ok = True
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def fake_request(monkeypatch, module, pages):
    """Serve `pages` in order from module._request; return the list of
    params dicts each call was made with."""
    calls: list[dict] = []
    remaining = list(pages)

    def _request(method, url, **kwargs):
        # Bluesky logs in (createSession + getProfile) before every fetch.
        if "createSession" in url:
            return FakeResponse({"accessJwt": "jwt", "did": "did:me", "handle": "me"})
        if "getProfile" in url:
            return FakeResponse({})
        calls.append(dict(kwargs.get("params") or {}))
        return FakeResponse(remaining.pop(0))

    monkeypatch.setattr(module, "_request", _request)
    if module is bluesky:
        monkeypatch.setattr(bluesky, "_sessions", {})  # no session reuse across tests
    return calls


@pytest.fixture
def mastodon_account():
    return SimpleNamespace(metadata={"instance_domain": "example.social"}, access_token="t")


def test_mastodon_first_sync_fetches_one_page(monkeypatch, mastodon_account) -> None:
    calls = fake_request(monkeypatch, mastodon, [[{"id": "3"}, {"id": "2"}]])
    statuses, resume = mastodon.fetch_statuses(mastodon_account)
    assert [s["id"] for s in statuses] == ["3", "2"]
    assert resume is None
    assert len(calls) == 1
    assert "since_id" not in calls[0]


def test_mastodon_catch_up_pages_back_to_since_id(monkeypatch, mastodon_account) -> None:
    calls = fake_request(monkeypatch, mastodon, [[{"id": "9"}, {"id": "8"}], [{"id": "7"}], []])
    statuses, resume = mastodon.fetch_statuses(mastodon_account, since_id="6")
    assert [s["id"] for s in statuses] == ["9", "8", "7"]
    assert resume is None
    assert [c.get("max_id") for c in calls] == [None, "8", "7"]
    assert all(c["since_id"] == "6" for c in calls)


def test_mastodon_catch_up_stops_at_chunk_with_resume_point(monkeypatch, mastodon_account) -> None:
    monkeypatch.setattr(mastodon, "CATCH_UP_CHUNK", 3)
    pages = [[{"id": str(i)} for i in range(n, n - 2, -1)] for n in (99, 97, 95)]
    calls = fake_request(monkeypatch, mastodon, pages)
    statuses, resume = mastodon.fetch_statuses(mastodon_account, since_id="1")
    assert len(statuses) == 4
    assert resume == "96"
    assert len(calls) == 2


def test_mastodon_resumes_from_max_id(monkeypatch, mastodon_account) -> None:
    calls = fake_request(monkeypatch, mastodon, [[{"id": "95"}], []])
    statuses, resume = mastodon.fetch_statuses(mastodon_account, since_id="1", max_id="96")
    assert [s["id"] for s in statuses] == ["95"]
    assert resume is None
    assert calls[0]["max_id"] == "96"


def _bsky_item(when: str, repost_at: str | None = None) -> dict:
    item = {"post": {"indexedAt": when}}
    if repost_at:
        item["reason"] = {"indexedAt": repost_at}
    return item


def test_bluesky_feed_item_time_prefers_repost_time() -> None:
    item = _bsky_item("2026-01-01T00:00:00.000Z", repost_at="2026-03-01T00:00:00.000Z")
    assert bluesky.feed_item_time(item) == datetime(2026, 3, 1, tzinfo=UTC)


def test_bluesky_catch_up_stops_at_since(monkeypatch) -> None:
    pages = [
        {
            "feed": [_bsky_item("2026-01-05T00:00:00Z"), _bsky_item("2026-01-04T00:00:00Z")],
            "cursor": "c1",
        },
        {
            "feed": [_bsky_item("2026-01-03T00:00:00Z"), _bsky_item("2026-01-01T00:00:00Z")],
            "cursor": "c2",
        },
    ]
    calls = fake_request(monkeypatch, bluesky, pages)
    account = SimpleNamespace(
        metadata={"identifier": "me", "app_password": "pw"}, external_id="did:me"
    )
    items, resume = bluesky.fetch_timeline(account, since=datetime(2026, 1, 2, tzinfo=UTC))
    assert resume is None
    assert [i["post"]["indexedAt"][:10] for i in items] == [
        "2026-01-05",
        "2026-01-04",
        "2026-01-03",
    ]
    assert [c.get("cursor") for c in calls] == [None, "c1"]


def test_x_catch_up_merges_pages(monkeypatch) -> None:
    pages = [
        {
            "data": [{"id": "30"}],
            "includes": {"users": [{"id": "u1"}]},
            "meta": {"newest_id": "30", "next_token": "n1"},
        },
        {"data": [{"id": "20"}], "includes": {"media": [{"media_key": "m1"}]}, "meta": {}},
    ]
    calls = fake_request(monkeypatch, x_adapter, pages)
    account = SimpleNamespace(external_id="me", access_token="t")
    payload = x_adapter.fetch_home_timeline(account, since_id="10")
    assert [t["id"] for t in payload["data"]] == ["30", "20"]
    assert payload["includes"] == {"users": [{"id": "u1"}], "media": [{"media_key": "m1"}]}
    assert payload["meta"]["newest_id"] == "30"
    assert [c.get("pagination_token") for c in calls] == [None, "n1"]


def test_bluesky_catch_up_stops_at_chunk_with_cursor(monkeypatch) -> None:
    monkeypatch.setattr(bluesky, "CATCH_UP_CHUNK", 2)
    pages = [
        {
            "feed": [_bsky_item("2026-01-05T00:00:00Z"), _bsky_item("2026-01-04T00:00:00Z")],
            "cursor": "c1",
        },
    ]
    fake_request(monkeypatch, bluesky, pages)
    account = SimpleNamespace(
        metadata={"identifier": "me", "app_password": "pw"}, external_id="did:me"
    )
    items, resume = bluesky.fetch_timeline(account, since=datetime(2026, 1, 1, tzinfo=UTC))
    assert len(items) == 2
    assert resume == "c1"


# Sync layer: backfill bookkeeping in Account.metadata. Fetches and upserts
# are faked, and the account is a stand-in whose save() is a no-op.


def _account(**metadata):
    return SimpleNamespace(metadata=dict(metadata), save=lambda **kwargs: None)


def test_sync_backfill_then_advance(monkeypatch) -> None:
    pages = iter(
        [
            ([{"id": "99"}, {"id": "98"}], "98"),  # first chunk: more remains
            ([{"id": "97"}], None),  # resumed chunk: caught up
        ]
    )
    seen: list[tuple] = []

    def fake_fetch(account, since_id=None, max_id=None):
        seen.append((since_id, max_id))
        return next(pages)

    monkeypatch.setattr(sync.mastodon, "fetch_statuses", fake_fetch)
    monkeypatch.setattr(sync, "_upsert_mastodon_status", lambda account, status: True)
    account = _account(home_since_id="50")

    assert sync._sync_mastodon_account(account) == (2, True)
    assert account.metadata["home_since_id"] == "50"  # not advanced past the gap yet
    assert account.metadata["home_backfill"] == {"resume": "98", "top": "99"}

    assert sync._sync_mastodon_account(account) == (1, False)
    assert account.metadata["home_since_id"] == "99"
    assert "home_backfill" not in account.metadata
    assert seen == [("50", None), ("50", "98")]


def test_sync_bluesky_since_is_newest_feed_time(monkeypatch) -> None:
    items = [
        _bsky_item("2026-01-04T00:00:00Z"),
        _bsky_item("2026-01-01T00:00:00Z", "2026-01-05T00:00:00Z"),
    ]
    monkeypatch.setattr(
        sync.bluesky, "fetch_timeline", lambda account, since, cursor: (items, None)
    )
    monkeypatch.setattr(sync, "_upsert_bluesky_post", lambda account, item: True)
    account = _account()

    assert sync._sync_bluesky_account(account) == (2, False)
    assert account.metadata["home_since"] == "2026-01-05T00:00:00+00:00"
