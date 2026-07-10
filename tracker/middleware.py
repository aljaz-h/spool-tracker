from django.shortcuts import redirect
from django.urls import Resolver404, resolve

from .models import Profile

EXEMPT_URL_NAMES = {"change_credentials", "logout"}


class ForceCredentialChangeMiddleware:
    """Redirects a logged-in user whose Profile.must_change_credentials is
    still True to the change-credentials form for every request except
    that form itself and logout - so the account bootstrap_admin creates
    from ADMIN_USERNAME/ADMIN_PASSWORD (see
    management/commands/bootstrap_admin.py) can't be used for anything
    else on this instance until its real username/password are set."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not request.path.startswith(("/static/", "/media/")):
            profile = Profile.objects.filter(user=request.user).only("must_change_credentials").first()
            if profile is not None and profile.must_change_credentials:
                try:
                    url_name = resolve(request.path).url_name
                except Resolver404:
                    url_name = None
                if url_name not in EXEMPT_URL_NAMES:
                    return redirect("change_credentials")
        return self.get_response(request)
