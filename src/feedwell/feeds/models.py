"""Models for aggregating social media accounts and posts.

Uses django-mongodb-backend's EmbeddedModelField/ArrayField so that
platform-specific, variably-shaped data (metrics, media attachments) can
live as embedded documents inside a single Mongo collection per model,
instead of forcing everything into rigid relational tables.
"""

from urllib.parse import urlparse

from django.conf import settings
from django.db import models
from django_mongodb_backend.fields import EmbeddedModelArrayField, EmbeddedModelField
from django_mongodb_backend.models import EmbeddedModel

PLATFORM_CHOICES = [
    ("x", "X"),
    ("facebook", "Facebook"),
    ("mastodon", "Mastodon"),
    ("bluesky", "Bluesky"),
]


class MastodonApp(models.Model):
    """OAuth app credentials for a single Mastodon instance, registered once and reused.

    Mastodon requires each client application to register itself with every
    instance it talks to (there's no single central API like X/Facebook).
    We cache the client_id/secret per instance domain so repeated connect
    attempts against the same instance don't re-register every time.
    """

    instance_domain = models.CharField(max_length=255, unique=True)
    client_id = models.CharField(max_length=255)
    client_secret = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.instance_domain


class Account(models.Model):
    """A connected social media account belonging to a feedwell user."""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="accounts")
    platform = models.CharField(max_length=32, choices=PLATFORM_CHOICES)
    external_id = models.CharField(max_length=255, help_text="Account ID on the source platform")
    display_name = models.CharField(max_length=255, blank=True)
    handle = models.CharField(max_length=255, blank=True)
    avatar_url = models.URLField(blank=True)
    access_token = models.CharField(max_length=1024, blank=True)
    metadata = models.JSONField(
        default=dict, blank=True, help_text="Platform-specific extras, e.g. Mastodon instance domain"
    )
    connected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["platform", "handle"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "platform", "external_id"], name="unique_account_per_owner")
        ]

    def __str__(self) -> str:
        return f"{self.platform}:{self.handle or self.external_id}"

    @property
    def profile_url(self) -> str:
        """Best-effort link to this account's profile on its source platform."""
        handle = self.handle.lstrip("@")

        if self.platform == "mastodon":
            instance_domain = self.metadata.get("instance_domain")
            username = handle.split("@", 1)[0]
            if instance_domain and username:
                return f"https://{instance_domain}/@{username}"
            return ""

        if self.platform == "x":
            return f"https://x.com/{handle}" if handle and "pending" not in handle else ""

        if self.platform == "facebook":
            return f"https://www.facebook.com/{self.external_id}" if self.external_id else ""

        if self.platform == "bluesky":
            return f"https://bsky.app/profile/{handle}" if handle else ""

        return ""

    @property
    def connection_label(self) -> str:
        """Short label identifying this specific connection.

        For Mastodon, the platform name alone ("mastodon") is ambiguous if
        someone has connected accounts on more than one instance -- show
        the instance domain (e.g. "fosstodon.org") instead so each
        connection reads as itself. Other platforms are single, global
        services, so their display name is unambiguous as-is.
        """
        if self.platform == "mastodon":
            return self.metadata.get("instance_domain") or self.get_platform_display()
        return self.get_platform_display()


class Metrics(EmbeddedModel):
    """Engagement counts embedded directly on a Post document."""

    likes = models.IntegerField(default=0)
    reposts = models.IntegerField(default=0)
    replies = models.IntegerField(default=0)


class MediaItem(EmbeddedModel):
    """A single media attachment (image, video, etc.) embedded on a Post."""

    url = models.URLField(blank=True)
    media_type = models.CharField(max_length=32, blank=True)
    alt_text = models.CharField(max_length=1000, blank=True)


