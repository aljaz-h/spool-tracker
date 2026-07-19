from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class MediaType(models.TextChoices):
    MOVIE = "movie", "Movie"
    TV = "tv", "TV"
    ANIME = "anime", "Anime"


class Profile(models.Model):
    """One per household member. Everything else is scoped to a Profile,
    not directly to the Django User — see spool-product-spec.md §2."""

    class TimeFormat(models.TextChoices):
        H12 = "12h", "12-hour (AM/PM)"
        H24 = "24h", "24-hour"

    class LandingPage(models.TextChoices):
        """Values are the URL name to redirect to after login (see
        views.SpoolLoginView) - movies_tv/anime always land on their
        trending category, the same place their own nav link goes."""

        DASHBOARD = "dashboard", "Dashboard"
        MOVIES_TV = "movies_tv", "Movies & TV"
        ANIME = "anime", "Anime"
        HISTORY = "history", "History"
        CALENDAR = "calendar", "Calendar"
        LISTS = "lists", "Lists"
        STATS = "stats", "Stats"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    display_name = models.CharField(max_length=50)
    avatar_color = models.CharField(max_length=7, default="#3a2a1c")
    # Settings → Appearance. The only persisted preference with real
    # downstream behavior (History's time column) — the mockup's dark/light
    # theme swatch has no second theme built, so it stays decorative.
    time_format = models.CharField(max_length=3, choices=TimeFormat.choices, default=TimeFormat.H12)
    # Settings → Appearance - where login lands you (views.SpoolLoginView).
    default_landing_page = models.CharField(max_length=20, choices=LandingPage.choices, default=LandingPage.DASHBOARD)
    # Settings → Appearance - pre-fills Movies & TV/Anime's own language
    # filter (views.DISCOVER_LANGUAGES) instead of "Any language"; blank
    # means no default. Not TMDB response localization (titles/overviews
    # stay in TMDB's own language) - just a starting filter value.
    preferred_language = models.CharField(max_length=5, blank=True, default="")
    # Settings → Privacy - whether this profile's watches/ratings/list-adds
    # appear in the household-wide Activity feed for other profiles
    # (selectors.activity_feed). Only ever shown/relevant with >1 profile
    # on the instance, same gating Activity itself already uses.
    share_activity = models.BooleanField(default=True)
    # Settings → Notifications - each in-app notification source
    # (tracker/notifications.py) checks its own flag before creating a
    # Notification row for this profile.
    notify_new_releases = models.BooleanField(default=True)
    notify_upcoming_releases = models.BooleanField(default=True)
    notify_sync_failures = models.BooleanField(default=True)
    # Settings - bring-your-own free Gemini API key, optional and per
    # profile (not instance-wide like Trakt/Simkl/TMDB in InstanceConfig -
    # this powers a personal "what should I watch" ask, not a shared
    # sync). Stored in cleartext, same as every other integration
    # credential this app already stores.
    gemini_api_key = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    # Set on the account bootstrap_admin creates from ADMIN_USERNAME/
    # ADMIN_PASSWORD (see management/commands/bootstrap_admin.py) so its
    # first login is forced through a real username/password change
    # instead of leaving the .env-sourced credentials as permanent ones.
    must_change_credentials = models.BooleanField(default=False)

    class Meta:
        ordering = ["display_name"]

    def __str__(self):
        return self.display_name

    @property
    def is_owner(self):
        """No dedicated role field — the mockup's Owner/Member badge maps
        onto Django's own superuser flag instead of inventing new schema
        for a distinction Django auth already expresses."""
        return self.user.is_superuser


