"""Bluesky (AT Protocol) adapter: app-password login and post fetching.

Unlike X/Facebook (OAuth2) or Mastodon (OAuth2 + per-instance app
registration), Bluesky's simplest supported login for third-party apps is
an "app password" (a per-app credential a user generates at
https://bsky.app/settings/app-passwords, separate from their real
account password) exchanged directly for a session via
com.atproto.server.createSession -- no redirect/callback dance needed.

Session tokens (accessJwt) are short-lived (about 2 hours), and rather
than juggle refreshJwt renewal, this adapter just re-authenticates with
the stored identifier/app password at the start of every sync -- simple
and correct for a local, on-demand-refresh app like feedwell.

See https://docs.bsky.app for the full API reference.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

REQUEST_TIMEOUT = 10
API_BASE = "https://bsky.social/xrpc"
POSTS_PER_PAGE = 40  # Bluesky's default/max "limit" per request
MAX_OWN_POSTS = 200  # how far back "My posts" pages via cursor per sync


class BlueskyAPIError(Exception):
    """Raised when Bluesky's API returns an unexpected response, or isn't reachable."""


def _request(method: str, url: str, **kwargs) -> requests.Response:
    try:
        return requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise BlueskyAPIError(f"Could not reach {url} ({exc.__class__.__name__}).") from exc


@dataclass
class SessionResult:
    access_token: str
    did: str
    handle: str
    display_name: str
    avatar_url: str


def create_session(identifier: str, app_password: str) -> SessionResult:
    """Log in with an identifier (handle or email) + app password.

    Raises a friendly BlueskyAPIError on bad credentials so the connect
    form can show it inline instead of a raw 401.
    """
    response = _request(
        "post",
        f"{API_BASE}/com.atproto.server.createSession",
        json={"identifier": identifier.strip(), "password": app_password.strip()},
    )
    if not response.ok:
        if response.status_code in (400, 401):
            raise BlueskyAPIError(
                "Bluesky rejected that handle/app password. Double check the handle "
                "(e.g. yourname.bsky.social) and that you generated an app password "
                "at bsky.app/settings/app-passwords, not your real account password."
            )
        raise BlueskyAPIError(f"Could not log in to Bluesky ({response.status_code}).")

    data = response.json()
    access_token = data["accessJwt"]
    did = data["did"]

    profile = fetch_profile(access_token, did)
    return SessionResult(
        access_token=access_token,
        did=did,
        handle=profile.get("handle") or data.get("handle", ""),
        display_name=profile.get("displayName") or profile.get("handle", ""),
        avatar_url=profile.get("avatar", ""),
    )


def fetch_profile(access_token: str, did: str) -> dict:
    response = _request(
        "get",
        f"{API_BASE}/app.bsky.actor.getProfile",
        headers={"Authorization": "Bearer " + access_token},
        params={"actor": did},
    )
    if not response.ok:
        raise BlueskyAPIError(f"Could not fetch your Bluesky profile ({response.status_code}).")
    return response.json()


def _authenticate(account) -> tuple[str, str]:
    """Re-authenticate using the identifier/app password saved at connect time,
    returning (access_token, did). See module docstring for why this
    re-logs-in every sync rather than juggling refresh tokens."""
    identifier = account.metadata.get("identifier")
    app_password = account.metadata.get("app_password")
    if not identifier or not app_password:
        raise BlueskyAPIError("This account is missing its Bluesky login details.")
    session = create_session(identifier, app_password)
    return session.access_token, account.external_id or session.did


def fetch_timeline(account) -> list[dict]:
    """Fetch the account's home timeline: posts from accounts they follow,
    which is what feedwell's unified "feed" is meant to show.

    Returns raw feed-view items (each with a "post" and optional "reason"
    for reposts); normalization into Post rows happens in the sync layer.
    """
    access_token, _ = _authenticate(account)
    response = _request(
        "get",
        f"{API_BASE}/app.bsky.feed.getTimeline",
        headers={"Authorization": "Bearer " + access_token},
        params={"limit": POSTS_PER_PAGE},
    )
    if not response.ok:
        raise BlueskyAPIError(f"Could not fetch your Bluesky feed ({response.status_code}).")
    return response.json().get("feed", [])


def fetch_own_posts(account) -> list[dict]:
    """Fetch the account's own posts (its profile's post feed), paging
    through multiple requests via Bluesky's "cursor" until it's gathered
    MAX_OWN_POSTS posts or run out of history. Powers feedwell's separate
    "My posts" feed.
    """
    access_token, did = _authenticate(account)

    items: list[dict] = []
    cursor: str | None = None
    while len(items) < MAX_OWN_POSTS:
        params = {"actor": did, "filter": "posts_no_replies", "limit": POSTS_PER_PAGE}
        if cursor:
            params["cursor"] = cursor
        response = _request(
            "get",
            f"{API_BASE}/app.bsky.feed.getAuthorFeed",
            headers={"Authorization": "Bearer " + access_token},
            params=params,
        )
        if not response.ok:
            raise BlueskyAPIError(f"Could not fetch your Bluesky posts ({response.status_code}).")
        payload = response.json()
        batch = payload.get("feed") or []
        if not batch:
            break
        items.extend(batch)
        cursor = payload.get("cursor")
        if not cursor:
            break
    return items
