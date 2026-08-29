from django.contrib import admin

from .models import Account, Post


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("platform", "handle", "display_name", "owner", "connected_at")
    list_filter = ("platform",)


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ("platform", "author_handle", "posted_at", "account")
    list_filter = ("platform",)
