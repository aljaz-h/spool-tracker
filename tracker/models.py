import random
import secrets

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.contrib.postgres.indexes import GinIndex
from django.db import models


class MediaType(models.TextChoices):
    MOVIE = "movie", "Movie"
    TV = "tv", "TV"
    ANIME = "anime", "Anime"


# Same 14-color palette used to color Stats' genre legend, and the source
# both my_profile.html's own color picker and a new profile's random
# starting color draw from - proven to look good against the dark theme,
# rather than an open color picker.
AVATAR_COLOR_CHOICES = [
    "#e8a63c", "#3fa9a0", "#8b85d6", "#c0473a", "#5b8fd6", "#d67ab1", "#7fae5b",
    "#d6c14c", "#a67ac9", "#e08a4c", "#4ca6c9", "#9a9fb0", "#c9574c", "#5bc9a0",
]


def random_avatar_color():
    """A new profile's starting avatar color - every profile getting the
    same fixed default read as a bug more than a feature. Prefers a color
    no *existing* profile is already using, so a small household doesn't
    end up with two coincidentally-matching avatars; once every palette
    color is already taken (more profiles than colors), falls back to a
    plain random pick from the full palette."""
    used = set(Profile.objects.values_list("avatar_color", flat=True))
    available = [c for c in AVATAR_COLOR_CHOICES if c not in used]
    return random.choice(available or AVATAR_COLOR_CHOICES)


class Profile(models.Model):
    """One per household member. Everything else is scoped to a Profile,
    not directly to the Django User — see spool-product-spec.md §2."""

    class TimeFormat(models.TextChoices):
        H12 = "12h", "12-hour (AM/PM)"
        H24 = "24h", "24-hour"

    class LandingPage(models.TextChoices):
        """Values are the URL name to redirect to after login (see
        views.SpoolLoginView) - movies/tv/anime always land on their
        trending category, the same place their own nav link goes.
        MOVIES_TV is gone from these choices (Movies & TV split into
        separate Movies/TV pages) but is still handled as a legacy
        fallback in views._landing_page_url for profiles that had it
        stored as their preference before the split."""

        DASHBOARD = "dashboard", "Dashboard"
        MOVIES = "movies", "Movies"
        TV = "tv", "TV"
        ANIME = "anime", "Anime"
        HISTORY = "history", "History"
        CALENDAR = "calendar", "Calendar"
        LISTS = "lists", "Lists"
        STATS = "stats", "Stats"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    display_name = models.CharField(max_length=50)
    avatar_color = models.CharField(max_length=7, default=random_avatar_color)
    # My Profile's optional uploaded photo - takes priority over
    # avatar_color/initial everywhere an avatar renders when set; falls
    # back to the color circle when blank (never uploaded, or removed).
    # No server-side resizing yet - stored at whatever resolution was
    # uploaded, capped at MAX_AVATAR_IMAGE_SIZE (see views.my_profile).
    avatar_image = models.ImageField(upload_to="avatars/", blank=True, null=True)
    # My Profile's optional one-line status, shown next to the display
    # name - purely decorative flavor text, nothing else reads it.
    bio = models.CharField(max_length=160, blank=True, default="")
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
    # Settings → Appearance - an IANA zone name (e.g. "America/New_York"),
    # activated per-request by middleware.ProfileTimezoneMiddleware. Blank
    # means "use the server's own TIME_ZONE" - the only option before this
    # field existed, and still the common case for a single-household,
    # single-timezone instance.
    timezone = models.CharField(max_length=50, blank=True, default="")
    # Settings → Appearance - subtle CSS transitions on a handful of
    # interactions (mark-watched, list add/remove, popovers, ...) - see
    # static/src/app.css's own "Animation system" section and
    # base.html's <body data-animations>. Off by default: this is
    # motion layered onto interactions that already work fine instantly,
    # not something the app depends on, so a new install shouldn't have
    # it on without the person asking for it. prefers-reduced-motion at
    # the OS level always overrides this when true, regardless of what
    # this field says (handled entirely in CSS, not here).
    animations_enabled = models.BooleanField(default=False)
    # Settings → Preferences - pre-fills Movies & TV/Anime's own genre
    # filter (with_genres) on a fresh visit. Populated from a chip picker
    # sourced from tmdb.genres("movie") - the movie catalog is reused for
    # TV/Anime too rather than fetching a second one; TMDB's movie/TV
    # genre id spaces mostly overlap (Action=28, Comedy=35, Drama=18, ...
    # are identical across both), so an id that doesn't exist in a given
    # type's own catalog (a couple of mismatches, e.g. Sci-Fi/War) just
    # won't pre-select a chip there - a harmless miss, not an error.
    preferred_genre_ids = models.JSONField(default=list, blank=True)
    # Settings → Preferences - pre-fills Movies & TV/Anime's own streaming-
    # provider filter (with_watch_providers). Populated from a chip picker
    # sourced from tmdb.watch_provider_catalog(), scoped to preferred_region
    # below (TMDB's provider catalog and availability are both region-keyed).
    preferred_provider_ids = models.JSONField(default=list, blank=True)
    # Settings → Preferences - which TMDB watch_region powers both the
    # provider catalog above and the existing Availability filter (see
    # tmdb.discover's watch_region param) - previously hardcoded to "US"
    # (tmdb.AVAILABILITY_WATCH_REGION) since there was no per-profile
    # setting to use instead.
    preferred_region = models.CharField(max_length=2, default="US")

    class DiscoverDisplay(models.TextChoices):
        SHOW = "show", "Show"
        DIM = "dim", "Dim"
        HIDE = "hide", "Hide"

    # Settings → Preferences - how Movies & TV/Anime's discover grid renders
    # a title you've already watched, or already have on your Watchlist
    # (views._apply_display_modes). A rendering preference over results TMDB
    # already returned, not a filter criterion - moved off the Filters
    # panel's querystring (never belonged there conceptually) to a
    # persisted per-profile preference here instead.
    discover_watched_display = models.CharField(max_length=4, choices=DiscoverDisplay.choices, default=DiscoverDisplay.SHOW)
    discover_watchlisted_display = models.CharField(max_length=4, choices=DiscoverDisplay.choices, default=DiscoverDisplay.SHOW)
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
    # Settings → Integrations "Wrapped" card - opt-in to the companion
    # spool-wrapped app's recap/Year-in-Review reports (see
    # api/routers/reports.py). Self-service like ApiToken minting, not
    # owner-gated: a profile absent here because this is False is the
    # only opt-out mechanism spool-wrapped's /api/reports/profiles/
    # endpoint has - there's no separate "delete my data" step, so this
    # flag alone controls whether spool-wrapped ever learns this profile
    # exists at all.
    wrapped_enabled = models.BooleanField(default=False)
    # A Spool-side default delivery target - spool-wrapped's own local
    # per-profile config (set in its own admin UI) always overrides this
    # once it exists, using this only as the initial pre-fill the first
    # time spool-wrapped sees this profile id (see contract.md's
    # "Delivery config precedence"). Not the source of truth.
    wrapped_webhook_url = models.URLField(blank=True, default="")
    wrapped_email_enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # Set on the account bootstrap_admin creates from ADMIN_USERNAME/
    # ADMIN_PASSWORD (see management/commands/bootstrap_admin.py) so its
    # first login is forced through a real username/password change
    # instead of leaving the .env-sourced credentials as permanent ones.
    must_change_credentials = models.BooleanField(default=False)
    # Topbar's Friends dropdown "Active X ago" badge - when this profile
    # was last actually present in the app (any request), not when they
    # last watched something. Touched by middleware.LastSeenMiddleware,
    # throttled there to avoid a DB write on every single request.
    last_seen_at = models.DateTimeField(null=True, blank=True)

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