class Genre(models.Model):
    name = models.CharField(max_length=50, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


def attach_genres(title, genre_names):
    """Get-or-create each Genre by name and set them on the title - shared
    by every import path (Trakt/Simkl/CSV, plus the discover/preview
    materialize flow) that discovers genre names via a TMDB match at
    title-creation time, and by the backfill_genres management command
    for titles that predate this existing."""
    if genre_names:
        title.genres.set([Genre.objects.get_or_create(name=n)[0] for n in genre_names])


class Title(models.Model):
    """A movie, show, or anime. media_type is what routes a title into the
    Movies & TV vs. Anime sections — never genre (spool-product-spec.md §5)."""

    media_type = models.CharField(max_length=10, choices=MediaType.choices)
    name = models.CharField(max_length=255)
    year = models.PositiveSmallIntegerField()
    poster_url = models.URLField(blank=True)
    # Movie runtime only — episode runtime lives on Episode. Needed for the
    # "X min left" progress captions and the Stats "total watch time" figure;
    # null for titles imported/entered without it, which those features
    # degrade gracefully around rather than assuming.
    runtime_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    genres = models.ManyToManyField(Genre, related_name="titles", blank=True)
    # {"trakt": "...", "simkl": "...", "tmdb": "..."} — used to upsert-match
    # incoming rows during Trakt/Simkl import instead of creating duplicates.
    external_ids = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name", "year"]
        indexes = [models.Index(fields=["media_type"])]

    def __str__(self):
        return f"{self.name} ({self.year})"


class Episode(models.Model):
    title = models.ForeignKey(Title, on_delete=models.CASCADE, related_name="episodes")
    season = models.PositiveSmallIntegerField()
    episode = models.PositiveSmallIntegerField()
    name = models.CharField(max_length=255, blank=True)
    runtime_minutes = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["title", "season", "episode"]
        constraints = [
            models.UniqueConstraint(
                fields=["title", "season", "episode"], name="unique_episode_per_title"
            )
        ]

    def __str__(self):
        return f"{self.title} S{self.season}E{self.episode}"


class ExternalRating(models.Model):
    class Source(models.TextChoices):
        IMDB = "imdb", "IMDb"
        RT = "rt", "Rotten Tomatoes"
        TRAKT = "trakt", "Trakt"

    title = models.ForeignKey(Title, on_delete=models.CASCADE, related_name="ratings")
    source = models.CharField(max_length=20, choices=Source.choices)
    score = models.CharField(max_length=10)  # "7.8" or "92%" — display string, not normalized

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["title", "source"], name="unique_rating_source_per_title")
        ]

    def __str__(self):
        return f"{self.title} · {self.source}={self.score}"


class WatchEvent(models.Model):
    """One row = one movie watched, or one episode watched. Single source of
    truth for History, streaks, the heatmap, and stats (spool-product-spec.md §2)."""

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="watch_events")
    title = models.ForeignKey(Title, on_delete=models.CASCADE, related_name="watch_events")
    episode = models.ForeignKey(
        Episode, null=True, blank=True, on_delete=models.SET_NULL, related_name="watch_events"
    )
    watched_at = models.DateTimeField()
    is_rewatch = models.BooleanField(default=False)
    user_rating = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(10)]
    )

    class Meta:
        ordering = ["-watched_at"]
        indexes = [models.Index(fields=["profile", "watched_at"])]

    def __str__(self):
        return f"{self.profile} watched {self.title} @ {self.watched_at:%Y-%m-%d}"


class WatchProgress(models.Model):
    """Current state backing the 'Watching' tab's sprocket progress bars."""

    class Status(models.TextChoices):
        WATCHING = "watching", "Watching"
        PLANNED = "planned", "Planned"
        # Not in the original doc sketch — added because the Dashboard/Stats
        # "Shows completed" figure needs a real status to count rather than
        # a guessed-at derived query (spool-product-spec.md doesn't define
        # a completion signal otherwise).
        COMPLETED = "completed", "Completed"

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="watch_progress")
    title = models.ForeignKey(Title, on_delete=models.CASCADE, related_name="watch_progress")
    current_episode = models.ForeignKey(
        Episode, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    position_seconds = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, choices=Status.choices)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(fields=["profile", "title"], name="unique_progress_per_profile_title")
        ]

    def __str__(self):
        return f"{self.profile} · {self.title} ({self.status})"


