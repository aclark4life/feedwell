"""Sync connected accounts' recent posts into the local Post table.

Deliberately synchronous/on-demand (no Celery/background workers) since
feedwell is single-user and local-first -- a "Refresh" button click or a
`feedwell django sync` invocation is enough.
"""

from __future__ import annotations

from datetime import UTC, datetime

from django.utils.dateparse import parse_datetime
from django.utils.html import escape

from .adapters import bluesky
from .adapters import mastodon
from .adapters import x as x_adapter
from .adapters.bluesky import BlueskyAPIError
from .adapters.mastodon import MastodonAPIError
from .adapters.x import XAPIError
from .models import Account, MediaItem, Metrics, Post


class SyncError(Exception):
    """Raised when a single account's sync fails; the caller decides whether to
    continue syncing other accounts or surface the error."""


def sync_account(account: Account) -> tuple[int, bool]:
    """Fetch and upsert recent posts for one connected account.

    Returns (number of posts created or updated, whether a catch-up is still
    in progress and another sync should continue it -- see HOME_BACKFILL).
    """
    if account.platform == "mastodon":
        return _sync_mastodon_account(account)
    if account.platform == "x":
        return _sync_x_account(account)
    if account.platform == "bluesky":
        return _sync_bluesky_account(account)
    if account.platform == "facebook":
        # Facebook Login is connect-only (see adapters/facebook.py): there's
        # no API for reading a personal News Feed, so there's nothing to
        # sync, ever, for any account on this platform.
        return 0, False
    raise SyncError(f"No sync support yet for {account.get_platform_display()}.")


def sync_all_accounts(owner, catching_up: bool = False) -> tuple[int, list[str], bool]:
    """Sync every account belonging to `owner`.

    Returns (total_posts_synced, list_of_error_messages, more_to_catch_up)
    so the view can show a partial success (e.g. Mastodon synced fine, X
    isn't wired up yet) instead of failing the whole refresh over one broken
    account, and know whether to continue a catch-up with another request.

    With catching_up=True (those follow-up requests), only accounts with a
    backfill in progress are synced; the rest were just synced moments ago,
    and re-polling them would cost X API reads for nothing.
    """
    total = 0
    errors: list[str] = []
    more = False
    for account in Account.objects.filter(owner=owner):
        if catching_up and HOME_BACKFILL not in account.metadata:
            continue
        try:
            count, account_more = sync_account(account)
            total += count
            more = more or account_more
        except SyncError as exc:
            errors.append(str(exc))
        except MastodonAPIError as exc:
            errors.append(f"{account}: {exc}")
        except BlueskyAPIError as exc:
            errors.append(f"{account}: {exc}")
        except XAPIError as exc:
            # For an X account still awaiting profile resolution (shown as
            # "(pending profile)"), the connect-time flash message already
            # explained why (e.g. X's billing enrollment requirement).
            # Don't repeat that same explanation on every Refresh click --
            # it'll resolve itself silently once the underlying issue is
            # fixed, via _resolve_x_profile().
            if account.external_id.startswith("pending:"):
                continue
            errors.append(f"{account}: {exc}")
    return total, errors, more


# Home-timeline sync state, kept in Account.metadata (see
# fetch_statuses/fetch_timeline/fetch_home_timeline):
#
# - The "since" cursor is how far up the timeline we're fully synced; the
#   next Refresh pages back down to exactly there.
# - A catch-up too big for one request leaves a HOME_BACKFILL entry: where
#   to resume paging down from, plus the newest point of this catch-up
#   ("top"). The since cursor only moves up to "top" once the backfill
#   reaches it, so an interrupted catch-up resumes instead of leaving a gap.
#
# Both only change after every post in the batch is upserted, so a failed
# sync just retries.
HOME_SINCE_ID = "home_since_id"  # Mastodon, X: newest status/tweet id synced
HOME_SINCE = "home_since"  # Bluesky: ISO feed time of the newest item synced
HOME_BACKFILL = "home_backfill"  # {"resume": max_id or cursor, "top": since value}


