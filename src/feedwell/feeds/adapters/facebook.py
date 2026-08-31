"""Facebook Login adapter: OAuth2 authorization and basic profile lookup only.

Facebook's Graph API has never allowed third-party apps to read a
personal News Feed -- Meta removed that permission in 2018 (post
Cambridge Analytica) and it has no successor. There is no scope this
app (or any app) can request that returns feed posts for a personal
profile. See https://developers.facebook.com/docs/facebook-login/

Because of that, this adapter intentionally does NOT implement any
timeline/feed fetch -- there's no endpoint to call. It only implements
enough of standard OAuth2 (no PKCE required by Facebook) to let a user
"Connect Facebook" and prove who they are via `public_profile`, mirroring
X's connect UX without pretending posts will ever sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import requests
from django.conf import settings

REQUEST_TIMEOUT = 10
GRAPH_API_VERSION = "v21.0"
AUTHORIZE_URL = "https://www.facebook.com/v21.0/dialog/oauth"
TOKEN_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}/oauth/access_token"
API_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
SCOPES = "public_profile"


class FacebookAPIError(Exception):
    """Raised when Facebook's API returns an unexpected response, or isn't reachable."""


def _request(method: str, url: str, **kwargs) -> requests.Response:
    try:
        return requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise FacebookAPIError(f"Could not reach {url} ({exc.__class__.__name__}).") from exc


def build_authorize_url(redirect_uri: str, state: str) -> str:
    params = {
        "client_id": getattr(settings, "FACEBOOK_CLIENT_ID", ""),
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
        "response_type": "code",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


@dataclass
class TokenResult:
    access_token: str
    account_id: str
    handle: str
    display_name: str
    avatar_url: str


def exchange_code_for_token(code: str, redirect_uri: str) -> TokenResult:
    client_id = getattr(settings, "FACEBOOK_CLIENT_ID", "")
    client_secret = getattr(settings, "FACEBOOK_CLIENT_SECRET", "")
    if not client_id:
        raise FacebookAPIError(
            "No Facebook API credentials configured. Set FEEDWELL_FACEBOOK_CLIENT_ID "
            "and FEEDWELL_FACEBOOK_CLIENT_SECRET to connect a Facebook account."
        )

    response = _request(
        "get",
        TOKEN_URL,
        params={
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code": code,
        },
    )
    if not response.ok:
        raise FacebookAPIError(
            f"Could not complete login with Facebook "
            f"({response.status_code}): {response.text[:300]}"
        )

    access_token = response.json().get("access_token", "")

    me = _request(
        "get",
        f"{API_BASE}/me",
        params={"fields": "id,name,picture", "access_token": access_token},
    )
    if not me.ok:
        raise FacebookAPIError(
            f"Logged in but could not fetch your Facebook profile "
            f"({me.status_code}): {me.text[:300]}"
        )

    user = me.json()
    return TokenResult(
        access_token=access_token,
        account_id=user.get("id", ""),
        handle=user.get("id", ""),
        display_name=user.get("name", ""),
        avatar_url=(user.get("picture") or {}).get("data", {}).get("url", ""),
    )
