"""Bearer-token auth for api/routers/scrobble.py and api/routers/reports.py -
every other router in this app uses django_auth (the browser's own session
cookie), which only ever makes sense for this app's own HTMX calls. A
player/script scrobbling from outside a browser has no session to send,
just one of the profile's own named tokens from Settings -> Integrations
(ApiToken) - see docs/SCROBBLE_API.md."""

from ninja.security import HttpBearer

from tracker.models import ApiToken, ServiceAPIKey


class ScrobbleTokenAuth(HttpBearer):
    def authenticate(self, request, token):
        """Returns the matched Profile (available in the view as
        request.auth) or None, which ninja turns into a 401 on its own -
        no separate "is this profile allowed" check needed, since any one
        of a profile's tokens only ever authorizes actions as that same
        profile, regardless of which named token it was."""
        api_token = ApiToken.objects.filter(token=token).select_related("profile").first()
        return api_token.profile if api_token else None


class ServiceAPIKeyAuth(HttpBearer):
    """Reports API auth (see contract.md and models.ServiceAPIKey's own
    docstring for why this is a separate model/class from ApiToken/
    ScrobbleTokenAuth, not a shared one) - a ServiceAPIKey must never
    authenticate a scrobble call and vice versa, so this only ever checks
    ServiceAPIKey, never ApiToken."""

    def authenticate(self, request, token):
        """Returns the matched ServiceAPIKey (available in the view as
        request.auth, though api/routers/reports.py doesn't currently
        need anything off it beyond "a valid key was presented") or None."""
        return ServiceAPIKey.objects.filter(key=token, scope="reports:read").first()
