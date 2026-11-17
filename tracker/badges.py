"""Achievement badges, XP and levels.

Badges are earned from the watch history, whatever its source (Nuvio sync,
Trakt import, marked by hand). The catalog is the static registry below (not a
table: nobody defines new ones from the UI); what a profile has earned is
`ProfileBadgeTier` rows, which are only ever added, so a tier stays earned if
the title behind it is later deleted. Each badge has five tiers worth 5 / 10 /
20 / 40 / 80 XP, and a tier that fewer than a quarter of the household holds
pays 1.5x. XP adds up to a level (Newcomer ... Legend).

`rebuild(profile)` recounts everything and awards what is newly reached. It is
cheap enough to run on demand, so the history signals only mark the profile
stale (`mark_stale`) and `ensure_fresh` rebuilds on the next page that needs a
level. The first rebuild of a profile backfills silently: no toasts for a
history that already existed.
"""

import datetime
import logging
from collections import Counter, defaultdict, namedtuple
from dataclasses import dataclass
from functools import cached_property

from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from . import selectors
from .models import (
    MediaType,
    Profile,
    ProfileBadgeTier,
    ProfileLevelUp,
    Recommendation,
    Title,
    WatchEvent,
    WatchProgress,
)

logger = logging.getLogger(__name__)

TIER_XP = (5, 10, 20, 40, 80)
TIER_COUNT = len(TIER_XP)
RARE_SHARE = 0.25
RARE_MULTIPLIER = 1.5
NEW_TIER_HOURS = 24
# A profile whose own history hasn't moved still catches up on the badges that
# count the rest of the household now and then.
RECHECK_AFTER = datetime.timedelta(hours=6)

LEVELS = (
    ("Newcomer", 0),
    ("Explorer", 100),
    ("Regular", 250),
    ("Cinephile", 400),
    ("Curator", 650),
    ("Expert", 1000),
    ("Legend", 1500),
)


@dataclass(frozen=True)
class Category:
    key: str
    name: str
    shape: str
    blurb: str


CATEGORIES = (
    Category("movies", "Movies", "Ticket", "Features, from the first to the four-thousandth."),
    Category("tv", "TV shows", "Screen", "Episodes, seasons and the shows you see through to the end."),
    Category("anime", "Anime", "Reel", "Openings, seasons and simulcasts."),
    Category("habits", "Habits", "Arch", "When and how you watch: streaks, late nights, weekends."),
    Category("household", "Household", "House", "Watching together and sharing with the people you live with."),
    Category("unique", "Unique", "Diamond", "One-off badges. Some stay hidden until the day you earn them."),
)
CATEGORY_BY_KEY = {c.key: c for c in CATEGORIES}


@dataclass(frozen=True)
class Badge:
    key: str
    name: str
    category: str
    icon: str
    description: str
    tiers: tuple
    goal: str  # "{n} movies": the sentence for a tier's threshold
    unit: str  # what the running count is of, e.g. "episodes"
    hidden: bool = False


def _b(key, name, category, icon, description, tiers, goal, unit, hidden=False):
    return Badge(key, name, category, icon, description, tuple(tiers), goal, unit, hidden)