class ApiToken(models.Model):
    """Settings → Integrations "Custom Player" card - a bearer credential
    a profile hands to their own player/script so it can POST scrobble
    events to api/routers/scrobble.py without a browser session (see
    docs/SCROBBLE_API.md). Stored in cleartext like every other
    integration credential in this app (Profile.gemini_api_key,
    InstanceConfig's own docstring) rather than hashed like a password -
    this is a revocable, narrowly-scoped ("record a watch for this
    profile") credential a person may legitimately need to re-view/
    re-copy into a player's config, not an account login.

    Originally a single nullable api_token field directly on Profile;
    split into its own model (migration 0050, backfilling any existing
    value as a "Custom Player" row so nobody's already-configured player
    silently 401s after upgrading) so a profile can hold up to
    MAX_TOKENS_PER_PROFILE named tokens instead of exactly zero or one -
    one per app/device, each independently nameable/regenerable/
    revocable without disturbing the others."""

    MAX_TOKENS_PER_PROFILE = 5

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="api_tokens")
    name = models.CharField(max_length=60)
    # unique, not (profile, token) - see Profile's old api_token field
    # comment for why this needs to be a direct equality lookup
    # (api.auth.ScrobbleTokenAuth) rather than hashed.
    token = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.name} ({self.profile})"

    @staticmethod
    def generate_value():
        """token_hex(32) (64 hex chars, matches max_length) colliding with
        an existing row is astronomically unlikely, but the unique
        constraint means a retry is still correct if it ever did rather
        than silently handing out a duplicate token."""
        while True:
            token = secrets.token_hex(32)
            if not ApiToken.objects.filter(token=token).exists():
                return token

    def regenerate(self):
        """Settings' per-token "Regenerate" button - the old value stops
        working the moment this returns, same as rotating any other
        credential. Keeps the row (and its name) - a rotation, not a
        delete-and-recreate."""
        self.token = self.generate_value()
        self.save(update_fields=["token"])


