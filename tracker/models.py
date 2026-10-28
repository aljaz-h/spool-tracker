import datetime
import logging
import random
import secrets

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.contrib.postgres.indexes import GinIndex
from django.db import models

logger = logging.getLogger(__name__)


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
    # Settings → Notifications - per notification type, whether it reaches
    # the in-app bell and/or push: {"digital_release": {"app": true}, ...}.
    # Only choices that differ from the defaults are stored; the types,
    # their defaults and the lookups live in tracker/notification_prefs.py.
    notification_prefs = models.JSONField(default=dict, blank=True)
    # Settings → Notifications "Push notifications" card - mirrors
    # whatever already reaches the in-app bell out through Apprise
    # (tracker/push.py) to a fixed allowlist of hosted services
    # (Discord/Slack/Telegram/Pushover/Teams/Webex - see push.py's own
    # docstring for why it's that list and not Apprise's full catalog),
    # for the subset of Notification.Kind values that are genuinely
    # one-shot events (see tracker/apps.py's push-dispatch signal) -
    # never import_status/backup_status, which stay in-app only. One
    # Apprise URL per line; a separate master toggle from "has URLs
    # configured" so delivery can be paused without losing them. Stored
    # encrypted - these are live webhook/bot-token credentials, same
    # sensitivity class as encrypted_access_token above, not something
    # to leave in plaintext in a DB dump/backup.
    encrypted_push_notification_urls = models.TextField(blank=True, default="")
    push_notifications_enabled = models.BooleanField(default=False)
    # Settings → Security - optional per-profile TOTP two-factor auth
    # (see tracker/totp.py). totp_secret gets written as soon as setup
    # starts (so the QR code shown stays stable across page reloads
    # mid-setup instead of regenerating into a different one each time),
    # but totp_enabled stays False until the profile proves they can
    # actually produce a matching code with it - enabling on the strength
    # of "the QR was scanned" alone would risk a mistyped/failed scan
    # silently locking the account on its very next login with no way in.
    totp_secret = models.CharField(max_length=32, blank=True, default="")
    totp_enabled = models.BooleanField(default=False)
    # Single-use recovery codes, hashed with Django's own password hasher
    # (see tracker/totp.py) rather than stored in cleartext like this
    # model's other integration credentials - these are a full
    # authentication-bypass secret, the same class of thing a real
    # account password is. Shown to the profile once, right after they
    # enable 2FA; regenerating (disable, then re-enable) replaces this
    # list wholesale.
    totp_backup_codes = models.JSONField(default=list, blank=True)
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
    # Calendar page's "Subscribe" button - the secret in the .ics feed
    # URL (views.calendar_feed) that stands in for login, since an
    # external calendar app fetching that URL periodically has no Spool
    # session to send. null (not "") so multiple profiles can go
    # un-generated at once without colliding on a shared unique "" value -
    # lazily created the first time the Subscribe panel is opened (see
    # get_or_create_calendar_feed_token), not at profile-creation time,
    # same "only exists once actually needed" idiom ApiToken's own
    # tokens already use.
    calendar_feed_token = models.CharField(max_length=64, null=True, blank=True, unique=True)

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

    @staticmethod
    def _generate_calendar_feed_token():
        """secrets.token_hex(32) (64 hex chars, matches max_length) -
        same generation idiom as ApiToken.generate_value(), with the same
        retry-on-collision correctness even though an accidental
        collision is astronomically unlikely."""
        while True:
            token = secrets.token_hex(32)
            if not Profile.objects.filter(calendar_feed_token=token).exists():
                return token

    def get_or_create_calendar_feed_token(self):
        if not self.calendar_feed_token:
            self.calendar_feed_token = self._generate_calendar_feed_token()
            self.save(update_fields=["calendar_feed_token"])
        return self.calendar_feed_token

    def regenerate_calendar_feed_token(self):
        """Settings/Calendar's "Regenerate" action - the old URL stops
        working the moment this returns, same contract as
        ApiToken.regenerate()."""
        self.calendar_feed_token = self._generate_calendar_feed_token()
        self.save(update_fields=["calendar_feed_token"])
        return self.calendar_feed_token

    def get_push_notification_urls(self):
        if not self.encrypted_push_notification_urls:
            return ""
        from . import crypto

        return crypto.decrypt(self.encrypted_push_notification_urls)

    def set_push_notification_urls(self, plaintext):
        from . import crypto

        self.encrypted_push_notification_urls = crypto.encrypt(plaintext) if plaintext else ""


