"""Sync connected accounts' recent posts into the local Post table.

Deliberately synchronous/on-demand (no Celery/background workers) since
feedwell is single-user and local-first -- a "Refresh" button click or a
`feedwell django sync` invocation is enough.
"""

from __future__ import annotations

from datetime import UTC, datetime

from django.utils.dateparse import parse_datetime

from .adapters import mastodon
from .adapters.mastodon import MastodonAPIError
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
    return total, errors


def _sync_mastodon_account(account: Account) -> int:
    statuses = mastodon.fetch_statuses(account)
    count = 0
    for status in statuses:
        if _upsert_mastodon_status(account, status):
            count += 1
    return count


def _upsert_mastodon_status(account: Account, status: dict) -> bool:
    posted_at = _parse_datetime(status.get("created_at")) or datetime.now(tz=UTC)
    author = status.get("account") or {}
    metrics = Metrics(
        likes=status.get("favourites_count") or 0,
        reposts=status.get("reblogs_count") or 0,
        replies=status.get("replies_count") or 0,
    )
    media = [
        MediaItem(
            url=item.get("url") or "",
            media_type=item.get("type") or "",
            alt_text=item.get("description") or "",
        )
        for item in status.get("media_attachments") or []
    ]

    _, created = Post.objects.update_or_create(
        account=account,
        platform="mastodon",
        external_id=status["id"],
        defaults={
            "author_name": author.get("display_name") or author.get("acct") or "",
            "author_handle": author.get("acct") or "",
            "content": status.get("content") or "",
            "url": status.get("url") or "",
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