class ServiceAPIKey(models.Model):
    """A different shape of credential from ApiToken above, on purpose -
    one key per external *service* (e.g. spool-wrapped), not per profile,
    minted by an owner in the admin dashboard's "Server Integrations" tab
    rather than self-service in Settings. Authorizes read access to every
    profile that's separately opted in to Wrapped (Profile.wrapped_enabled)
    - the key itself doesn't pick which profiles it can see, that flag
    does (see contract.md). Checked by api.auth.ServiceAPIKeyAuth, which
    must never also accept an ApiToken and vice versa - reusing one model
    for both would mean either minting a token per profile for a service
    that needs to read across all of them, or widening what a leaked
    scrobble token can do. Same generate_value() mechanics/cleartext
    storage convention as ApiToken, different everything else."""

    name = models.CharField(max_length=60)
    key = models.CharField(max_length=64, unique=True)
    scope = models.CharField(max_length=30, default="reports:read")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.name} ({self.scope})"

    @staticmethod
    def generate_value():
        while True:
            key = secrets.token_hex(32)
            if not ServiceAPIKey.objects.filter(key=key).exists():
                return key


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


def attach_reports_metadata(title, metadata):
    """Persists tmdb.get_reports_metadata()'s already-resolved dict onto
    title.country/studio/network/cast/directors/writers - same "given
    resolved values, just save them" role attach_genres plays for genres,
    called from the same import-path sites (see each's own call site) and
    by the enrich_titles_reports_metadata management command for titles
    that predate this existing."""
    title.country = metadata.get("country") or ""
    title.studio = metadata.get("studio") or ""
    title.network = metadata.get("network") or ""
    title.cast = metadata.get("cast") or []
    title.directors = metadata.get("directors") or []
    title.writers = metadata.get("writers") or []
    title.save(update_fields=["country", "studio", "network", "cast", "directors", "writers"])


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
    # The Reports API's Year in Review fields (see api/routers/reports.py) -
    # populated at title-creation time by every import path via
    # attach_reports_metadata() below, from tmdb.get_reports_metadata().
    # Blank/empty for a title tracked before this existed, until
    # enrich_titles_reports_metadata backfills it - the Reports API and
    # Year in Review both treat missing values as "unknown," not an error
    # (see contract.md), so an un-backfilled library degrades gracefully.
    # Single primary production country - movie: production_countries[0];
    # tv/anime: origin_country[0] (resolved to a display name, see
    # tmdb._ISO_COUNTRY_NAMES).
    country = models.CharField(max_length=100, blank=True, default="")
    # Movies only - production_companies[0]. Always blank for tv/anime
    # (network below is their equivalent).
    studio = models.CharField(max_length=150, blank=True, default="")
    # tv/anime only - networks[0]. Always blank for a movie.
    network = models.CharField(max_length=150, blank=True, default="")
    # Plain display-name strings, not a Person model/FK - nothing else in
    # this app needs to query "everything this person was in," and the
    # Reports API only ever needs a handful of names per title (see
    # contract.md's own "no actors/actresses split" note), so a full
    # relational credits model would be over-engineering for what's
    # actually used. Top ~5 billed cast (get_credits' own limit).
    cast = models.JSONField(default=list, blank=True)
    directors = models.JSONField(default=list, blank=True)
    writers = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["name", "year"]
        indexes = [
            models.Index(fields=["media_type"]),
            # Speeds up the search bar's name__icontains scan (an ILIKE
            # '%...%' a plain btree index can't use, since the leading
            # wildcard defeats prefix matching) - see the migration that
            # adds this for why it's wrapped to be a no-op on SQLite
            # (pg_trgm/GIN are Postgres-only; the app's SQLite dev-
            # fallback needs to keep migrating cleanly without it).
            GinIndex(fields=["name"], name="tracker_title_name_trgm", opclasses=["gin_trgm_ops"]),
            # Trakt/Simkl sync and CSV import all dedupe against this field
            # (external_ids__tmdb=<id>, etc. - see its own comment above) on
            # every row they touch; a plain GIN index (no trigram opclass
            # needed for a JSONField, unlike the name index above) lets
            # Postgres use an index scan for that instead of a full table
            # scan. Same SQLite-degrades-gracefully behavior as the name
            # index (confirmed via sqlmigrate on that one's own migration).
            GinIndex(fields=["external_ids"], name="tracker_title_external_ids_gin"),
        ]

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
        MAL = "mal", "MAL"

    title = models.ForeignKey(Title, on_delete=models.CASCADE, related_name="ratings")
    source = models.CharField(max_length=20, choices=Source.choices)
    score = models.CharField(max_length=10)  # "7.8" or "92%" — display string, not normalized

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["title", "source"], name="unique_rating_source_per_title")
        ]

    def __str__(self):
        return f"{self.title} · {self.source}={self.score}"