class ApiToken(models.Model):
    """Settings → Integrations "Custom Player" card - a bearer credential
    a profile hands to their own player/script so it can POST scrobble
    events to api/routers/scrobble.py without a browser session (see
    docs/SCROBBLE_API.md). Stored in cleartext like every other
    integration credential in this app (see InstanceConfig's own
    docstring) rather than hashed like a password -
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
    # name, lowercased with accents and punctuation stripped and spaces
    # collapsed (search_text.normalize) - what the library search matches
    # against, so "One-Punch Man", "one punch man" and "Pokémon" are found
    # by what people actually type. Kept in step by save(); SQL can't
    # strip accents portably (SQLite has no unaccent), hence stored.
    search_name = models.CharField(max_length=255, blank=True, default="", editable=False)

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

    def save(self, *args, **kwargs):
        from tracker import search_text

        self.search_name = search_text.normalize(self.name)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "name" in update_fields and "search_name" not in update_fields:
            kwargs["update_fields"] = [*update_fields, "search_name"]
        super().save(*args, **kwargs)


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


class TitleFanartCache(models.Model):
    """One row per title, lazily populated the first time its detail page
    is actually viewed (see views._fanart_context) - same shape/reasoning
    as TitleRatingsCache above, but for Fanart.tv's transparent artwork
    instead of MDBList's ratings. Unlike ratings, artwork is near-static
    once uploaded, so there's no popularity-tiered refresh schedule here -
    just a single long fixed interval (see tasks.fetch_fanart_images)."""

    title = models.OneToOneField(Title, on_delete=models.CASCADE, related_name="fanart_cache")
    # Each blank ("") means "fetched, Fanart had nothing usable" - distinct
    # from fetch_attempted=False below ("never actually called the API
    # yet"), same convention TitleRatingsCache's own ratings=[] carries.
    clearlogo_url = models.URLField(blank=True)
    clearart_url = models.URLField(blank=True)
    backdrop_url = models.URLField(blank=True)
    # {"1": "url", "2": "url", ...} - string season-number keys, matching
    # how Fanart's own seasonbanner field gives them (see
    # fanart._season_banners). TV/anime only, always {} for a movie.
    season_banners = models.JSONField(default=dict, blank=True)
    fetch_attempted = models.BooleanField(default=False)
    last_fetched_at = models.DateTimeField(null=True, blank=True)
    next_refresh_at = models.DateTimeField(null=True, blank=True, db_index=True)

    def __str__(self):
        return f"{self.title} fanart cache"


class TitleTvmazeCache(models.Model):
    """One row per TV title, lazily populated the first time its release
    schedule is synced (see release_sync._sync_tv_release) - resolves and
    remembers which TVmaze show (if any) matches this title, so the
    IMDb-id lookup only ever happens once rather than on every sync.
    Movies never get a row here - TVmaze has no movie data at all.

    last_update_timestamp is TVmaze's own per-show "updated" field (a
    Unix timestamp bumped whenever any child item - an episode, an image -
    changes) - tasks.sync_release_schedules compares this against one
    bulk /updates/shows call to decide whether this show's episode list
    even needs re-fetching this run, instead of every tracked show
    hitting TVmaze on every nightly sync regardless of whether anything
    changed."""

    title = models.OneToOneField(Title, on_delete=models.CASCADE, related_name="tvmaze_cache")
    # None means "looked up, TVmaze has no matching show" - distinct from
    # fetch_attempted=False below ("never actually looked up at all").
    tvmaze_id = models.PositiveIntegerField(null=True, blank=True)
    fetch_attempted = models.BooleanField(default=False)
    last_update_timestamp = models.PositiveBigIntegerField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.title} tvmaze cache"


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
        WETRAKR = "wetrakr", "WeTrakr"
        MAL = "mal", "MyAnimeList"
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
        # The (US) digital release of a movie - only ever drives the
        # "now available at home" notification, so selectors that build
        # the calendar/Up Next leave it out.
        DIGITAL_RELEASE = "digital_release", "Digital release"

    title = models.ForeignKey(Title, on_delete=models.CASCADE, related_name="releases")
    episode = models.ForeignKey(
        Episode, null=True, blank=True, on_delete=models.SET_NULL, related_name="releases"
    )
    release_type = models.CharField(max_length=20, choices=ReleaseType.choices)
    release_date = models.DateTimeField()
    # True only for a release_date resolved from TVmaze's own airstamp
    # (see release_sync.py) - an exact, timezone-correct instant, not
    # TMDB's date-only fallback (always local midnight, which could in
    # principle BE a real air time, so this has to be an explicit flag
    # rather than inferred from the time component). Drives whether the
    # Calendar/"Up Next" show a real time-of-day or just a date.
    has_exact_time = models.BooleanField(default=False)

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
    """The bell (see tracker/notifications.py); push mirrors it per profile
    choice (tracker/push.py, tracker/notification_prefs.py). Kind
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
        DIGITAL_RELEASE = "digital_release", "Digital release"
        SYNC_FAILED = "sync_failed", "Sync failed"
        SYSTEM_UPDATE = "system_update", "System update"
        RECOMMENDATION_RECEIVED = "recommendation_received", "Recommendation received"
        RECOMMENDATION_WATCHED = "recommendation_watched", "Recommendation watched"
        RECOMMENDATION_REPLIED = "recommendation_replied", "Recommendation replied"
        WATCHLIST_STALE = "watchlist_stale", "Watchlist time capsule"
        IMPORT_STATUS = "import_status", "Import"
        BACKUP_STATUS = "backup_status", "Backup"

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
    # False for a row kept only so its kind can dedupe (a release already
    # announced, an update already pushed) when the profile turned the
    # in-app channel off but still wants push - never shown in the bell.
    in_app = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Where the row links to when it has no title - the Import Review page
    # or Settings' backups card (import_status/backup_status kinds).
    link = models.CharField(max_length=255, blank=True, default="")
    # import_status rows: the one notification per import, rewritten in
    # place as it moves scanning -> ready -> importing -> done (see
    # tracker/transfer_tracking.py), so the bell always shows where an
    # import is even after the user has left the page.
    import_session = models.ForeignKey(
        "ImportSession", null=True, blank=True, on_delete=models.SET_NULL, related_name="notifications"
    )

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
        WETRAKR = "wetrakr", "WeTrakr"
        # TMDB has no OAuth2 of its own - this account's "tokens" are a
        # v3 request-token-flow session_id (see tracker/integrations/
        # tmdb_account.py), stored via the same encrypted_access_token/
        # get_access_token()/set_access_token() every OAuth2 provider
        # above uses, even though it isn't technically an OAuth token.
        TMDB = "tmdb", "TMDB"
        # MyAnimeList - standard OAuth2 + PKCE (see tracker/integrations/
        # myanimelist.py), using the same encrypted_access_token/
        # encrypted_refresh_token/token_expires_at/redirect_uri fields
        # every other OAuth2 provider above already has - MAL needs no
        # provider-specific field of its own the way TMDB/WeTrakr do.
        # Short code ("mal", not "myanimelist") to fit this field's own
        # max_length=10 below.
        MAL = "mal", "MyAnimeList"

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
    # WeTrakr only - its free plan caps a connected user at 1000 API
    # requests/day (no X-RateLimit-* headers exist to read a live
    # "remaining" count, so this is a self-tracked count, not a mirror of
    # one - see tasks._wetrakr_account_budget_ok). wetrakr_plan is cached
    # from GET /plan-usage (free/vip/patron) so a sync can tell up front
    # how much headroom it has instead of discovering it via a 429.
    # wetrakr_quota_count/date use the exact same lazy-rollover-on-read
    # idiom as InstanceConfig's mdblist_quota_count/date below - rolled to
    # today/0 the first time either is checked past UTC midnight, no
    # dedicated reset job.
    wetrakr_plan = models.CharField(max_length=10, blank=True)
    wetrakr_plan_checked_at = models.DateTimeField(null=True, blank=True)
    wetrakr_quota_date = models.DateField(null=True, blank=True)
    wetrakr_quota_count = models.PositiveIntegerField(default=0)
    # TMDB only - every /account/{account_id}/favorite|watchlist/* call
    # needs this numeric id, and TMDB gives no way to look it up except
    # GET /account?session_id=... (see tmdb_account.get_account_id) -
    # fetched once right after create_session and cached here rather
    # than re-fetched on every sync.
    tmdb_account_id = models.PositiveIntegerField(null=True, blank=True)

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

    @property
    def paused_import_session(self):
        """WeTrakr only - the currently-paused-on-quota ImportSession for
        this account, if any (see ImportSession.Status.PAUSED). Used by
        sync_schedule_form.html to show a "paused, resuming..." line on
        the Settings card without the template needing its own query.
        ImportSession is defined further down this same module - safe to
        reference directly with no import, since by the time any instance
        method actually runs the whole module has already finished
        loading."""
        return ImportSession.objects.filter(
            profile=self.profile, source=self.provider, status=ImportSession.Status.PAUSED,
        ).order_by("-created_at").first()

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
    wetrakr_client_id = models.CharField(max_length=255, blank=True)
    encrypted_wetrakr_client_secret = models.TextField(blank=True, default="")
    mal_client_id = models.CharField(max_length=255, blank=True)
    encrypted_mal_client_secret = models.TextField(blank=True, default="")
    encrypted_tmdb_api_key = models.TextField(blank=True, default="")
    encrypted_mdblist_api_key = models.TextField(blank=True, default="")
    encrypted_fanart_api_key = models.TextField(blank=True, default="")
    # Self-tracked daily request counter for WeTrakr's app-wide quota (one
    # Spool instance's combined requests across every connected household's
    # WeTrakr account - distinct from each ExternalAccount's own per-user
    # wetrakr_quota_count above). Same lazy-rollover-on-read idiom as
    # mdblist_quota_date/count just below - see tasks._wetrakr_app_budget_ok.
    wetrakr_app_quota_date = models.DateField(null=True, blank=True)
    wetrakr_app_quota_count = models.PositiveIntegerField(default=0)
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
    # OAuth app credentials for scheduled backups to Google Drive/Dropbox
    # (see tracker/backup_destinations.py) - same public-id/encrypted-
    # secret split as Trakt/Simkl above, with the same .env fallback
    # (GOOGLE_CLIENT_ID/... - see instance_config.get_backup_credentials).
    google_client_id = models.CharField(max_length=255, blank=True)
    encrypted_google_client_secret = models.TextField(blank=True, default="")
    dropbox_app_key = models.CharField(max_length=255, blank=True)
    encrypted_dropbox_app_secret = models.TextField(blank=True, default="")

    # Every caller needing a Trakt/Simkl/TMDB credential goes through
    # this (instance_config.py's own get_*_credentials/get_tmdb_api_key)
    # with zero caching until this - confirmed via a real query-count
    # probe against a Dashboard request that this alone was 40 of 131
    # total queries (one per TMDB call anywhere on the page, each
    # independently re-loading this same singleton row). Admin
    # credentials change rarely, so a short cache window - invalidated
    # immediately on save() below for the common single-process case,
    # same cache-aside shape selectors._cache_get_or_set already uses
    # for its own DB-backed data - trades a few minutes of staleness in
    # a multi-process deployment (a worker that hasn't seen the
    # invalidation yet) for cutting what was a literal DB round-trip
    # out of nearly every page in the app.
    _CACHE_KEY = "instance_config:singleton"
    _CACHE_TTL = 300

    @classmethod
    def load(cls):
        from django.core.cache import cache

        try:
            cached = cache.get(cls._CACHE_KEY)
        except Exception:
            logger.warning("InstanceConfig cache read failed, continuing without cache", exc_info=True)
            cached = None
        if cached is not None:
            return cached
        obj, _ = cls.objects.get_or_create(pk=1)
        try:
            cache.set(cls._CACHE_KEY, obj, cls._CACHE_TTL)
        except Exception:
            logger.warning("InstanceConfig cache write failed, continuing without cache", exc_info=True)
        return obj

    def save(self, *args, **kwargs):
        from django.core.cache import cache

        super().save(*args, **kwargs)
        try:
            cache.delete(self._CACHE_KEY)
        except Exception:
            logger.warning("InstanceConfig cache invalidation failed after save", exc_info=True)

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

    def get_wetrakr_client_secret(self):
        if not self.encrypted_wetrakr_client_secret:
            return ""
        from . import crypto

        return crypto.decrypt(self.encrypted_wetrakr_client_secret)

    def set_wetrakr_client_secret(self, plaintext):
        from . import crypto

        self.encrypted_wetrakr_client_secret = crypto.encrypt(plaintext) if plaintext else ""

    def get_mal_client_secret(self):
        if not self.encrypted_mal_client_secret:
            return ""
        from . import crypto

        return crypto.decrypt(self.encrypted_mal_client_secret)

    def set_mal_client_secret(self, plaintext):
        from . import crypto

        self.encrypted_mal_client_secret = crypto.encrypt(plaintext) if plaintext else ""

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

    def get_fanart_api_key(self):
        if not self.encrypted_fanart_api_key:
            return ""
        from . import crypto

        return crypto.decrypt(self.encrypted_fanart_api_key)

    def set_fanart_api_key(self, plaintext):
        from . import crypto

        self.encrypted_fanart_api_key = crypto.encrypt(plaintext) if plaintext else ""

    def get_sso_shared_secret(self):
        if not self.encrypted_sso_shared_secret:
            return ""
        from . import crypto

        return crypto.decrypt(self.encrypted_sso_shared_secret)

    def set_sso_shared_secret(self, plaintext):
        from . import crypto

        self.encrypted_sso_shared_secret = crypto.encrypt(plaintext) if plaintext else ""

    def get_google_client_secret(self):
        if not self.encrypted_google_client_secret:
            return ""
        from . import crypto

        return crypto.decrypt(self.encrypted_google_client_secret)

    def set_google_client_secret(self, plaintext):
        from . import crypto

        self.encrypted_google_client_secret = crypto.encrypt(plaintext) if plaintext else ""

    def get_dropbox_app_secret(self):
        if not self.encrypted_dropbox_app_secret:
            return ""
        from . import crypto

        return crypto.decrypt(self.encrypted_dropbox_app_secret)

    def set_dropbox_app_secret(self, plaintext):
        from . import crypto

        self.encrypted_dropbox_app_secret = crypto.encrypt(plaintext) if plaintext else ""

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
        # WeTrakr only - this run was cut off by its daily request quota,
        # not a failure. account.last_synced_at is deliberately left where
        # it was, so the account's own next scheduled sync just retries the
        # same incremental delta - see tasks.sync_wetrakr_history.
        PAUSED = "paused", "Paused"

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
        IMPORT = "import", "Import"
        EXPORT = "export", "Export"
        TRAKT_CONNECT = "trakt_connect", "Trakt Connect"
        SIMKL_CONNECT = "simkl_connect", "Simkl Connect"
        NUVIO_CONNECT = "nuvio_connect", "Nuvio Connect"
        WETRAKR_CONNECT = "wetrakr_connect", "WeTrakr Connect"
        TMDB_CONNECT = "tmdb_connect", "TMDB Connect"
        MAL_CONNECT = "mal_connect", "MyAnimeList Connect"
        MERGE_DUPLICATES = "merge_duplicates", "Merge Duplicate Titles"
        BACKFILL_POSTERS = "backfill_posters", "Backfill Posters"
        BACKFILL_GENRES = "backfill_genres", "Backfill Genres"
        BACKFILL_COMPLETION = "backfill_completion", "Backfill Completion"
        BACKFILL_REWATCHES = "backfill_rewatches", "Backfill Rewatches"
        DISCONNECT = "disconnect", "Disconnect"
        MDBLIST_REFRESH = "mdblist_refresh", "MDBList Refresh"
        RECLASSIFY_ANIME = "reclassify_anime", "Reclassify Anime"
        RELEASE_SYNC = "release_sync", "Release Sync"
        RELEASE_NOTIFICATIONS = "release_notifications", "Release Notifications"
        WATCHLIST_STALE = "watchlist_stale", "Watchlist Reminders"
        UPDATE_CHECK = "update_check", "Update Check"
        LOG_RETENTION = "log_retention", "Log Retention"
        BACKUP = "backup", "Automatic Backup"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        # An Import Review session that's scanned and is waiting on the
        # user to review/confirm it - not "running" (nothing is working
        # on it), and not polled by the Logs tab.
        WAITING = "waiting", "Awaiting review"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        # Mirrors ImportSession.Status.PAUSED - a WeTrakr import that's
        # checkpointed mid-pull, waiting for its daily quota to roll over.
        # Distinct from WAITING, which is specifically "scanned, waiting on
        # the user to review" - this is "waiting on WeTrakr, not the user".
        PAUSED = "paused", "Paused"

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="data_logs")
    action = models.CharField(max_length=25, choices=Action.choices)
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
    # Set once a RUNNING/WAITING row reaches its final status - Logs shows
    # the difference as the row's duration. Blank for rows written
    # already finished (most actions).
    finished_at = models.DateTimeField(null=True, blank=True)
    # IMPORT rows written by Import Review: the session this row tracks
    # from scan to finish (tracker/transfer_tracking.py).
    import_session = models.ForeignKey(
        "ImportSession", null=True, blank=True, on_delete=models.SET_NULL, related_name="data_logs"
    )

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


