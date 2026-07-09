from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.security import django_auth

from tracker.models import Profile, WatchEvent

router = Router(auth=django_auth)


@router.delete("/{event_id}")
def delete_history_event(request, event_id: int):
    """Returns an empty 200, not 204 — htmx hard-codes 204 responses to
    skip swapping entirely (confirmed in static/vendor/htmx.js), which
    would leave the removed tile visible until the next full reload."""
    profile = get_object_or_404(Profile, user=request.user)
    event = get_object_or_404(WatchEvent, pk=event_id, profile=profile)
    event.delete()
    return HttpResponse(status=200)
