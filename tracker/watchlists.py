"""The profile's auto-managed Watchlist.

Every profile has exactly one WatchList flagged is_watchlist=True - the
Trakt-style "want to watch" list that titles drop off automatically once
finished (completion.sync_watchlist_removal). ensure_watchlist() is the
one place that finds or creates it, so every caller (profile creation,
the preview page's Add to Watchlist, Trakt list sync, backup restore)
agrees on which list that is.
"""

WATCHLIST_NAME = "Watchlist"


def ensure_watchlist(profile):
    """Returns this profile's Watchlist, creating it if needed.

    A profile that already has a list literally named "Watchlist" (made
    by hand, or by an older import) has that list adopted - flagged as
    the Watchlist - instead of getting a second list of the same name."""
    from tracker.models import WatchList

    watchlist = WatchList.objects.filter(profile=profile, is_watchlist=True).order_by("id").first()
    if watchlist:
        return watchlist
    watchlist = WatchList.objects.filter(profile=profile, name__iexact=WATCHLIST_NAME).order_by("id").first()
    if watchlist:
        watchlist.is_watchlist = True
        watchlist.save(update_fields=["is_watchlist"])
        return watchlist
    return WatchList.objects.create(profile=profile, name=WATCHLIST_NAME, is_watchlist=True)
