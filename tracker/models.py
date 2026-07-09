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

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    display_name = models.CharField(max_length=50)
    avatar_color = models.CharField(max_length=7, default="#3a2a1c")
    # Settings → Appearance. The only persisted preference with real
    # downstream behavior (History's time column) — the mockup's dark/light
    # theme swatch has no second theme built, so it stays decorative.
    time_format = models.CharField(max_length=3, choices=TimeFormat.choices, default=TimeFormat.H12)
    created_at = models.DateTimeField(auto_now_add=True)

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
    connected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["profile", "provider"], name="unique_provider_per_profile")
        ]

    def __str__(self):
        return f"{self.profile} · {self.get_provider_display()}"