class TitleRatingsCache(models.Model):
    """One row per title, lazily populated the first time anyone views it
    (see views._mdblist_ratings_context) - MDBList's raw ratings payload
    plus enough bookkeeping to drive the tiered refresh schedule (see
    tasks.fetch_mdblist_ratings/_classify_next_refresh) without ever
    scanning the whole catalog on a timer. Never bulk pre-fetched."""

    title = models.OneToOneField(Title, on_delete=models.CASCADE, related_name="ratings_cache")
    # [{"source": "imdb", "value": 7.8, "score": 78, "votes": 12345, "url": "..."}, ...]
    # straight from MDBList's own response shape - rendered by
    # partials/pill_badges.html via a data-driven loop, not a hardcoded
    # per-source list, since MDBList's provider set can grow.
    ratings = models.JSONField(default=list, blank=True)
    # False only ever means "never actually called the API yet" (e.g. still
    # queued, or paused for quota) - distinct from a completed fetch that
    # found nothing, which sets this True with an empty ratings list.
    fetch_attempted = models.BooleanField(default=False)
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    next_refresh_at = models.DateTimeField(null=True, blank=True, db_index=True)

    def __str__(self):
        return f"{self.title} ratings cache"


class WatchEvent(models.Model):
    """One row = one movie watched, or one episode watched. Single source of
    truth for History, streaks, the heatmap, and stats (spool-product-spec.md §2)."""

    class Source(models.TextChoices):
        # A row's third-party sync origin, for History's own small badge
        # marking rows that came from an external app rather than being
        # logged directly in Spool - not a general provenance system, so
        # manual entries and CSV imports still just leave this blank
        # (no badge) rather than getting their own choice. Not a DB
        # migration - CharField choices aren't enforced at the schema
        # level, so adding a new one here is safe on its own.
        NUVIO = "nuvio", "Nuvio"
        SIMKL = "simkl", "Simkl"
        TRAKT = "trakt", "Trakt"
        WEBHOOK = "webhook", "Scrobble API"

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
    source = models.CharField(max_length=20, choices=Source.choices, blank=True, default="")

    class Meta:
        ordering = ["-watched_at"]
        indexes = [
            models.Index(fields=["profile", "watched_at"]),
            # profile+title (no watched_at) - the shape most call sites
            # actually filter on (a title's own watch history/count on its
            # detail page), not covered by the index above since watched_at
            # isn't a leading/matching column for that query.
            models.Index(fields=["profile", "title"]),
        ]

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
        # Same reasoning as COMPLETED above, added later - quitting a show
        # partway through had no way to be recorded other than deleting the
        # WatchProgress row outright (views.title_drop), which throws away
        # current_episode/position_seconds a later "actually, let's finish
        # it" (views.title_resume_watching) would want back.
        DROPPED = "dropped", "Dropped"

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
        # The profile+title unique constraint above doesn't help a
        # profile+status lookup (Watching tab, dashboard's completed
        # count) - status isn't a leading column of it.
        indexes = [models.Index(fields=["profile", "status"])]

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
    # Owner-only curation flag (views.toggle_list_featured) - surfaces this
    # list in the Dashboard's Featured Lists rail for every profile, not
    # just this list's own creator. Only meaningful alongside is_shared;
    # selectors.featured_lists() requires both.
    is_featured = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # Free-text organization for a profile with many lists (comfort
    # watches, in progress, recommend to Alex, ...) - a flat list of
    # already-trimmed, deduped, non-empty strings (see views._parse_tags),
    # not a separate Tag model. Nothing here needs to be queried/joined
    # across lists at the database level (Lists' own tag filter just
    # narrows an already-small, already-fetched queryset in Python -
    # see selectors.visible_lists's caller), so the relational-model
    # overhead of a real M2M isn't worth it for what's fundamentally
    # small, per-list, creator-only free text.
    tags = models.JSONField(default=list, blank=True)

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
    # Manual drag-order within the list (views.reorder_list) - the default
    # sort in list_detail. Assigned as (current max + 1) when an item is
    # added (views.add_to_list), so new titles land at the end instead of
    # colliding with 0.
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position"]
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
    determines what title/release_schedule mean: release-based kinds, both
    recommendation kinds, and watchlist_stale always carry title;
    sync_failed and system_update carry neither (title is
    unavailable/irrelevant, there's no ReleaseSchedule to dedupe on -
    system_update dedupes on its own message text instead, see
    tracker/tasks.check_for_new_version). watchlist_stale has no
    ReleaseSchedule either, so it dedupes on a cooldown window instead -
    see notifications.generate_watchlist_stale_notifications."""

    class Kind(models.TextChoices):
        NEW_RELEASE = "new_release", "New release"
        UPCOMING_RELEASE = "upcoming_release", "Upcoming release"
        SYNC_FAILED = "sync_failed", "Sync failed"
        SYSTEM_UPDATE = "system_update", "System update"
        RECOMMENDATION_RECEIVED = "recommendation_received", "Recommendation received"
        RECOMMENDATION_WATCHED = "recommendation_watched", "Recommendation watched"
        RECOMMENDATION_REPLIED = "recommendation_replied", "Recommendation replied"
        WATCHLIST_STALE = "watchlist_stale", "Watchlist time capsule"

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=25, choices=Kind.choices)
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
        # profile+read - the unread-badge count runs on every page load
        # (context_processors.py) and isn't covered by the constraint
        # above (read isn't one of its columns).
        indexes = [models.Index(fields=["profile", "read"])]

    def __str__(self):
        return f"{self.profile}: {self.message}"


class Recommendation(models.Model):
    """One profile pointing another at a title worth watching - a
    lightweight nudge, distinct from WatchList (a standing list) and
    Notification (one-way, system-generated). Fulfillment is resolved
    explicitly wherever a WatchEvent gets created (tracker/recommendations.py's
    mark_title_watched) - the same pattern rewatches.recompute_is_rewatch/
    completion.sync_watchlist_removal already use for their own "something
    else needs to happen on every watch" concerns, not a Django signal
    (used nowhere else in this codebase) - a missed explicit call sitting
    right next to already-established ones is easy to catch in review and
    in tests; a missed signal connection is a quieter failure mode.

    The reply_* fields below are a separate, optional acknowledgement from
    to_profile back to from_profile - a quick "interested!" reaction and/or
    a short message - and deliberately don't feed into status: someone can
    react enthusiastically and still not watch it for weeks, and the two
    shouldn't be conflated."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        WATCHED = "watched", "Watched"
        DISMISSED = "dismissed", "Dismissed"

    class ReplyReaction(models.TextChoices):
        WATCHLISTING = "watchlisting", "Adding to Watchlist \U0001f44d"
        ALREADY_SEEN = "already_seen", "Already Seen It \U0001f60d"
        EXCITED = "excited", "Excited \U0001f525"
        NOT_FOR_ME = "not_for_me", "Not For Me \U0001f605"

    from_profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="sent_recommendations")
    to_profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="received_recommendations")
    title = models.ForeignKey(Title, on_delete=models.CASCADE, related_name="recommendations")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    # is_blind: sender opted to hide the title until opened. revealed:
    # recipient has opened it - tracked separately from is_blind (rather
    # than clearing is_blind on open) so "was this ever a mystery pick"
    # stays visible afterwards (see dashboard_recommendations.html's gift
    # icon on an opened blind rec). Irrelevant once is_blind is False.
    is_blind = models.BooleanField(default=False)
    revealed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # A reply is deliberately independent of status - "interested!" doesn't
    # mean watched yet, and the recipient not getting around to it for a
    # month shouldn't erase what they said. One-shot, like every other
    # inline action in this codebase (dismiss/reveal above): once
    # replied_at is set, tracker.recommendations.reply() no-ops on a second
    # attempt rather than overwriting - a reply is a quick reaction in the
    # moment, not a message thread. reply_reaction and reply_message are
    # each independently optional (the UI requires at least one, not both -
    # see partials/dashboard_recommendations.html), so either can be blank
    # on its own; replied_at is the actual "has this been replied to yet"
    # signal, set the first time either one is filled in.
    reply_reaction = models.CharField(
        max_length=20, choices=ReplyReaction.choices, null=True, blank=True, default=None
    )
    reply_message = models.CharField(max_length=160, blank=True, default="")
    replied_at = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Only one *pending* recommendation per (sender, recipient,
            # title) at a time - re-recommending something already
            # watched or dismissed is fine and creates a fresh row: the
            # condition means a past dismissed/watched row (status !=
            # pending) never blocks a new one.
            models.UniqueConstraint(
                fields=["from_profile", "to_profile", "title"],
                condition=models.Q(status="pending"),
                name="unique_pending_recommendation",
            )
        ]
        # to_profile+status - the pending-recommendations query (a
        # recipient's own inbox) isn't covered by the constraint above,
        # since that's scoped to (from_profile, to_profile, title).
        indexes = [models.Index(fields=["to_profile", "status"])]

    def __str__(self):
        return f"{self.from_profile} recommended {self.title} to {self.to_profile}"


