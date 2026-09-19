from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.security import django_auth

from tracker import completion
from tracker.models import Profile, WatchEvent

router = Router(auth=django_auth)


@router.delete("/{event_id}")
def delete_history_event(request, event_id: int):
    """Returns an empty 200, not 204 — htmx hard-codes 204 responses to
    skip swapping entirely (confirmed in static/vendor/htmx.js), which
    would leave the removed tile visible until the next full reload.

    sync_show_completion re-validates this title's own WatchProgress
    against what's left of its watch history now that this event is
    gone - without it, deleting the only/last event for a title that was
    ever bulk-marked COMPLETED left that WatchProgress row COMPLETED
    forever, and Up Next/Calendar (both scoped to "any WatchProgress row
    for this title", not just WATCHING) kept showing it as still being
    watched even though History now showed nothing for it at all -
    confirmed as a real reported case."""
    profile = get_object_or_404(Profile, user=request.user)
    event = get_object_or_404(WatchEvent, pk=event_id, profile=profile)
    title = event.title
    event.delete()
    completion.sync_show_completion(profile, title)
    return HttpResponse(status=200)
