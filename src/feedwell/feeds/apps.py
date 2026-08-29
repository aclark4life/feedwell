from django.apps import AppConfig


class FeedsConfig(AppConfig):
    default_auto_field = "django_mongodb_backend.fields.ObjectIdAutoField"
    name = "feedwell.feeds"
    label = "feeds"