class ExternalAccount(models.Model):
    """OAuth connection state for Trakt/Simkl — Settings (this step) needs
    to display real connect/connected status, and the sync jobs (build
    step 12) need somewhere to keep the tokens, so the model lands now
    rather than getting invented twice."""

    class Provider(models.TextChoices):
        TRAKT = "trakt", "Trakt"
        SIMKL = "simkl", "Simkl"
        # Nuvio never gets an ExternalAccount row (see NuvioConnection
        # below) - this choice exists purely so SyncLog.provider, whose
        # choices reuse this enum, can represent a Nuvio sync run in the
        # Logs tab without a parallel provider enum.
        NUVIO = "nuvio", "Nuvio"

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="external_accounts")
    provider = models.CharField(max_length=10, choices=Provider.choices)
    # Encrypted at rest via tracker/crypto.py (Fernet, same convention as
    # NuvioConnection.encrypted_refresh_token below) - these are live
    # OAuth credentials that let a sync job act as the connected Trakt/
    # Simkl account, worth the same bar as a Nuvio refresh token. Access
    # via get_access_token()/set_access_token()/get_refresh_token()/
    # set_refresh_token(), never these fields directly.
    encrypted_access_token = models.TextField(blank=True, default="")
    encrypted_refresh_token = models.TextField(blank=True, default="")
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

    def get_access_token(self):
        if not self.encrypted_access_token:
            return ""
        from . import crypto

        return crypto.decrypt(self.encrypted_access_token)

    def set_access_token(self, plaintext):
        from . import crypto

        self.encrypted_access_token = crypto.encrypt(plaintext) if plaintext else ""

    def get_refresh_token(self):
        if not self.encrypted_refresh_token:
            return ""
        from . import crypto

        return crypto.decrypt(self.encrypted_refresh_token)

    def set_refresh_token(self, plaintext):
        from . import crypto

        self.encrypted_refresh_token = crypto.encrypt(plaintext) if plaintext else ""

    def __str__(self):
        return f"{self.profile} · {self.get_provider_display()}"


