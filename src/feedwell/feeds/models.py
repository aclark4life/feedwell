"""Models for aggregating social media accounts and posts.

Uses django-mongodb-backend's EmbeddedModelField/ArrayField so that
platform-specific, variably-shaped data (metrics, media attachments) can
live as embedded documents inside a single Mongo collection per model,
instead of forcing everything into rigid relational tables.
"""

from django.conf import settings
from django.db import models
from django_mongodb_backend.fields import ArrayField, EmbeddedModelField
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


class Metrics(EmbeddedModel):
    """Engagement counts embedded directly on a Post document."""

    likes = models.IntegerField(default=0)
    reposts = models.IntegerField(default=0)
    replies = models.IntegerField(default=0)


class MediaItem(EmbeddedModel):
    """A single media attachment (image, video, etc.) embedded on a Post."""

    url = models.URLField()
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
    metrics = EmbeddedModelField(Metrics, null=True, blank=True)
    media = ArrayField(EmbeddedModelField(MediaItem), null=True, blank=True)
    raw = models.JSONField(default=dict, blank=True, help_text="Original payload from the source platform")

    class Meta:
        ordering = ["-posted_at"]
        constraints = [
            models.UniqueConstraint(fields=["account", "platform", "external_id"], name="unique_post_per_account")
        ]

    def __str__(self) -> str:
        return f"{self.author_handle or self.author_name}: {self.content[:40]}"
