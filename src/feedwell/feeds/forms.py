from django import forms

from .models import PLATFORM_CHOICES, Account


class ConnectAccountForm(forms.ModelForm):
    """Stub account-connection form for platforms without real OAuth yet.

    No real auth yet: this just records the handle a user wants to see
    in their unified feed for a given platform, so the multi-platform UX
    can be validated before any real API integration. display_name isn't
    collected here since real integrations will populate it from the
    platform itself.
    """

    class Meta:
        model = Account
        fields = ["handle"]
        widgets = {
            "handle": forms.TextInput(attrs={"placeholder": "@yourhandle"}),
        }


class MastodonInstanceForm(forms.Form):
    """Collects which Mastodon instance to log in to, since it's federated."""

    instance_domain = forms.CharField(
        label="Your Mastodon instance",
        widget=forms.TextInput(attrs={"placeholder": "mastodon.social"}),
    )


PLATFORM_LABELS = dict(PLATFORM_CHOICES)
