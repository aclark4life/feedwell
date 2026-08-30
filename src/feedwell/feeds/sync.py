"""Sync connected accounts' recent posts into the local Post table.

Deliberately synchronous/on-demand (no Celery/background workers) since
feedwell is single-user and local-first -- a "Refresh" button click or a
`feedwell django sync` invocation is enough.
"""

from __future__ import annotations

from datetime import UTC, datetime

from django.utils.dateparse import parse_datetime
from django.utils.html import escape

from .adapters import mastodon
from .adapters import x as x_adapter
from .adapters.mastodon import MastodonAPIError
from .adapters.x import XAPIError
from .models import Account, MediaItem, Metrics, Post


class SyncError(Exception):
    """Raised when a single account's sync fails; the caller decides whether to
    continue syncing other accounts or surface the error."""


def sync_account(account: Account) -> int:
    """Fetch and upsert recent posts for one connected account.

    Returns the number of posts created or updated.
    """
    if account.platform == "mastodon":
        return _sync_mastodon_account(account)
    if account.platform == "x":
        return _sync_x_account(account)
    raise SyncError(f"No sync support yet for {account.get_platform_display()}.")


def sync_all_accounts(owner) -> tuple[int, list[str]]:
    """Sync every account belonging to `owner`.

    Returns (total_posts_synced, list_of_error_messages) so the view can
    show a partial success (e.g. Mastodon synced fine, X isn't wired up yet)
    instead of failing the whole refresh over one broken account.
    """
    total = 0
    errors: list[str] = []
    for account in Account.objects.filter(owner=owner):
        try:
            total += sync_account(account)
        except SyncError as exc:
            errors.append(str(exc))
        except MastodonAPIError as exc:
            errors.append(f"{account}: {exc}")
        except XAPIError as exc:
            errors.append(f"{account}: {exc}")
    return total, errors


def _sync_mastodon_account(account: Account) -> int:
    statuses = mastodon.fetch_statuses(account)
    count = 0
    for status in statuses:
        if _upsert_mastodon_status(account, status):
            count += 1
    return count


def _sync_x_account(account: Account) -> int:
    if account.external_id.startswith("pending:"):
        _resolve_x_profile(account)

    payload = x_adapter.fetch_home_timeline(account)
    tweets = payload.get("data") or []
    users_by_id = {u["id"]: u for u in (payload.get("includes", {}).get("users") or [])}
    media_by_key = {m["media_key"]: m for m in (payload.get("includes", {}).get("media") or [])}
    count = 0
    for tweet in tweets:
        if _upsert_x_tweet(account, tweet, users_by_id, media_by_key):
            count += 1
    return count


def _resolve_x_profile(account: Account) -> None:
    """Fill in a placeholder X account's real identity once its profile
    becomes reachable (e.g. after enabling billing enrollment).

    Re-keying external_id from the "pending:<hash>" placeholder to the
    real X user ID means any Posts already synced under the placeholder
    stay attached (they're linked via account_id, not external_id), and
    future connects of the same X account correctly recognize it as
    already-connected instead of creating a duplicate.
    """
    user = x_adapter.fetch_profile(account.access_token)
    account.external_id = user.get("id") or account.external_id
    account.handle = user.get("username") or account.handle
    account.display_name = user.get("name") or account.handle
    account.avatar_url = user.get("profile_image_url") or account.avatar_url
    account.save(update_fields=["external_id", "handle", "display_name", "avatar_url"])


def _upsert_x_tweet(account: Account, tweet: dict, users_by_id: dict, media_by_key: dict) -> bool:
    author = users_by_id.get(tweet.get("author_id")) or {}
    metrics_data = tweet.get("public_metrics") or {}
    metrics = Metrics(
        likes=metrics_data.get("like_count") or 0,
        reposts=metrics_data.get("retweet_count") or 0,
        replies=metrics_data.get("reply_count") or 0,
    )
    media_keys = (tweet.get("attachments") or {}).get("media_keys") or []
    media = [
        MediaItem(
            url=(media_by_key.get(key) or {}).get("url") or "",
            media_type=(media_by_key.get(key) or {}).get("type") or "",
            alt_text=(media_by_key.get(key) or {}).get("alt_text") or "",
        )
        for key in media_keys
    ]
    handle = author.get("username") or ""
    posted_at = _parse_datetime(tweet.get("created_at")) or datetime.now(tz=UTC)

    _, created = Post.objects.update_or_create(
        account=account,
        platform="x",
        external_id=tweet["id"],
        defaults={
            "author_name": author.get("name") or handle,
            "author_handle": handle,
            "content": tweet.get("text") or "",
            "url": f"https://x.com/{handle}/status/{tweet['id']}" if handle else "",
            "posted_at": posted_at,
            "metrics": metrics,
            "media": media or None,
            "raw": tweet,
        },
    )
    return created


def _upsert_mastodon_status(account: Account, status: dict) -> bool:
    booster = status.get("account") or {}
    reblog = status.get("reblog")
    # Boosts carry no content/media/url of their own -- the actual post lives
    # in the nested "reblog" object. Unwrap it so boosted posts render with
    # real content instead of showing up blank, while noting who boosted it.
    content_source = reblog if reblog else status
    author = content_source.get("account") or {}
    booster_name = booster.get("display_name") or booster.get("acct") or ""
    boosted_by = (
        f"<p><em>🔁 Boosted by {escape(booster_name)}</em></p>" if reblog and booster_name else ""
    )

    posted_at = _parse_datetime(status.get("created_at")) or datetime.now(tz=UTC)
    metrics = Metrics(
        likes=content_source.get("favourites_count") or 0,
        reposts=content_source.get("reblogs_count") or 0,
        replies=content_source.get("replies_count") or 0,
    )
    media = [
        MediaItem(
            url=item.get("url") or "",
            media_type=item.get("type") or "",
            alt_text=item.get("description") or "",
        )
        for item in content_source.get("media_attachments") or []
    ]

    _, created = Post.objects.update_or_create(
        account=account,
        platform="mastodon",
        external_id=status["id"],
        defaults={
            "author_name": author.get("display_name") or author.get("acct") or "",
            "author_handle": author.get("acct") or "",
            "content": boosted_by + (content_source.get("content") or ""),
            "url": content_source.get("url") or "",
            "posted_at": posted_at,
            "metrics": metrics,
            "media": media or None,
            "raw": status,
        },
    )
    return created


def _parse_datetime(value: str | None):
    if not value:
        return None
    return parse_datetime(value)