class ImportSession(models.Model):
    """Universal Import Review - the staging area for a full/initial
    import from any source (Trakt/Simkl/Nuvio/CSV/JSON/ZIP), created by
    a scan (tracker.tasks.scan_import_session) and reviewed/selected by
    the user before tracker.tasks.commit_import_session writes anything
    into canonical Spool state (Title/Episode/WatchEvent/WatchProgress/
    WatchListItem). The core rule this exists to enforce: nothing an
    initial import would write ever lands until the user has reviewed
    and confirmed it - see tracker/import_pipeline.py's own docstring.

    Deliberately no FK to ExternalAccount/NuvioConnection - those are
    two different models for Trakt/Simkl vs Nuvio, and neither is
    needed once scanning is done (commit only ever touches
    ImportCandidate rows + canonical models), so scan_import_session
    just looks the account up transiently by (profile, source) instead
    of carrying a reference across the model's whole lifetime."""

    class Source(models.TextChoices):
        TRAKT = "trakt", "Trakt"
        SIMKL = "simkl", "Simkl"
        NUVIO = "nuvio", "Nuvio"
        WETRAKR = "wetrakr", "WeTrakr"
        TMDB = "tmdb", "TMDB"
        MAL = "mal", "MyAnimeList"
        IMDB = "imdb", "IMDb"
        LETTERBOXD = "letterboxd", "Letterboxd"
        TVTIME = "tvtime", "TV Time"
        CSV = "csv", "CSV"
        JSON = "json", "JSON"
        ZIP = "zip", "ZIP"

    class Status(models.TextChoices):
        SCANNING = "scanning", "Scanning"
        READY = "ready", "Ready for review"
        IMPORTING = "importing", "Importing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"
        # WeTrakr only - a scan that hit its daily request quota mid-pull,
        # not an error. source_metadata["wetrakr_raw_items"]/["wetrakr_cursor"]
        # hold what's been fetched so far; tasks.resume_paused_wetrakr_imports
        # re-dispatches scan_import_session once the quota window rolls over.
        PAUSED = "paused", "Paused — waiting for quota"

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="import_sessions")
    source = models.CharField(max_length=10, choices=Source.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SCANNING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # A scanning/ready session past this point is fair game for the
    # nightly cleanup job (tasks.expire_import_sessions) - set well out
    # (see the task's own default) so a large scan or a user reviewing
    # a big import over several sittings isn't cut off mid-way.
    expires_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    # File imports only - the original uploaded filename, for the
    # review page's own header ("Review your-export.zip").
    source_filename = models.CharField(max_length=255, blank=True, default="")
    # Anything source-specific worth keeping around for display/debugging
    # that doesn't need its own column - e.g. the file's on-disk temp
    # path (file imports) or a raw remote item count before normalization.
    source_metadata = models.JSONField(default=dict, blank=True)
    # e.g. CSV's column mapping, Trakt's import_lists flag - whatever the
    # scan/commit steps need that was decided before scanning started.
    import_options = models.JSONField(default=dict, blank=True)
    # The review page's own summary header (new/existing/progress/
    # ratings/watchlist/conflict counts) - computed once at the end of
    # scanning, not recomputed on every page view.
    summary = models.JSONField(default=dict, blank=True)
    total_items = models.PositiveIntegerField(default=0)
    selected_items = models.PositiveIntegerField(default=0)
    processed_items = models.PositiveIntegerField(default=0)
    imported_items = models.PositiveIntegerField(default=0)
    skipped_items = models.PositiveIntegerField(default=0)
    failed_items = models.PositiveIntegerField(default=0)
    conflict_items = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["profile", "status"]),
            # The cleanup job's own query - stale sessions past expiry,
            # regardless of which profile.
            models.Index(fields=["status", "expires_at"]),
        ]

    def __str__(self):
        return f"{self.profile} · {self.get_source_display()} import · {self.get_status_display()}"

    @property
    def is_active(self):
        """Still scanning or awaiting review/commit - used to gate the
        review page's own polling fragment (same hx-trigger="every Ns"
        idiom settings_logs_table.html already uses) and to decide
        whether a fresh "Review full import" click should reuse an
        in-progress session instead of starting a second one."""
        return self.status in (self.Status.SCANNING, self.Status.READY, self.Status.IMPORTING, self.Status.PAUSED)


