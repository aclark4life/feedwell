import hashlib
import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.dateformat import format as format_date
from django.views import View
from django.views.generic import ListView
from django.views.generic.edit import DeleteView, FormView

from .adapters import bluesky
from .adapters import facebook as facebook_adapter
from .adapters import mastodon
from .adapters import x as x_adapter
from .adapters.bluesky import BlueskyAPIError
from .adapters.facebook import FacebookAPIError
from .adapters.mastodon import MastodonAPIError
from .adapters.x import XAPIError
from .forms import BlueskyLoginForm, ConnectAccountForm, MastodonInstanceForm
from .models import PLATFORM_CHOICES, Account, Post
from .sync import sync_all_accounts, sync_my_posts


def _annotate_day_headers(posts, last_day: str | None) -> None:
    """Mark the first post of each new calendar day (in local time) with a
    `day_header` display string, so the template can render a divider
    there as you scroll past each day's posts.

    `last_day` is an ISO date string (e.g. "2026-08-28") carried over from
    the previous infinite-scroll page's last post via a query param, so a
    day that happens to split across a page boundary doesn't get a
    duplicate header, and a genuinely new day at the top of a page still
    gets one.
    """
    previous_day = last_day
    for post in posts:
        local_dt = timezone.localtime(post.posted_at)
        day = local_dt.date().isoformat()
        post.day_header = format_date(local_dt, "l, F j, Y") if day != previous_day else ""
        previous_day = day


class InfiniteScrollListMixin:
    """For paginated feed views: on an htmx "load more" request (fired by the
    sentinel element at the bottom of the last post), render just the bare
    post-list partial instead of the full page, so htmx can append it.
    """

    partial_template_name: str
    context_object_name: str

    def get_template_names(self):
        if self.request.headers.get("HX-Request") == "true" and self.request.GET.get("page"):
            return [self.partial_template_name]
        return super().get_template_names()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        posts = context.get(self.context_object_name)
        if posts is not None:
            _annotate_day_headers(posts, self.request.GET.get("last_day"))
        return context


class FeedView(InfiniteScrollListMixin, ListView):
    """The unified feed: every post from every connected account, newest first."""

    model = Post
    template_name = "feeds/index.html"
    partial_template_name = "feeds/_post_list.html"
    context_object_name = "posts"
    paginate_by = 25

    def get_queryset(self):
        queryset = super().get_queryset().select_related("account")
        if self.request.user.is_authenticated:
            # Own posts have their own separate "My posts" feed (/me) --
            # keep them out of the friends/home timeline.
            queryset = queryset.filter(account__owner=self.request.user, is_own=False)
        else:
            queryset = queryset.none()
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            context["has_accounts"] = Account.objects.filter(owner=self.request.user).exists()
        return context


class MyPostsView(InfiniteScrollListMixin, LoginRequiredMixin, ListView):
    """The "My posts" feed: just the signed-in user's own posts, synced
    separately from the friends/home-timeline feed at "/".
    """

    model = Post
    template_name = "feeds/my_posts.html"
    partial_template_name = "feeds/_post_list.html"
    context_object_name = "posts"
    paginate_by = 25

    def get(self, request, *args, **kwargs):
        # Auto-sync the first time there's nothing to show yet, so viewing
        # "My posts" for the first time (or after connecting Mastodon/Bluesky)
        # doesn't require a manual Refresh click first. Skip this for htmx's
        # infinite-scroll page requests -- those only fire once there's
        # already at least one post to paginate past.
        is_load_more = request.headers.get("HX-Request") == "true" and request.GET.get("page")
        if not is_load_more:
            has_synced_account = Account.objects.filter(
                owner=request.user, platform__in=["mastodon", "bluesky"]
            ).exists()
            has_own_posts = Post.objects.filter(account__owner=request.user, is_own=True).exists()
            if has_synced_account and not has_own_posts:
                total, errors = sync_my_posts(request.user)
                for error in errors:
                    messages.warning(request, error)
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("account")
            .filter(account__owner=self.request.user, is_own=True)
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_synced_account"] = Account.objects.filter(
            owner=self.request.user, platform__in=["mastodon", "bluesky"]
        ).exists()
        return context


