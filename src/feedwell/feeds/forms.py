from django import forms

from .models import Account, PLATFORM_CHOICES


class ConnectAccountForm(forms.ModelForm):
    """Stub account-connection form.

    No real OAuth yet: this just records the handle/display name a user
    wants to see in their unified feed for a given platform, so the
    multi-platform UX can be validated before any real API integration.
    """

    class Meta:
        model = Account
        fields = ["handle", "display_name"]
        widgets = {
            "handle": forms.TextInput(attrs={"placeholder": "@yourhandle"}),
            "display_name": forms.TextInput(attrs={"placeholder": "Display name (optional)"}),
        }


PLATFORM_LABELS = dict(PLATFORM_CHOICES)
