"""Django settings for the feedwell project.

Configured to use MongoDB via django-mongodb-backend. Override the
connection with the MONGODB_URI environment variable.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "FEEDWELL_SECRET_KEY",
    "django-insecure-feedwell-dev-key-change-me",
)

DEBUG = os.environ.get("FEEDWELL_DEBUG", "1") == "1"

ALLOWED_HOSTS = os.environ.get("FEEDWELL_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")

INSTALLED_APPS = [
    "feedwell.apps.MongoAdminConfig",
    "feedwell.apps.MongoAuthConfig",
    "feedwell.apps.MongoContentTypesConfig",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_mongodb_backend",
    "feedwell.feeds",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "feedwell.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "feedwell" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "feedwell.wsgi.application"

# Database
# https://www.mongodb.com/docs/languages/python/django-mongodb/current/

DATABASES = {
    "default": {
        "ENGINE": "django_mongodb_backend",
        # MONGODB_URI is the conventional env var name (e.g. set by
        # mongodb-runner's `exec` command).
        "HOST": os.environ.get("MONGODB_URI", "mongodb://localhost:27017/"),
        "NAME": "feedwell",
    },
}

DATABASE_ROUTERS = ["django_mongodb_backend.routers.MongoRouter"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "feedwell" / "static"]

DEFAULT_AUTO_FIELD = "django_mongodb_backend.fields.ObjectIdAutoField"

MIGRATION_MODULES = {
    "admin": "feedwell.mongo_migrations.admin",
    "auth": "feedwell.mongo_migrations.auth",
    "contenttypes": "feedwell.mongo_migrations.contenttypes",
}

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"
