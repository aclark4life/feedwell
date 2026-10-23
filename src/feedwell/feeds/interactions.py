"""Likes, boosts/reposts and replies on synced posts.

Each action calls the post's platform, then writes the result back into
the stored Post -- its `raw` payload (where Post.is_liked/is_boosted read
the signed-in user's own state from) and its embedded Metrics -- so the
re-rendered post reflects it right away, without waiting for a resync.

Only Mastodon and Bluesky for now (see Post.supports_interactions): X
needs write scopes plus a paid API tier, and Facebook has no feed at all.
"""

from __future__ import annotations

from .adapters import bluesky, mastodon
from .models import Metrics, Post


class InteractionError(Exception):
    """Raised for an action a post's platform doesn't support."""


def toggle_like(post: Post) -> None:
    if post.platform == "mastodon":
        status = post.mastodon_status()
        _store_mastodon_status(
            post, mastodon.set_favourite(post.account, status["id"], not post.is_liked)
        )
    elif post.platform == "bluesky":
        _toggle_bluesky_record(post, "like", "app.bsky.feed.like", "likeCount")
    else:
        raise InteractionError(f"Liking isn't supported on {post.get_platform_display()} yet.")


def toggle_boost(post: Post) -> None:
    if post.platform == "mastodon":
        status = post.mastodon_status()
        _store_mastodon_status(
            post, mastodon.set_reblog(post.account, status["id"], not post.is_boosted)
        )
    elif post.platform == "bluesky":
        _toggle_bluesky_record(post, "repost", "app.bsky.feed.repost", "repostCount")
    else:
        raise InteractionError(f"Boosting isn't supported on {post.get_platform_display()} yet.")


def reply(post: Post, text: str) -> None:
    if post.platform == "mastodon":
        status = post.mastodon_status()
        mastodon.post_reply(post.account, status["id"], text)
        status["replies_count"] = (status.get("replies_count") or 0) + 1
        _store_mastodon_status(post, status)
    elif post.platform == "bluesky":
        bsky_post = post.raw["post"]
        parent = {"uri": bsky_post["uri"], "cid": bsky_post["cid"]}
        # A reply names both its direct parent and the thread's root post.
        root = ((bsky_post.get("record") or {}).get("reply") or {}).get("root") or parent
        bluesky.create_record(
            post.account,
            "app.bsky.feed.post",
            {"text": text, "reply": {"root": root, "parent": parent}},
        )
        bsky_post["replyCount"] = (bsky_post.get("replyCount") or 0) + 1
        _store_bluesky_post(post)
    else:
        raise InteractionError(f"Replying isn't supported on {post.get_platform_display()} yet.")


def _store_mastodon_status(post: Post, status: dict) -> None:
    """Save an updated status (as returned by a Mastodon action) back onto
    the Post, inside its boost wrapper if it has one."""
    if post.raw.get("reblog"):
        post.raw["reblog"] = status
    else:
        post.raw = status
    post.metrics = Metrics(
        likes=status.get("favourites_count") or 0,
        reposts=status.get("reblogs_count") or 0,
        replies=status.get("replies_count") or 0,
    )
    post.save(update_fields=["raw", "metrics"])


def _toggle_bluesky_record(post: Post, viewer_key: str, collection: str, count_key: str) -> None:
    """Like/repost (or undo it) by creating (or deleting) a record pointing
    at the post. Bluesky reports our own like/repost as the URI of that
    record under post.viewer, which is also what's needed to delete it."""
    bsky_post = post.raw["post"]
    viewer = bsky_post.setdefault("viewer", {})
    if viewer.get(viewer_key):
        bluesky.delete_record(post.account, viewer.pop(viewer_key))
        bsky_post[count_key] = max((bsky_post.get(count_key) or 0) - 1, 0)
    else:
        subject = {"uri": bsky_post["uri"], "cid": bsky_post["cid"]}
        viewer[viewer_key] = bluesky.create_record(post.account, collection, {"subject": subject})
        bsky_post[count_key] = (bsky_post.get(count_key) or 0) + 1
    _store_bluesky_post(post)


def _store_bluesky_post(post: Post) -> None:
    bsky_post = post.raw["post"]
    post.metrics = Metrics(
        likes=bsky_post.get("likeCount") or 0,
        reposts=bsky_post.get("repostCount") or 0,
        replies=bsky_post.get("replyCount") or 0,
    )
    post.save(update_fields=["raw", "metrics"])