class NuvioConnection(models.Model):
    """Per-profile Nuvio Cloud connection - a separate model from
    ExternalAccount rather than widening it, because Nuvio isn't OAuth
    (email/password exchanged once for a refresh token, no
    client_id/secret, no redirect_uri) and needs a selected
    nuvio_profile_id ExternalAccount has no concept of (a Nuvio account
    can have multiple profiles, like Trakt slate). See
    tracker/integrations/nuvio.py for the client and tracker/crypto.py
    for why the token is encrypted here but not on ExternalAccount - a
    Nuvio refresh token is closer to a password-equivalent than an OAuth
    token, worth the extra bar."""

    profile = models.OneToOneField(Profile, on_delete=models.CASCADE, related_name="nuvio_connection")
    email = models.EmailField()
    encrypted_refresh_token = models.TextField()
    nuvio_profile_id = models.PositiveSmallIntegerField()
    nuvio_profile_name = models.CharField(max_length=100, blank=True)
    sync_enabled = models.BooleanField(default=True)
    connected_at = models.DateTimeField(auto_now_add=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    sync_interval_days = models.PositiveSmallIntegerField(default=1)
    sync_hour = models.PositiveSmallIntegerField(default=4, validators=[MaxValueValidator(23)])
    sync_minute = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(59)])

    # Duck-typed to match ExternalAccount's shape so scheduling.py's
    # ensure_periodic_task/remove_periodic_task and the provider-keyed
    # views (save_sync_schedule/trigger_manual_sync/disconnect_provider/
    # disconnect_and_wipe_provider) work against either model unmodified.
    provider = "nuvio"

    def get_refresh_token(self):
        from . import crypto

        return crypto.decrypt(self.encrypted_refresh_token)

    def set_refresh_token(self, plaintext):
        from . import crypto

        self.encrypted_refresh_token = crypto.encrypt(plaintext)

    def __str__(self):
        return f"{self.profile} · Nuvio ({self.email})"