class ImportCandidate(models.Model):
    """One row = one thing the Import Review scan found that could be
    written to Spool - a watch, a progress update, a rating, or a
    watchlist change. selected is what the user's own review controls
    toggle; status is the commit step's own idempotency tracker (see
    tasks.commit_import_session's own docstring) - a retried/resumed
    commit only ever re-queries status="pending", so an already-
    processed row never runs twice."""

    class Category(models.TextChoices):
        HISTORY = "history", "Watch history"
        PROGRESS = "progress", "Playback progress"
        RATING = "rating", "Rating"
        WATCHLIST = "watchlist", "Watchlist/list state"

    class Action(models.TextChoices):
        CREATE_TITLE = "create_title", "Create new title"
        LINK_PROVIDER_ID = "link_provider_id", "Link to existing title"
        CREATE_EVENT = "create_event", "Log watch"
        UPDATE_PROGRESS = "update_progress", "Update progress"
        UPDATE_RATING = "update_rating", "Update rating"
        LIST_CHANGE = "list_change", "Change list membership"
        NOOP_DUPLICATE = "noop_duplicate", "Already in Spool"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IMPORTED = "imported", "Imported"
        SKIPPED = "skipped", "Skipped"
        FAILED = "failed", "Failed"

    class Resolution(models.TextChoices):
        KEEP_SPOOL = "keep_spool", "Keep Spool's value"
        USE_IMPORTED = "use_imported", "Use imported value"
        SKIP = "skip", "Skip this item"

    import_session = models.ForeignKey(ImportSession, on_delete=models.CASCADE, related_name="candidates")
    # Invalid/unresolved candidates default unselected - "candidates that
    # are invalid or unresolved should not be silently selected."
    selected = models.BooleanField(default=True)
    category = models.CharField(max_length=10, choices=Category.choices)
    media_type = models.CharField(max_length=10, choices=MediaType.choices)
    title_name = models.CharField(max_length=255, blank=True, default="")
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    season = models.PositiveSmallIntegerField(null=True, blank=True)
    episode = models.PositiveSmallIntegerField(null=True, blank=True)
    watched_at = models.DateTimeField(null=True, blank=True)
    progress_seconds = models.PositiveIntegerField(null=True, blank=True)
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    list_name = models.CharField(max_length=100, blank=True, default="")
    list_action = models.CharField(max_length=10, blank=True, default="")  # "add" / "remove"
    # Raw ids as given by the source (provider id, tmdb id, imdb id, ...).
    source_external_ids = models.JSONField(default=dict, blank=True)
    # What title_matching.resolve_title_match resolved during scan - kept
    # separate from source_external_ids so the review UI/commit step can
    # tell "what the source said" from "what we figured out" apart.
    resolved_external_ids = models.JSONField(default=dict, blank=True)
    matched_title = models.ForeignKey(
        Title, null=True, blank=True, on_delete=models.SET_NULL, related_name="import_candidates"
    )
    action = models.CharField(max_length=20, choices=Action.choices, blank=True, default="")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    conflict_type = models.CharField(max_length=30, blank=True, default="")
    conflict_data = models.JSONField(default=dict, blank=True)
    resolution = models.CharField(max_length=15, choices=Resolution.choices, blank=True, default="")
    warning = models.CharField(max_length=255, blank=True, default="")
    error = models.CharField(max_length=255, blank=True, default="")
    # The full normalized item, so commit never needs to re-parse a file
    # or re-fetch from the source - everything it needs to act is already
    # here.
    normalized_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["import_session", "status"]),
            models.Index(fields=["import_session", "category", "status"]),
        ]

    def __str__(self):
        return f"{self.get_category_display()} · {self.title_name} ({self.get_status_display()})"


