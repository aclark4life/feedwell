"""Template context processors for the feeds app."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.shortcuts import resolve_url
from django.urls import NoReverseMatch
from django.utils import timezone

from .models import Post


def auth_urls(request):
    """Adds login_url/logout_url to every template's context.

    The feeds templates need somewhere to send anonymous visitors to log in
    and signed-in users to log out, but a host project may use Django's
    built-in ``django.contrib.auth.urls`` (url names ``login``/``logout``),
    django-allauth (``account_login``/``account_logout``), or something else
    entirely. Rather than hardcoding a url name, resolve whatever the host
    project has configured via ``settings.LOGIN_URL``/``settings.LOGOUT_URL``
    (each may be a url name or an absolute path), falling back to sensible
    defaults if those don't resolve.
    """
    login_url = resolve_url(getattr(settings, "LOGIN_URL", "login"))

    logout_setting = getattr(settings, "LOGOUT_URL", None)
    if logout_setting:
        logout_url = resolve_url(logout_setting)
    else:
        try:
            logout_url = resolve_url("logout")
        except NoReverseMatch:
            try:
                logout_url = resolve_url("account_logout")
            except NoReverseMatch:
                logout_url = "/accounts/logout/"

    return {"login_url": login_url, "logout_url": logout_url}


def connection_stats(request):
    """Adds stats_count/stats_platforms to every template's context: how
    many posts from the signed-in user's connections (not their own posts)
    showed up in the last 24 hours, and across how many platforms.

    Rendered in the shared footer (feeds/base.html) so it shows up on every
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
