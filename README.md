# feedwell

> "All social media clients suck. This one just sucks less." —[aclark4life](https://github.com/aclark4life), 2026.

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

## Connecting X

Connecting X requires your own X API app (free developer signup at
https://developer.x.com/). Create an app with OAuth 2.0 enabled, set its
callback URL to `http://127.0.0.1:8000/connections/x/callback/` (adjust
host/port to match how you run feedwell), then save the credentials with
either of these (a running `feedwell` reads both, env vars take priority):

```bash
feedwell config-set x.client_id "your-client-id"
feedwell config-set x.client_secret "your-client-secret"
```

```bash
export FEEDWELL_X_CLIENT_ID="your-client-id"
export FEEDWELL_X_CLIENT_SECRET="your-client-secret"
```

`feedwell config-set` writes to a `feedwell.toml` file in the current
directory (created automatically the first time you run `feedwell`, with
commented-out placeholders) so credentials persist across runs without
exporting env vars every session. This file isn't tracked by git.

Note: X now requires your developer Project to be enrolled in its
pay-per-use API plan (a payment method on file) before *any* API v2 call
works — including looking up your own profile right after connecting, not
just reading timelines. Without that, you'll see a "client-not-enrolled"
warning after the OAuth login step succeeds. This is an X-side billing
requirement, nothing to fix in feedwell — the connection is still saved
(shown as "(pending profile)" until resolved), and clicking Refresh after
you enable billing automatically fills in your real profile and starts
syncing your timeline, no need to reconnect.

## Connecting Facebook

Facebook Login works the same way (create an app at
https://developers.facebook.com/, set its OAuth redirect URI to
`http://127.0.0.1:8000/connections/facebook/callback/`, then
`feedwell config-set facebook.client_id/facebook.client_secret` or the
`FEEDWELL_FACEBOOK_CLIENT_ID`/`FEEDWELL_FACEBOOK_CLIENT_SECRET` env vars).

**Important:** this only proves who you are. Meta removed the ability
for third-party apps to read a personal News Feed back in 2018 and it
has never come back — there's no scope any app can request that
returns feed posts for a personal profile, paid or not. Connecting
Facebook saves your name/photo and nothing else ever syncs; feedwell
tells you this right after you connect.

## Status

Early: `Account`/`Post` models, Django admin, and a unified-feed view.
Mastodon is fully wired up (OAuth2, home timeline sync). X's connect flow
is wired up too, but reading its timeline requires a paid X API tier.
Facebook Login is wired up as an identity-only connection (no posts ever
sync — see above). Bluesky and RSS aren't connected yet.
