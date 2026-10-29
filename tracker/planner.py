"""Calendar -> My schedule: a personal viewing plan built from the lists a
profile picks, fitted into the hours they say they are free.

The plan is stored (ScheduledSlot rows) so it does not reshuffle between
page loads. What changes it:

- ensure_plan()  - run before any view of the plan. Reconciles first, then
  rebuilds (settings changed) or just tops up free time with whatever the
  lists now offer (new titles, slots that moved).
- reconcile()    - decides what happened to past/planned slots from the
  profile's real WatchEvents: a slot is done once every episode (or the
  movie) has a watch, whichever source reported it - Trakt, Simkl, Nuvio,
  the Scrobble API, an import or a button here - and it matches on the
  watch itself, never on when a sync ran. A slot that was not watched
  rolls to MOVED only once the profile's sync sources have run since it
  ended (so a watch still on its way is not mistaken for a miss); what is
  left of it is planned again into the next open slot.
- estimate()     - when the current lists would be finished at this pace.

Only released content is planned: an episode needs an air date that has
passed, a movie needs to be out. Episodes come from TMDB's (cached)
season lists, planned in order, skipping anything already watched.
"""

import contextlib
import datetime
import logging
import random
from datetime import timedelta

from django.core.cache import cache
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from . import completion, recommendations, rewatches, selectors, show_progress
from .integrations import tmdb
from .models import (
    Episode,
    ExternalAccount,
    MediaType,
    NuvioConnection,
    ReleaseSchedule,
    SchedulePlan,
    ScheduledSlot,
    WatchEvent,
    WatchList,
    WatchListItem,
)

logger = logging.getLogger(__name__)

HORIZON_DAYS = 14
SLOT_GAP = timedelta(minutes=5)
# A show's block takes episodes until it is about this long, so a half-hour
# sitcom gets a few in a row and an hour-long drama gets one or two.
BLOCK_TARGET_MINUTES = 100
DEFAULT_MOVIE_MINUTES = 110
DEFAULT_EPISODE_MINUTES = 45
DEFAULT_ANIME_EPISODE_MINUTES = 24
# A missed slot is rolled on even if a sync source never reports back.
ROLL_AFTER = timedelta(hours=36)
FEED_LIMIT = 40
# Real time is never packed perfectly - gaps, a long ending, a pause.
PACKING_EFFICIENCY = 0.9
ESTIMATE_CACHE_SECONDS = 1800
FILL_CACHE_SECONDS = 600
MAX_ESTIMATE_DAYS = 3 * 365
PIXELS_PER_HOUR = 56
# Room above the first and below the last hour line, so the half of a label
# that hangs outside the grid does not make the timeline scroll for nothing.
GRID_PAD = 10
# The timeline's first hour, unless something is planned earlier.
DAY_STARTS_AT = 7

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SYNC_PROVIDERS = (
    ExternalAccount.Provider.TRAKT,
    ExternalAccount.Provider.SIMKL,
    ExternalAccount.Provider.WETRAKR,
    ExternalAccount.Provider.MAL,
)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def get_plan(profile):
    plan, _ = SchedulePlan.objects.get_or_create(profile=profile, defaults={"free_days": [0, 1, 2, 3, 4]})
    return plan


def parse_time(value):
    try:
        hour, minute = str(value).split(":")[:2]
        return datetime.time(int(hour), int(minute))
    except (ValueError, TypeError):
        return None


def window_for(plan, weekday):
    """(start, end) times for a weekday, or None when it is not a free day
    (or its window is empty/backwards)."""
    if weekday not in (plan.free_days or []):
        return None
    start, end = plan.window_start, plan.window_end
    override = (plan.day_windows or {}).get(str(weekday))
    if override and len(override) == 2:
        start = parse_time(override[0]) or start
        end = parse_time(override[1]) or end
    return (start, end) if end > start else None


def _at(day, time, tz):
    return datetime.datetime.combine(day, time, tzinfo=tz)


def _ceil5(moment):
    moment = moment.replace(second=0, microsecond=0)
    return moment + timedelta(minutes=(-moment.minute) % 5)


