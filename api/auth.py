"""Bearer-token auth for api/routers/scrobble.py - every other router in
this app uses django_auth (the browser's own session cookie), which only
ever makes sense for this app's own HTMX calls. A player/script scrobbling
from outside a browser has no session to send, just the per-profile token
from Settings -> Integrations (Profile.get_or_create_api_token) - see
docs/SCROBBLE_API.md."""

from ninja.security import HttpBearer

from tracker.models import Profile


class ScrobbleTokenAuth(HttpBearer):
    def authenticate(self, request, token):
        """Returns the matched Profile (available in the view as
        request.auth) or None, which ninja turns into a 401 on its own -
        no separate "is this profile allowed" check needed, since a
        profile's own token only ever authorizes actions as itself."""
        return Profile.objects.filter(api_token=token).first()