class Post(models.Model):
    """A single post pulled in from a connected account's platform."""

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="posts")
    platform = models.CharField(max_length=32, choices=PLATFORM_CHOICES)
    external_id = models.CharField(max_length=255)
    author_name = models.CharField(max_length=255, blank=True)
    author_handle = models.CharField(max_length=255, blank=True)
    content = models.TextField(blank=True)
    url = models.URLField(blank=True)
    posted_at = models.DateTimeField()
    fetched_at = models.DateTimeField(auto_now_add=True)
    is_own = models.BooleanField(
        default=False,
        help_text="True if this is a post the account itself made, synced via the "
        "'My posts' feed rather than the friends/home-timeline feed.",
    )
    metrics = EmbeddedModelField(Metrics, null=True, blank=True)
    media = EmbeddedModelArrayField(MediaItem, null=True, blank=True)
    raw = models.JSONField(default=dict, blank=True, help_text="Original payload from the source platform")

    class Meta:
        ordering = ["-posted_at"]
        constraints = [
            models.UniqueConstraint(fields=["account", "platform", "external_id"], name="unique_post_per_account")
        ]

    def __str__(self) -> str:
        return f"{self.author_handle or self.author_name}: {self.content[:40]}"

    @property
    def author_profile_url(self) -> str:
        """Link to the post's author's own profile (not the connection that
        surfaced it -- see Account.profile_url for that).

        For Mastodon, author_handle is the acct Mastodon returned: bare
        ("alex") if the author is on your own instance, or qualified
        ("bascht@yakshed.social") if remote. Either way, the author's own
        instance can be derived from it (falling back to your connected
        account's instance when it's bare), so the profile link always
        points at the author's real home, not wherever the post happened
        to be federated to.
        """
        handle = self.author_handle.lstrip("@")
        if not handle:
            return ""

        if self.platform == "mastodon":
            if "@" in handle:
                username, domain = handle.split("@", 1)
            else:
                username, domain = handle, self.account.metadata.get("instance_domain")
            return f"https://{domain}/@{username}" if username and domain else ""

        if self.platform == "x":
            return f"https://x.com/{handle}"

        if self.platform == "bluesky":
            return f"https://bsky.app/profile/{handle}"

        return ""

    @property
    def author_federated_profile_url(self) -> str:
        """Link to the *author's* federated profile page on your own
        connected instance -- e.g. https://fosstodon.org/@bascht@yakshed.social
        for a remote author, mirroring how federated_url links to the
        federated copy of the post itself rather than the canonical one.

        This is what the "(fosstodon.org)" badge next to an author's name
        should point to: not your own profile (Account.profile_url), but
        this author as federated onto your instance -- so clicking it
        follows/views them the same way federated_url lets you view their
        boosted post, without needing an account on their home instance.

        Only meaningful for Mastodon, since it's the only federated
        platform here; other platforms are single global services with no
        separate "your instance's copy" of a profile.
        """
        if self.platform != "mastodon":
            return ""

        handle = self.author_handle.lstrip("@")
        instance_domain = self.account.metadata.get("instance_domain")
        return f"https://{instance_domain}/@{handle}" if handle and instance_domain else ""

    @property
    def federated_url(self) -> str:
        """Link to the federated copy on *your own* Mastodon instance.

        Mastodon federates a local copy of any remote post you see (whether
        it's a direct follow or a boost), and that copy gets its own local
        database ID on your instance -- distinct from the canonical url on
        the author's home instance (see link_label above). That local copy
        is reachable at /@{acct}/{local_id} on your instance, using the
        *local* status ID and the author's full "user@domain" acct, so you
        can view it (and reply/boost/favourite it) without ever needing an
        account on the author's instance.

        Only meaningful when the author isn't on your own instance --
        Mastodon's API only qualifies "acct" with "@domain" for remote
        accounts, so that's what gates this.
        """
        if self.platform != "mastodon":
            return ""

        content_source = self.raw.get("reblog") or self.raw
        acct = (content_source.get("account") or {}).get("acct") or ""
        local_id = content_source.get("id")
        instance_domain = self.account.metadata.get("instance_domain")
        if not (instance_domain and local_id and "@" in acct):
            return ""

        return f"https://{instance_domain}/@{acct}/{local_id}"

    @property
    def link_label(self) -> str:
        """Label for the "Open on ..." link.

        Mastodon is federated, so a boosted post's url can point to a
        completely different instance than the account that surfaced it
        (e.g. your fosstodon.org account boosts a post that actually lives
        on mastodon.social). Showing the platform's generic display name
        ("Mastodon") in that case reads as if the link stays on your home
        instance, when it doesn't. Show the link's real host instead so
        it's obvious where you're actually being sent.
        """
        if self.platform == "mastodon" and self.url:
            host = urlparse(self.url).netloc
            if host:
                return host
        return self.get_platform_display()
