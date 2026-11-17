"""Calendar -> My schedule: "who's watching tonight?".

Pick some of the household and get titles that suit everyone: nothing any of
them has watched, drawn from the shared lists and each person's own Watchlist,
with the ones several of them have lined up and the genres they all watch
coming first. It is a one-off look for the evening: the people picked are only
used to answer this request and nothing is saved to anyone's schedule.

Privacy: a profile that does not share its activity only counts through the
lists it has shared. Its own Watchlist is private, and neither its watch
history (the "already watched" filter) nor its genres (the shared-genre
ranking) is used. The profile asking is always counted in full.
"""

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from . import planner, selectors
from .models import MediaType, Profile, ScheduledSlot, Title, WatchEvent, WatchList, WatchListItem, WatchProgress

DEFAULT_LIMIT = 8


def household():
    return list(Profile.objects.order_by("display_name"))


def parse_selection(viewer, raw_ids):
    """The profiles named by ?with=... (unknown ids are dropped); the viewer
    alone when nothing valid is named."""
    wanted = {int(v) for v in raw_ids if str(v).isdigit()}
    chosen = list(Profile.objects.filter(pk__in=wanted).order_by("display_name")) if wanted else []
    return chosen or [viewer]


def _counts_as_member(profile, viewer):
    return profile.pk == viewer.pk or profile.share_activity


def free_evening(profile, plan, now):
    """The viewer's next free stretch: {"day", "is_today", "minutes"} where
    minutes is the free time left in that day's window after what is already
    planned, or None when the plan is off or has no free day ahead."""
    if not plan.enabled:
        return None
    tz = selectors.profile_zoneinfo(profile)
    local_now = now.astimezone(tz)
    today = local_now.date()
    for offset in range(planner.HORIZON_DAYS):
        day = today + timedelta(days=offset)
        window = planner.window_for(plan, day.weekday())
        if not window:
            continue
        start, end = planner._at(day, window[0], tz), planner._at(day, window[1], tz)
        if offset == 0:
            start = max(start, local_now)
        if end <= start:
            continue
        planned = 0
        for slot in ScheduledSlot.objects.filter(
            profile=profile, status=ScheduledSlot.Status.PLANNED, end__gt=start, start__lt=end
        ):
            planned += planner._minutes(min(slot.end, end) - max(slot.start, start))
        return {"day": day, "is_today": offset == 0, "minutes": max(0, planner._minutes(end - start) - planned)}
    return None


def _shared_genres(viewer, profiles):
    """Genre names every counted profile watches (just that profile's own for a
    single one). Profiles that keep their activity private take no part."""
    members = [p for p in profiles if _counts_as_member(p, viewer)]
    counts = selectors._genre_counts_by_profile(members) if members else {}
    sets = [set(counts[p.pk]) for p in members if counts.get(p.pk)]
    if not sets:
        return set()
    if len(sets) == 1:
        return sets[0]
    shared = set.intersection(*sets)
    return shared


def _released(title, now):
    if title.media_type == MediaType.MOVIE:
        return planner._movie_released(title, now)
    return title.year <= timezone.localdate(now).year


def _runtime(title):
    if title.media_type == MediaType.MOVIE:
        return title.runtime_minutes or planner.DEFAULT_MOVIE_MINUTES
    return title.runtime_minutes or planner._default_episode_minutes(title)


def suggest(viewer, profiles, now=None, free_minutes=None, limit=DEFAULT_LIMIT):
    """Titles for `profiles` to watch together, best first:
    [{"title", "kind", "runtime", "runtime_label", "fits", "backers", "genres", "shared_lists"}].
    Never includes a title a sharing selected profile (or the viewer) has watched or dropped."""
    now = now or timezone.now()
    selected = {p.pk: p for p in profiles}
    # Whose own Watchlist is open to this: the viewer's, and anyone sharing activity.
    watchlist_owners = [p.pk for p in profiles if _counts_as_member(p, viewer)]
    lists = list(
        WatchList.objects.filter(Q(is_shared=True) | Q(is_watchlist=True, profile_id__in=watchlist_owners)).select_related("profile")
    )
    items = list(WatchListItem.objects.filter(watchlist__in=lists).select_related("title", "watchlist__profile"))
    if not items:
        return []
    title_ids = {i.title_id for i in items}
    # Only the watch history of people who share it (and the viewer's own) can
    # filter the suggestions: a private history must not be probed by seeing
    # which titles disappear when that person is picked.
    history = [p for p in profiles if _counts_as_member(p, viewer)]
    seen = set(WatchEvent.objects.filter(profile__in=history, title_id__in=title_ids).values_list("title_id", flat=True))
    seen |= set(
        WatchProgress.objects.filter(
            profile__in=history,
            title_id__in=title_ids,
            status__in=[WatchProgress.Status.COMPLETED, WatchProgress.Status.DROPPED],
        ).values_list("title_id", flat=True)
    )
    shared_genres = _shared_genres(viewer, profiles)
    title_genres = {}
    for title_id, name in Title.genres.through.objects.filter(title_id__in=title_ids).values_list("title_id", "genre__name"):
        title_genres.setdefault(title_id, set()).add(name)

    grouped = {}
    for item in items:
        title = item.title
        if title.id in seen or not _released(title, now):
            continue
        entry = grouped.setdefault(title.id, {"title": title, "backers": set(), "lists": set()})
        entry["lists"].add(item.watchlist_id)
        owner = item.watchlist.profile_id
        # A list only backs a title for the person who owns it, and only when
        # that list is one they put in play (their Watchlist, or one they shared).
        if owner in selected and (item.watchlist.is_shared or (item.watchlist.is_watchlist and owner in watchlist_owners)):
            entry["backers"].add(owner)

    results = []
    for entry in grouped.values():
        title = entry["title"]
        runtime = _runtime(title)
        matched = sorted(shared_genres & title_genres.get(title.id, set()))
        results.append(
            {
                "title": title,
                "kind": title.media_type,
                "runtime": runtime,
                "runtime_label": planner._duration_label(runtime),
                "fits": free_minutes is None or runtime <= free_minutes,
                "backers": [selected[pk].display_name for pk in sorted(entry["backers"], key=lambda pk: selected[pk].display_name)],
                "genres": matched,
                "shared_lists": len(entry["lists"]),
            }
        )
    results.sort(key=lambda r: (-len(r["backers"]), -len(r["genres"]), -r["shared_lists"], not r["fits"], r["title"].name.lower()))
    return results[:limit]
