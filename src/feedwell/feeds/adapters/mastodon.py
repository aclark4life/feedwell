"""Mastodon adapter: app registration, OAuth2 authorization, and post fetching.

Mastodon is federated -- there's no single API server like X or Facebook.
Each instance (mastodon.social, fosstodon.org, ...) runs the same Mastodon
API, so a client must:

  1. Register itself with that specific instance once (POST /api/v1/apps),
     getting back a client_id/client_secret pair specific to that instance.
  2. Send the user to that instance's OAuth authorize page.
  3. Exchange the returned code for an access token, scoped to that
     instance + app.
  4. Use the access token against that instance's API from then on.

See https://docs.joinmastodon.org/client/token/ for the full flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import requests

from ..models import MastodonApp

REQUEST_TIMEOUT = 10
SCOPES = "read"


class MastodonAPIError(Exception):
    """Raised when a Mastodon instance's API returns an unexpected response."""


def _request(method: str, url: str, **kwargs) -> requests.Response:
    """Wrap requests calls so network failures become friendly MastodonAPIErrors
    instead of raw exceptions bubbling up to a Django 500 page."""
    try:
        return requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise MastodonAPIError(f"Could not reach {url} ({exc.__class__.__name__}).") from exc


def normalize_instance_domain(raw: str) -> str:
    """Turn user input like 'https://mastodon.social/' or '@user@mastodon.social'
    into a bare domain: 'mastodon.social'.
    """
    domain = raw.strip().lower()
    domain = domain.removeprefix("https://").removeprefix("http://")
    domain = domain.split("/")[0]
    if "@" in domain:
        domain = domain.rsplit("@", 1)[-1]
    return domain


def get_or_register_app(instance_domain: str, redirect_uri: str) -> MastodonApp:
    """Return a cached MastodonApp for this instance, registering one if needed."""
    app = MastodonApp.objects.filter(instance_domain=instance_domain).first()
    if app is not None:
        return app

    response = _request(
        "post",
        f"https://{instance_domain}/api/v1/apps",
        data={
            "client_name": "feedwell",
            "redirect_uris": redirect_uri,
            "scopes": SCOPES,
            "website": "https://github.com/aclark4life/feedwell",
        },
    )
    if not response.ok:
        raise MastodonAPIError(
            f"Could not register feedwell with {instance_domain} ({response.status_code})."
        )

    data = response.json()
    return MastodonApp.objects.create(
        instance_domain=instance_domain,
        client_id=data["client_id"],
        client_secret=data["client_secret"],
    )


def build_authorize_url(app: MastodonApp, redirect_uri: str) -> str:
    params = {
        "client_id": app.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
    }
    return f"https://{app.instance_domain}/oauth/authorize?{urlencode(params)}"


@dataclass
class TokenResult:
    access_token: str
    account_id: str
    handle: str
    display_name: str
    avatar_url: str


def exchange_code_for_token(app: MastodonApp, code: str, redirect_uri: str) -> TokenResult:
    response = _request(
        "post",
        f"https://{app.instance_domain}/oauth/token",
        data={
            "client_id": app.client_id,
            "client_secret": app.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code": code,
            "scope": SCOPES,
        },
    )
    if not response.ok:
        raise MastodonAPIError(
            f"Could not complete login with {app.instance_domain} ({response.status_code})."
        )

    access_token = response.json()["access_token"]

    verify = _request(
        "get",
        f"https://{app.instance_domain}/api/v1/accounts/verify_credentials",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if not verify.ok:
        raise MastodonAPIError(
            f"Logged in but could not fetch account details from {app.instance_domain}."
        )

    account = verify.json()
    return TokenResult(
        access_token=access_token,
        account_id=account["id"],
        handle=account["acct"],
        display_name=account.get("display_name") or account["acct"],
        avatar_url=account.get("avatar", ""),
    )


def fetch_statuses(account) -> list[dict]:
    """Fetch recent statuses (posts) for a connected Mastodon account.

    Returns raw Mastodon API status dicts; normalization into Post rows
    happens in the sync layer so all adapters share one upsert path.
    """
    instance_domain = account.metadata.get("instance_domain")
    if not instance_domain:
        raise MastodonAPIError("This account is missing its Mastodon instance domain.")

    response = _request(
        "get",
        f"https://{instance_domain}/api/v1/accounts/{account.external_id}/statuses",
        headers={"Authorization": f"Bearer {account.access_token}"},
        params={"limit": 40, "exclude_replies": "true", "exclude_reblogs": "false"},
    )
    if not response.ok:
        raise MastodonAPIError(
            f"Could not fetch posts from {instance_domain} ({response.status_code})."
        )
    return response.json()
