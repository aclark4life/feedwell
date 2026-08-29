from django.views.generic import ListView

from .models import Post


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
