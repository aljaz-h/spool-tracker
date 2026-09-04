import random
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tracker.integrations import tmdb
from tracker.models import (
    Episode,
    MediaType,
    Profile,
    ReleaseSchedule,
    Title,
    WatchEvent,
    WatchList,
    WatchListItem,
    WatchProgress,
    attach_genres,
)

# Real, well-known titles (not fictional placeholders) so a configured
# TMDB_API_KEY resolves real posters/genres for screenshots - see
# tmdb.find_match()'s title+year search.
MOVIES = [
    ("Inception", 2010),
    ("Parasite", 2019),
    ("The Grand Budapest Hotel", 2014),
    ("Mad Max: Fury Road", 2015),
    ("Everything Everywhere All at Once", 2022),
]
TV_SHOWS = [
    ("Breaking Bad", 2008),
    ("The Bear", 2022),
    ("Succession", 2018),
    ("Severance", 2022),
    ("Fleabag", 2016),
]
ANIME = [
    ("Attack on Titan", 2013),
    ("Fullmetal Alchemist: Brotherhood", 2009),
    ("Spy x Family", 2022),
    ("Frieren: Beyond Journey's End", 2023),
    ("Cowboy Bebop", 1998),
]
WATCHLIST_MOVIES = [("Dune: Part Two", 2024), ("The Substance", 2024)]
WATCHLIST_TV = [("Shogun", 2024)]

# Titles demo hasn't watched, used only for the recommendations seeded
# below - recommendations.send() no-ops on anything demo already has a
# WatchEvent for, so these are kept out of MOVIES/TV_SHOWS/ANIME above.
RECOMMENDED_MOVIES = [("Oppenheimer", 2023), ("Poor Things", 2023)]
RECOMMENDED_TV = [("The Last of Us", 2023)]

# A couple more household members beyond Alex, so Social Activity and
# the recommendations carousel show more than one sender/face.
FRIENDS = [("sam", "Sam"), ("jamie", "Jamie")]

# Left partway-through (WatchProgress.WATCHING) instead of fully watched,
# so Continue Watching/Up Next have something to show.
IN_PROGRESS_TITLES = {"Severance", "Frieren: Beyond Journey's End"}


