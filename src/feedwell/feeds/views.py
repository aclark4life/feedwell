from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import ListView
from django.views.generic.edit import DeleteView, FormView

from .forms import ConnectAccountForm
from .models import PLATFORM_CHOICES, Account, Post


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
    """Stub 'connect' flow: records a handle for a platform, no real auth yet."""

    form_class = ConnectAccountForm
    template_name = "feeds/connect.html"

    def dispatch(self, request, *args, **kwargs):
        self.platform_key = kwargs["platform"]
        self.platform_label = dict(PLATFORM_CHOICES).get(self.platform_key, self.platform_key)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["platform_key"] = self.platform_key
        context["platform_label"] = self.platform_label
        return context

    def form_valid(self, form):
        account = form.save(commit=False)
        account.owner = self.request.user
        account.platform = self.platform_key
        account.external_id = account.handle or f"stub-{self.platform_key}"
        account.save()
        return redirect(reverse("connections"))


class DisconnectAccountView(LoginRequiredMixin, DeleteView):
    model = Account
    template_name = "feeds/disconnect_confirm.html"

    def get_queryset(self):
        return Account.objects.filter(owner=self.request.user)

    def get_success_url(self):
        return reverse("connections")