def _advance(account: Account, since_key: str, top: str | None, resume: str | None) -> None:
    """Record a sync's progress: either a pending backfill, or (when caught
    up) move the since cursor to the top of what was fetched."""
    before = dict(account.metadata)
    if resume:
        account.metadata[HOME_BACKFILL] = {"resume": resume, "top": top}
    else:
        account.metadata.pop(HOME_BACKFILL, None)
        if top:
            account.metadata[since_key] = top
    if account.metadata != before:
        account.save(update_fields=["metadata"])


def _sync_mastodon_account(account: Account) -> tuple[int, bool]:
    backfill = account.metadata.get(HOME_BACKFILL)
    statuses, resume = mastodon.fetch_statuses(
        account,
        since_id=account.metadata.get(HOME_SINCE_ID),
        max_id=backfill["resume"] if backfill else None,
    )
    count = 0
    for status in statuses:
        if _upsert_mastodon_status(account, status):
            count += 1
    top = backfill["top"] if backfill else (statuses[0]["id"] if statuses else None)
    _advance(account, HOME_SINCE_ID, top, resume)
    return count, resume is not None


def _sync_bluesky_account(account: Account) -> tuple[int, bool]:
    backfill = account.metadata.get(HOME_BACKFILL)
    items, resume = bluesky.fetch_timeline(
        account,
        since=_parse_datetime(account.metadata.get(HOME_SINCE)),
        cursor=backfill["resume"] if backfill else None,
    )
    count = 0
    for item in items:
        if _upsert_bluesky_post(account, item):
            count += 1
    if backfill:
        top = backfill["top"]
    else:
        times = [t for t in map(bluesky.feed_item_time, items) if t]
        top = max(times).isoformat() if times else None
    _advance(account, HOME_SINCE, top, resume)
    return count, resume is not None


def sync_my_posts(owner) -> tuple[int, list[str]]:
    """Sync the signed-in user's own posts (not the aggregated home timeline)
    into the Post table, tagged is_own=True, powering the separate "My
    posts" feed. Mastodon and Bluesky only for now, since they're the only
    platforms whose connect flows can fetch this.
    """
    total = 0
    errors: list[str] = []
    for account in Account.objects.filter(owner=owner, platform="mastodon"):
        try:
            statuses = mastodon.fetch_own_statuses(account)
        except MastodonAPIError as exc:
            errors.append(f"{account}: {exc}")
            continue
        for status in statuses:
            if _upsert_mastodon_status(account, status, own=True):
                total += 1
    for account in Account.objects.filter(owner=owner, platform="bluesky"):
        try:
            items = bluesky.fetch_own_posts(account)
        except BlueskyAPIError as exc:
            errors.append(f"{account}: {exc}")
            continue
        for item in items:
            if _upsert_bluesky_post(account, item, own=True):
                total += 1
    return total, errors