class WatchList(models.Model):
    """A shared list is visible to every profile on the instance, but only
    its creator may edit/delete it (spool-product-spec.md §5)."""

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="watchlists")
    name = models.CharField(max_length=100)
    is_shared = models.BooleanField(default=False)
    # True for exactly one list per profile - the auto-managed Watchlist
    # (as opposed to a custom list a profile created themselves). Titles
    # come off this list automatically once finished (completion.py's
    # sync_watchlist_removal) - custom lists are never touched by that,
    # regardless of what they're named.
    is_watchlist = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def can_edit(self, profile):
        return self.profile_id == profile.id


class WatchListItem(models.Model):
    watchlist = models.ForeignKey(WatchList, on_delete=models.CASCADE, related_name="items")
    title = models.ForeignKey(Title, on_delete=models.CASCADE, related_name="watchlist_items")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-added_at"]
        constraints = [
            models.UniqueConstraint(fields=["watchlist", "title"], name="unique_title_per_watchlist")
        ]

    def __str__(self):
        return f"{self.title} in {self.watchlist}"


class ReleaseSchedule(models.Model):
    """One row per known/expected future release, populated by the
    Trakt/Simkl calendar sync (spool-handoff-addendum.md §1)."""

    class ReleaseType(models.TextChoices):
        EPISODE = "episode", "New episode"
        SEASON_PREMIERE = "season_premiere", "Season premiere"
        MOVIE_RELEASE = "movie_release", "Movie release"

    title = models.ForeignKey(Title, on_delete=models.CASCADE, related_name="releases")
    episode = models.ForeignKey(
        Episode, null=True, blank=True, on_delete=models.SET_NULL, related_name="releases"
    )
    release_type = models.CharField(max_length=20, choices=ReleaseType.choices)
    release_date = models.DateTimeField()

    class Meta:
        ordering = ["release_date"]
        indexes = [models.Index(fields=["release_date"])]
        # NB: doesn't dedupe movie_release rows on its own — episode is NULL
        # for movies, and SQL unique constraints treat NULL as distinct from
        # NULL, so this only guards episode-level releases. The calendar
        # sync job (build step 12) needs its own get_or_create-on-title
        # check for movie_release rows.
        constraints = [
            models.UniqueConstraint(
                fields=["title", "episode", "release_type"], name="unique_release_per_title_episode_type"
            )
        ]

    def __str__(self):
        return f"{self.title} · {self.get_release_type_display()} @ {self.release_date:%Y-%m-%d}"


class Notification(models.Model):
    """In-app only (see tracker/notifications.py) - no email/push. Kind
    determines what title/release_schedule mean: release-based kinds
    always carry both; sync_failed carries neither (title is
    unavailable/irrelevant, there's no ReleaseSchedule to dedupe on)."""

    class Kind(models.TextChoices):
        NEW_RELEASE = "new_release", "New release"
        UPCOMING_RELEASE = "upcoming_release", "Upcoming release"
        SYNC_FAILED = "sync_failed", "Sync failed"

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.ForeignKey(
        Title, null=True, blank=True, on_delete=models.CASCADE, related_name="notifications"
    )
    release_schedule = models.ForeignKey(
        ReleaseSchedule, null=True, blank=True, on_delete=models.CASCADE, related_name="notifications"
    )
    message = models.CharField(max_length=255)
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Only guards release-based kinds - sync_failed rows always
            # have release_schedule=NULL, and NULL is never equal to NULL
            # in a unique constraint, so they're untouched by this.
            models.UniqueConstraint(
                fields=["profile", "kind", "release_schedule"],
                condition=models.Q(release_schedule__isnull=False),
                name="unique_notification_per_profile_kind_release",
            )
        ]

    def __str__(self):
        return f"{self.profile}: {self.message}"