BADGES = (
    # Movies
    _b("projectionist", "Projectionist", "movies", "film", "Watch lots of different movies.",
       (10, 50, 100, 250, 500), "{n} movies", "movies"),
    _b("epic_length", "Epic length", "movies", "clock", "Sit through the long ones: movies over 2h 30m.",
       (1, 10, 25, 50, 100), "{n} movies over 2h 30m", "movies"),
    _b("double_bill", "Double bill", "movies", "zap", "Two different movies in one day.",
       (1, 5, 25, 50, 100), "{n} days with two movies", "days"),
    _b("saga_keeper", "Saga keeper", "movies", "trophy", "Follow a director through three or more films.",
       (1, 3, 5, 10, 20), "{n} directors with three or more films", "directors"),
    _b("studio_tour", "Studio tour", "movies", "compass", "Movies from many different studios.",
       (3, 5, 10, 25, 50), "{n} studios", "studios"),
    # TV shows
    _b("sunrise_to_sunset", "Sunrise to sunset", "tv", "calendar-days",
       "Watch something in the morning and again at night, on the same day.",
       (1, 10, 25, 50, 100), "{n} days from morning to night", "days"),
    _b("three_in_a_row", "Three in a row", "tv", "tv", "Watch the same show three days in a row.",
       (1, 3, 5, 10, 25), "{n} shows watched three days in a row", "shows"),
    _b("pilot_season", "Pilot season", "tv", "play", "Start lots of different shows.",
       (10, 25, 50, 100, 250), "{n} shows started", "shows"),
    _b("marathoner", "Marathoner", "tv", "flame",
       "Watch lots of episodes back to back in a single day. Counted by your best day.",
       (3, 5, 10, 20, 30), "{n} episodes in one day", "episodes"),
    _b("credits_roll", "Credits roll", "tv", "check", "Episodes watched, all the way to the credits.",
       (50, 250, 500, 1000, 2500), "{n} episodes watched", "episodes"),
    _b("series_finale", "Series finale", "tv", "trophy", "See shows through to the very end.",
       (1, 5, 10, 25, 50), "{n} shows finished", "shows"),
    # Anime
    _b("opening_theme", "Opening theme", "anime", "play", "Start lots of different anime.",
       (5, 10, 25, 50, 100), "{n} anime started", "anime"),
    _b("season_sweep", "Season sweep", "anime", "calendar-days",
       "Watch a whole season: twelve or more episodes of one season.",
       (1, 3, 10, 25, 50), "{n} seasons with 12 or more episodes watched", "seasons"),
    _b("filler_free", "Filler free", "anime", "check", "Finish an anime, skipping the filler.",
       (1, 3, 5, 10, 25), "{n} anime finished", "anime"),
    _b("simulcast", "Simulcast", "anime", "clock", "Three different anime in one day.",
       (1, 5, 10, 25, 50), "{n} days with three anime", "days"),
    # Habits
    _b("night_owl", "Night owl", "habits", "moon", "Watch late: between 9pm and 5am.",
       (1, 10, 25, 50, 100), "{n} watches between 9pm and 5am", "watches"),
    _b("streak_keeper", "Streak keeper", "habits", "flame", "Keep your watching streak going.",
       (3, 7, 14, 30, 60), "A {n}-day streak", "days"),
    _b("matinee", "Matinee", "habits", "clock", "Watch in the afternoon: between noon and 5pm.",
       (5, 15, 50, 100, 250), "{n} watches between noon and 5pm", "watches"),
    _b("weekender", "Weekender", "habits", "calendar-days", "Watch on both days of the same weekend.",
       (1, 4, 12, 26, 52), "{n} weekends with a watch on Saturday and Sunday", "weekends"),
    _b("comfort_title", "Comfort title", "habits", "star", "Come back to the same title again and again.",
       (2, 3, 5, 10, 20), "{n} plays of the same title", "plays"),
    # Household
    _b("movie_night", "Movie night", "household", "users",
       "Watch the same movie as someone else in the household, on the same day.",
       (1, 5, 10, 25, 50), "{n} movies watched together", "movies"),
    _b("cheering_section", "Cheering section", "household", "users",
       "Recommend something that someone then watches.",
       (1, 5, 10, 25, 50), "{n} recommendations watched", "recommendations"),
    _b("shared_taste", "Shared taste", "household", "star",
       "Love the same titles as someone else: you both rated them 8 or more.",
       (1, 3, 5, 10, 25), "{n} titles you both rated 8 or more", "titles"),
    _b("group_binge", "Group binge", "household", "tv",
       "Watch the same show as someone else in the household, on the same day.",
       (1, 3, 10, 25, 50), "{n} shows watched together", "shows"),
    # Unique
    _b("first_watch", "First watch", "unique", "star", "Log your first watch, and keep logging.",
       (1, 10, 100, 500, 1000), "{n} watches logged", "watches"),
    _b("time_capsule", "Time capsule", "unique", "clock", "Watch titles from many different decades.",
       (2, 4, 6, 8, 10), "Titles from {n} different decades", "decades", hidden=True),
    _b("globetrotter", "Globetrotter", "unique", "compass", "Watch titles from many different countries.",
       (3, 5, 10, 15, 25), "Titles from {n} different countries", "countries", hidden=True),
    _b("critic", "Critic", "unique", "star", "Rate what you watch.",
       (1, 10, 25, 100, 250), "{n} rated titles", "titles", hidden=True),
    _b("genre_explorer", "Genre explorer", "unique", "compass", "Watch something in many different genres.",
       (3, 6, 10, 15, 19), "{n} different genres", "genres", hidden=True),
)
BADGE_BY_KEY = {b.key: b for b in BADGES}
TOTAL_BADGES = len(BADGES)
TOTAL_TIERS = TOTAL_BADGES * TIER_COUNT