class RefreshMyPostsView(LoginRequiredMixin, View):
    """Manual sync trigger for the "My posts" feed."""

    def post(self, request, *args, **kwargs):
        total, errors = sync_my_posts(request.user)
        if total:
            count_label = "post" if total == 1 else "posts"
            messages.success(request, f"Synced {total} new/updated {count_label}.")
        for error in errors:
            messages.warning(request, error)
        if not total and not errors:
            messages.info(request, "Nothing new to sync.")
        return redirect(reverse("my_posts"))


class ConnectionsView(LoginRequiredMixin, ListView):
    """Shows every supported platform and whichever accounts are connected."""

    model = Account
    template_name = "feeds/connections.html"
    context_object_name = "accounts"

    def get_queryset(self):
        return Account.objects.filter(owner=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        accounts_by_platform = {}
        for account in context["accounts"]:
            accounts_by_platform.setdefault(account.platform, []).append(account)
        platforms = {
            key: {"key": key, "label": label, "accounts": accounts_by_platform.get(key, [])}
            for key, label in PLATFORM_CHOICES
        }
        order = self.request.session.get("connection_order") or []
        ordered_keys = [key for key in order if key in platforms]
        ordered_keys += [key for key in platforms if key not in ordered_keys]
        context["platforms"] = [platforms[key] for key in ordered_keys]
        return context


class ReorderConnectionsView(LoginRequiredMixin, View):
    """Persists the drag-and-drop order of connection panels in the session."""

    def post(self, request, *args, **kwargs):
        valid_keys = {key for key, _ in PLATFORM_CHOICES}
        order = [key for key in request.POST.getlist("order") if key in valid_keys]
        request.session["connection_order"] = order
        return HttpResponse(status=204)


class ConnectAccountView(LoginRequiredMixin, FormView):
    """Stub 'connect' flow for platforms without real auth wired up yet."""

    form_class = ConnectAccountForm
    template_name = "feeds/connect.html"

    def dispatch(self, request, *args, **kwargs):
        self.platform_key = kwargs["platform"]
        self.platform_label = dict(PLATFORM_CHOICES).get(self.platform_key, self.platform_key)
        if self.platform_key == "mastodon":
            return redirect(reverse("mastodon_connect_start"))
        if self.platform_key == "x":
            return redirect(reverse("x_connect_start"))
        if self.platform_key == "facebook":
            return redirect(reverse("facebook_connect_start"))
        if self.platform_key == "bluesky":
            return redirect(reverse("bluesky_connect_start"))
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["platform_key"] = self.platform_key
        context["platform_label"] = self.platform_label
        return context

    def form_valid(self, form):
        handle = form.cleaned_data["handle"]
        external_id = handle or f"stub-{self.platform_key}"
        if Account.objects.filter(
            owner=self.request.user, platform=self.platform_key, external_id=external_id
        ).exists():
            form.add_error("handle", f"You've already connected {handle or 'this account'} on {self.platform_label}.")
            return self.form_invalid(form)

        account = form.save(commit=False)
        account.owner = self.request.user
        account.platform = self.platform_key
        account.external_id = external_id
        account.save()
        return redirect(reverse("connections"))


class DisconnectAccountView(LoginRequiredMixin, DeleteView):
    model = Account
    template_name = "feeds/disconnect_confirm.html"

    def get_queryset(self):
        return Account.objects.filter(owner=self.request.user)

    def get_success_url(self):
        return reverse("connections")


class MastodonConnectStartView(LoginRequiredMixin, FormView):
    """Step 1 of the Mastodon connect flow: ask which instance to log in to."""

    form_class = MastodonInstanceForm
    template_name = "feeds/mastodon_connect.html"

    def form_valid(self, form):
        instance_domain = mastodon.normalize_instance_domain(form.cleaned_data["instance_domain"])
        redirect_uri = self.request.build_absolute_uri(reverse("mastodon_connect_callback"))

        try:
            app = mastodon.get_or_register_app(instance_domain, redirect_uri)
        except MastodonAPIError as exc:
            form.add_error("instance_domain", str(exc))
            return self.form_invalid(form)

        self.request.session["mastodon_instance_domain"] = instance_domain
        return redirect(mastodon.build_authorize_url(app, redirect_uri))


class MastodonConnectCallbackView(LoginRequiredMixin, View):
    """Step 2: handle the redirect back from the Mastodon instance with an auth code."""

    def get(self, request, *args, **kwargs):
        instance_domain = request.session.pop("mastodon_instance_domain", None)
        code = request.GET.get("code")
        if not instance_domain or not code:
            messages.error(request, "Mastodon login was cancelled or incomplete.")
            return redirect(reverse("connections"))

        from .models import MastodonApp

        app = MastodonApp.objects.filter(instance_domain=instance_domain).first()
        if app is None:
            messages.error(request, f"No app registration found for {instance_domain}.")
            return redirect(reverse("connections"))

        redirect_uri = request.build_absolute_uri(reverse("mastodon_connect_callback"))
        try:
            token = mastodon.exchange_code_for_token(app, code, redirect_uri)
        except MastodonAPIError as exc:
            messages.error(request, str(exc))
            return redirect(reverse("connections"))

        Account.objects.update_or_create(
            owner=request.user,
            platform="mastodon",
            external_id=token.account_id,
            defaults={
                "handle": f"{token.handle}@{instance_domain}",
                "display_name": token.display_name,
                "avatar_url": token.avatar_url,
                "access_token": token.access_token,
                "metadata": {"instance_domain": instance_domain},
            },
        )
        messages.success(request, f"Connected {token.handle}@{instance_domain}.")
        return redirect(reverse("connections"))


class XConnectStartView(LoginRequiredMixin, View):
    """Step 1 of the X connect flow: kick off OAuth2 PKCE authorization."""

    def get(self, request, *args, **kwargs):
        if not settings.X_CLIENT_ID:
            messages.error(
                request,
                "X isn't configured yet. Set FEEDWELL_X_CLIENT_ID and "
                "FEEDWELL_X_CLIENT_SECRET to enable connecting an X account.",
            )
            return redirect(reverse("connections"))

        code_verifier, code_challenge = x_adapter.generate_pkce_pair()
        state = secrets.token_urlsafe(16)
        request.session["x_code_verifier"] = code_verifier
        request.session["x_oauth_state"] = state

        redirect_uri = request.build_absolute_uri(reverse("x_connect_callback"))
        return redirect(x_adapter.build_authorize_url(redirect_uri, state, code_challenge))


class XConnectCallbackView(LoginRequiredMixin, View):
    """Step 2: handle the redirect back from X with an auth code."""

    def get(self, request, *args, **kwargs):
        code_verifier = request.session.pop("x_code_verifier", None)
        expected_state = request.session.pop("x_oauth_state", None)
        code = request.GET.get("code")
        state = request.GET.get("state")

        if not code_verifier or not code or state != expected_state:
            messages.error(request, "X login was cancelled or incomplete.")
            return redirect(reverse("connections"))

        redirect_uri = request.build_absolute_uri(reverse("x_connect_callback"))
        try:
            token = x_adapter.exchange_code_for_token(code, redirect_uri, code_verifier)
        except XAPIError as exc:
            messages.error(request, str(exc))
            return redirect(reverse("connections"))

        # The OAuth login succeeded even if X's profile lookup didn't (e.g.
        # its pay-per-use billing enrollment requirement) -- persist the
        # connection using the token itself as a stable placeholder ID, so
        # a later sync can resolve the real profile once it's reachable,
        # instead of discarding a perfectly valid login.
        external_id = token.account_id or (
            "pending:" + hashlib.sha256(token.access_token.encode()).hexdigest()[:24]
        )
        handle = token.handle or "(pending profile)"
        display_name = token.display_name or handle

        Account.objects.update_or_create(
            owner=request.user,
            platform="x",
            external_id=external_id,
            defaults={
                "handle": handle,
                "display_name": display_name,
                "avatar_url": token.avatar_url,
                "access_token": token.access_token,
                "metadata": {"refresh_token": token.refresh_token},
            },
        )
        if token.profile_error:
            messages.success(request, "Connected to X.")
            messages.warning(request, token.profile_error)
        else:
            messages.success(request, f"Connected @{token.handle} on X.")
        return redirect(reverse("connections"))


class FacebookConnectStartView(LoginRequiredMixin, View):
    """Step 1: redirect to Facebook's OAuth authorize screen.

    Note: this only proves who you are (public_profile scope) -- there's
    no API for reading a personal News Feed, for any app, so connecting
    a Facebook account never syncs any posts. See
    feeds/adapters/facebook.py for why.
    """

    def get(self, request, *args, **kwargs):
        if not settings.FACEBOOK_CLIENT_ID:
            messages.error(
                request,
                "Facebook isn't configured yet. Set FEEDWELL_FACEBOOK_CLIENT_ID and "
                "FEEDWELL_FACEBOOK_CLIENT_SECRET to enable connecting a Facebook account.",
            )
            return redirect(reverse("connections"))

        state = secrets.token_urlsafe(16)
        request.session["facebook_oauth_state"] = state

        redirect_uri = request.build_absolute_uri(reverse("facebook_connect_callback"))
        return redirect(facebook_adapter.build_authorize_url(redirect_uri, state))


class FacebookConnectCallbackView(LoginRequiredMixin, View):
    """Step 2: handle the redirect back from Facebook with an auth code."""

    def get(self, request, *args, **kwargs):
        expected_state = request.session.pop("facebook_oauth_state", None)
        code = request.GET.get("code")
        state = request.GET.get("state")

        if not code or state != expected_state:
            messages.error(request, "Facebook login was cancelled or incomplete.")
            return redirect(reverse("connections"))

        redirect_uri = request.build_absolute_uri(reverse("facebook_connect_callback"))
        try:
            token = facebook_adapter.exchange_code_for_token(code, redirect_uri)
        except FacebookAPIError as exc:
            messages.error(request, str(exc))
            return redirect(reverse("connections"))

        Account.objects.update_or_create(
            owner=request.user,
            platform="facebook",
            external_id=token.account_id,
            defaults={
                "handle": token.handle,
                "display_name": token.display_name,
                "avatar_url": token.avatar_url,
                "access_token": token.access_token,
            },
        )
        messages.success(request, f"Connected {token.display_name or 'your account'} on Facebook.")
        messages.warning(
            request,
            "Heads up: Facebook has no API for reading a personal News Feed, for any "
            "app, so this connection is identity-only and will never sync posts.",
        )
        return redirect(reverse("connections"))


class BlueskyConnectStartView(LoginRequiredMixin, FormView):
    """Bluesky login: a single form collecting a handle + app password
    (no redirect/callback needed -- see feeds/adapters/bluesky.py)."""

    form_class = BlueskyLoginForm
    template_name = "feeds/bluesky_connect.html"

    def form_valid(self, form):
        identifier = form.cleaned_data["identifier"]
        app_password = form.cleaned_data["app_password"]
        try:
            session = bluesky.create_session(identifier, app_password)
        except BlueskyAPIError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)

        Account.objects.update_or_create(
            owner=self.request.user,
            platform="bluesky",
            external_id=session.did,
            defaults={
                "handle": session.handle,
                "display_name": session.display_name or session.handle,
                "avatar_url": session.avatar_url,
                # No long-lived access_token stored -- every sync re-logs-in
                # with the identifier/app password below.
                "metadata": {"identifier": identifier, "app_password": app_password},
            },
        )
        messages.success(self.request, f"Connected @{session.handle} on Bluesky.")
        return redirect(reverse("connections"))


class RefreshFeedView(LoginRequiredMixin, View):
    """Manual sync trigger: fetches recent posts for every connected account."""

    def post(self, request, *args, **kwargs):
        total, errors = sync_all_accounts(request.user)
        if total:
            count_label = "post" if total == 1 else "posts"
            messages.success(request, f"Synced {total} new/updated {count_label}.")
        for error in errors:
            messages.warning(request, error)
        if not total and not errors:
            messages.info(request, "Nothing new to sync.")
        return redirect(reverse("feed"))