class InstanceConfig(models.Model):
    """Singleton row (always pk=1) holding admin-configurable Trakt/Simkl/
    TMDB credentials, so they're settable from the app instead of only via
    .env + a container restart. A blank field here falls back to the
    .env-sourced Django setting (see tracker/instance_config.py) - so
    upgrading an existing install with working .env credentials doesn't
    silently break anything."""

    # Client ids aren't secret (they're the public half of an OAuth app
    # registration, visible in the browser's own redirect during login) -
    # only the secret/key fields below are encrypted at rest, same Fernet
    # convention as ExternalAccount/NuvioConnection's own tokens. Access
    # via get_trakt_client_secret()/set_trakt_client_secret() etc., never
    # these fields directly.
    trakt_client_id = models.CharField(max_length=255, blank=True)
    encrypted_trakt_client_secret = models.TextField(blank=True, default="")
    simkl_client_id = models.CharField(max_length=255, blank=True)
    encrypted_simkl_client_secret = models.TextField(blank=True, default="")
    encrypted_tmdb_api_key = models.TextField(blank=True, default="")
    encrypted_mdblist_api_key = models.TextField(blank=True, default="")
    # Self-tracked daily request counter for MDBList's free-tier quota (see
    # tasks.fetch_mdblist_ratings) - rolled over to today/0 the first time
    # it's checked past UTC midnight, rather than on a timer, so a quiet
    # instance doesn't need a dedicated reset job. mdblist_rate_limit_remaining
    # mirrors the X-RateLimit-Remaining header MDBList itself returns on
    # every response - read as a sanity backstop (pause immediately if it
    # ever reports 0) independent of whether our own count agrees.
    mdblist_quota_date = models.DateField(null=True, blank=True)
    mdblist_quota_count = models.PositiveIntegerField(default=0)
    mdblist_rate_limit_remaining = models.PositiveIntegerField(null=True, blank=True)
    # Guards the quota-paused DataLog entry to once per day instead of once
    # per skipped title - see tasks.fetch_mdblist_ratings.
    mdblist_quota_pause_logged_date = models.DateField(null=True, blank=True)
    # Set by tasks.check_for_new_version (see tracker/update_check.py) -
    # the newest VERSION seen on the repo as of the last nightly check.
    # Read back through update_check.available_version(), which only
    # ever surfaces it while it's still actually newer than APP_VERSION -
    # self-correcting after an upgrade rather than needing this cleared
    # on deploy.
    latest_known_version = models.CharField(max_length=20, blank=True)
    # Blank/null = keep forever (the default - matches every install's
    # behavior before this field existed). Only prunes SyncLog/DataLog -
    # the operational noise Settings' Logs tab shows - never
    # AdminAuditLogEntry, which is a security-relevant audit trail meant
    # to outlive routine sync/import log rows. See tasks.prune_old_logs.
    log_retention_days = models.PositiveIntegerField(null=True, blank=True)
    # Admin dashboard's "Manage Wrapped" button (see views.manage_wrapped) -
    # the companion spool-wrapped app's own base URL, and the HMAC secret
    # both sides sign/verify the SSO handshake token with (contract.md's
    # "SSO handshake" section). sso_shared_secret is encrypted at rest,
    # same Fernet convention as every other secret field on this model;
    # spool_wrapped_url isn't a secret (it's a destination, not a
    # credential), same reasoning as trakt_client_id above.
    spool_wrapped_url = models.URLField(blank=True, default="")
    encrypted_sso_shared_secret = models.TextField(blank=True, default="")

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def get_trakt_client_secret(self):
        if not self.encrypted_trakt_client_secret:
            return ""
        from . import crypto

        return crypto.decrypt(self.encrypted_trakt_client_secret)

    def set_trakt_client_secret(self, plaintext):
        from . import crypto

        self.encrypted_trakt_client_secret = crypto.encrypt(plaintext) if plaintext else ""

    def get_simkl_client_secret(self):
        if not self.encrypted_simkl_client_secret:
            return ""
        from . import crypto

        return crypto.decrypt(self.encrypted_simkl_client_secret)

    def set_simkl_client_secret(self, plaintext):
        from . import crypto

        self.encrypted_simkl_client_secret = crypto.encrypt(plaintext) if plaintext else ""

    def get_tmdb_api_key(self):
        if not self.encrypted_tmdb_api_key:
            return ""
        from . import crypto

        return crypto.decrypt(self.encrypted_tmdb_api_key)

    def set_tmdb_api_key(self, plaintext):
        from . import crypto

        self.encrypted_tmdb_api_key = crypto.encrypt(plaintext) if plaintext else ""

    def get_mdblist_api_key(self):
        if not self.encrypted_mdblist_api_key:
            return ""
        from . import crypto

        return crypto.decrypt(self.encrypted_mdblist_api_key)

    def set_mdblist_api_key(self, plaintext):
        from . import crypto

        self.encrypted_mdblist_api_key = crypto.encrypt(plaintext) if plaintext else ""

    def get_sso_shared_secret(self):
        if not self.encrypted_sso_shared_secret:
            return ""
        from . import crypto

        return crypto.decrypt(self.encrypted_sso_shared_secret)

    def set_sso_shared_secret(self, plaintext):
        from . import crypto

        self.encrypted_sso_shared_secret = crypto.encrypt(plaintext) if plaintext else ""

    def __str__(self):
        return "Instance configuration"


IMPORTED_TITLES_LOG_CAP = 200  # settings_logs_table.html's own "+N more" cap