# What the tier ladder says next to the tier you are working on.
PROGRESS_LABELS = {"marathoner": "Your best day: {n:,}", "streak_keeper": "Your best streak: {n:,}"}


def goal_text(badge, tier):
    return badge.goal.format(n=f"{badge.tiers[tier - 1]:,}")


# --- levels -----------------------------------------------------------------

LevelInfo = namedtuple("LevelInfo", "level name floor next_floor next_name xp percent to_next")

# The filled progress block's colours: teal like a list's progress, blue for a level.
TEAL_FILL = "color-mix(in oklab, var(--color-anime) 82%, var(--color-base-100))"
LEVEL_FILL = "color-mix(in oklab, var(--color-level) 80%, var(--color-base-100))"


def level_block(info, fill=TEAL_FILL):
    """The level's filled progress block, for partials/progress_block.html."""
    if info.next_floor is None:
        return {"percent": 100, "headline": "100%", "sub": f"{info.xp:,} XP", "side_title": "Top level",
                "side_sub": info.name, "fill": fill, "label": f"Level {info.level}, {info.name}: top level"}
    return {
        "percent": info.percent,
        "headline": f"Level {info.level} · {info.name}" if fill == LEVEL_FILL else f"{info.percent}%",
        "sub": f"{info.xp:,} / {info.next_floor:,} XP",
        "side_title": f"Next: {info.next_name}" if fill != LEVEL_FILL else f"{info.to_next:,} to {info.next_name}",
        "side_sub": f"{info.to_next:,} XP to go" if fill != LEVEL_FILL else "",
        "fill": fill,
        "label": f"Level {info.level}, {info.name}: {info.xp} of {info.next_floor} XP, {info.to_next} XP to {info.next_name}",
    }


def level_info(xp):
    """Level, name and progress toward the next one for a total XP."""
    index = 0
    for i, (_, floor) in enumerate(LEVELS):
        if xp >= floor:
            index = i
    name, floor = LEVELS[index]
    if index + 1 < len(LEVELS):
        next_name, next_floor = LEVELS[index + 1]
        span = next_floor - floor
        percent = min(100, int((xp - floor) / span * 100))
        return LevelInfo(index + 1, name, floor, next_floor, next_name, xp, percent, next_floor - xp)
    return LevelInfo(index + 1, name, floor, None, None, xp, 100, 0)


def level_name(level):
    return LEVELS[max(1, min(level, len(LEVELS))) - 1][0]


def pin_limit(level):
    """Badges a profile may pin: 3, then one more per level from Curator."""
    return 3 if level < 5 else level - 1


# --- counting ---------------------------------------------------------------

Row = namedtuple("Row", "title_id kind episode_id season at rating")
TitleInfo = namedtuple("TitleInfo", "kind runtime year studio country directors genres")


