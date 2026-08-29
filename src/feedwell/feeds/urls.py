from django.urls import path

from .views import (
    ConnectAccountView,
    ConnectionsView,
    DisconnectAccountView,
    FeedView,
    MastodonConnectCallbackView,
    MastodonConnectStartView,
    RefreshFeedView,
    ReorderConnectionsView,
    XConnectCallbackView,
    XConnectStartView,
)

urlpatterns = [
    path("", FeedView.as_view(), name="feed"),
    path("refresh/", RefreshFeedView.as_view(), name="refresh_feed"),
    path("connections/", ConnectionsView.as_view(), name="connections"),
    path(
        "connections/<str:platform>/connect/",
        ConnectAccountView.as_view(),
        name="connect_account",
    ),
    path(
        "connections/<str:pk>/disconnect/",
        DisconnectAccountView.as_view(),
        name="disconnect_account",
    ),
    path(
        "connections/mastodon/start/",
        MastodonConnectStartView.as_view(),
        name="mastodon_connect_start",
    ),
    path(
        "connections/mastodon/callback/",
        MastodonConnectCallbackView.as_view(),
        name="mastodon_connect_callback",
    ),
    path(
        "connections/reorder/",
        ReorderConnectionsView.as_view(),
        name="reorder_connections",
    ),
    path(
        "connections/x/start/",
        XConnectStartView.as_view(),
        name="x_connect_start",
    ),
    path(
        "connections/x/callback/",
        XConnectCallbackView.as_view(),
        name="x_connect_callback",
    ),
]
