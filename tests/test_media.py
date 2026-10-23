"""Media extraction and rendering tests (no database connection required)."""

from datetime import UTC, datetime

from django.template.loader import render_to_string

from feedwell.feeds.models import MediaItem, Post
from feedwell.feeds.sync import _bluesky_media


def _post(platform: str, *media: MediaItem) -> Post:
    return Post(
        platform=platform,
        url=f"https://example.com/{platform}/1",
        posted_at=datetime.now(tz=UTC),
        media=list(media),
    )


def test_media_kind_normalizes_platform_types() -> None:
    assert MediaItem(media_type="photo").kind == "image"
    assert MediaItem(media_type="animated_gif").kind == "gif"
    assert MediaItem(media_type="gifv").kind == "gif"
    assert MediaItem(media_type="unknown").kind == ""


def test_bluesky_images() -> None:
    media = _bluesky_media(
        {
            "$type": "app.bsky.embed.images#view",
            "images": [
                {"fullsize": "https://cdn/full.jpg", "thumb": "https://cdn/t.jpg", "alt": "a cat"}
            ],
        }
    )
    assert [(m.url, m.preview_url, m.media_type, m.alt_text) for m in media] == [
        ("https://cdn/full.jpg", "https://cdn/t.jpg", "image", "a cat")
    ]


def test_bluesky_quote_post_with_images() -> None:
    media = _bluesky_media(
        {
            "$type": "app.bsky.embed.recordWithMedia#view",
            "record": {},
            "media": {
                "$type": "app.bsky.embed.images#view",
                "images": [{"fullsize": "https://cdn/q.jpg"}],
            },
        }
    )
    assert [m.url for m in media] == ["https://cdn/q.jpg"]


def test_bluesky_video_keeps_only_thumbnail() -> None:
    (video,) = _bluesky_media(
        {
            "$type": "app.bsky.embed.video#view",
            "playlist": "https://video/playlist.m3u8",
            "thumbnail": "https://video/thumb.jpg",
        }
    )
    assert video.url == ""
    assert video.preview_url == "https://video/thumb.jpg"
    assert video.kind == "video"


def test_image_renders_as_lightbox_link() -> None:
    html = render_to_string(
        "feeds/_post_media.html",
        {
            "post": _post(
                "bluesky",
                MediaItem(url="https://cdn/full.jpg", media_type="image", alt_text="a cat"),
            )
        },
    )
    assert 'class="media-link" href="https://cdn/full.jpg"' in html
    assert 'alt="a cat"' in html
    assert "post-media-single" in html


def test_playable_video_renders_inline() -> None:
    html = render_to_string(
        "feeds/_post_media.html",
        {
            "post": _post(
                "mastodon",
                MediaItem(url="https://m/v.mp4", preview_url="https://m/p.jpg", media_type="video"),
                MediaItem(url="https://m/g.mp4", media_type="gifv"),
            )
        },
    )
    assert '<video src="https://m/v.mp4" poster="https://m/p.jpg" controls' in html
    assert '<video src="https://m/g.mp4"  autoplay loop muted' in html
    assert "post-media-single" not in html


def test_thumbnail_only_video_links_to_post() -> None:
    html = render_to_string(
        "feeds/_post_media.html",
        {"post": _post("x", MediaItem(preview_url="https://x/thumb.jpg", media_type="video"))},
    )
    assert 'class="media-external" href="https://example.com/x/1"' in html
    assert "media-play" in html