class SyncLog(models.Model):
    """Audit trail for Trakt/Simkl sync runs - timing/outcome/item count,
    plus (imported_titles) a capped list of what was actually imported -
    the Logs tab's own "Items" column expands it in a collapsible detail
    row rather than showing item_count alone."""

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="sync_logs")
    provider = models.CharField(max_length=10, choices=ExternalAccount.Provider.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    item_count = models.PositiveIntegerField(null=True, blank=True)
    # Capped at IMPORTED_TITLES_LOG_CAP (see tasks._run_sync) - a full
    # history sync can be thousands of items, and this is only ever a
    # human-readable sample for the Logs tab, not a data export.
    imported_titles = models.JSONField(default=list, blank=True)
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


class DataLog(models.Model):
    """Audit trail for CSV import/export and Trakt/Simkl connect attempts
    - the request/response-shaped data actions SyncLog doesn't cover
    (that model is for the recurring background sync task specifically,
    see tasks._run_sync). Together with SyncLog, backs Settings' Logs tab
    (selectors.combined_logs)."""

    class Action(models.TextChoices):
        IMPORT = "import", "CSV Import"
        EXPORT = "export", "Export"
        TRAKT_CONNECT = "trakt_connect", "Trakt Connect"
        SIMKL_CONNECT = "simkl_connect", "Simkl Connect"
        NUVIO_CONNECT = "nuvio_connect", "Nuvio Connect"
        MERGE_DUPLICATES = "merge_duplicates", "Merge Duplicate Titles"
        BACKFILL_POSTERS = "backfill_posters", "Backfill Posters"
        BACKFILL_GENRES = "backfill_genres", "Backfill Genres"
        BACKFILL_COMPLETION = "backfill_completion", "Backfill Completion"
        BACKFILL_REWATCHES = "backfill_rewatches", "Backfill Rewatches"
        DISCONNECT = "disconnect", "Disconnect"
        MDBLIST_REFRESH = "mdblist_refresh", "MDBList Refresh"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="data_logs")
    action = models.CharField(max_length=20, choices=Action.choices)
    # Not constrained to ExternalAccount.Provider's choices - also holds
    # "tmdb" for the backfill_posters/genres/completion actions, which
    # isn't a connectable account. Blank wherever nothing meaningfully
    # "belongs" to one provider (import/export/merge_duplicates/
    # backfill_rewatches). Settings' Logs tab Provider filter is the only
    # reader (see selectors.combined_logs).
    provider = models.CharField(max_length=10, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices)
    item_count = models.PositiveIntegerField(null=True, blank=True)
    detail = models.CharField(max_length=255, blank=True)
    # Same IMPORTED_TITLES_LOG_CAP-capped sample as SyncLog.imported_titles,
    # for the IMPORT action specifically - blank for every other action.
    imported_titles = models.JSONField(default=list, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["-created_at"])]

    def __str__(self):
        return f"{self.profile} · {self.get_action_display()} · {self.get_status_display()} @ {self.created_at:%Y-%m-%d %H:%M}"


class AdminAuditLogEntry(models.Model):
    """Who added/removed/promoted which profile, and when - Admin
    Dashboard's own audit trail, separate from SyncLog (which is about
    Trakt/Simkl sync runs, not account administration). target_display_name
    is a plain string snapshot, not a FK, because the target Profile is
    often gone by the time this is read back (removed, or self-deleted)."""

    class Action(models.TextChoices):
        PROFILE_CREATED = "profile_created", "Profile created"
        PROFILE_REMOVED = "profile_removed", "Profile removed"
        PROFILE_PROMOTED = "profile_promoted", "Promoted to owner"
        PROFILE_DEMOTED = "profile_demoted", "Demoted to member"
        PROFILE_SELF_DELETED = "profile_self_deleted", "Deleted own account"
        PROFILE_PASSWORD_RESET = "profile_password_reset", "Password reset"

    # Null once the actor's own Profile is gone (e.g. they deleted their
    # own account - see views.delete_own_account) rather than losing the
    # log entry entirely.
    actor = models.ForeignKey(
        Profile, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_log_entries_as_actor"
    )
    action = models.CharField(max_length=30, choices=Action.choices)
    target_display_name = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_action_display()} · {self.target_display_name} @ {self.created_at:%Y-%m-%d %H:%M}"


class ProfileAchievement(models.Model):
    """A badge a profile has earned - just persisted state (which key, and
    when). The catalog of achievements themselves (name, description,
    what qualifies) is a static Python registry in tracker/achievements.py,
    not a DB table - there's no user-facing way to define a new one, so a
    full catalog model would be pure ceremony. key matches one of that
    registry's Achievement.key values; earned_at sticks once awarded even
    if the underlying stat later drops (e.g. a rewatch getting removed
    lowers a count back below a threshold) - see
    achievements.check_and_award."""

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="achievements")
    key = models.CharField(max_length=50)
    earned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-earned_at"]
        constraints = [
            models.UniqueConstraint(fields=["profile", "key"], name="unique_profile_achievement")
        ]

    def __str__(self):
        return f"{self.profile}: {self.key}"