class BackupDestination(models.Model):
    """Where a profile's scheduled backups go - one row per kind per
    profile. Each run uploads a full Spool backup (tracker/backup.py,
    the same file Settings' "Export full backup" downloads, importable
    back through Import Review) and then keeps only the newest keep_last
    of this profile's backups there. See tracker/backup_destinations.py
    for the per-kind upload code and tasks.run_due_backups for the
    hourly check that decides what's due."""

    class Kind(models.TextChoices):
        GOOGLE_DRIVE = "gdrive", "Google Drive"
        DROPBOX = "dropbox", "Dropbox"
        WEBDAV = "webdav", "WebDAV"
        FOLDER = "folder", "Server folder"

    class Frequency(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="backup_destinations")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    enabled = models.BooleanField(default=True)
    frequency = models.CharField(max_length=10, choices=Frequency.choices, default=Frequency.WEEKLY)
    # 0 = keep every backup; otherwise older ones beyond this are deleted
    # after each successful run (only files this profile's backups wrote).
    keep_last = models.PositiveSmallIntegerField(default=10)
    # WebDAV: the folder URL backups go into (https://nas.local/remote.php/dav/files/me/Spool).
    url = models.CharField(max_length=500, blank=True)
    username = models.CharField(max_length=255, blank=True)
    # WebDAV password, or the OAuth refresh token for Google Drive/Dropbox -
    # Fernet-encrypted like every other stored secret (tracker/crypto.py).
    encrypted_secret = models.TextField(blank=True, default="")
    # Server folder: a subfolder under settings.BACKUP_ROOT. Google Drive:
    # the id of the "Spool Backups" folder once created.
    folder = models.CharField(max_length=255, blank=True)
    # Who the remote account is (email / display name) - shown in Settings.
    account_label = models.CharField(max_length=255, blank=True)
    # True while a backup to this destination is running - Settings'
    # import/export activity panel keeps polling until it clears.
    in_progress = models.BooleanField(default=False)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)
    last_filename = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["profile", "kind"], name="one_backup_destination_per_kind")]
        ordering = ["id"]

    def get_secret(self):
        if not self.encrypted_secret:
            return ""
        from . import crypto

        return crypto.decrypt(self.encrypted_secret)

    def set_secret(self, plaintext):
        from . import crypto

        self.encrypted_secret = crypto.encrypt(plaintext) if plaintext else ""

    def __str__(self):
        return f"{self.get_kind_display()} backups for {self.profile}"