def _sync_x_account(account: Account) -> tuple[int, bool]:
    if account.external_id.startswith("pending:"):
        _resolve_x_profile(account)

    payload = x_adapter.fetch_home_timeline(account, since_id=account.metadata.get(HOME_SINCE_ID))
    tweets = payload.get("data") or []
    users_by_id = {u["id"]: u for u in (payload.get("includes", {}).get("users") or [])}
    media_by_key = {m["media_key"]: m for m in (payload.get("includes", {}).get("media") or [])}
    count = 0
    for tweet in tweets:
        if _upsert_x_tweet(account, tweet, users_by_id, media_by_key):
            count += 1
    _advance(account, HOME_SINCE_ID, (payload.get("meta") or {}).get("newest_id"), None)
    return count, False


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
    # X only returns "url" for photos; videos and GIFs just get a
    # preview_image_url (the playable variants need a separate field and
    # are often auth-gated), so keep the preview to show a thumbnail.
    media = [
        MediaItem(
            url=item.get("url") or "",
            preview_url=item.get("preview_image_url") or "",
            media_type=item.get("type") or "",
            alt_text=item.get("alt_text") or "",
        )
        for item in (media_by_key.get(key) or {} for key in media_keys)
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


def _upsert_mastodon_status(account: Account, status: dict, own: bool = False) -> bool:
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
            preview_url=item.get("preview_url") or "",
            media_type=item.get("type") or "",
            alt_text=item.get("description") or "",
        )
        for item in content_source.get("media_attachments") or []
    ]

    defaults = {
        "author_name": author.get("display_name") or author.get("acct") or "",
        "author_handle": author.get("acct") or "",
        "content": boosted_by + (content_source.get("content") or ""),
        "url": content_source.get("url") or "",
        "posted_at": posted_at,
        "metrics": metrics,
        "media": media or None,
        "raw": status,
    }
    if own:
        # Only ever set is_own=True, never flip it back to False -- a post
        # picked up by the home-timeline sync too (e.g. seeing your own
        # boost) is still one of "my posts".
        defaults["is_own"] = True

    _, created = Post.objects.update_or_create(
        account=account,
        platform="mastodon",
        external_id=status["id"],
        defaults=defaults,
    )
    return created


def _upsert_bluesky_post(account: Account, item: dict, own: bool = False) -> bool:
    post = item.get("post") or {}
    reason = item.get("reason") or {}
    reposter = reason.get("by") or {}
    is_repost = reason.get("$type") == "app.bsky.feed.defs#reasonRepost"
    reposter_name = reposter.get("displayName") or reposter.get("handle") or ""
    reposted_by = (
        f"<p><em>🔁 Reposted by {escape(reposter_name)}</em></p>" if is_repost and reposter_name else ""
    )

    author = post.get("author") or {}
    record = post.get("record") or {}
    handle = author.get("handle") or ""
    uri = post.get("uri") or ""
    rkey = uri.rsplit("/", 1)[-1] if uri else ""

    text = escape(record.get("text") or "").replace("\n", "<br>")
    posted_at = _parse_datetime(record.get("createdAt")) or datetime.now(tz=UTC)
    metrics = Metrics(
        likes=post.get("likeCount") or 0,
        reposts=post.get("repostCount") or 0,
        replies=post.get("replyCount") or 0,
    )
    media = _bluesky_media(post.get("embed") or {})

    defaults = {
        "author_name": author.get("displayName") or handle,
        "author_handle": handle,
        "content": reposted_by + text,
        "url": f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else "",
        "posted_at": posted_at,
        "metrics": metrics,
        "media": media or None,
        "raw": item,
    }
    if own:
        # Only ever set is_own=True, never flip it back to False -- see the
        # matching note in _upsert_mastodon_status.
        defaults["is_own"] = True

    _, created = Post.objects.update_or_create(
        account=account,
        platform="bluesky",
        external_id=uri,
        defaults=defaults,
    )
    return created


def _bluesky_media(embed: dict) -> list[MediaItem]:
    """Extract media from a Bluesky post's embed view.

    A quote post with attached media ("recordWithMedia") nests the images
    or video one level down under "media". Videos are HLS playlists that a
    plain <video> tag can't play, so only their thumbnail is kept.
    """
    if embed.get("$type") == "app.bsky.embed.recordWithMedia#view":
        embed = embed.get("media") or {}

    if embed.get("$type") == "app.bsky.embed.video#view":
        return [
            MediaItem(
                preview_url=embed.get("thumbnail") or "",
                media_type="video",
                alt_text=embed.get("alt") or "",
            )
        ]

    return [
        MediaItem(
            url=image.get("fullsize") or "",
            preview_url=image.get("thumb") or "",
            media_type="image",
            alt_text=image.get("alt") or "",
        )
        for image in embed.get("images") or []
    ]


def _parse_datetime(value: str | None):
    if not value:
        return None
    return parse_datetime(value)
