from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from ninja import Router
from ninja.security import django_auth

from tracker.models import Profile, WatchProgress

router = Router(auth=django_auth)


@router.delete("/{title_id}")
def remove_watch_progress(request, title_id: int):
    """Dismisses a Dashboard "Watching" tile - deletes only the
    WatchProgress row, never touches WatchEvent/history, so clearing a
    stale/finished/abandoned entry can't be mistaken for "mark as
    unwatched" or affect stats. Returns an empty 200, not 204 - htmx
    hard-codes 204 responses to skip swapping entirely (same note as
    api/routers/history.py's delete_history_event)."""
    profile = get_object_or_404(Profile, user=request.user)
    progress = get_object_or_404(WatchProgress, profile=profile, title_id=title_id)
    progress.delete()
    return HttpResponse(status=200)
