"""Template context processors for the feeds app."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from .models import Post


def connection_stats(request):
    """Adds stats_count/stats_platforms to every template's context: how
    many posts from the signed-in user's connections (not their own posts)
    showed up in the last 24 hours, and across how many platforms.

    Rendered in the shared footer (base.html) so it shows up on every
    page, not just the home feed.
    """
    if not request.user.is_authenticated:
        return {}

    recent = Post.objects.filter(
        account__owner=request.user,
        is_own=False,
        posted_at__gte=timezone.now() - timedelta(hours=24),
    )
    return {
        "stats_count": recent.count(),
        "stats_platforms": recent.values_list("platform", flat=True).distinct().count(),
    }
