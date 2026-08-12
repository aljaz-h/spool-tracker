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


@router.post("/{title_id}/drop")
def drop_watch_progress(request, title_id: int):
    """The Dashboard "Watching" tile's own "Drop" action - same intent as
    views.title_drop (the title detail page's own "Your history" card),
    just reachable straight from the tile without navigating there first.
    Sets status rather than deleting the row (unlike remove_watch_progress
    above) - current_episode/position_seconds survive so a later Resume
    (from the detail page - there's no dedicated Dashboard "undrop" tile)
    picks back up where it left off. Same empty-200 shape as
    remove_watch_progress for the same reason (htmx's 204 special-case)."""
    profile = get_object_or_404(Profile, user=request.user)
    progress = get_object_or_404(WatchProgress, profile=profile, title_id=title_id)
    progress.status = WatchProgress.Status.DROPPED
    progress.save(update_fields=["status"])
    return HttpResponse(status=200)
