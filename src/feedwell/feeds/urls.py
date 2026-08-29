from django.urls import path

from .views import ConnectAccountView, ConnectionsView, DisconnectAccountView, FeedView

urlpatterns = [
    path("", FeedView.as_view(), name="feed"),
    path("connections/", ConnectionsView.as_view(), name="connections"),
    path("connections/<str:platform>/connect/", ConnectAccountView.as_view(), name="connect_account"),
    path("connections/<str:pk>/disconnect/", DisconnectAccountView.as_view(), name="disconnect_account"),
]
