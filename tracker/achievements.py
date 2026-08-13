"""Lightweight gamification - a static registry of achievements, each
checked against existing streak/genre/watch-time data (selectors.py,
WatchEvent directly), with earned state persisted in ProfileAchievement
once a check first passes. Kept out of selectors.py since these are a
distinct concern (badges, not chart/panel data) with their own
award/persist step selectors.py's read-only functions don't have."""

from dataclasses import dataclass
from typing import Callable

from django.db.models import Count, Q
from django.db.models.functions import ExtractHour, TruncDate
from django.utils import timezone

from . import selectors
from .models import Genre, MediaType, Profile, ProfileAchievement, WatchEvent


@dataclass(frozen=True)
class Achievement:
    key: str
    name: str
    description: str
    check: Callable[[Profile], bool]


def _genre_explorer(profile):
    total_genres = Genre.objects.count()
    if not total_genres:
        return False
    watched_genres = (
        WatchEvent.objects.filter(profile=profile, title__genres__isnull=False)
        .values("title__genres__id")
        .distinct()
        .count()
    )
    return watched_genres >= total_genres


def _night_owl(profile):
    # Same 21:00-05:00 window as selectors.py's own "Night" time-of-day
    # bucket (_TIME_OF_DAY_BUCKETS) - local hour, not the UTC hour
    # watched_at is stored as.
    count = (
        WatchEvent.objects.filter(profile=profile)
        .annotate(hour=ExtractHour("watched_at", tzinfo=timezone.get_current_timezone()))
        .filter(Q(hour__gte=21) | Q(hour__lt=5))
        .count()
    )
    return count >= 20


def _century_club(profile):
    return (
        WatchEvent.objects.filter(profile=profile, title__media_type=MediaType.MOVIE)
        .values("title_id")
        .distinct()
        .count()
        >= 100
    )


def _streak_master(profile):
    _, longest = selectors.streaks(profile)
    return longest >= 30


def _marathoner(profile):
    top_day = (
        WatchEvent.objects.filter(profile=profile)
        .annotate(day=TruncDate("watched_at", tzinfo=timezone.get_current_timezone()))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("-count")
        .first()
    )
    return bool(top_day) and top_day["count"] >= 5


ACHIEVEMENTS = [
    Achievement("genre_explorer", "Genre Explorer", "Watched something in every genre", _genre_explorer),
    Achievement("night_owl", "Night Owl", "20+ watches between 9pm and 5am", _night_owl),
    Achievement("century_club", "Century Club", "100 movies watched", _century_club),
    Achievement("streak_master", "Streak Master", "Hit a 30-day watch streak", _streak_master),
    Achievement("marathoner", "Marathoner", "5+ watches in a single day", _marathoner),
]


def check_and_award(profile):
    """Runs every not-yet-earned achievement's check against profile,
    persisting any newly-earned ones. Idempotent and side-effect-free
    for anything already earned - safe to call on every Stats page
    render (own or a household member's) rather than needing a
    separate periodic job."""
    earned_keys = set(ProfileAchievement.objects.filter(profile=profile).values_list("key", flat=True))
    for achievement in ACHIEVEMENTS:
        if achievement.key in earned_keys:
            continue
        if achievement.check(profile):
            ProfileAchievement.objects.get_or_create(profile=profile, key=achievement.key)


def achievement_progress(profile):
    """The full badge collection for the Stats page's Achievements grid -
    every registered achievement, earned or not, so locked badges show
    up too (not just what's already been unlocked). Calls
    check_and_award first so a badge earned by this very page load
    (e.g. a rewatch that just crossed a threshold) shows as earned
    immediately."""
    check_and_award(profile)
    earned_at_by_key = dict(
        ProfileAchievement.objects.filter(profile=profile).values_list("key", "earned_at")
    )
    return [
        {
            "key": a.key,
            "name": a.name,
            "description": a.description,
            "earned": a.key in earned_at_by_key,
            "earned_at": earned_at_by_key.get(a.key),
        }
        for a in ACHIEVEMENTS
    ]
