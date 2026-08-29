"""X (formerly Twitter) adapter: OAuth2 PKCE authorization and post fetching.

Unlike Mastodon, X has a single central API (no per-instance registration)
but requires PKCE (a code_verifier/code_challenge pair) as part of its
OAuth2 authorization code flow. See
https://developer.x.com/en/docs/authentication/oauth-2-0/authorization-code

Reading a home timeline requires X's paid API tiers (Basic and above) --
the free tier only allows posting and looking up your own profile. This
adapter still implements the full connect + fetch flow so a paid key can
be dropped in later without further code changes; on the free tier,
fetch_home_timeline will raise a friendly XAPIError explaining why no
posts came back.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import requests
from django.conf import settings

REQUEST_TIMEOUT = 10
AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
API_BASE = "https://api.x.com/2"
SCOPES = "tweet.read users.read offline.access"


class XAPIError(Exception):
    """Raised when X's API returns an unexpected response, or isn't reachable."""


def _request(method: str, url: str, **kwargs) -> requests.Response:
    try:
        return requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise XAPIError(f"Could not reach {url} ({exc.__class__.__name__}).") from exc


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for the OAuth2 PKCE handshake."""
    code_verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def build_authorize_url(redirect_uri: str, state: str, code_challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": settings.X_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


@dataclass
class TokenResult:
    access_token: str
    refresh_token: str
    account_id: str
    handle: str
    display_name: str
    avatar_url: str


def exchange_code_for_token(code: str, redirect_uri: str, code_verifier: str) -> TokenResult:
    if not settings.X_CLIENT_ID:
        raise XAPIError(
            "No X API credentials configured. Set FEEDWELL_X_CLIENT_ID and "
            "FEEDWELL_X_CLIENT_SECRET to connect an X account."
        )

    response = _request(
        "post",
        TOKEN_URL,
        auth=(settings.X_CLIENT_ID, settings.X_CLIENT_SECRET),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
    )
    if not response.ok:
        raise XAPIError(f"Could not complete login with X ({response.status_code}).")

    tokens = response.json()
    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token", "")

    me = _request(
        "get",
        f"{API_BASE}/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"user.fields": "profile_image_url,name,username"},
    )
    if not me.ok:
        raise XAPIError("Logged in but could not fetch your X account details.")

    user = me.json().get("data", {})
    return TokenResult(
        access_token=access_token,
        refresh_token=refresh_token,
        account_id=user.get("id", ""),
        handle=user.get("username", ""),
        display_name=user.get("name") or user.get("username", ""),
        avatar_url=user.get("profile_image_url", ""),
    )


def fetch_home_timeline(account) -> list[dict]:
    """Fetch the account's home timeline.

    Note: X's free API tier does not permit reading any timeline -- this
    will return a 403/401 and raise XAPIError until the connected app has
    at least the Basic paid tier. Kept as a real call (not a stub) so
    upgrading later just works without further code changes.
    """
    response = _request(
        "get",
        f"{API_BASE}/users/{account.external_id}/timelines/reverse_chronological",
        headers={"Authorization": f"Bearer {account.access_token}"},
        params={
            "max_results": 40,
            "tweet.fields": "created_at,public_metrics,author_id",
            "expansions": "author_id,attachments.media_keys",
            "media.fields": "url,type,alt_text",
        },
    )
    if not response.ok:
        raise XAPIError(
            f"Could not fetch your X feed ({response.status_code}). "
            "Reading timelines requires a paid X API tier (Basic or higher)."
        )
    return response.json()