def source_lists(profile, plan):
    """The lists the plan draws from: the ones picked (still visible to the
    profile), else just the profile's own Watchlist."""
    chosen = list(plan.source_lists.filter(Q(profile=profile) | Q(is_shared=True)).distinct())
    if chosen:
        return chosen
    return list(WatchList.objects.filter(profile=profile, is_watchlist=True)[:1])


def list_choices(profile, plan):
    """[(list, selected)] for the settings chips - every list the profile
    can see, with the Watchlist standing in as selected when none is."""
    chosen_ids = {wl.id for wl in source_lists(profile, plan)}
    lists = WatchList.objects.filter(Q(profile=profile) | Q(is_shared=True)).select_related("profile").distinct()
    ordered = sorted(lists, key=lambda wl: (not wl.is_watchlist, wl.name.lower()))
    return [(wl, wl.id in chosen_ids, wl.items.count()) for wl in ordered]


# ---------------------------------------------------------------------------
# Candidates and episodes
# ---------------------------------------------------------------------------


def candidates(profile, plan, now):
    """Titles to plan, in plan order: [WatchListItem...] deduplicated by
    title, filtered by the media choice."""
    lists = source_lists(profile, plan)
    if not lists:
        return []
    order_of = {wl.id: i for i, wl in enumerate(lists)}
    items = list(
        WatchListItem.objects.filter(watchlist__in=lists).select_related("title", "watchlist").order_by("added_at", "id")
    )
    if plan.media != SchedulePlan.Media.ALL:
        items = [i for i in items if i.title.media_type == plan.media]
    if plan.order == SchedulePlan.Order.LIST:
        items.sort(key=lambda i: (order_of.get(i.watchlist_id, 0), i.position, i.id))
    elif plan.order == SchedulePlan.Order.SHUFFLE:
        year, week, _ = timezone.localdate().isocalendar()
        random.Random(f"{profile.id}:{year}-{week}").shuffle(items)
    seen, result = set(), []
    for item in items:
        if item.title_id not in seen:
            seen.add(item.title_id)
            result.append(item)
    return result


def _movie_released(title, now):
    if title.year > timezone.localdate(now).year:
        return False
    row = ReleaseSchedule.objects.filter(title=title, release_type=ReleaseSchedule.ReleaseType.MOVIE_RELEASE).first()
    return row is None or row.release_date <= now


def _default_episode_minutes(title):
    return DEFAULT_ANIME_EPISODE_MINUTES if title.media_type == MediaType.ANIME else DEFAULT_EPISODE_MINUTES


def _parse_air_date(raw):
    try:
        return datetime.date.fromisoformat(raw[:10]) if raw else None
    except ValueError:
        return None


def episode_feed(title, watched, today, limit=FEED_LIMIT):
    """The next released, unwatched episodes of a show, in order:
    [{"season", "episode", "name", "runtime"}]. watched: set of
    (season, episode). Empty for a title TMDB cannot resolve."""
    tmdb_id = title.external_ids.get("tmdb")
    kind = tmdb.media_type_for(title)
    if not tmdb_id or kind == "movie":
        return []
    details = tmdb.get_full_details(kind, tmdb_id)
    if not details:
        return []
    last = details.get("last_episode_to_air") or {}
    last_season = last.get("season_number") or details.get("number_of_seasons") or 0
    counts = dict(details.get("seasons") or [])
    watched_per_season = {}
    for season, _episode in watched:
        watched_per_season[season] = watched_per_season.get(season, 0) + 1
    typical = details.get("episode_runtime") or _default_episode_minutes(title)
    feed = []
    for season in range(1, int(last_season) + 1):
        total = counts.get(season)
        if total and watched_per_season.get(season, 0) >= total:
            continue
        data = tmdb.get_season_details(tmdb_id, season)
        if not data:
            continue
        for ep in data["episodes"]:
            number = ep.get("episode_number")
            aired = _parse_air_date(ep.get("air_date"))
            if number is None or (season, number) in watched or not aired or aired > today:
                continue
            feed.append(
                {"season": season, "episode": number, "name": ep.get("name") or "", "runtime": ep.get("runtime") or typical}
            )
            if len(feed) >= limit:
                return feed
    return feed