class Command(BaseCommand):
    help = (
        "Populates a disposable dev database with realistic demo data - a "
        "profile with weeks of watch history, lists, a watchlist, and "
        "upcoming releases - so the app has something worth screenshotting "
        "instead of an empty state. This is throwaway data (real, "
        "well-known titles with synthetic watch history), never meant for "
        "a real instance, so it refuses to run outside DEBUG unless "
        "--force is passed."
    )

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Run even when DEBUG is False.")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to seed demo data outside DEBUG - this is throwaway "
                "screenshot/dev data, not meant for a real instance. Pass "
                "--force to override."
            )
        if Profile.objects.filter(display_name="Demo").exists():
            self.stdout.write("A 'Demo' profile already exists - skipping (delete it first to reseed).")
            return

        demo = self._profile("demo", "Demo")
        self._profile("alex", "Alex")

        watched_titles = (
            [self._make_title(MediaType.MOVIE, "movie", name, year) for name, year in MOVIES]
            + [self._make_title(MediaType.TV, "tv", name, year) for name, year in TV_SHOWS]
            + [self._make_title(MediaType.ANIME, "anime", name, year) for name, year in ANIME]
        )

        self._seed_watch_history(demo, watched_titles)
        self._seed_lists(demo, watched_titles)
        self._seed_watchlist_and_releases(demo)
        self._seed_on_this_day(demo, watched_titles)
        friends = self._seed_friends_activity()
        self._seed_recommendations(demo, friends)

        self.stdout.write(self.style.SUCCESS("Demo data seeded - log in as 'demo' to view it."))

    def _profile(self, username, display_name):
        user, _ = User.objects.get_or_create(username=username)
        user.set_password("demo12345")
        user.save()
        profile, _ = Profile.objects.get_or_create(user=user, defaults={"display_name": display_name})
        return profile

    def _make_title(self, media_type, tmdb_category, name, year):
        match = tmdb.find_match(tmdb_category, name, year)
        details = tmdb.get_full_details(match["kind"], match["id"]) if match else None
        title = Title.objects.create(
            media_type=media_type,
            name=name,
            year=year,
            poster_url=(match or {}).get("poster_url") or "",
            runtime_minutes=details.get("runtime") if (details and media_type == MediaType.MOVIE) else None,
            external_ids={"tmdb": str(match["id"]), "tmdb_kind": match["kind"]} if match else {},
        )
        if details and details.get("genres"):
            attach_genres(title, details["genres"])
        return title

    def _seed_watch_history(self, profile, watched_titles):
        today = timezone.localdate()
        # Two separate runs of consecutive days - an older, longer one (so
        # "personal best" reads differently from the current streak) and a
        # short one ending today (so the streak pill isn't at 0), plus a
        # handful of scattered days between them for heatmap/genre texture.
        far_streak = [today - timedelta(days=d) for d in range(42, 24, -1)]
        scatter = [today - timedelta(days=d) for d in (21, 19, 16, 13, 10)]
        near_streak = [today - timedelta(days=d) for d in range(6, -1, -1)]
        watch_days = far_streak + scatter + near_streak

        pool = []
        for title in watched_titles:
            if title.media_type == MediaType.MOVIE:
                rating = random.choice([None, 7, 8, 8, 9, 9, 10])
                pool.append({"title": title, "episode": None, "rating": rating})
                continue
            episode_count = random.randint(6, 10)
            episode_runtime = 24 if title.media_type == MediaType.ANIME else 45
            episodes = [
                Episode.objects.create(
                    title=title, season=1, episode=n, name=f"Episode {n}", runtime_minutes=episode_runtime
                )
                for n in range(1, episode_count + 1)
            ]
            in_progress = title.name in IN_PROGRESS_TITLES
            watched_count = episode_count - 3 if in_progress else episode_count
            for ep in episodes[:watched_count]:
                pool.append({"title": title, "episode": ep, "rating": None})
            WatchProgress.objects.create(
                profile=profile,
                title=title,
                status=WatchProgress.Status.WATCHING if in_progress else WatchProgress.Status.COMPLETED,
                current_episode=episodes[watched_count - 1],
            )
            if in_progress:
                # A real future ReleaseSchedule row (not just an already-
                # created-but-unwatched Episode) so Dashboard's Up Next -
                # which only reads ReleaseSchedule, unlike Calendar's wider
                # "ready to watch" section - has something to show too.
                next_ep = Episode.objects.create(
                    title=title,
                    season=1,
                    episode=episode_count + 1,
                    name=f"Episode {episode_count + 1}",
                    runtime_minutes=episode_runtime,
                )
                ReleaseSchedule.objects.create(
                    title=title,
                    episode=next_ep,
                    release_type=ReleaseSchedule.ReleaseType.EPISODE,
                    release_date=timezone.now() + timedelta(days=random.choice([2, 4])),
                )
        random.shuffle(pool)

        events = []
        day_iter = iter(watch_days)
        day = next(day_iter)
        remaining_today = random.randint(1, 3)
        for item in pool:
            if remaining_today <= 0:
                day = next(day_iter, watch_days[-1])
                remaining_today = random.randint(1, 3)
            watched_at = timezone.make_aware(
                timezone.datetime.combine(day, timezone.datetime.min.time())
                + timedelta(hours=random.randint(18, 23))
            )
            events.append(
                WatchEvent(
                    profile=profile,
                    title=item["title"],
                    episode=item["episode"],
                    watched_at=watched_at,
                    user_rating=item["rating"],
                )
            )
            remaining_today -= 1
        WatchEvent.objects.bulk_create(events)

    def _seed_lists(self, profile, titles):
        featured = WatchList.objects.create(
            profile=profile, name="Best of the Decade", is_shared=True, is_featured=True
        )
        for pos, title in enumerate(random.sample(titles, k=min(4, len(titles)))):
            WatchListItem.objects.create(watchlist=featured, title=title, position=pos)

        private = WatchList.objects.create(profile=profile, name="Weekend Rewatches")
        for pos, title in enumerate(random.sample(titles, k=min(3, len(titles)))):
            WatchListItem.objects.create(watchlist=private, title=title, position=pos)

    def _seed_watchlist_and_releases(self, profile):
        watchlist, _ = WatchList.objects.get_or_create(
            profile=profile, is_watchlist=True, defaults={"name": "Watchlist"}
        )
        movie_titles = [self._make_title(MediaType.MOVIE, "movie", name, year) for name, year in WATCHLIST_MOVIES]
        tv_titles = [self._make_title(MediaType.TV, "tv", name, year) for name, year in WATCHLIST_TV]
        for pos, title in enumerate(movie_titles + tv_titles):
            WatchListItem.objects.create(watchlist=watchlist, title=title, position=pos)

        now = timezone.localtime()
        for offset, title in zip((3, 9), movie_titles):
            ReleaseSchedule.objects.create(
                title=title,
                release_type=ReleaseSchedule.ReleaseType.MOVIE_RELEASE,
                release_date=now + timedelta(days=offset),
            )
        for title in tv_titles:
            episode = Episode.objects.create(title=title, season=2, episode=1, name="Season Premiere")
            ReleaseSchedule.objects.create(
                title=title,
                episode=episode,
                release_type=ReleaseSchedule.ReleaseType.SEASON_PREMIERE,
                release_date=now + timedelta(days=21),
            )

    def _seed_on_this_day(self, profile, watched_titles):
        """A couple of WatchEvents dated on today's month/day in prior
        years - selectors.on_this_day() excludes the current year, and
        _seed_watch_history's 42-day lookback never reaches that far, so
        without this the Dashboard's "On This Day" row is always empty."""
        today = timezone.localdate()
        movies = [t for t in watched_titles if t.media_type == MediaType.MOVIE]
        for years_ago, title in zip((1, 2), movies):
            try:
                day = today.replace(year=today.year - years_ago)
            except ValueError:
                day = today.replace(year=today.year - years_ago, day=28)  # Feb 29 in a non-leap year
            watched_at = timezone.make_aware(
                timezone.datetime.combine(day, timezone.datetime.min.time()) + timedelta(hours=20)
            )
            WatchEvent.objects.create(
                profile=profile, title=title, watched_at=watched_at, user_rating=random.choice([7, 8, 9])
            )

    def _seed_friends_activity(self):
        """Alex plus a couple more household members with their own light
        watch history - gives Social Activity more than one face, and
        gives _seed_recommendations senders other than Alex."""
        alex = Profile.objects.get(display_name="Alex")
        friends = [alex] + [self._profile(username, display_name) for username, display_name in FRIENDS]

        now = timezone.localtime()
        movie_pool = list(Title.objects.filter(media_type=MediaType.MOVIE).order_by("?"))
        for i, friend in enumerate(friends):
            for j, title in enumerate(movie_pool[i : i + 3]):
                WatchEvent.objects.create(
                    profile=friend, title=title, watched_at=now - timedelta(days=i + j, hours=2 + j)
                )
        return friends

    def _seed_recommendations(self, demo, friends):
        """A few pending Recommendations from friends to demo, on titles
        demo hasn't watched (recommendations.send() no-ops otherwise), so
        Dashboard's "Recommended by Friends" carousel has more than one
        card to page through - the last one sent blind, for variety."""
        from tracker import recommendations

        movies = [self._make_title(MediaType.MOVIE, "movie", name, year) for name, year in RECOMMENDED_MOVIES]
        tv = [self._make_title(MediaType.TV, "tv", name, year) for name, year in RECOMMENDED_TV]
        rec_titles = movies + tv

        for i, title in enumerate(rec_titles):
            sender = friends[i % len(friends)]
            recommendations.send(sender, demo, title, is_blind=(i == len(rec_titles) - 1))