class Counts:
    """Every counter for one profile, computed from the history loaded once."""

    def __init__(self, profile):
        self.profile = profile
        self.tz = selectors.profile_zoneinfo(profile)

    @cached_property
    def rows(self):
        raw = WatchEvent.objects.filter(profile=self.profile).values_list(
            "title_id", "title__media_type", "episode_id", "episode__season", "watched_at", "user_rating"
        )
        return [Row(t, k, e, s, at.astimezone(self.tz), r) for t, k, e, s, at, r in raw]

    @cached_property
    def titles(self):
        ids = {r.title_id for r in self.rows}
        genres = defaultdict(set)
        for title_id, genre_id in Title.genres.through.objects.filter(title_id__in=ids).values_list("title_id", "genre_id"):
            genres[title_id].add(genre_id)
        info = {}
        for t in Title.objects.filter(id__in=ids).values_list(
            "id", "media_type", "runtime_minutes", "year", "studio", "country", "directors"
        ):
            info[t[0]] = TitleInfo(t[1], t[2] or 0, t[3] or 0, t[4] or "", t[5] or "", t[6] or [], genres[t[0]])
        return info

    def _of(self, *kinds):
        return [r for r in self.rows if r.kind in kinds]

    @cached_property
    def movie_ids(self):
        return {r.title_id for r in self._of(MediaType.MOVIE)}

    # movies
    def projectionist(self):
        return len(self.movie_ids)

    def epic_length(self):
        return sum(1 for t in self.movie_ids if self.titles[t].runtime >= 150)

    def double_bill(self):
        per_day = defaultdict(set)
        for r in self._of(MediaType.MOVIE):
            per_day[r.at.date()].add(r.title_id)
        return sum(1 for ids in per_day.values() if len(ids) >= 2)

    def saga_keeper(self):
        by_director = Counter()
        for t in self.movie_ids:
            for director in set(self.titles[t].directors):
                if director:
                    by_director[director] += 1
        return sum(1 for n in by_director.values() if n >= 3)

    def studio_tour(self):
        return len({self.titles[t].studio for t in self.movie_ids if self.titles[t].studio})

    # TV shows
    def sunrise_to_sunset(self):
        morning, night = set(), set()
        for r in self.rows:
            if r.at.hour < 12:
                morning.add(r.at.date())
            if r.at.hour >= 21:
                night.add(r.at.date())
        return len(morning & night)

    def three_in_a_row(self):
        days_by_show = defaultdict(set)
        for r in self._of(MediaType.TV):
            days_by_show[r.title_id].add(r.at.date())
        total = 0
        for days in days_by_show.values():
            if any({d + datetime.timedelta(days=1), d + datetime.timedelta(days=2)} <= days for d in days):
                total += 1
        return total

    def pilot_season(self):
        return len({r.title_id for r in self._of(MediaType.TV)})

    def marathoner(self):
        per_day = Counter(r.at.date() for r in self._of(MediaType.TV, MediaType.ANIME) if r.episode_id)
        return max(per_day.values(), default=0)

    def credits_roll(self):
        return len({r.episode_id for r in self._of(MediaType.TV) if r.episode_id})

    def _finished(self, kind):
        return WatchProgress.objects.filter(
            profile=self.profile, status=WatchProgress.Status.COMPLETED, title__media_type=kind
        ).count()

    def series_finale(self):
        return self._finished(MediaType.TV)

    # Anime
    def opening_theme(self):
        return len({r.title_id for r in self._of(MediaType.ANIME)})

    def season_sweep(self):
        episodes = defaultdict(set)
        for r in self._of(MediaType.ANIME):
            if r.episode_id:
                episodes[(r.title_id, r.season)].add(r.episode_id)
        return sum(1 for ids in episodes.values() if len(ids) >= 12)

    def filler_free(self):
        return self._finished(MediaType.ANIME)

    def simulcast(self):
        per_day = defaultdict(set)
        for r in self._of(MediaType.ANIME):
            per_day[r.at.date()].add(r.title_id)
        return sum(1 for ids in per_day.values() if len(ids) >= 3)

    # Habits
    def night_owl(self):
        return sum(1 for r in self.rows if r.at.hour >= 21 or r.at.hour < 5)

    def streak_keeper(self):
        return selectors.streaks(self.profile)[1]

    def matinee(self):
        return sum(1 for r in self.rows if 12 <= r.at.hour < 17)

    def weekender(self):
        days = defaultdict(set)
        for r in self.rows:
            weekday = r.at.weekday()
            if weekday >= 5:
                days[r.at.date().isocalendar()[:2]].add(weekday)
        return sum(1 for d in days.values() if d == {5, 6})

    def comfort_title(self):
        plays = Counter()
        for r in self.rows:
            plays[r.episode_id and ("e", r.episode_id) or ("t", r.title_id)] += 1
        return max(plays.values(), default=0)

    # Household
    @cached_property
    def others(self):
        """Other profiles' watches of the titles this profile watched."""
        ids = {r.title_id for r in self.rows}
        raw = (
            WatchEvent.objects.filter(title_id__in=ids)
            .exclude(profile=self.profile)
            .values_list("title_id", "title__media_type", "watched_at", "user_rating")
        )
        return [Row(t, k, None, None, at.astimezone(self.tz), r) for t, k, at, r in raw]

    def movie_night(self):
        mine = {(r.title_id, r.at.date()) for r in self._of(MediaType.MOVIE)}
        theirs = {(r.title_id, r.at.date()) for r in self.others if r.kind == MediaType.MOVIE}
        return len({title for title, _ in mine & theirs})

    def cheering_section(self):
        return Recommendation.objects.filter(from_profile=self.profile, status=Recommendation.Status.WATCHED).count()

    def shared_taste(self):
        mine = {r.title_id for r in self.rows if r.rating and r.rating >= 8}
        theirs = {r.title_id for r in self.others if r.rating and r.rating >= 8}
        return len(mine & theirs)

    def group_binge(self):
        kinds = (MediaType.TV, MediaType.ANIME)
        mine = {(r.title_id, r.at.date()) for r in self._of(*kinds)}
        theirs = {(r.title_id, r.at.date()) for r in self.others if r.kind in kinds}
        return len({title for title, _ in mine & theirs})

    # Unique
    def first_watch(self):
        return len(self.rows)

    def time_capsule(self):
        return len({info.year // 10 for info in self.titles.values() if info.year})

    def globetrotter(self):
        return len({info.country for info in self.titles.values() if info.country})

    def critic(self):
        return len({r.title_id for r in self.rows if r.rating})

    def genre_explorer(self):
        return len(set().union(*(info.genres for info in self.titles.values())) if self.titles else set())

    def all(self):
        return {badge.key: int(getattr(self, badge.key)()) for badge in BADGES}


def compute_counts(profile):
    """{badge key: running count} for a profile's whole history."""
    return Counts(profile).all()


# --- rarity -----------------------------------------------------------------


def household_size():
    return Profile.objects.count()


def tier_holders():
    """{(badge key, tier): profiles holding it}."""
    rows = ProfileBadgeTier.objects.values("badge_key", "tier").annotate(n=Count("profile", distinct=True))
    return {(r["badge_key"], r["tier"]): r["n"] for r in rows}


def tier_xp(tier, holders, size):
    """What a tier pays: its base XP, 1.5x when under a quarter of the
    household holds it (never in a household of one, where nothing is rare)."""
    base = TIER_XP[tier - 1]
    if size > 1 and holders / size < RARE_SHARE:
        return int(base * RARE_MULTIPLIER + 0.5)
    return base


def rarity_text(holders, size):
    return f"{holders} of {size} profile{'s' if size != 1 else ''}"


# --- rebuilding -------------------------------------------------------------


@dataclass
class RebuildResult:
    new_tiers: list
    level_before: int
    level_after: int
    silent: bool


def mark_stale(profile_id):
    """Cheap: flag a profile for a rebuild on the next page that needs one."""
    Profile.objects.filter(pk=profile_id, badges_stale=False).update(badges_stale=True)


def mark_all_stale():
    Profile.objects.filter(badges_stale=False).update(badges_stale=True)


def rebuild(profile):
    """Recount the history and award every tier newly reached. Never removes
    one. The first rebuild of a profile backfills silently."""
    silent = profile.badge_level_seen == 0
    counts = compute_counts(profile)
    size = household_size()
    holders = tier_holders()
    have = set(ProfileBadgeTier.objects.filter(profile=profile).values_list("badge_key", "tier"))
    now = timezone.now()
    new_tiers = []
    for badge in BADGES:
        for tier, threshold in enumerate(badge.tiers, start=1):
            if counts[badge.key] < threshold or (badge.key, tier) in have:
                continue
            xp = tier_xp(tier, holders.get((badge.key, tier), 0), size)
            try:
                with transaction.atomic():
                    row = ProfileBadgeTier.objects.create(
                        profile=profile, badge_key=badge.key, tier=tier, xp=xp,
                        earned_at=None if silent else now, notified=silent,
                    )
            except IntegrityError:  # a concurrent rebuild got there first
                continue
            holders[(badge.key, tier)] = holders.get((badge.key, tier), 0) + 1
            new_tiers.append(row)
    total_xp = sum(ProfileBadgeTier.objects.filter(profile=profile).values_list("xp", flat=True))
    before = level_info(profile.badge_xp).level
    after = level_info(total_xp).level
    if not silent:
        for level in range(before + 1, after + 1):
            ProfileLevelUp.objects.get_or_create(profile=profile, level=level, defaults={"xp_total": total_xp})
    Profile.objects.filter(pk=profile.pk).update(
        badge_xp=total_xp, badges_stale=False, badges_checked_at=now, badge_level_seen=after
    )
    profile.badge_xp, profile.badges_stale, profile.badges_checked_at = total_xp, False, now
    profile.badge_level_seen = after
    return RebuildResult(new_tiers, before, after, silent)


def needs_rebuild(profile):
    if profile.badges_stale or profile.badge_level_seen == 0:
        return True
    checked = profile.badges_checked_at
    return checked is None or timezone.now() - checked > RECHECK_AFTER


def ensure_fresh(profile):
    """Rebuild when the history moved (or it has been a while). Never raises:
    a badge problem must not take a page down."""
    if profile is None or not needs_rebuild(profile):
        return None
    try:
        return rebuild(profile)
    except Exception:
        logger.exception("Rebuilding badges for profile %s failed", profile.pk)
        return None


# --- what the pages show ----------------------------------------------------

STATES = ("new", "complete", "earned", "progress", "locked", "hidden")


def _earned_by_badge(profile):
    earned = defaultdict(dict)
    for row in ProfileBadgeTier.objects.filter(profile=profile):
        earned[row.badge_key][row.tier] = row
    return earned


def badge_card(badge, counts, earned_tiers, holders, size, now=None):
    """Everything a page needs to draw one badge for one profile."""
    now = now or timezone.now()
    tiers_done = len(earned_tiers)
    count = counts.get(badge.key, 0)
    complete = tiers_done >= TIER_COUNT
    recent = any(
        t.earned_at and now - t.earned_at < datetime.timedelta(hours=NEW_TIER_HOURS) for t in earned_tiers.values()
    )
    if badge.hidden and tiers_done == 0:
        state = "hidden"
    elif complete:
        state = "complete"
    elif recent:
        state = "new"
    elif tiers_done:
        state = "earned"
    elif count > 0:
        state = "progress"
    else:
        state = "locked"
    masked = state == "hidden"
    target = None if complete else badge.tiers[tiers_done]
    rarest = None
    if earned_tiers:
        rarest = min(holders.get((badge.key, t), 0) for t in earned_tiers)
    if masked:
        caption = "Keep watching"
    elif complete:
        caption = "Complete"
    elif recent and tiers_done == 1 and count <= badge.tiers[0]:
        caption = "Just now"
    else:
        caption = f"{count:,} / {target:,}"
    return {
        "key": badge.key,
        "name": "Hidden badge" if masked else badge.name,
        "category": badge.category,
        "icon": "lock" if masked else badge.icon,
        "description": "" if masked else badge.description,
        "state": state,
        "masked": masked,
        "tiers_done": tiers_done,
        "tier_total": TIER_COUNT,
        "pips": [
            "complete" if complete and i <= tiers_done else ("earned" if i <= tiers_done else "empty")
            for i in range(1, TIER_COUNT + 1)
        ],
        "count": count,
        "target": target,
        "caption": caption,
        "percent": 100 if complete else min(100, int(count / target * 100)) if target else 0,
        "complete": complete,
        "rarest_holders": rarest,
        "rarest_text": rarity_text(rarest, size) if rarest is not None else "",
        "xp_earned": sum(t.xp for t in earned_tiers.values()),
        "xp_total": sum(TIER_XP),
    }


def profile_cards(profile):
    """Every badge for a profile, grouped by category, plus the page's stats."""
    ensure_fresh(profile)
    counts = compute_counts(profile)
    earned = _earned_by_badge(profile)
    holders = tier_holders()
    size = household_size()
    now = timezone.now()
    cards = [badge_card(b, counts, earned.get(b.key, {}), holders, size, now) for b in BADGES]
    by_category = []
    for category in CATEGORIES:
        items = [c for c in cards if c["category"] == category.key]
        by_category.append({
            "category": category,
            "cards": items,
            "tiers_done": sum(c["tiers_done"] for c in items),
            "tiers_total": len(items) * TIER_COUNT,
            "badges_done": sum(1 for c in items if c["tiers_done"]),
            "badges_total": len(items),
        })
    held = [(holders.get((b.key, t), 0), b, t) for b in BADGES for t in earned.get(b.key, {})]
    rarest = None
    if held:
        n, badge, tier = min(held, key=lambda h: (h[0], -h[2]))
        rarest = {"badge": badge, "tier": tier, "holders": n, "text": rarity_text(n, size),
                  "note": f"Tier {tier} · {n} of {size} profile{'s' if size != 1 else ''} {'have' if n != 1 else 'has'} it"}
    info = level_info(profile.badge_xp)
    return {
        "level": info,
        "levels": [{"level": i + 1, "name": n, "floor": f, "reached": info.level >= i + 1} for i, (n, f) in enumerate(LEVELS)],
        "categories": by_category,
        "cards": cards,
        "level_block": level_block(info),
        "badges_earned": sum(1 for c in cards if c["tiers_done"]),
        "badges_total": TOTAL_BADGES,
        "badges_unit": f"of {TOTAL_BADGES}",
        "tiers_unit": f"of {TOTAL_TIERS}",
        "not_earned_note": f"{TOTAL_BADGES - sum(1 for c in cards if c['tiers_done'])} not earned yet",
        "tiers_earned": sum(c["tiers_done"] for c in cards),
        "tiers_total": TOTAL_TIERS,
        "xp": profile.badge_xp,
        "rarest": rarest,
        "household_size": size,
        "pin_limit": pin_limit(info.level),
    }


def badge_detail(profile, key):
    """The detail popup's data for one badge: its tier ladder, the profile's
    progress and who else in the household has reached which tier."""
    badge = BADGE_BY_KEY.get(key)
    if badge is None:
        return None
    ensure_fresh(profile)
    counts = compute_counts(profile)
    earned = _earned_by_badge(profile).get(key, {})
    holders = tier_holders()
    size = household_size()
    card = badge_card(badge, counts, earned, holders, size)
    count = counts[key]
    ladder = []
    for tier, threshold in enumerate(badge.tiers, start=1):
        row = earned.get(tier)
        masked = card["masked"]
        ladder.append({
            "tier": tier,
            "goal": "Hidden" if masked else goal_text(badge, tier),
            "xp": row.xp if row else tier_xp(tier, holders.get((key, tier), 0), size),
            "base_xp": TIER_XP[tier - 1],
            "earned": row is not None,
            "earned_at": row.earned_at if row else None,
            "current": row is None and tier == card["tiers_done"] + 1,
            "progress": f"{count:,}",
            "locked": row is None and tier > card["tiers_done"] + 1,
        })
    members = []
    by_profile = defaultdict(int)
    if not card["masked"]:
        for pk, tier in ProfileBadgeTier.objects.filter(badge_key=key).values_list("profile_id", "tier"):
            by_profile[pk] = max(by_profile[pk], tier)
        for other in Profile.objects.filter(pk__in=by_profile, show_badges=True).order_by("display_name"):
            members.append({"profile": other, "tier": by_profile[other.pk]})
        members.sort(key=lambda m: (-m["tier"], m["profile"].display_name.lower()))
    viewer_tiers = max(earned, default=0)
    ref = max(1, viewer_tiers)
    holding = sum(1 for tier in by_profile.values() if tier >= ref)
    next_xp = ladder[card["tiers_done"]]["xp"] if not card["complete"] else 0
    if card["complete"]:
        block = {"percent": 100, "headline": "100%", "sub": "All five tiers", "side_title": "Complete",
                 "side_sub": f"{card['xp_earned']} XP earned", "fill": TEAL_FILL, "label": "All five tiers earned"}
    else:
        to_go = card["target"] - count
        block = {
            "percent": card["percent"], "headline": f"{card['percent']}%",
            "sub": f"{count:,} / {card['target']:,} {badge.unit}",
            "side_title": f"Next: Tier {card['tiers_done'] + 1}",
            "side_sub": f"{max(to_go, 0):,} to go · +{next_xp} XP", "fill": TEAL_FILL,
            "label": f"{card['percent']}%: {count} of {card['target']} {badge.unit}",
        }
    return {
        "badge": badge,
        "card": card,
        "category": CATEGORY_BY_KEY[badge.category],
        "ladder": ladder,
        "count": count,
        "next_xp": next_xp,
        "block": block,
        "progress_label": PROGRESS_LABELS.get(key, "You have {n:,} so far").format(n=count),
        "household_text": f"{holding} of {size} profile{'s' if size != 1 else ''} {'have' if holding != 1 else 'has'} tier {ref} or higher",
        "to_go": (card["target"] - count) if card["target"] else 0,
        "members": members,
        "household_size": size,
        "household_holding": len(by_profile),
        "viewer_tier": viewer_tiers,
    }


def pinned_cards(profile, limit=None):
    """The profile's pinned badges as cards (only ones it has earned)."""
    keys = [k for k in (profile.pinned_badges or []) if k in BADGE_BY_KEY]
    if not keys:
        return []
    earned = _earned_by_badge(profile)
    holders = {}
    size = 1
    out = []
    for key in keys:
        tiers = earned.get(key)
        if not tiers:
            continue
        badge = BADGE_BY_KEY[key]
        card = badge_card(badge, {key: 0}, tiers, holders, size)
        card["tier_label"] = f"Tier {max(tiers)}"
        out.append(card)
    return out[:limit] if limit else out


def pin_badge(profile, key, pinned):
    """Pin or unpin an earned badge. Returns (ok, message)."""
    if key not in BADGE_BY_KEY:
        return False, "Unknown badge."
    keys = [k for k in (profile.pinned_badges or []) if k in BADGE_BY_KEY]
    if not pinned:
        keys = [k for k in keys if k != key]
    else:
        if not ProfileBadgeTier.objects.filter(profile=profile, badge_key=key).exists():
            return False, "Earn a tier of this badge first."
        if key not in keys:
            if len(keys) >= pin_limit(level_info(profile.badge_xp).level):
                return False, "You have no pins left."
            keys.append(key)
    profile.pinned_badges = keys
    profile.save(update_fields=["pinned_badges"])
    return True, ""


def pending_unlocks(profile):
    """Tier unlocks and level-ups not yet shown, marking them shown. Returns
    ([tier rows], [level-up rows]); empty when the profile turned badge
    notifications off (they are still marked, so turning it on later doesn't
    replay old ones)."""
    tiers = list(ProfileBadgeTier.objects.filter(profile=profile, notified=False).order_by("earned_at", "id"))
    levels = list(ProfileLevelUp.objects.filter(profile=profile, shown=False).order_by("level"))
    if tiers:
        ProfileBadgeTier.objects.filter(pk__in=[t.pk for t in tiers]).update(notified=True)
    if levels:
        ProfileLevelUp.objects.filter(pk__in=[lv.pk for lv in levels]).update(shown=True)
    if not profile.badge_notifications:
        return [], []
    return tiers, levels


def popup_context(viewer, target):
    """The quick profile's badge section: the level, its progress block, the
    pinned badges and the count. Nothing for someone who keeps badges private
    (the viewer's own always shows)."""
    if viewer.pk != target.pk and not target.show_badges:
        return {"badges_shown": False}
    ensure_fresh(target)
    info = level_info(target.badge_xp)
    return {
        "badges_shown": True,
        "level": info,
        "level_block": level_block(info, LEVEL_FILL),
        "pinned": pinned_cards(target),
        "badges_earned": ProfileBadgeTier.objects.filter(profile=target).values("badge_key").distinct().count(),
        "badges_link": f"?profile={target.pk}" if viewer.pk != target.pk else "",
    }


def feed_items(limit=30):
    """Activity-feed entries for tier unlocks and level-ups, for profiles that
    opted in (Settings → Badges: post to the feed) and share their activity.
    Silent first-rebuild tiers (earned_at NULL) never appear."""
    items = []
    tiers = (
        ProfileBadgeTier.objects.filter(
            profile__badges_to_feed=True,
            profile__show_badges=True,
            profile__share_activity=True,
            earned_at__isnull=False,
        )
        .select_related("profile")
        .order_by("-earned_at")[:limit]
    )
    for row in tiers:
        badge = BADGE_BY_KEY.get(row.badge_key)
        if badge is None:
            continue
        items.append(
            {
                "profile": row.profile,
                "timestamp": row.earned_at,
                "kind": "badge_tier",
                "badge_key": badge.key,
                "badge_name": badge.name,
                "tier": row.tier,
                "xp": row.xp,
            }
        )
    levels = (
        ProfileLevelUp.objects.filter(
            profile__badges_to_feed=True, profile__show_badges=True, profile__share_activity=True
        )
        .select_related("profile")
        .order_by("-reached_at")[:limit]
    )
    for row in levels:
        items.append(
            {
                "profile": row.profile,
                "timestamp": row.reached_at,
                "kind": "level_up",
                "level": row.level,
                "level_name": level_name(row.level),
            }
        )
    return items