def _watched_pairs(profile, title):
    return set(
        WatchEvent.objects.filter(profile=profile, title=title, episode__isnull=False).values_list(
            "episode__season", "episode__episode"
        )
    )


# ---------------------------------------------------------------------------
# Building the plan
# ---------------------------------------------------------------------------


def _free_intervals(start, end, taken):
    """[(a, b)] of [start, end) not covered by the (a, b) pairs in taken."""
    free, cursor = [], start
    for a, b in sorted(taken):
        if b <= cursor or a >= end:
            continue
        if a > cursor:
            free.append((cursor, a))
        cursor = max(cursor, b + SLOT_GAP)
    if cursor < end:
        free.append((cursor, end))
    return free


def _minutes(delta):
    return int(delta.total_seconds() // 60)


class _State:
    """Per-fill bookkeeping: each candidate's remaining episodes, what is
    already planned, and how many blocks it has in the horizon (the fewer,
    the sooner it is picked - that is what rotates the plan between titles)."""

    def __init__(self, profile, items, planned_slots, today):
        self.profile = profile
        self.items = items
        self.today = today
        self.planned_count = {}
        self.planned_pairs = {}
        self.planned_movies = set()
        for slot in planned_slots:
            self.planned_count[slot.title_id] = self.planned_count.get(slot.title_id, 0) + 1
            eps = list(slot.episodes.all())
            if not eps:
                self.planned_movies.add(slot.title_id)
            for ep in eps:
                self.planned_pairs.setdefault(slot.title_id, set()).add((ep.season, ep.episode))
        self._feeds = {}
        self._watched_movies = set(
            WatchEvent.objects.filter(
                profile=profile, title__in=[i.title_id for i in items], episode__isnull=True
            ).values_list("title_id", flat=True)
        )

    def remaining_episodes(self, title):
        if title.id not in self._feeds:
            watched = _watched_pairs(self.profile, title) | self.planned_pairs.get(title.id, set())
            self._feeds[title.id] = episode_feed(title, watched, self.today)
        return self._feeds[title.id]

    def is_movie(self, title):
        return tmdb.media_type_for(title) == "movie" or title.media_type == MediaType.MOVIE

    def order(self):
        return sorted(range(len(self.items)), key=lambda i: (self.planned_count.get(self.items[i].title_id, 0), i))

    def block(self, item, room):
        """What this title would add in `room` minutes: ("movie", minutes) or
        ("tv", [episodes]); None if it has nothing releasable that fits."""
        title = item.title
        if self.is_movie(title):
            if title.id in self._watched_movies or title.id in self.planned_movies:
                return None
            if not _movie_released(title, timezone.now()):
                return None
            minutes = title.runtime_minutes or DEFAULT_MOVIE_MINUTES
            return ("movie", minutes) if minutes <= room else None
        taken, total = [], 0
        for ep in self.remaining_episodes(title):
            if ep["season"] is None or (ep["season"], ep["episode"]) in self.planned_pairs.get(title.id, set()):
                continue
            if total + ep["runtime"] > room:
                break
            if taken and total + ep["runtime"] > BLOCK_TARGET_MINUTES:
                break
            taken.append(ep)
            total += ep["runtime"]
        return ("tv", taken) if taken else None

    def commit(self, item, kind, payload):
        title = item.title
        self.planned_count[title.id] = self.planned_count.get(title.id, 0) + 1
        if kind == "movie":
            self.planned_movies.add(title.id)
        else:
            self.planned_pairs.setdefault(title.id, set()).update((e["season"], e["episode"]) for e in payload)


def fill(profile, plan, now=None):
    """Plans unwatched, released titles into every free window from now to
    the horizon that has room, without touching existing planned slots.
    Returns the number of slots created."""
    now = now or timezone.now()
    tz = selectors.profile_zoneinfo(profile)
    local_now = now.astimezone(tz)
    today = local_now.date()
    items = candidates(profile, plan, now)
    if not items:
        return 0
    horizon_end = _at(today + timedelta(days=HORIZON_DAYS), datetime.time(0, 0), tz)
    planned = list(
        ScheduledSlot.objects.filter(
            profile=profile, status=ScheduledSlot.Status.PLANNED, end__gt=now, start__lt=horizon_end
        ).prefetch_related("episodes")
    )
    state = _State(profile, items, planned, today)
    created = 0
    for offset in range(HORIZON_DAYS):
        day = today + timedelta(days=offset)
        window = window_for(plan, day.weekday())
        if not window:
            continue
        start, end = _at(day, window[0], tz), _at(day, window[1], tz)
        start = max(start, _ceil5(local_now))
        if start >= end:
            continue
        taken = [(s.start.astimezone(tz), s.end.astimezone(tz)) for s in planned if start - timedelta(days=1) < s.start < end]
        for a, b in _free_intervals(start, end, taken):
            cursor = a
            while True:
                room = _minutes(b - cursor)
                pick = None
                for index in state.order():
                    block = state.block(state.items[index], room)
                    if block:
                        pick = (state.items[index], block)
                        break
                if not pick:
                    break
                item, (kind, payload) = pick
                minutes = payload if kind == "movie" else sum(e["runtime"] for e in payload)
                slot = ScheduledSlot.objects.create(
                    profile=profile, title=item.title, start=cursor, end=cursor + timedelta(minutes=minutes)
                )
                if kind == "tv":
                    slot.episodes.set(
                        [
                            Episode.objects.get_or_create(
                                title=item.title,
                                season=e["season"],
                                episode=e["episode"],
                                defaults={"name": e["name"][:255], "runtime_minutes": e["runtime"]},
                            )[0]
                            for e in payload
                        ]
                    )
                state.commit(item, kind, payload)
                created += 1
                cursor = slot.end + SLOT_GAP
    return created


def rebuild(profile, plan, now=None):
    """Drops every planned slot that has not started and plans again."""
    now = now or timezone.now()
    ScheduledSlot.objects.filter(profile=profile, status=ScheduledSlot.Status.PLANNED, start__gt=now).delete()
    return fill(profile, plan, now)


# ---------------------------------------------------------------------------
# What happened
# ---------------------------------------------------------------------------


def sync_caught_up(profile, since, now):
    """True when every watch-syncing connection has run since `since` (or
    there are none, or it has been long enough that waiting is pointless):
    only then does a slot with no watch count as missed."""
    if now - since >= ROLL_AFTER:
        return True
    stamps = list(
        ExternalAccount.objects.filter(profile=profile, provider__in=SYNC_PROVIDERS, sync_direction__in=("pull", "both")).values_list("last_synced_at", flat=True)
    )
    stamps += list(NuvioConnection.objects.filter(profile=profile, sync_direction__in=("pull", "both")).values_list("last_synced_at", flat=True))
    return all(stamp is not None and stamp >= since for stamp in stamps)


def reconcile(profile, now=None):
    """Settles planned slots against real watches; returns how many changed."""
    now = now or timezone.now()
    slots = list(
        ScheduledSlot.objects.filter(profile=profile, status=ScheduledSlot.Status.PLANNED)
        .select_related("title")
        .prefetch_related("episodes")
    )
    if not slots:
        return 0
    title_ids = {s.title_id for s in slots}
    watched_eps = set(
        WatchEvent.objects.filter(profile=profile, title_id__in=title_ids, episode__isnull=False).values_list(
            "title_id", "episode_id"
        )
    )
    watched_movies = set(
        WatchEvent.objects.filter(profile=profile, title_id__in=title_ids, episode__isnull=True).values_list(
            "title_id", flat=True
        )
    )
    changed = 0
    for slot in slots:
        eps = list(slot.episodes.all())
        if eps:
            left = [e for e in eps if (slot.title_id, e.id) not in watched_eps]
            done = not left
        else:
            left, done = [], slot.title_id in watched_movies
        if done:
            slot.status = ScheduledSlot.Status.DONE
            slot.save(update_fields=["status"])
            changed += 1
        elif slot.end <= now and sync_caught_up(profile, slot.end, now):
            slot.status = ScheduledSlot.Status.MOVED
            slot.save(update_fields=["status"])
            changed += 1
        elif eps and len(left) < len(eps) and slot.start > now:
            # Part of a block that has not started was watched early.
            removed = sum(e.runtime_minutes or _default_episode_minutes(slot.title) for e in eps if e not in left)
            slot.episodes.set(left)
            slot.end = max(slot.start + timedelta(minutes=10), slot.end - timedelta(minutes=removed))
            slot.save(update_fields=["end"])
            changed += 1
    return changed


@contextlib.contextmanager
def _plan_lock(plan):
    """Holds the plan row's lock for the length of a plan/rebuild. Only
    Postgres has row locks to take (SQLite, the dev/test fallback, locks
    the whole file and would fail an upgrade from a read outright), so
    elsewhere this is a plain pass-through."""
    if connection.vendor != "postgresql":
        yield plan
        return
    with transaction.atomic():
        yield SchedulePlan.objects.select_for_update().get(pk=plan.pk)


def ensure_plan(profile, now=None):
    """Called before the plan is shown. Does nothing while the plan is off.
    A manual plan is only reconciled (done / missed), never filled. The
    plan row is locked while this runs, so two requests at once (a double
    click, a refresh mid-load) plan one after the other instead of both
    planning the same hours."""
    plan = get_plan(profile)
    if not plan.enabled:
        return plan
    now = now or timezone.now()
    with _plan_lock(plan) as plan:
        changed = reconcile(profile, now)
        key = f"schedule:fill:{profile.id}"
        if plan.mode == SchedulePlan.Mode.MANUAL:
            if plan.dirty:
                plan.dirty = False
                plan.save(update_fields=["dirty"])
        elif plan.dirty:
            plan.dirty = False
            plan.save(update_fields=["dirty"])
            rebuild(profile, plan, now)
            cache.set(key, 1, FILL_CACHE_SECONDS)
        elif changed or cache.add(key, 1, FILL_CACHE_SECONDS):
            fill(profile, plan, now)
            if changed:
                cache.set(key, 1, FILL_CACHE_SECONDS)
    return plan


def invalidate(profile):
    cache.delete(f"schedule:fill:{profile.id}")


# ---------------------------------------------------------------------------
# Manual plans
# ---------------------------------------------------------------------------


def add_manual_slot(profile, plan, title_id, start, count=1, now=None):
    """One block put into the plan by hand: a movie, or the next `count`
    released, unwatched episodes of a show, starting at `start`. Returns
    (slot, None) or (None, "what is wrong")."""
    now = now or timezone.now()
    item = next((i for i in candidates(profile, plan, now) if i.title_id == title_id), None)
    if item is None:
        return None, "That title is not on a list you plan from."
    if start < now - timedelta(minutes=5):
        return None, "Pick a time that has not passed."
    tz = selectors.profile_zoneinfo(profile)
    planned = list(
        ScheduledSlot.objects.filter(profile=profile, status=ScheduledSlot.Status.PLANNED).prefetch_related("episodes")
    )
    state = _State(profile, [item], planned, now.astimezone(tz).date())
    title = item.title
    if state.is_movie(title):
        block = state.block(item, 10**6)
        if block is None:
            return None, "That movie is already watched or planned, or is not out yet."
        minutes, episodes = block[1], []
    else:
        taken = [
            ep
            for ep in state.remaining_episodes(title)
            if (ep["season"], ep["episode"]) not in state.planned_pairs.get(title.id, set())
        ][: max(1, min(int(count or 1), 6))]
        if not taken:
            return None, "Nothing released and unwatched is left to plan for that show."
        minutes, episodes = sum(e["runtime"] for e in taken), taken
    end = start + timedelta(minutes=minutes)
    clash = ScheduledSlot.objects.filter(profile=profile, start__lt=end, end__gt=start).exclude(
        status=ScheduledSlot.Status.MOVED
    )
    if clash.exists():
        other = clash.select_related("title").first()
        return None, f"That overlaps {other.title.name} at {other.start.astimezone(tz):%H:%M}."
    slot = ScheduledSlot.objects.create(profile=profile, title=title, start=start, end=end)
    if episodes:
        slot.episodes.set(
            [
                Episode.objects.get_or_create(
                    title=title,
                    season=e["season"],
                    episode=e["episode"],
                    defaults={"name": e["name"][:255], "runtime_minutes": e["runtime"]},
                )[0]
                for e in episodes
            ]
        )
    return slot, None


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------


def mark_slot_watched(profile, slot, now=None):
    """The manual "watched" fallback: records the slot's episodes (or the
    movie) as watched now, with the same follow-ups every other place a
    watch is logged runs."""
    now = now or timezone.now()
    title = slot.title
    eps = list(slot.episodes.all())
    if eps:
        for ep in eps:
            if not WatchEvent.objects.filter(profile=profile, title=title, episode=ep).exists():
                WatchEvent.objects.create(profile=profile, title=title, episode=ep, watched_at=now)
                rewatches.recompute_is_rewatch(profile, title, ep)
        completion.sync_show_completion(profile, title, ensure_watching=True)
    else:
        WatchEvent.objects.create(profile=profile, title=title, watched_at=now)
        rewatches.recompute_is_rewatch(profile, title, None)
    completion.sync_watchlist_removal(profile, title)
    recommendations.mark_title_watched(profile, title)
    slot.status = ScheduledSlot.Status.DONE
    slot.save(update_fields=["status"])
    invalidate(profile)


def skip_slot(profile, slot, now=None):
    """The manual "skip": the slot is moved on and its titles are planned
    again into a later slot."""
    plan = get_plan(profile)
    if plan.mode == SchedulePlan.Mode.MANUAL:
        # Nothing re-plans a manual schedule, so skipping takes it off.
        slot.delete()
        return
    slot.status = ScheduledSlot.Status.MOVED
    slot.save(update_fields=["status"])
    invalidate(profile)
    fill(profile, plan, now or timezone.now())


# ---------------------------------------------------------------------------
# What the pages show
# ---------------------------------------------------------------------------


def _episode_label(title, episodes):
    eps = sorted(episodes, key=lambda e: (e.season, e.episode))
    if not eps:
        return ""
    anime = title.media_type == MediaType.ANIME

    def one(e):
        return f"E{e.episode}" if anime and e.season == 1 else f"S{e.season}E{e.episode}"

    if len(eps) == 1:
        return one(eps[0])
    first, last = eps[0], eps[-1]
    if first.season == last.season:
        return f"{one(first)} to E{last.episode}"
    return f"{one(first)} to {one(last)}"


def _duration_label(minutes):
    hours, mins = divmod(int(minutes), 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    return f"{hours}h" if hours else f"{mins}m"


def _slot_view(slot, now, tz, caught_up_cache):
    start, end = slot.start.astimezone(tz), slot.end.astimezone(tz)
    eps = list(slot.episodes.all())
    status = slot.status
    waiting = (
        status == ScheduledSlot.Status.PLANNED
        and slot.end <= now
        and not caught_up_cache(slot)
    )
    return {
        "id": slot.id,
        "title": slot.title,
        "kind": slot.title.media_type,
        "label": _episode_label(slot.title, eps) or _duration_label(_minutes(slot.end - slot.start)),
        "start": start,
        "end": end,
        "duration": _duration_label(_minutes(slot.end - slot.start)),
        "status": status,
        "waiting": waiting,
        "in_progress": status == ScheduledSlot.Status.PLANNED and slot.start <= now < slot.end,
        "actionable": status == ScheduledSlot.Status.PLANNED,
    }


def _planned_minutes(slots):
    return sum(_minutes(s.end - s.start) for s in slots if s.status != ScheduledSlot.Status.MOVED)


def week_context(profile, plan, start_day, days, now=None):
    """Everything the week/day timeline template draws, positions in
    pixels from the top of the shown hour range."""
    now = now or timezone.now()
    tz = selectors.profile_zoneinfo(profile)
    today = now.astimezone(tz).date()
    day_list = [start_day + timedelta(days=i) for i in range(days)]
    lo, hi = _at(day_list[0], datetime.time(0, 0), tz), _at(day_list[-1] + timedelta(days=1), datetime.time(0, 0), tz)
    slots = list(
        ScheduledSlot.objects.filter(profile=profile, start__gte=lo, start__lt=hi)
        .select_related("title")
        .prefetch_related("episodes")
    )
    stamps_cache = {}

    def caught_up(slot):
        if slot.id not in stamps_cache:
            stamps_cache[slot.id] = sync_caught_up(profile, slot.end, now)
        return stamps_cache[slot.id]

    views = [_slot_view(s, now, tz, caught_up) for s in slots]
    next_id = next(
        (v["id"] for v in sorted(views, key=lambda v: v["start"]) if v["status"] == ScheduledSlot.Status.PLANNED and v["end"] > now.astimezone(tz)),
        None,
    )
    # The whole day is drawn; the card is as tall as the sidebar beside it
    # and opens on the free time (focus_top/focus_bottom), so the space is
    # filled with the hours around it instead of stretching the slots.
    marks = []
    for day in day_list:
        window = window_for(plan, day.weekday())
        if window:
            marks += [window[0].hour * 60 + window[0].minute, window[1].hour * 60 + window[1].minute]
    for v in views:
        marks += [v["start"].hour * 60 + v["start"].minute, v["end"].hour * 60 + v["end"].minute or 24 * 60]
    # From early morning to midnight - the dead small hours only if
    # something is actually planned in them.
    first_hour = min(DAY_STARTS_AT, max(0, min(marks) // 60 - 1)) if marks else DAY_STARTS_AT
    last_hour = 24
    range_minutes = (last_hour - first_hour) * 60

    def px(moment_minutes):
        return round((moment_minutes - first_hour * 60) / 60 * PIXELS_PER_HOUR + GRID_PAD, 1)

    columns = []
    for day in day_list:
        window = window_for(plan, day.weekday())
        day_slots = [v for v in views if v["start"].date() == day]
        for v in day_slots:
            v["top"] = px(v["start"].hour * 60 + v["start"].minute)
            v["height"] = max(26.0, round(_minutes(v["end"] - v["start"]) / 60 * PIXELS_PER_HOUR, 1))
            v["is_next"] = v["id"] == next_id
            v["show_poster"] = v["height"] >= 46
        columns.append(
            {
                "date": day,
                "is_today": day == today,
                "window": window,
                "window_top": px(window[0].hour * 60 + window[0].minute) if window else None,
                "window_height": round(
                    ((window[1].hour * 60 + window[1].minute) - (window[0].hour * 60 + window[0].minute)) / 60 * PIXELS_PER_HOUR, 1
                )
                if window
                else None,
                "slots": day_slots,
            }
        )
    now_local = now.astimezone(tz)
    return {
        "columns": columns,
        "hours": [{"hour": h, "top": px(h * 60)} for h in range(first_hour, last_hour + 1)],
        "grid_height": round(range_minutes / 60 * PIXELS_PER_HOUR + 2 * GRID_PAD, 1),
        "now_top": px(now_local.hour * 60 + now_local.minute) if first_hour * 60 <= now_local.hour * 60 + now_local.minute <= last_hour * 60 else None,
        "now_day": today,
        "focus_top": round((min(marks) - first_hour * 60) / 60 * PIXELS_PER_HOUR + GRID_PAD) if marks else round(18 * PIXELS_PER_HOUR),
        "focus_bottom": round((max(marks) - first_hour * 60) / 60 * PIXELS_PER_HOUR + GRID_PAD) if marks else round(23 * PIXELS_PER_HOUR),
        "days": days,
        "start_day": start_day,
        "end_day": day_list[-1],
    }


def tonight_context(profile, plan, now=None):
    """The Tonight card: today's remaining blocks and what is open, or - when
    today has nothing left - the next day with a plan."""
    now = now or timezone.now()
    tz = selectors.profile_zoneinfo(profile)
    local_now = now.astimezone(tz)
    today = local_now.date()
    for offset in range(HORIZON_DAYS):
        day = today + timedelta(days=offset)
        window = window_for(plan, day.weekday())
        day_start, day_end = _at(day, datetime.time(0, 0), tz), _at(day + timedelta(days=1), datetime.time(0, 0), tz)
        slots = list(
            ScheduledSlot.objects.filter(profile=profile, start__gte=day_start, start__lt=day_end)
            .exclude(status=ScheduledSlot.Status.MOVED)
            .select_related("title")
            .prefetch_related("episodes")
        )
        live = [s for s in slots if s.status == ScheduledSlot.Status.DONE or s.end > now]
        if not live:
            continue
        views = [_slot_view(s, now, tz, lambda slot: True) for s in live]
        planned = [v for v in views if v["status"] == ScheduledSlot.Status.PLANNED]
        left_after = None
        if window and planned:
            window_end = _at(day, window[1], tz)
            last_end = max(v["end"] for v in planned)
            if window_end - last_end >= timedelta(minutes=30):
                left_after = {"start": last_end + SLOT_GAP, "end": window_end}
        return {
            "day": day,
            "is_today": offset == 0,
            "is_tomorrow": offset == 1,
            "window": window,
            "slots": views,
            "planned_label": _duration_label(_planned_minutes(live)) if _planned_minutes(live) else "",
            "open": left_after,
        }
    return None


def remaining_minutes(profile, plan, now=None):
    """(minutes, titles counted) still to watch across the plan's lists:
    released episodes and movies only - nothing unaired or upcoming."""
    now = now or timezone.now()
    total, counted = 0, 0
    for item in candidates(profile, plan, now):
        title = item.title
        is_movie = title.media_type == MediaType.MOVIE or tmdb.media_type_for(title) == "movie"
        if is_movie:
            if WatchEvent.objects.filter(profile=profile, title=title).exists() or not _movie_released(title, now):
                continue
            total += title.runtime_minutes or DEFAULT_MOVIE_MINUTES
            counted += 1
            continue
        tmdb_id = title.external_ids.get("tmdb")
        details = tmdb.get_full_details(tmdb.media_type_for(title), tmdb_id) if tmdb_id else None
        if not details:
            continue
        aired = show_progress.aired_episode_count(details)
        watched = (
            WatchEvent.objects.filter(profile=profile, title=title, episode__isnull=False)
            .values("episode__season", "episode__episode")
            .distinct()
            .count()
        )
        left = max(0, aired - watched)
        if left:
            total += left * (details.get("episode_runtime") or _default_episode_minutes(title))
            counted += 1
    return total, counted


def finish_date(plan, minutes, now, tz):
    """The day `minutes` of viewing would be used up by the free windows,
    or None if there is no free time at all (or it is further than
    MAX_ESTIMATE_DAYS away)."""
    if minutes <= 0:
        return None
    local_now = now.astimezone(tz)
    left = minutes / PACKING_EFFICIENCY
    for offset in range(MAX_ESTIMATE_DAYS):
        day = local_now.date() + timedelta(days=offset)
        window = window_for(plan, day.weekday())
        if not window:
            continue
        start, end = _at(day, window[0], tz), _at(day, window[1], tz)
        start = max(start, local_now)
        if end > start:
            left -= (end - start).total_seconds() / 60
        if left <= 0:
            return day
    return None


def estimate(profile, plan, now=None):
    """{"minutes", "titles", "date", "hours_label", "no_time"}: when the
    lists would be finished. Cached; the key changes whenever the settings,
    the lists or the watch history do, so it follows what you do."""
    now = now or timezone.now()
    tz = selectors.profile_zoneinfo(profile)
    signature = "-".join(
        str(part)
        for part in (
            profile.id,
            int(plan.updated_at.timestamp()) if plan.updated_at else 0,
            WatchEvent.objects.filter(profile=profile).count(),
            WatchListItem.objects.filter(watchlist__in=source_lists(profile, plan)).count(),
            now.astimezone(tz).date().isoformat(),
        )
    )
    key = f"schedule:estimate:{signature}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    minutes, titles = remaining_minutes(profile, plan, now)
    date = finish_date(plan, minutes, now, tz)
    result = {
        "minutes": minutes,
        "titles": titles,
        "date": date,
        "hours_label": _duration_label(minutes),
        "empty": minutes <= 0,
        "no_time": minutes > 0 and date is None,
    }
    cache.set(key, result, ESTIMATE_CACHE_SECONDS)
    return result
