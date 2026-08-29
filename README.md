# feedwell

> "All social media clients suck. This one just sucks less." —me, 2026.

A unified, local-first client for your social media feeds — one place to
read and interact with everything, instead of juggling a pile of terrible
individual apps.

## Install

```bash
pip install feedwell
```

Requires a running MongoDB instance. Point `feedwell` at it with the
`MONGODB_URI` environment variable (defaults to `mongodb://localhost:27017/`).

For local development without installing MongoDB yourself, you can use
[mongodb-runner](https://www.npmjs.com/package/mongodb-runner):

```bash
npx mongodb-runner start --id feedwell-dev
# note the printed connection string, then:
export MONGODB_URI="mongodb://127.0.0.1:<port>/"
```

## Run

```bash
feedwell
```

This runs migrations, starts a local dev server, and opens it in your
browser. `feedwell <command>` also forwards to Django's manage.py, e.g.
`feedwell createsuperuser`.

## Stack

Built with Django and [django-mongodb-backend](https://www.mongodb.com/docs/languages/python/django-mongodb/current/),
so posts and their platform-specific metadata (metrics, media) can be stored
as embedded documents instead of forced into rigid relational tables.

## Status

Early scaffolding: a `feeds` app with `Account`/`Post` models, Django admin,
and a bare unified-feed view. No platform integrations (Mastodon, Bluesky,
RSS) or auth flow yet. Not yet useful. 🙂