class SchedulePlan(models.Model):
    """Calendar -> My schedule: one profile's opt-in viewing plan - where
    titles are drawn from, when they are free to watch, and how the plan
    is ordered. The plan itself is the ScheduledSlot rows; tracker/planner.py
    builds, reconciles and reads them. Off by default."""

    class Media(models.TextChoices):
        ALL = "all", "Mixed"
        MOVIE = "movie", "Movies"
        TV = "tv", "TV"
        ANIME = "anime", "Anime"

    class Order(models.TextChoices):
        OLDEST = "oldest", "Oldest first"
        LIST = "list", "List order"
        SHUFFLE = "shuffle", "Shuffle"

    class Mode(models.TextChoices):
        AUTO = "auto", "Automatic"
        MANUAL = "manual", "Manual"

    profile = models.OneToOneField(Profile, on_delete=models.CASCADE, related_name="schedule_plan")
    enabled = models.BooleanField(default=False)
    # Automatic: the plan fills the free time itself. Manual: only what the
    # profile adds is planned (the lists are then the pool to pick from).
    mode = models.CharField(max_length=6, choices=Mode.choices, default=Mode.AUTO)
    # Lists the plan draws from (the profile's own and shared ones).
    source_lists = models.ManyToManyField(WatchList, blank=True, related_name="+")
    media = models.CharField(max_length=5, choices=Media.choices, default=Media.ALL)
    # Weekday numbers (Mon=0) that are free to watch.
    free_days = models.JSONField(default=list, blank=True)
    window_start = models.TimeField(default=datetime.time(19, 0))
    window_end = models.TimeField(default=datetime.time(22, 30))
    # Optional per-weekday override of the window: {"5": ["14:00", "23:00"]}.
    day_windows = models.JSONField(default=dict, blank=True)
    order = models.CharField(max_length=7, choices=Order.choices, default=Order.OLDEST)
    # Set when the settings change, so the next view rebuilds the plan
    # instead of only topping it up.
    dirty = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Schedule plan for {self.profile}"


class ScheduledSlot(models.Model):
    """One block of the plan: a movie, or one or more consecutive episodes
    of a show, at a time. Done is derived from real WatchEvents
    (planner.reconcile), never from which sync source reported them."""

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        DONE = "done", "Done"
        # Not watched in its slot (or skipped by hand); what is left of it
        # is planned again into a later slot.
        MOVED = "moved", "Moved"

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name="schedule_slots")
    title = models.ForeignKey(Title, on_delete=models.CASCADE, related_name="schedule_slots")
    episodes = models.ManyToManyField(Episode, blank=True, related_name="schedule_slots")
    start = models.DateTimeField()
    end = models.DateTimeField()
    status = models.CharField(max_length=7, choices=Status.choices, default=Status.PLANNED)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start"]
        indexes = [models.Index(fields=["profile", "start"])]

    def __str__(self):
        return f"{self.title} @ {self.start:%Y-%m-%d %H:%M} ({self.status})"
