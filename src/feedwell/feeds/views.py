from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse
from django.views import View
from django.views.generic import ListView
from django.views.generic.edit import DeleteView, FormView

from .adapters import mastodon
from .adapters.mastodon import MastodonAPIError
from .forms import ConnectAccountForm, MastodonInstanceForm
from .models import PLATFORM_CHOICES, Account, Post
from .sync import sync_all_accounts


class FeedView(ListView):
    """The unified feed: every post from every connected account, newest first."""

    model = Post
    template_name = "feeds/index.html"
    context_object_name = "posts"
    paginate_by = 25

    def get_queryset(self):
        queryset = super().get_queryset().select_related("account")
        if self.request.user.is_authenticated:
            queryset = queryset.filter(account__owner=self.request.user)
        else:
            queryset = queryset.none()
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            context["has_accounts"] = Account.objects.filter(owner=self.request.user).exists()
        return context


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
        context["platforms"] = [
            {"key": key, "label": label, "accounts": accounts_by_platform.get(key, [])}
            for key, label in PLATFORM_CHOICES
        ]
        return context


class ConnectAccountView(LoginRequiredMixin, FormView):
    """Stub 'connect' flow for platforms without real auth wired up yet."""

    form_class = ConnectAccountForm
    template_name = "feeds/connect.html"

    def dispatch(self, request, *args, **kwargs):
        self.platform_key = kwargs["platform"]
        self.platform_label = dict(PLATFORM_CHOICES).get(self.platform_key, self.platform_key)
        if self.platform_key == "mastodon":
            return redirect(reverse("mastodon_connect_start"))
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