class ExternalAccount(models.Model):
    """OAuth connection state for Trakt/Simkl — Settings (this step) needs
    to display real connect/connected status, and the sync jobs (build
    step 12) need somewhere to keep the tokens, so the model lands now
    rather than getting invented twice."""

    class Provider(models.TextChoices):
        TRAKT = "trakt", "Trakt"
        SIMKL = "simkl", "Simkl"

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="external_accounts")
    provider = models.CharField(max_length=10, choices=Provider.choices)
    access_token = models.TextField(blank=True)
    refresh_token = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    # The exact redirect_uri used for the authorization-code exchange that
    # produced the tokens above - Trakt's refresh grant requires the same
    # redirect_uri be echoed back, and there's no request object available
    # to rebuild it from inside a Celery task, so it's captured once here
    # at connect time instead. Blank on accounts connected before this
    # field existed - those just fall back to the old "reconnect manually"
    # behavior until they reconnect once.
    redirect_uri = models.CharField(max_length=255, blank=True)
    connected_at = models.DateTimeField(auto_now_add=True)
    # High-water mark for incremental sync (Trakt only - see trakt.py's
    # fetch_history start_at param). Set to the sync's own start time on
    # success, not left null and not backfilled from watched_at, so a sync
    # that started while new Trakt activity was still landing doesn't miss
    # anything on the next run.
    last_synced_at = models.DateTimeField(null=True, blank=True)
    # Backs a per-account django-celery-beat PeriodicTask (see
    # tracker/scheduling.py) - "every N days" is approximated via crontab's
    # day_of_month=*/N, which resets each calendar month rather than
    # counting N days from whenever this was set. Good enough for "sync
    # roughly every few days at a time I chose", not a precise rolling
    # interval.
    sync_interval_days = models.PositiveSmallIntegerField(default=1)
    sync_hour = models.PositiveSmallIntegerField(default=4, validators=[MaxValueValidator(23)])
    sync_minute = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(59)])
    # Trakt only for now (see trakt.py's fetch_lists/upsert_lists) - Simkl's
    # list-equivalent endpoints are additional unverified surface on top of
    # an already-unverified integration, not worth layering on yet.
    import_lists = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["profile", "provider"], name="unique_provider_per_profile")
        ]

    def __str__(self):
        return f"{self.profile} · {self.get_provider_display()}"


class InstanceConfig(models.Model):
    """Singleton row (always pk=1) holding admin-configurable Trakt/Simkl/
    TMDB credentials, so they're settable from the app instead of only via
    .env + a container restart. A blank field here falls back to the
    .env-sourced Django setting (see tracker/instance_config.py) - so
    upgrading an existing install with working .env credentials doesn't
    silently break anything."""

    trakt_client_id = models.CharField(max_length=255, blank=True)
    trakt_client_secret = models.CharField(max_length=255, blank=True)
    simkl_client_id = models.CharField(max_length=255, blank=True)
    simkl_client_secret = models.CharField(max_length=255, blank=True)
    tmdb_api_key = models.CharField(max_length=255, blank=True)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "Instance configuration"


class SyncLog(models.Model):
    """Audit trail for Trakt/Simkl sync runs - deliberately just timing and
    outcome (when it ran, how long, success/failure/item count), never a
    per-title breakdown of what was imported."""

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="sync_logs")
    provider = models.CharField(max_length=10, choices=ExternalAccount.Provider.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    item_count = models.PositiveIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["profile", "-started_at"])]

    def __str__(self):
        return f"{self.profile} · {self.get_provider_display()} · {self.get_status_display()} @ {self.started_at:%Y-%m-%d %H:%M}"

    @property
    def duration_seconds(self):
        # Django's timesince/timeuntil template filters round to whole
        # minutes, which makes every real sync (usually a few seconds)
        # misleadingly show as "0 minutes" - computed here instead so the
        # template can format it with sub-second precision.
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()
