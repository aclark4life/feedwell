from django import forms

from .models import Account, PLATFORM_CHOICES


class ConnectAccountForm(forms.ModelForm):
    """Stub account-connection form.

    No real OAuth yet: this just records the handle a user wants to see
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


PLATFORM_LABELS = dict(PLATFORM_CHOICES)
