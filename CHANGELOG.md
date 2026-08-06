# Changelog

All notable changes to Spool are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`) — patch
for fixes, minor for new features, major for anything requiring a manual
migration/env step or breaking an existing workflow.

## [Unreleased]

## [0.75.1] - 2026-08-06

### Fixed

- A title matched/imported before this app started recording which TMDB
  catalog (movie vs. TV) it came from could permanently show as
  untracked on Trending/Popular/similar/Because You Watched, even though
  it was correctly marked watched in History. It now matches via its
  local movie/TV type as a fallback and fixes its own record so this
  only ever has to happen once per title.

## [0.75.0] - 2026-08-06

### Added

- The episode browser's watched checkmark now offers a menu once an
  episode is watched (View history plays / Watch again / Remove last
  watched / Remove all watched history), instead of blindly logging
  another play on every click - undoing a watch no longer requires going
  to History.
- "Mark season watched" / "Mark all watched" flip to "Unmark season
  watched" / "Unmark all watched" once every episode in that scope is
  already watched, clearing every play in one action.

## [0.74.0] - 2026-08-06

### Added

- Notifications get a "For you" / "System" toggle to separate release and
  recommendation notifications from sync-failure/update-available ones,
  a wider panel, and slightly larger text.

### Changed

- Settings → Logs now updates itself live: a "running" sync/backfill/import
  row flips to success/failed on its own, no manual refresh.
- Marking or unmarking a watch (an episode, a whole season, a whole show,
  or the header's own Watched toggle) now updates the title page's "Your
  history" card immediately - it used to only refresh on the next full
  page load.

## [0.73.0] - 2026-08-05

### Changed

- Settings' left-hand nav now has an icon next to every item.
- The Logs filter panel gets a stronger shadow and a lighter surface so
  it visually separates from the page instead of blending into it.
- "Keep logs for" moved from the Logs tab header into the Maintenance
  tab, alongside the other cleanup-job settings.

## [0.72.0] - 2026-08-05

### Added

- Settings → Logs gets a proper filter panel: a "Filters" button opens a
  panel with Profile / Action Type / Provider / Status pill filters and a
  date range (with Today/Last 7 days/Last 30 days/This month presets),
  replacing the old plain Profile/Sort dropdown pair. Disconnecting
  Trakt/Simkl/Nuvio is now logged (previously invisible in the Logs tab
  entirely), and every log entry that relates to a specific provider
  (including the TMDB-touching backfill maintenance actions) is now
  taggable and filterable by it.

## [0.71.0] - 2026-08-05

### Added

- Settings → Logs: a "Keep logs for N days" field (blank = keep forever,
  the previous behavior) - a nightly job prunes SyncLog/DataLog rows past
  that age. Never touches the separate Activity Log, which is a security
  audit trail meant to outlive routine sync/import log rows.

### Changed

- Settings → Logs now paginates at 20 entries per page instead of 50.

## [0.70.0] - 2026-08-05

### Added

- Settings → Server Integrations: a "Test connection" button next to each
  of Trakt/Simkl/TMDB's credential fields, making one live request with
  the currently typed (or already-saved, if left blank) value and
  reporting success/failure without saving anything. TMDB's check fully
  validates the key; Trakt/Simkl's only confirm the client ID is live,
  since neither checks the secret outside the OAuth flow.
- Settings → Profiles: the server owner can now reset another profile's
  password directly (a "Set new password" form next to promote/demote/
  delete), for when a household member forgets theirs. Logged to the
  existing Activity Log; the target's other sessions are invalidated on
  their next request, same as any password change.
- Settings → Maintenance (new tab, owner-only): one-click buttons for the
  `merge_duplicate_titles` (preview/commit) and `backfill_posters`/
  `backfill_genres`/`backfill_completion`/`backfill_rewatches` management
  commands, previously reachable only via `docker compose exec web
  python manage.py ...`. The three TMDB-touching backfills run in the
  background (Celery) since a real library can take longer than a
  request's timeout; results show up automatically in the existing Logs
  tab.

### Fixed

- Saving Server Integrations credentials, or running any of the actions
  above, no longer bounces the admin back to the Profiles tab - it now
  stays on the tab they were on.

### Added

- Search now has a trigram GIN index (`pg_trgm`) on title name for
  Postgres deployments, speeding up the search bar's substring match on
  larger libraries. Postgres-only - both the extension activation and
  the index degrade to a no-op/plain index on SQLite automatically
  (confirmed via `sqlmigrate`), so the dev-fallback path is unaffected.

### Changed

- Poster/backdrop/still images in every grid, carousel, and list context
  (Discover, History, Watchlist, Dashboard, Calendar, Cast, episode
  browser) now render as `<img loading="lazy">` instead of a CSS
  background-image, so images below the fold no longer all load
  eagerly on page load. Above-the-fold hero images (title/person/
  collection header posters) are left eager on purpose. Visually
  verified via screenshot - identical layout, badges, and aspect ratios.

## [0.68.0] - 2026-08-05

### Fixed

- History's day-grouped pagination no longer loads a profile's entire
  watch history into memory before paginating - it now fetches only the
  distinct watched dates, then just the events on the requested page.
  Measured on a 25k-event profile: ~580ms → ~77ms per page load (7.5x),
  and a deep page (e.g. page 50) now costs the same as page 1 instead of
  scaling with total history size.
- `get_movie_details`/`get_tv_details` (TMDB runtime/episode-count
  lookups, called on every TV show detail/episode-browser page view)
  now go through the same 6h cache every other TMDB lookup already uses
  - previously an uncached request per page view.
- Stats page: `current_streak`/`longest_streak` and
  `watch_time_breakdown` issued redundant per-type queries (12 queries
  for watch-time breakdown alone); now 2 conditional-aggregate queries.
  Measured: 39 → 28 queries for one Stats page load.
- Poster images in grid/carousel tiles (Discover, History, Watchlist,
  Dashboard, Calendar) now request TMDB's smaller w185/w342 sizes
  instead of the w500 stored on every title, cutting image payload for
  the highest-tile-count pages.

## [0.67.0] - 2026-08-05

### Changed

- Upgraded Django 5.1.15 → 5.2.16 LTS. Django 5.1 reached end-of-life on
  2025-12-03 and stopped receiving security patches; 5.2 is the current
  LTS release, supported until 2028-04. No code changes were needed -
  full test suite (1155 tests) passes unchanged, no deprecation warnings.

## [0.66.0] - 2026-08-05

### Added

- Rate limiting on login and password/credential-change endpoints (10
  attempts per 5 minutes per IP) - Django doesn't protect these by
  default. Fails open if the cache backend is briefly unreachable rather
  than locking everyone out.
- CSV/JSON/zip import now enforces a 50MB upload size limit.

### Changed

- Trakt/Simkl OAuth tokens and any Trakt/Simkl/TMDB credentials entered
  via Settings → Server are now encrypted at rest (previously plaintext
  columns) - same Fernet convention `crypto.py` already used for Nuvio's
  refresh token. Existing values are encrypted in place by this upgrade's
  migration, no action needed.
- `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_SSL_REDIRECT`,
  and HSTS are now configurable via `.env` (all default off, so this
  doesn't change behavior for existing installs) - see
  docs/CONFIGURATION.md for when to turn them on.
- CSV history export neutralizes formula-injection characters
  (`=`/`+`/`-`/`@`) in exported title names, and CSV/JSON/zip import
  truncates title length and validates year/season/episode fields before
  they reach the database.
- Bumped `cryptography` to 50.0.0 (patches a padding-oracle CVE in a
  code path this app doesn't use, but worth clearing anyway).

### Security

- Full access-control/IDOR review of owner-only pages and profile-scoped
  data - no issues found; documented for future reference.

## [0.65.3] - 2026-08-05

### Fixed

- Trakt/Simkl import (both the "Export now" zip/JSON upload and the OAuth
  sync) now uses the TMDB id Trakt/Simkl already hand over for each item
  instead of re-deriving it with a fuzzy name/year search - the fuzzy
  search could come up empty or match the wrong TMDB entry, leaving an
  already-watched title (visible in History) showing as unwatched on the
  Movies & TV / Anime grid because it never linked up with the grid's own
  live TMDB data. A title that's already missing this link gets it
  backfilled the next time it's re-imported or re-synced.

## [0.65.2] - 2026-08-02

### Fixed

- Movies & TV / Anime's results grid no longer leaves a stray tile or
  two dangling alone on the last row - the column count is purely a
  function of the responsive auto-fill grid's CSS, not knowable
  server-side, so this measures the actually-rendered grid after paint
  and hides whichever tiles landed in an incomplete final row, re-run
  on window resize.

## [0.65.1] - 2026-08-02

### Fixed

- Calendar's agenda sidebar no longer grows unbounded when it has more
  releases than the calendar grid has room for - `align-items: stretch`
  can only grow a shorter flex item up to match a taller one, never the
  reverse, so a long agenda list was dragging the whole row down to its
  own height instead of being capped and scrolling internally. Now
  pinned to the calendar grid's actual rendered height via JS
  (ResizeObserver, kept in sync across month navigation), in both
  directions - a short list stretches to fill it, a long one scrolls
  inside it.
- The refresh button's tooltip and confirmation now make clear the sync
  runs in the background (same as Settings' "Sync now" buttons) and
  won't be reflected until the page is reloaded a few seconds later -
  clicking it doesn't refresh what's currently on screen.

## [0.65.0] - 2026-08-02

### Added

- Calendar: a manual refresh button (next to the source filter) kicks
  off the release sync immediately instead of waiting for its nightly
  run.

### Changed

- Calendar's source filter ("Watching & Watchlist"/"Watching only"/
  "Watchlist only") is now a Both/Watching/Watchlist segmented tab
  control matching the rest of the app, sized to the agenda sidebar's
  width, instead of a plain dropdown.
- The agenda sidebar now stretches to match the calendar grid's actual
  height instead of a fixed max-height, so a short release list no
  longer leaves empty space below it when the grid is taller.

## [0.64.1] - 2026-08-02

### Added

- Episode browser: an upcoming episode now shows a countdown pill
  ("Tomorrow", "In 8 days", "In 3 weeks", "In 2 months") based on its
  TMDB air date, so you can tell when it's actually coming out instead
  of just that it hasn't aired yet. No pill for an episode TMDB hasn't
  scheduled a date for at all.

## [0.64.0] - 2026-08-02

### Changed

- Calendar's release sync now pulls a TV show's whole current season
  (every dated episode, past and future) instead of only TMDB's single
  "next episode" - a weekly show now shows all its upcoming air dates at
  once instead of one at a time, and already-aired episodes populate the
  calendar's past months instead of leaving them empty. Also backfills
  the previous season when the current one premiered within the last 60
  days, and falls back to the last-aired season for a show with nothing
  currently upcoming (ended, or between seasons). Movie releases are now
  synced regardless of whether the date has already passed, so a movie
  added to a list after it came out still shows up when browsing back.

## [0.63.1] - 2026-08-02

### Fixed

- Nuvio sync now backfills the History "N" marker onto rows that were
  logged before the marker existed - it re-pulls the whole Nuvio history
  on every sync anyway, so a matching already-logged row just gets its
  source tagged instead of being skipped outright. Trigger a sync (or
  wait for the next scheduled one) to have it apply to existing history.

## [0.63.0] - 2026-08-02

### Added

- History: rows synced in from Nuvio now carry a small "N" marker (poster
  corner on single tiles, next to the title on binge-group tiles) so
  they're visually distinguishable from manually-logged/other-source
  entries - a deliberately temporary debug aid, not a general provenance
  display.
- Dashboard's Social Activity row: episode watches now show a "S1:E5"
  style pill in the poster's bottom-right corner alongside the existing
  "who watched it" pill, so you can tell which episode without opening
  the title.

## [0.62.3] - 2026-08-02

### Changed

- Dashboard's Watching row: the progress bar now sits above the title
  instead of below it, redesigned as a filled pill (rounded to match
  the watched/list button row above it, slightly shorter) with its
  caption ("S1E1 of 8", "126 min left") centered inside the bar itself
  rather than as separate text underneath.

## [0.62.2] - 2026-08-02

### Changed

- Person detail page redesign: header and personal stats now live in one
  unified card (photo with an accent ring, "Known for X - also Y, Z"
  computed from actual credit counts rather than TMDB's single guess or
  display order, birthday formatted with the full month name, inline
  stat row with a tooltip on the credit-cap figure). Each filmography
  section's All/Watched/Unwatched filter now uses the same segmented-tab
  style as the Movies & TV page's Movies/TV and category tabs. Each
  section also now shows only its first two rows by default, with a
  "Show all N credits" button to reveal the rest - the clip is exactly
  two grid rows regardless of viewport width or which filter is active,
  not a fixed item count.

## [0.62.1] - 2026-08-02

### Fixed

- A movie and a tv/anime title sharing the same raw TMDB numeric id (TMDB
  numbers movies and tv shows in separate, overlapping namespaces) could
  get treated as the same title anywhere a TMDB id alone was matched
  against the local library - a tv credit on a person's filmography page
  could show an unrelated already-tracked movie's watched badge/count
  and link straight through to that movie's page instead of its own.
  Matching now also checks the stored `tmdb_kind` (movie vs. tv), not
  just the numeric id, in `discover_action_context` (Cast/similar/
  filmography tiles, Movies & TV/Anime grids, search), `title_preview`'s
  already-tracked redirect, and preview materialization (mark
  watched/add to list/watchlist).

## [0.62.0] - 2026-08-02

### Added

- Person detail page (`/person/<tmdb_id>/`) - click through from any cast
  or director credit on a movie, TV, or anime title page. Shows TMDB's
  bio/photo/birthday alongside household-specific stats computed from
  your own watch history (titles watched vs. total credits, your average
  rating across their rated work, total watch time, and overlap with any
  other profile that shares activity), plus a filmography grouped into
  Acting/Directing/Writing sections, each filterable to All/Watched/
  Unwatched and sorted watched-first, newest release first within each
  group.

### Changed

- Cast/director entries on the title detail page are now links (when
  TMDB has a person id for them) instead of plain text.

## [0.61.6] - 2026-08-02

### Changed

- Removed the redundant circular "watched" checkmark on episode
  thumbnails in the episode browser - the box watched-button next to it
  already shows the same state.
- The "Details" panel on a show/anime's title page now sits between
  Cast and Episodes instead of after Episodes (unchanged for movies,
  which never have an episode browser to sit above).

## [0.61.5] - 2026-08-01

### Fixed

- Horizontally-scrolling card rows (Cast/similar titles on the title
  detail page, Watching/Featured Lists on the dashboard) now use a thin,
  theme-colored scrollbar instead of falling back to the raw OS one.

## [0.61.4] - 2026-08-01

### Removed

- The "Not in your library yet — this is a live preview from TMDB..."
  banner on preview title pages.

## [0.61.3] - 2026-08-01

### Changed

- Title detail hero now sits flush against the navbar (removed the
  leftover top gap from `<main>`'s own padding) and no longer has the
  film-strip perforation borders down its left/right edges.
- "If you like this, check out" is now a single horizontally scrolling
  row instead of a wrapping grid, matching the Cast row's pattern.

## [0.61.2] - 2026-08-01

### Fixed

- Title detail hero's full-bleed edges (0.61.1) left a growing gap on
  wide monitors - the negative margin reused `<main>`'s own padding
  percentage verbatim, but percentage margins/padding resolve against
  the *element's own* containing block, and this div's containing block
  is already narrowed by that same padding, not the wider box `<main>`'s
  own percentage is based on. Corrected to the value that actually
  cancels it at every width (verified at 1280/1920/2560px).

## [0.61.1] - 2026-08-01

### Changed

- Title detail page's hero backdrop is now full-bleed (edge-to-edge,
  taller, no card border) instead of sitting in a padded, rounded panel -
  poster/title/watched-button sit directly on the image behind a
  stronger two-layer gradient scrim. The "Watched" pill now has a soft
  green glow instead of reading as a plain button.
- Added a "Details" panel (status, original language, budget, revenue,
  production companies, country - whichever the title actually has)
  directly below Cast, and moved "If you like this, check out" to
  follow it immediately - closes the large empty gap that used to sit
  between Cast and the carousel on titles with a short main column next
  to a tall sidebar.
- Sidebar's "Recommend to" list now caps at 4 visible people with a
  "+N more" / "Show less" toggle, instead of always rendering the full
  friend list and growing the sidebar indefinitely as it does.

## [0.61.0] - 2026-08-01

### Fixed

- Trakt/Simkl/Nuvio sync each only checked their *own* provider id for an
  already-tracked title before creating a new one, so a title already
  synced through one provider got a second, duplicate Title (with its own
  WatchEvent) the first time a different provider synced it - symptoms
  were a title showing "not watched" on Movies & TV/Anime despite History
  showing it watched, or the same watch appearing twice in History at the
  exact same timestamp. All three now check for an existing Title matched
  by TMDB id first. New `manage.py merge_duplicate_titles` command
  (`--commit` to apply, dry run by default) cleans up any duplicates
  already created before this fix - see the README.

### Added

- Title detail page's synopsis now has a "Description" heading above it,
  matching Cast/Lists/Your History's own section headers instead of
  reading as an unlabeled paragraph.

## [0.60.5] - 2026-07-31

### Changed

- The episode browser's "Mark season watched"/"Mark all watched" buttons
  now match the season dropdown's height exactly, instead of sitting
  visibly shorter next to it.
- An episode card's per-episode watched button is now a box that fills
  the available height next to the episode's title/runtime line, instead
  of a small fixed-size circle - same fill/checkmark idea as the poster
  grid's own watched button, just narrower.

## [0.60.4] - 2026-07-31

### Added

- History tiles' delete (×) button is now always visible instead of
  hover-only. Binge-group tiles get their own delete button too - it
  removes every play in the group at once, guarded by a confirm dialog
  that states the exact count (e.g. `Remove all 150 plays of "Bleach"
  from your watch history?`) so a single click can't silently wipe a
  whole binge.

## [0.60.3] - 2026-07-31

### Changed

- History now paginates by calendar date (10 dates per page) instead of
  by rendered tile count - a page's length is predictable regardless of
  how many tiles a given date happens to produce, and a heavy binge day
  no longer changes how many *other* dates fit alongside it on the page.

## [0.60.2] - 2026-07-31

### Fixed

- History paginated by raw watch-event rows instead of the tiles actually
  rendered on screen, so a single day with a big binge-marked run
  (consecutive episodes of one show collapse into one group tile) could
  eat an entire page's row budget and leave that page showing just one
  tile with the rest blank. Pagination now operates on the grouped tiles
  themselves, so a page's density matches what's actually on screen.

## [0.60.1] - 2026-07-31

### Fixed

- Nuvio-synced "Watching" entries could sit stuck at "0 min left"/
  "1 min left" indefinitely — Nuvio's own continue-watching feed can keep
  reporting something as in-progress even once actually finished (many
  players never clear a completed entry on their own), and every sync was
  force-writing that back as `WATCHING` with no way out. An item within 2
  minutes of its own duration is now treated as finished instead and
  marked `COMPLETED`, dropping it out of Watching on the next sync -
  watch history is untouched either way.

### Added

- A dismiss ("×", shown on hover) on Dashboard Watching tiles for entries
  that don't fit the above — abandoned partway through, for instance,
  which nothing can auto-detect. Removes only the progress row; your
  watch history is never touched.

## [0.60.0] - 2026-07-31

### Added

- **Nuvio Cloud integration** — a third sync source alongside Trakt/Simkl,
  per-profile, pulling watch history and continue-watching progress. Unlike
  Trakt/Simkl, Nuvio has no public developer API or OAuth flow: each
  profile connects with its own email/password (never stored — exchanged
  once for a refresh token, which is encrypted at rest with a new Fernet
  helper keyed off `DJANGO_SECRET_KEY`), needs no server-owner setup step,
  and picks which Nuvio profile to sync from if the account has more than
  one. Runs on the same daily background schedule and "Sync now" flow as
  Trakt/Simkl, and shows up in the same Settings & Import → Logs tab.
- New `tmdb.find_by_imdb_id` — matches a title by IMDb id directly rather
  than a fuzzy title/year search, used when Nuvio hands over an IMDb id.

### Note

- Nuvio has no documented API. This integration
  (`tracker/integrations/nuvio.py`) is built from a third-party
  open-source reference implementation
  ([github.com/ellite/scrob](https://github.com/ellite/scrob)), not
  official docs, and is unverified against a live Nuvio account — same
  caveat this project already carries for Simkl. A failed sync surfaces
  its real error in Settings & Import → Logs rather than failing silently.

## [0.59.1] - 2026-07-31

### Fixed

- Importing a real Trakt "Export now" zip could 500 partway through and
  miss most rewatch history. Root-caused against an actual export:
  1. The zip's ~85 non-history JSON files (aggregated watched-movies/
     watched-shows, ratings, lists, collection, network data, etc.) were
     being parsed too, dumping thousands of spurious "missing title"/
     "unparseable watched_at" errors — only `watched-history-*.json`
     turned out to hold real per-play events; the aggregate files can't
     reconstruct rewatch history anyway (just a play count, no per-play
     timestamps). Non-history JSON files are now skipped entirely.
  2. A real export's ~10,500 watch events took ~85s to commit
     synchronously (measured, with zero TMDB lookups) — well past any
     reverse-proxy/request timeout, which is what produced the 500 and
     left only a partial, timing-dependent slice of history imported.
     Imports over 500 rows now run in the background via Celery (the
     same mechanism Trakt/Simkl sync already uses) instead of inside the
     request — Settings → Logs shows it as `running` immediately and
     flips to `success`/`failed` once the worker finishes. Small
     imports are unaffected and still commit instantly with the same
     result page as before.

### Added

- Import Data now accepts `.json` and `.zip` files, not just `.csv` —
  including Trakt's own "Export now" zip (a bundle of JSON files, not
  CSVs). JSON entries are read either in Trakt's own history/API shape
  or a generic flat object (same field names as the CSV importer's
  columns); a zip is walked for every `.csv`/`.json` file inside and
  each is parsed and merged into one import, skipping anything that
  isn't watch-history data (images, a README, etc.).
- Rows sourced from Trakt-shaped JSON/zip imports now carry a Trakt id,
  so they dedupe against a title already synced in via Trakt OAuth
  instead of only matching by name/year the way CSV imports always have.

### Note

- The exact JSON structure inside Trakt's real export zip is unverified
  against a live export (matches the existing caveat on the Trakt OAuth
  sync code) — if your real file doesn't match, its rows will show up as
  parse errors in the preview step rather than failing silently or
  importing wrong data.

## [0.58.1] - 2026-07-31

### Changed

- Settings → Logs is wider now (matches the rest of Settings on most
  panels, but the log table needed the extra room) and gained a profile
  filter and a newest/oldest sort toggle.
- The merged sync/import/export/connect feed now looks back over the
  most recent 1000 rows per table instead of 200 (filtering to one
  profile narrows the same cap to just that profile's rows), so older
  history stays reachable through pagination instead of silently falling
  off the end. Note this only affects `SyncLog`, which already had
  history before this feature shipped — `DataLog` (CSV import/export,
  Trakt/Simkl connect attempts) only started recording with v0.58.0, so
  there's nothing from before that date to surface.

## [0.58.0] - 2026-07-30

### Added

- Settings → Admin → **Logs** — a new tab that relocates the old
  standalone Sync Log page and broadens it into one paginated,
  reverse-chronological feed across every profile: Trakt/Simkl syncs
  *and* connect attempts, and CSV import/export, each with status, item
  counts, and an expandable/copyable error message where applicable.
  Connect failures (expired OAuth state, missing authorization code, a
  failed token exchange) and CSV import failures were previously only
  ever shown once via a transient message — now persisted via a new
  `DataLog` model alongside the existing `SyncLog`.
- `/settings/sync-log/` now redirects to `Settings → Logs` instead of
  rendering its own page — old links/bookmarks still work.

## [0.57.0] - 2026-07-30

### Added

- `manage.py seed_demo` — populates a disposable dev database with
  realistic demo data (a profile with weeks of watch history, lists, a
  watchlist, and upcoming releases). Refuses to run outside `DEBUG` unless
  `--force` is passed, since it's throwaway data never meant for a real
  instance.
- `setup.sh` / `setup.ps1` — one-shot install scripts that write a working
  `.env` (random `DJANGO_SECRET_KEY`/`DB_PASSWORD`, prompted hostname) and
  bring the Docker Compose stack up, so a new user doesn't have to
  hand-edit `.env` just to get started.
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`.
- A GitHub Pages landing page (`docs/index.html`) and a banner + real
  screenshots (`docs/images/`) captured against `seed_demo` data.

### Changed

- `README.md` rewritten with a banner, badge row, table of contents, a
  Screenshots section, and the new setup scripts as the lead install path
  — content/instructions otherwise unchanged.

## [0.56.8] - 2026-07-29

### Changed

- Stats page hero card's streak/movies/shows items now spread across the
  full width of the card (`justify-between`) instead of clumping on the
  left with dead space before the "Last 30 days" column.

## [0.56.7] - 2026-07-29

### Changed

- Finished matching the Stats page hero card to the mockup: movies-watched
  and shows-completed are now two separate icon-badged items (a new
  vendored `tv` icon alongside the existing `clapperboard`), each past its
  own divider, instead of one combined "2041 / 95" line.

## [0.56.6] - 2026-07-29

### Changed

- Applied the same streak treatment to the Stats page's hero card: the
  150px dial is gone, replaced by a bold typographic headline ("Personal
  best N days" now sits under the number instead of in its own tile), with
  movies-watched/shows-completed alongside it past a divider.

## [0.56.5] - 2026-07-29

### Changed

- Reworked the profile popup's streak card: the day-streak dial (a small
  ring with the number boxed inside) is now a bold typographic headline —
  no shape, just scale and weight, matching the app's poster-title type.
  "Longest streak" moved from its own stat tile to a "Personal best N days"
  line under the headline; movies-watched/shows-completed and total watch
  time now sit below a divider as a clean 2-column row.

## [0.56.4] - 2026-07-29

### Changed

- Applied the same days-first flip to the profile popup's "Combined"
  rows (Last 30 days/All time) - "2h (≈ 0d)" becomes "0d (≈ 2h)",
  matching the Stats page change from the previous release. The
  standalone "Total watch time" hero figure in the popup is unchanged.

## [0.56.3] - 2026-07-29

### Changed

- Stats page's "Combined" rows (Last 30 days and All time) now lead
  with a whole rounded day count and show the precise hour/minute
  figure in parentheses - "8 days (≈ 198h)" instead of
  "198h (≈ 8.2 days)". Days round to the nearest whole day (430.6
  rounds up to 431, not down to 430).

## [0.56.2] - 2026-07-29

### Changed

- "Social Activity" cards are back to the normal vertical poster style
  (poster_card.html, with the full watched/add-to-list action bar) -
  the watcher avatar+name pill from the previous release now overlays
  the poster's top-left corner instead of living on a horizontal still-
  image card. "Recently Watched" keeps the horizontal episode-still
  cards from the previous release; only Social Activity changed.

## [0.56.1] - 2026-07-29

### Changed

- "Recently Watched" no longer dedupes by title - a 3-episode binge now
  shows as 3 separate cards, each using that specific episode's own
  still image (falls back to the show's poster when TMDB has none),
  fixing the row looking sparse/half-empty for anyone who mostly binges
  a few shows rather than sampling many different titles.
- "Recently Watched" and "Social Activity" cards are now horizontal
  (16:9), matching an episode still's actual aspect ratio instead of a
  vertical movie-poster crop.
- "Social Activity" cards now show a small avatar + name pill in the
  top-left corner so it's clear who watched what.
- Watchlist/Start Watching/Recently Watched/Social Activity's single-row
  layout now fades to the page background at the right edge instead of
  hard-clipping whichever card lands on the boundary.

### Added

- New `tracker/partials/watch_event_card.html` partial (horizontal,
  still/poster-backed card with an optional watcher pill) backing
  Recently Watched and Social Activity.

## [0.56.0] - 2026-07-29

### Added

- Three new Dashboard rows, replacing "Because you watched" (disabled
  for now, not removed - `DASHBOARD_BECAUSE_YOU_WATCHED_ENABLED` in
  views.py):
  - "Start Watching" - watchlist titles worth starting right now: a
    recent release/new episode, or something currently trending on
    TMDB. Skips anything already in progress.
  - "Recently Watched" - your own last watched titles, with a "History
    →" link.
  - "Social Activity" - what other profiles in the household have
    watched most recently.
  - All three use the same single-row, no-scroll layout as Watchlist.

## [0.55.2] - 2026-07-29

### Changed

- Dashboard "Up Next" now shows 4 cards instead of 3, wrapped together
  in one full-width tile with a subtle shadow, instead of sitting bare
  on the page.
- Dashboard's footer stats bar is now wrapped in its own tile too,
  matching Up Next.
- Replaced the dotted "filmstrip" section dividers on the Dashboard with
  a plain thin line (`.dashboard-rule`, replacing `.filmstrip-rule`).

## [0.55.1] - 2026-07-29

### Changed

- Dashboard's "Up Next" cards: the release-day label ("TOMORROW"/"FRI"/
  etc.) now sits as a third line under the episode caption, left-aligned
  with the title, instead of being pushed to the card's far right -
  matching the reference mockup.

## [0.55.0] - 2026-07-28

### Changed

- Redesigned the top of the Dashboard:
  - Removed the 4 stat tiles (day streak, movies this year, shows
    completed, total watch time).
  - Day streak now shows as a pill in the top-right corner, next to
    "longest streak".
  - "Up Next" is now its own full-width row with a "Full calendar →"
    link to the calendar page, instead of a side card.
  - "Recommended to you" now renders as poster cards (avatar badge,
    dismiss button, and "Add to Watchlist" overlaid on the artwork)
    instead of a thin list of rows.
  - Watchlist no longer scrolls through every item - it shows as many
    of the newest items as fit one row, with "See all lists →" for the
    rest; the header count still reflects the true total.
  - Movies this year / shows completed / total watch time now sit in a
    closing footer bar with a "View full stats →" link, instead of
    their own tiles.

## [0.54.6] - 2026-07-28

### Changed

- Title detail page: added a bold divider dot between the release date
  and the runtime/season count, and gave the "Released"/"Releases"/
  "Premieres" lead-in word and the runtime/season-count stat a bolder,
  brighter style so they stand out from the muted date text, matching
  the reference movie mockup.

## [0.54.5] - 2026-07-28

### Changed

- Title detail page: genres now render as individual colored badges
  (matching the rating/language tag style) instead of a comma-separated
  list behind a bold divider dot.
- Title detail page: dropped the redundant "Ended"/"Cancelled"/"Ongoing"
  text from the air-date row for shows - that status is already shown as
  its own badge next to the title, so the date row now only shows dates.

## [0.54.4] - 2026-07-28

### Changed

- Title detail page: "N seasons · N episodes" is now "N seasons (N EP)".

## [0.54.3] - 2026-07-28

### Fixed

- Moved the bold "·" divider on the title detail page - it now sits
  between the rating/language tags and the genre list, not between the
  rating and language tags themselves.

## [0.54.2] - 2026-07-28

### Changed

- Added a bold "·" divider between the age rating and language tags on
  the title detail page, matching the dot separator already used in the
  row below.

## [0.54.1] - 2026-07-28

### Changed

- Split the title detail page's metadata into two rows to reduce
  clutter on longer entries: age rating, language, and genres now sit
  in their own row right under the title, and release/air dates,
  runtime, and season/episode counts stay in the row below.

## [0.54.0] - 2026-07-28

### Added

- Movie and TV/Anime detail pages now show release/air date info next to
  the title. Movies show their release date ("Released"/"Releases" for
  past/future). Shows and anime don't reduce to one date the way a movie
  does - seasons can drop all at once or air weekly over months - so
  they show a status-aware summary instead: ongoing shows just their
  first-aired date, ended/cancelled shows the full first-to-last-aired
  span, and anything not yet aired shows its scheduled premiere date (or
  "Coming Soon" if TMDB hasn't scheduled one yet).

## [0.53.0] - 2026-07-28

### Changed

- Moved the Watched/Watchlisted "Display" preference back into the
  Filters panel (where it's actually used) as a 3-way eye-icon toggle -
  open eye for Show, half-shut eye for Dim, closed eye for Hide -
  instead of Settings → Preferences' dropdowns. It's still the same
  persisted per-profile preference and saves instantly on click; it just
  no longer requires leaving the page to change.

## [0.52.2] - 2026-07-28

### Fixed

- The new Filters dropdown (and, it turns out, the topbar's notification/
  friends/profile dropdowns too) closed itself the instant you clicked
  anything inside it - a select, a genre chip, a row's expand chevron -
  before the click could register. `@click.outside` was bound to the
  trigger button itself, so Alpine treated any click that wasn't
  literally on the button as "outside" and closed the panel, including
  clicks on the panel's own content. Moved it to the wrapper containing
  both the button and the panel.

## [0.52.1] - 2026-07-28

### Fixed

- The Filters panel is now a dropdown popover anchored under the Filters
  button instead of a sliding sidebar drawer. daisyUI's drawer keeps
  `will-change: transform` permanently set on the sliding panel, which on
  Windows Chrome/Edge disables ClearType subpixel text rendering inside
  it - the whole panel looked noticeably softer/blurrier than the rest of
  the page. The dropdown uses the same positioned-popover pattern as the
  topbar's notification/profile menus, which never had this problem.
- Genres, Year, Runtime, and Rating are now each their own row inside
  "Discover" (label + current value + a chevron that expands it), instead
  of Genres being an odd label-plus-separate-dropdown-button. Access
  (Language/Availability/Age Rating or Status) collapses into one row the
  same way.

## [0.52.0] - 2026-07-28

### Changed

- Redesigned the Movies & TV/Anime "Filters" drawer: Genres/Year/Runtime/
  Rating and Language/Availability/Age Rating now live in two collapsible
  "Discover"/"Access" sections instead of one long flat list, genres get
  their own scrollable chip well again (with a fade-out hint when there's
  more below), and Apply/Clear now stay pinned to the bottom of the drawer
  while scrolling instead of requiring a trip back down after every
  adjustment.
- Moved the "Display" (Watched/Watchlisted: Show/Dim/Hide) controls out of
  the Filters drawer entirely, into Settings → Preferences as a persisted
  per-profile preference. It changes how already-returned results render,
  not which titles come back, so it never belonged alongside real filters -
  it's now remembered across visits instead of resetting with every Clear.

## [0.51.0] - 2026-07-27

### Added

- Anime title pages now show a few more MyAnimeList-sourced details
  alongside TMDB's own: a MAL score badge next to IMDb/RT/Trakt, the
  native Japanese title under the English one, and the animation studio
  + source material (Manga/Light Novel/Original/...) in the metadata
  row. TMDB still drives everything else for anime (discovery, posters,
  matching) - this is additive detail, best-effort like the filler
  badges, never blocking the page if MyAnimeList has no match.

## [0.50.0] - 2026-07-27

### Added

- Anime episodes now show a Filler or Recap badge in the episode browser,
  sourced from Jikan (an unofficial MyAnimeList API) - TMDB has no filler
  data of its own. Best-effort: a title is matched to MyAnimeList by
  name/year once and cached, and any lookup failure (no match, Jikan
  unreachable) just means no badge, never a broken page.
- The sidebar now credits data sources: TMDB (movie/TV/anime metadata)
  and Jikan/MyAnimeList (anime filler data) - previously nothing in the
  app mentioned either, despite TMDB's own terms requiring it.

## [0.49.0] - 2026-07-27

### Added

- A list's own creator can now share/unshare it with the household at any
  time (a toggle next to the title/count on the Lists detail page) - the
  "Shared with household" checkbox previously only ever set this at
  creation time, with no way to change it on an existing list afterward.

### Changed

- Dragging a title to reorder a list now reorders live as you drag over
  another item (not just once you drop), with a smooth slide animation
  for the items shifting out of the way, instead of a single snap-into-
  place jump on drop.

## [0.48.1] - 2026-07-27

### Fixed

- A stray developer comment above the Lists detail page was rendering as
  visible text at the top of the page instead of being stripped - Django's
  `{# #}` comment syntax doesn't support embedded newlines (unlike
  `{% comment %}`), so a multi-line one meant for the daisyUI drawer
  structure leaked straight into the HTML.

## [0.48.0] - 2026-07-27

### Added

- Lists gained the same All/Movies/TV/Anime toggle and Filters drawer
  (Period/Sort) as History, so a big mixed list can be narrowed down.
  Sort includes a new "Manual order" option alongside the usual
  added/name/year choices.
- Titles in a list can now be manually reordered by dragging them (only
  while the list is fully unfiltered, since a filtered view can't
  unambiguously reposition items relative to whatever's hidden) - handy
  for putting a franchise in watch order rather than add-order.
- The instance owner can feature any shared list on the Dashboard (a
  star toggle on the Lists page), surfacing it in a new "Featured Lists"
  rail every profile sees - a way to spotlight a curated list (a
  chronological Marvel watch order, for example) for the whole household.

## [0.47.1] - 2026-07-27

### Fixed

- History's Filters button now shows a live active-filter dot - it
  previously only reflected whatever period/sort was true on the very
  first page load, since HTMX only ever swapped the results below the
  toolbar, never the toolbar itself.
- Switching the All/Movies/TV/Anime toggle no longer silently drops an
  applied Period/Sort filter - the toolbar's own form only ever
  submitted its own fields (type/search/title), never period/sort
  (which live in the Filters drawer), resetting them to their defaults
  on every type change.

## [0.47.0] - 2026-07-27

### Added

- History gained a search box (searches by title name) next to the
  All/Movies/TV/Anime toggle.
- History's Period and Sort dropdowns moved into a Filters drawer,
  matching the Movies & TV/Anime filter panel's own pattern.
- The Sort filter gained "Most watched"/"Least watched" - switches
  History from its usual day-by-day listing to a leaderboard of titles
  ordered by how many times each was watched, within whatever
  type/period/search filters are active.

## [0.46.0] - 2026-07-27

### Added

- The notifications panel gained a "Clear all" action (eraser icon)
  that deletes every notification outright, next to the existing
  "Mark all read" action (now an eye icon instead of text).

### Changed

- The notifications panel is slightly wider (288px → 320px).

## [0.45.0] - 2026-07-27

### Added

- TV shows and anime now get the same "watched ×N" rewatch counter
  movies already had on the poster card checkmark. Since a show has no
  single "watched" click, the count is the minimum watch count across
  every episode you've engaged with - "of the episodes you've watched,
  the least-rewatched one has been watched this many times."
- Clicking into any movie/show/anime you haven't tracked yet now shows
  the same Lists chip picker a tracked title's page uses, instead of a
  single dedicated "+ Add to Watchlist" button - "Watchlist" is just
  one of the chips, alongside any custom list.
- The episode browser now shows each episode's own runtime, and the
  season header shows the selected season's total runtime (e.g. "5h
  52m total") next to its average rating.

## [0.44.1] - 2026-07-27

### Fixed

- The Year/Runtime/Rating range sliders' handle-crossing fix in 0.44.0
  didn't actually work in practice - clamping the bound value from an
  `@input` handler doesn't stop Chrome (and others) from rendering the
  thumb at the raw pointer position while the mouse is still down, so it
  visibly sailed through the other handle anyway. Fixed properly this
  time by binding each handle's own min/max to the other handle's live
  value, a constraint the browser enforces natively during the drag
  itself.
- The Display panel's "Dim" opacity was too subtle - lowered further.

## [0.44.0] - 2026-07-27

### Added

- The Movies & TV/Anime filter panel gained three new filters: Availability
  (streaming now / all digital releases, via TMDB's watch-provider data,
  region fixed to US), Status (TV/Anime only - Returning Series, Planned,
  In Production, Ended, Canceled, Pilot; no TMDB equivalent for movies),
  and a Display section that controls how already-watched or watchlisted
  titles show up in results - Show (default), Dim (kept in the grid at
  lowered opacity, full brightness on hover), or Hide entirely.

### Fixed

- The Year/Runtime/Rating range sliders in the filter panel let you drag
  one handle past the other, producing an inverted range that broke the
  underlying filter. Each handle now clamps against the other's current
  value while dragging.

## [0.43.0] - 2026-07-26

### Added

- Title detail pages now show an age rating badge (e.g. "R", "TV-MA")
  next to the language badge.
- The Movies & TV filter panel has an Age Rating filter (movies only -
  TMDB has no equivalent filter for TV/anime).

### Fixed

- Anime browsing could surface explicit hentai content - TMDB's own
  "adult" flag isn't reliable for this (verified live: well-known
  explicit titles come back flagged non-adult, indistinguishable by
  genre from ordinary anime). Movies/TV/Anime browsing now excludes
  TMDB's hentai/ecchi/adult/erotic/porn keyword tags server-side. This
  is a real reduction, not a guarantee - some explicit titles on TMDB
  carry no matching tag at all, a gap in TMDB's own data this can't
  fully close.

## [0.42.0] - 2026-07-26

### Added

- Search now tolerates typos ("avangers" finds "The Avengers") - TMDB's
  own search API has no fuzzy matching at all (a single typo'd letter
  returns zero results), so a misspelled word gets a spelling-corrected
  retry merged in behind the direct results.
- Search now understands a trailing year to disambiguate a same-named
  movie/show ("avengers 2012" surfaces the 2012 film first, not a
  1960s TV series or an unrelated sequel).
- The search results page has an All/Movie/TV/Anime tab filter,
  narrowing both the "In your library" and "Discover more on TMDB"
  sections the same way.

### Changed

- Added a new dependency, `pyspellchecker`, for the typo-correction
  above - needs an image rebuild to pick up (`docker compose build`).

## [0.41.0] - 2026-07-26

### Changed

- Reworked the title detail page for mobile:
  - The poster/title header now stacks the poster above the title
    instead of squeezing both side by side, which used to wrap the
    title across several lines and clip it inside the header's fixed
    height on narrow screens.
  - Episodes below the `sm:` breakpoint now render as a compact row
    (small thumbnail + title inline) instead of a full-width
    video-thumbnail card per episode - the same card grid as before
    on `sm:` and up.
  - The 10-star "Your rating" row shrinks and wraps instead of
    risking overflow on narrow screens.
  - Reduced the app's main content padding on mobile/tablet
    (affects every page, not just title detail) so content isn't
    losing 64px total width to padding on a phone-sized screen.

## [0.40.0] - 2026-07-26

### Added

- A search button now shows up on mobile/tablet (below the desktop's
  own inline search bar's `xl:` breakpoint) - tapping it drops a
  full-width search bar under the header. Previously there was no way
  to search at all outside the desktop layout.

### Fixed

- The topbar's notifications/friends/profile icon cluster drifted back
  to the middle of the header on mobile instead of sitting at the
  right edge - a CSS grid auto-placement quirk (not a track-sizing
  one): once the middle nav is `display:none`, grid auto-placement
  drops the next item into the vacated column instead of skipping it,
  so the icon cluster landed in the header's middle column with the
  actual right-hand column sitting empty. Fixed by giving each of the
  header's three blocks an explicit column position instead of relying
  on auto-placement.
- Tightened the gap between the topbar's icons on mobile/tablet.

## [0.39.1] - 2026-07-26

### Fixed

- A show watched to completion entirely through the episode browser
  (one-by-one, or via "Mark season watched"/"Mark all watched") now
  correctly shows the green "watched" checkmark on poster cards
  (Dashboard, Watchlist, Search, Discover) - it previously only lit up
  once you'd also clicked the poster card's own one-click watch button,
  since the checkmark was keyed off whole-title plays only and ignored
  per-episode ones entirely.

## [0.39.0] - 2026-07-25

### Added

- The episode browser can now mark a whole season, or a whole show,
  as watched in one click ("Mark season watched" / "Mark all watched"
  next to the Episodes heading) - catches up every episode that
  doesn't already have a play logged, without touching ones you've
  already watched.
- The season picker is now a custom dropdown (replacing the plain
  browser `<select>`) showing every season's own TMDB rating next to
  it, not just the currently-selected one.

### Changed

- A show's title page no longer has the single "+ Mark as Watched"
  header button/popover a movie gets - a show isn't one item the way a
  movie is (many seasons, many episodes), so a single whole-title
  "watched" toggle didn't map to anything real. That control now
  belongs only to movies; shows use the new season/whole-show actions
  in the episode browser instead.

## [0.38.0] - 2026-07-25

### Added

- TV/anime episode tiles now show TMDB's own rating for that episode,
  and the season header shows the average across the season's rated
  episodes.

### Changed

- The episode browser (season picker + episode grid) now shows up on a
  TV/anime title's preview page too, not just after it's been added to
  a list, marked watched, or imported - previously it was hidden
  entirely until the title had a real library row. A preview's
  episodes are read-only (no watched button, since there's nothing to
  attach a watch to yet) and never show as watched; adding the title
  to your library unlocks marking episodes watched as before.

## [0.37.0] - 2026-07-25

### Changed

- The topbar's Friends dropdown "Active X ago" badge now reflects when
  that profile actually last used the app, not when they last watched
  something (which could be a backdated Trakt/Simkl/CSV import
  timestamp, unrelated to real presence). A new `Profile.last_seen_at`
  field is stamped by a new middleware on every request (throttled to
  once a minute, so normal browsing isn't a DB write on every page
  load). **New migration** (`0023_profile_last_seen_at`) - run it as
  usual on upgrade.
- The title detail page's own "Watched" header button now opens the
  same rewatch/undo/history menu the poster card's watched button
  already has, instead of a plain toggle that only ever cleared the
  whole watch history. Behaves identically either way - a title
  watched once still shows the popover with all four actions; a
  never-watched title still logs its first watch on a single click.

## [0.36.0] - 2026-07-25

### Added

- The poster card's watched checkmark now shows a "×N" play-count badge
  once a title's been watched more than once.
- Clicking an already-watched title's checkmark now opens a menu
  instead of silently logging another play: View history plays (jumps
  to History filtered to just that title), Mark as watched again,
  Remove last watched (undoes a single play), and Remove all watched
  history. A never-watched title still logs its first watch on a
  single click, unchanged.
- The History page can now be filtered to a single title via
  `?title=<id>` (what the new menu's "View history plays" link uses),
  with a "Filtered to X · Clear" banner and the filter preserved across
  type/period/sort changes and pagination.

## [0.35.1] - 2026-07-25

### Changed

- The new Settings page hugged the left edge with the sidebar+content
  column left-aligned, leaving a large empty gap on wide screens.
  Constrained and centered the whole page (header included) instead.

## [0.35.0] - 2026-07-25

### Changed

- Merged "My Profile," "Settings & Import," and the owner-only "Admin
  Dashboard" - three separate pages, each only reachable from the
  profile dropdown - into a single Settings page with a left sidebar
  (Account, Preferences, Notifications, Integrations, Import Data,
  Export Data, Danger Zone, plus an owner-only Admin group: Profiles,
  Server Integrations, Server, Activity Log), switching between
  sections instantly with no page reload. The profile dropdown's three
  links collapsed into one "Settings" entry.
- Every existing form still posts to the exact same endpoint it always
  did - this is a reorganization, not a rewrite. The two account forms
  that used to rely on posting back to whatever page rendered them
  (only ever My Profile before) now target it explicitly, since the
  page can load from three different URLs.
- The "share my activity" privacy toggle moved from its own card into
  the Account tab (next to the rest of your profile info), matching
  where the reference design for this change put it.

## [0.34.1] - 2026-07-24

### Changed

- Dropped the "self-hosted" subtitle next to the SPOOL wordmark
  (topbar and the mobile sidebar) and sized the wordmark up a bit
  (24px → 28px in the topbar, 28px → 32px in the mobile sidebar) now
  that it's not sharing the space.

## [0.34.0] - 2026-07-24

### Changed

- Removed the "AI Pick" Gemini mood-search box from the Dashboard - it
  needs a configured API key to do anything, so it was dead weight for
  most profiles, and it was crowding out "Recommended to you" (which
  is more interesting anyway, since it's from a real person, not a
  bot). The Gemini integration itself is untouched and still
  configurable in Settings; only its Dashboard entry point is gone for
  now.
- "Recommended to you" is now its own standalone, richer section
  instead of a plain list of text rows sharing a card with the ask
  box: each recommendation is a small card with the title's poster,
  the sender's actual avatar (not just their name), a relative
  timestamp ("2 days ago"), and - new - a one-click "+ Add to
  Watchlist" action alongside the existing dismiss (×). Previously the
  only thing you could do with a recommendation was dismiss it; there
  was no way to act on it. The section also now shows a count badge
  ("2 new") once there's more than one pending.
- Adding a recommended title to the Watchlist doesn't dismiss the
  recommendation - it stays pending (showing "Added") until the title
  is actually watched, so it keeps nudging you until you've seen it,
  not just queued it.

## [0.33.1] - 2026-07-24

### Fixed

- Docking "Up next" beside the Watchlist carousel (0.33.0) broke
  horizontal scrolling for large watchlists - a grid item's width
  defaults to fitting its content, so the Watchlist column stretched
  to fit every poster instead of scrolling within its own space,
  pushing the whole row (Up Next included) off the right edge of the
  page. Constrained the column so the carousel scrolls in place again.

## [0.33.0] - 2026-07-24

### Changed

- Reorganized the Dashboard into clear purpose-driven sections instead
  of a flat stack of same-weight boxes: "your numbers" (stat cards),
  "pick something" (AI Pick), and "your queue" (Watching/Watchlist/Up
  Next), each separated by a visible film-strip divider instead of
  uniform spacing.
- Merged "What should I watch?" and "Recommended to you" into a single
  AI Pick module - the ask box and the recommendations it's produced
  now live in one card instead of two visually unrelated ones stacked
  on top of each other.
- Removed "Recently added to lists" - it was showing the same items as
  the Watchlist row directly above it, adding visual repetition with
  no new information.
- The "Watching" section (continue-watching carousel) now disappears
  entirely when nothing's in progress, instead of showing an empty
  header with placeholder text.
- "Up next" now sits docked beside the Watchlist carousel rather than
  floating below it next to the now-removed "Recently added" section.

## [0.32.7] - 2026-07-24

### Changed

- The poster card action bar's "marked" state (watched checkmark,
  on-a-list icon) only changed the icon's color against the same dark
  gray background, which was hard to notice at a glance. Added a
  tinted background pill behind the icon when active (green for
  watched, amber for on-a-list) so the marked state pops instead of
  blending in.

## [0.32.6] - 2026-07-24

### Changed

- Poster card action bar was semi-transparent black over the poster
  art, with a dark gradient fading up from the bottom to keep the
  overlaid title readable. Made the bar opaque dark gray instead of
  translucent black, dropped the gradient, and moved the bar below the
  poster (flush against it) instead of floating on top of it, so the
  full poster art is visible. The redundant title caption that used to
  sit on the poster (readable only because of that gradient) is gone
  too - the title below the poster already shows it.

## [0.32.5] - 2026-07-24

### Changed

- Poster cards (library grids and Discover preview tiles) traded their
  floating circular icon buttons for a full-width flat action bar
  along the poster's bottom edge, matching a reference design the user
  provided. Still just the two actions Spool supports - mark as
  watched and add to list - now spanning the card edge-to-edge instead
  of sitting as an inset pill. The watched indicator changed from a
  filled green circle to a plain green checkmark so it reads
  consistently with the flat bar.

## [0.32.4] - 2026-07-24

### Changed

- Stats' Peak Hours widget showed a bare, unlabeled count per bucket
  ("6992") with nothing indicating what it meant. Added a caption
  under the heading ("Plays logged in each part of the day, lifetime")
  and a hover tooltip on each number, and widened the count column so
  4-digit totals aren't cramped.


## [0.32.3] - 2026-07-24

### Fixed

- TV/anime watch time was silently undercounted whenever TMDB's
  show-level "typical episode length" was missing (common for anime
  and foreign shows) - those episodes counted as 0 minutes toward
  every watch-time stat, permanently, with no retry. Added a fallback
  that pulls each episode's own runtime from TMDB's season/episode
  endpoint (already fetched elsewhere in the app for episode names,
  but the runtime field was being discarded) whenever the coarser
  show-level figure isn't available.

  **If your TV/anime total watch time looks too low, re-run
  `python manage.py backfill_completion`** (already existed, safe to
  re-run) to recompute it against the fix - no migration needed, it
  just needs to talk to TMDB again for shows it couldn't fully cover
  the first time.


## [0.32.2] - 2026-07-24

### Fixed

- Topbar's center nav pills were visibly off-center (dragged right)
  because they were centered within the leftover flex space between
  two unequal-width siblings (logo+search vs. the icon cluster), not
  the header's true center. Rebuilt the header as a 3-column grid
  (1fr / auto / 1fr) so the center column is genuinely centered
  regardless of how wide either side is.

### Changed

- Reverted the SPOOL logo back to its single-line "SPOOL · self-hosted"
  layout, removing the live clock added last round.
- Moved the topbar's vertical divider to sit between the logo and the
  search bar, instead of between the search bar and the nav.


## [0.32.1] - 2026-07-24

### Changed

- Desktop topbar's profile trigger is now a pill (avatar + display name
  + a chevron that flips when the dropdown is open) instead of a bare
  avatar circle, on screens sm: and up. Mobile keeps the plain avatar
  circle to stay compact.


## [0.32.0] - 2026-07-24

### Changed

- Redesigned the desktop topbar's nav into a centered pill/segmented
  control - each link is now a rounded pill with icon and label, and
  the active page's pill fills solid instead of an underline. The
  search bar is now a plain rounded pill instead of a bordered box,
  and a live clock (date + time, respecting the 12h/24h preference)
  now sits under the SPOOL logo. Notifications, Friends, and the
  profile avatar are unchanged.


## [0.31.3] - 2026-07-22

### Changed

- Neutral secondary buttons (Sync now, Save, Change password, Cancel,
  Connect, and others) switched from a bordered/outlined look to
  daisyUI's `btn-soft` style - a faint tinted background with no
  border, which doesn't read as a disproportionately thick outline on
  small buttons with short labels the way a fixed-width border does.
  Destructive (red-outlined) and already-solid/filled buttons are
  unchanged.


## [0.31.2] - 2026-07-22

### Changed

- Moved Notifications and Privacy from Settings & Import to My Profile
  - they're personal preferences, not import/integration setup. The
  underlying save endpoints are unchanged.


## [0.31.1] - 2026-07-22

### Added

- Settings & Import: a "Sync now" button on Trakt/Simkl for an immediate
  one-off sync, alongside the existing scheduled sync - doesn't change
  the schedule itself.
- Admin Dashboard: profiles can now be demoted back from owner to
  member, not just promoted.

### Changed

- Admin Dashboard's Profiles card: the Promote/Remove text links are
  now icon buttons (crown/ring/trash) with a tooltip on hover
  explaining what each does.


## [0.31.0] - 2026-07-21

### Added

- Settings & Import: a personal Timezone dropdown under Appearance -
  household members in a different timezone than the server now see
  their own local times, activated per-request by a new
  `ProfileTimezoneMiddleware`. Blank (the default) keeps using the
  server's own `TIME_ZONE`.
- Admin Dashboard: an Activity Log card recording who created, removed,
  or promoted a profile, and when.
- Admin Dashboard: a "Promote" control letting the owner hand another
  profile owner-level access.
- My Profile: a Danger Zone with self-service account deletion for
  Members - previously the only way to leave was asking the owner to
  remove you. An owner can only delete their own account once another
  owner exists to take over.

### Changed

- Settings & Import's "Import & Export" card is renamed to "Connected
  Apps".
- Trakt/Simkl's "Connect" button is disabled with an explanatory note
  when the server owner hasn't configured credentials for that
  provider yet, instead of linking through to an error.
- Moved the "Spool vX.Y.Z" footer from Settings & Import to Admin
  Dashboard's Server card, next to the Django version/database/debug
  info - it's server metadata, not a personal preference.


## [0.30.0] - 2026-07-21

### Added

- My Profile: an optional one-line bio field, a "Member since" date, and
  read-only Trakt/Simkl connected-status badges next to the page
  heading.
- My Profile: a live thumbnail preview of a chosen photo before saving,
  instead of just the filename text.
- Change password: a show/hide (eye icon) toggle on all three password
  fields, and an "at least 8 characters" hint under New password.

### Changed

- My Profile's "Remove" photo button is now a red trash-can icon
  instead of a text button.



## [0.29.8] - 2026-07-21

### Changed

- Moved the desktop topbar's search bar back next to the logo (it had
  briefly moved next to the icon cluster) and widened it slightly
  (`w-56` → `w-72`).

## [0.29.7] - 2026-07-21

### Changed

- The desktop topbar's search bar now shows a search icon and a slight
  vertical divider before the input text, instead of being a bare
  text field.

## [0.29.6] - 2026-07-21

### Changed

- Reordered the desktop topbar: the search bar now sits between the
  nav links and the notifications/friends/avatar cluster instead of
  next to the logo, and a vertical divider separates the logo from
  the rest of the header.

## [0.29.5] - 2026-07-21

### Fixed

- Topbar's Notifications/Friends/avatar icon cluster sat left-of-center
  on mobile instead of hugging the right edge - it's a `flex-none`
  sibling of the middle `<nav>`, which is `display:none` below `md:`
  and so isn't there to push it over as it does on desktop. Added
  `ml-auto` (with an explicit `md:ml-0` reset) so the cluster is pushed
  to the header's right edge on mobile without affecting desktop, where
  the nav's own flex-grow already claims that space.

## [0.29.4] - 2026-07-21

### Fixed

- Topbar's Notifications and Friends dropdowns ran off the left edge of
  the screen on mobile - they were positioned with `absolute right-0`
  off their trigger button, but on a narrow phone the icon cluster
  isn't pushed all the way to the true right edge (the middle nav is
  hidden below `md:`), so a fixed-width panel anchored that way
  overflowed. Switched all three header dropdowns (bell, Friends,
  avatar) to `fixed` positioning with JS-computed, viewport-clamped
  coordinates - the same idiom already used by the poster card and
  history group popovers - so every panel stays fully on-screen
  regardless of button position or viewport width.
- Movies & TV, Anime, Collections, Dashboard's "Because you watched",
  Search, and title detail's "If you like this" grids showed only a
  single oversized card per row on mobile - their `minmax()` floor was
  wide enough to force the grid down to one column on a phone-width
  screen. Added a smaller mobile-first floor with a `sm:` override
  restoring the original desktop size. Also fixed a latent bug in
  Search's library-results grid where the poster card's own hardcoded
  width was silently overriding the grid's track sizing entirely.
- Calendar's month grid was cramped and illegible on mobile - full
  poster thumbnails packed into ~40-50px-wide day cells. Below the
  `sm:` breakpoint, cells are now shorter and show a single presence
  dot instead of thumbnails, relying on the existing tap-through to the
  agenda sidebar for full detail.

## [0.29.3] - 2026-07-21

### Fixed

- Uneven spacing between the topbar's bell/Friends/avatar icons - the
  avatar button still carried a `-ml-2` left over from the old design,
  where household avatars sat in a tightly-overlapped stack. Removed
  now that the Friends dropdown replaced that stack, so the parent's
  own gap spaces all three evenly.

## [0.29.2] - 2026-07-21

### Added

- History's day-group headers now show total watch time next to the
  "1 movie · 4 episodes" count - in minutes, hours, or days depending
  on how much was watched that day (e.g. "45m", "5h 0m", "2d 17h"),
  matching Trakt/Simkl's own watch-time formatting.

## [0.29.1] - 2026-07-21

### Changed

- The topbar's household-member avatar circles are now a single
  Friends icon that opens a dropdown - each row shows the person's
  avatar, name, and when they were last active (time since their most
  recent watch, or "No activity yet"), then opens the same stats
  popup as before. A growing household no longer crowds the header
  with an ever-longer row of circles.

## [0.29.0] - 2026-07-20

### Added

- The detail page's "Mark as Watched" header button is now a real
  watched/unwatched status toggle (like a Follow/Following button),
  not a static call-to-action that never changed once clicked. Once
  watched it turns into a green "✓ Watched" indicator, and clicking it
  again removes the watch mark (confirmed first, since undoing a watch
  is a meaningful action) - a new title_unmark_watched action, separate
  from the poster card/episode browser's own quick-action buttons,
  which keep their existing "always log a fresh rewatch, never unmark"
  behavior.
- A not-yet-tracked preview page (a TMDB search/discovery result you
  haven't watched or listed yet) now offers "Mark as Watched"
  independently of "Add to Watchlist" - previously the only way to log
  a watch for something you'd already seen was to add it to a
  watchlist first, which isn't the same fact about a title and
  shouldn't have been a prerequisite.

## [0.28.1] - 2026-07-20

### Changed

- Redesigned the title detail page's Lists and Recommend To cards.
  Lists now uses filled/outlined chip toggles (matching the Filters
  drawer's genre-chip language) so membership is obvious at a glance,
  with "+ New list" as its own clearly separate, dashed-outline action
  instead of another same-looking "+". Recommend To now shows each
  profile's real avatar (color circle or photo, same as everywhere
  else in the app) with a compact icon-only send button, instead of a
  full-width text button and no avatars at all.

## [0.28.0] - 2026-07-20

### Fixed

- Recommending a title no longer requires adding it to a watchlist
  first - the "Recommend to" card now also shows on a not-yet-tracked
  preview page (TMDB search/discover results you haven't watched or
  listed yet), and materializes the title itself when you actually
  click Recommend, same as every other preview action.
- Sending a recommendation now actually notifies the recipient (header
  bell) - it previously only ever showed up passively on their
  Dashboard, with nothing pointing them at it.
- Movies & TV / Anime's discovery grid (and Dashboard's "Because you
  watched" row, a title's "similar" grid, ...) now correctly shows the
  green watched checkmark and list membership for a title you've
  already watched or listed elsewhere, reappearing there on a
  Trending/Popular page or as a suggestion - it previously always
  rendered as untracked, regardless of your real history.
- Marking a title (or episode) watched again once it's already green
  now asks for confirmation first, instead of silently logging another
  rewatch on a stray double-click - the first "mark watched" stays a
  single uninterrupted click.
- Replaced the browser's own native confirm() popup with a styled
  in-app dialog everywhere the app asks for confirmation before an
  action (History's single/per-episode/bulk delete, the new rewatch
  guard above) - one global handler, no per-template changes needed at
  each call site.

## [0.27.1] - 2026-07-20

### Removed

- The topbar's "+ Add title" button - it never had any click handler
  wired up, so it did nothing. With the search bar now able to find
  and add any title, a dead button offering the same job was just
  confusing. (The Lists detail page's own "+ Add title" button, which
  toggles that page's inline search and does work, is unrelated and
  unchanged.)

## [0.27.0] - 2026-07-20

### Added

- Profile pictures: My Profile now has a Photo uploader (JPG/PNG/WEBP,
  up to 5MB) that takes priority over the color-circle avatar everywhere
  one is shown - topbar, Activity feed, Admin Dashboard's profile list,
  and the household profile popup. "Remove" reverts to the color
  circle. Uploads are validated server-side (Pillow decodes the actual
  bytes, not just the filename/content-type) before being saved.
  Uploaded files are served at `/media/...` by Django itself - this
  self-hosted stack has no reverse proxy of its own to delegate to, and
  whitenoise (already in use) is static-asset-only.
- New profiles now get a random starting avatar color instead of every
  profile sharing the same hardcoded default - prefers a color no
  existing profile is already using, so a small household doesn't end
  up with two coincidentally-matching avatars.
- The navbar avatar circles (both the active-profile dropdown and the
  household stack) are a bit bigger - 36px, up from 30px.

### Fixed

- `STORAGES` in settings.py only defined a `staticfiles` backend;
  Django 4.2+ replaces its *entire* default STORAGES dict when you set
  it at all, so there was no `default` file-storage backend for any
  `FileField`/`ImageField` to resolve to. Discovered while adding the
  avatar-upload feature above - added the missing `default` entry
  (`FileSystemStorage`).

## [0.26.1] - 2026-07-20

### Added

- "Last 30 days" now gets its own Combined row (Movies + TV + Anime
  summed), matching the Combined row "All time" already had - on both
  the Stats page and the profile popup (click a household avatar), the
  two places this watch-time breakdown is shown.

## [0.26.0] - 2026-07-20

### Added

- Send a household member a movie/TV/anime recommendation. A "Recommend
  to" card on a title's own page lets you point any other profile at
  it in one click - no message field, deliberately kept simple. It
  shows up on their Dashboard under "Recommended to you" until they
  either watch it or dismiss it. The moment they watch any part of it
  (a movie, or a single episode of a show - finishing a whole series
  isn't required), you get a notification in the header bell linking
  straight to the title. Recommending something they've already
  watched, or recommending the same title to the same person twice
  while one's still pending, is caught and reflected in the card
  instead of silently doing nothing.
- Fulfillment is resolved by an explicit call
  (recommendations.mark_title_watched) at every place a watch event
  gets created - the manual mark-watched/rate actions, CSV import, and
  Trakt/Simkl sync - the same pattern this codebase already uses for
  rewatch detection and watchlist auto-removal, not a signal (used
  nowhere else here), so a missed call site is a visible test gap
  rather than a quiet one.

## [0.25.0] - 2026-07-19

### Added

- History's binge-group tiles (the "10×" badge) now open a dropdown
  listing every episode in the group, each with its own delete action -
  no need to nuke the whole binge just to remove one episode marked by
  mistake. Deleting shrinks the group in place (recomputed range/count/
  duration), degrades to a plain tile once only one episode is left, or
  removes the tile outright once none are.

### Changed

- History's poster tiles are slightly bigger (132px → 150px minimum
  width).

## [0.24.1] - 2026-07-19

### Fixed

- Activity's per-row explanatory comment was leaking onto the page as
  literal text above every entry. Django's `{# ... #}` comment tag is
  single-line only - a multi-line one silently isn't recognized as a
  comment at all and renders as-is instead of being stripped. Swapped
  it for the `{% comment %}...{% endcomment %}` block tag, which does
  support multiple lines, and added a regression test asserting no
  stray `{#`/`{%` text ever appears in the rendered page.

## [0.24.0] - 2026-07-19

### Changed

- Activity is back to being a lightweight household glance, not a
  second History page. Dropped the expand-to-full-episode-list/chevron
  interaction on grouped entries entirely - a group now shows only its
  collapsed summary line (count, episode range, one relative time).
  Full episode-level detail for a binge is what History is for.
- Each row now carries a left-border color by activity type (watched,
  added to a list, rated) so the feed can be scanned for "what kind of
  thing happened" without reading every line.

### Fixed

- A binge summary could silently merge two real, hours-apart viewing
  sessions of the same show into one group (same profile+title, and
  nothing else happened in between across the whole household feed to
  break the run) - the exact cause of a "14 episodes... 7 hours ago"
  entry where 5 of those episodes were actually watched the day before.
  Consecutive watches/list-adds now also need to be within 6 hours of
  each other to stay in the same group; a real, hours-long continuous
  binge still stays one group since the check is chain-based (each
  episode vs. the previous one), not a hard cap from the first episode.

Also confirmed (no changes needed): the feed already interleaves every
profile's activity by timestamp rather than grouping by user - a quiet
household member's activity from days ago just naturally sorts below a
more recently active one's.

## [0.23.0] - 2026-07-19

### Added

- Spool now tells you when a newer version is out, instead of you having
  to remember to check. A nightly job compares the running version
  against the VERSION file on the repo's master branch (this project
  doesn't cut GitHub Releases, so that file is already the versioning
  source of truth) and, if there's a newer one, surfaces it two ways:
  a notification in the header bell, and a banner at the top of
  Settings & Import. Both link straight to the GitHub changelog so you
  can see what's new before deciding to upgrade. Owner-only (a
  household member has no way to actually perform an upgrade), and
  self-correcting - once you actually upgrade, both alerts clear on
  their own without needing anything reset.

## [0.22.0] - 2026-07-19

### Added

- A "Because you watched X" discovery row on the Dashboard - TMDB's own
  recommendations for the most recently watched title that has a TMDB
  id, rendered as the same preview cards Movies & TV/Anime's discovery
  grid uses. This was one of the original Dashboard carousel ideas that
  had quietly dropped out somewhere along the way in favor of just
  Watchlist - the Dashboard was otherwise 100% "things you already
  have," with nothing suggesting what to watch next. Also fills the
  dead space that used to sit below "Up next" on a lighter day.

### Fixed

- "Recently added to lists" was showing the same handful of titles as
  the Watchlist carousel directly above it (Watchlist adds counted as
  "added to a list" too), so the two rows usually looked like
  duplicates. It now only shows adds to actual custom lists, so it
  carries information the Watchlist row doesn't.
- Dashboard's "Total watch time" stat now reads "217d 4h 3m" style,
  matching the Stats page's own watch-time breakdown format, instead of
  a flat "7342H" that read inconsistently next to it.

## [0.21.0] - 2026-07-19

### Added

- The navbar search box actually does something now - it was pure
  decoration before. Type anything and press Enter to jump to a results
  page with two sections: matches already in your library (full
  watched/list-picker actions, same as everywhere else) and everything
  else TMDB has for that query that isn't tracked yet (the same preview
  cards Movies & TV/Anime's own discovery grid uses, so you can add it
  straight from search).
- A "What should I watch?" box on the Dashboard - describe your mood in
  plain language and get a few specific picks back, grounded in your
  own recent watch history and favorite genres. Powered by Gemini,
  optional and bring-your-own-key per profile (Settings → AI
  Recommendations, with a link to get a free key) - nothing here is
  required or instance-wide, and every failure mode (no key, bad key,
  Gemini unreachable) degrades to a plain-language inline message
  instead of breaking the Dashboard.

## [0.20.0] - 2026-07-19

### Added

- Sync Log now surfaces problems instead of just listing them. When a
  provider's most recent syncs are consecutively failing (an unresolved
  streak, not a blip that already recovered), a banner appears above the
  table - e.g. "Trakt sync has failed 4 times in a row since Jul 17 -
  the access token may have expired or been revoked", with a direct
  Reconnect link when it's your own account and the errors look
  auth-shaped (a 401/Unauthorized). Other profiles' broken syncs get the
  same banner without a reconnect link, since only the account owner can
  reconnect their own integration.
- Error messages are no longer stuck truncated with only a native hover
  tooltip - click one to expand the full text in a monospace, selectable
  block with a Copy button.
- Status now pairs a check/x icon with the existing success/failed
  color, instead of relying on color alone.
- Failures under a second get a small "fast fail" badge on the Duration
  column - a sub-second failure almost always means the request was
  rejected before reaching Trakt/Simkl at all (an auth problem), not a
  timeout, and that distinction isn't obvious from the number alone.

## [0.19.0] - 2026-07-19

### Fixed

- Trakt/Simkl syncs now recover from an expired or revoked access token
  instead of failing every run forever. Previously the `refresh_token`
  captured at connect time was stored but never actually used anywhere -
  once an access token stopped working, every sync 401'd indefinitely
  until the user manually disconnected and reconnected. A sync that hits
  a 401 now refreshes the token via the stored refresh_token and retries
  once before giving up; the new tokens are saved back to the account.
  Requires the exact `redirect_uri` used at connect time (Trakt's refresh
  grant requires it match), so a new `ExternalAccount.redirect_uri` field
  captures that at connect time - accounts connected before this ships
  won't have one yet and fall back to the old behavior (manual reconnect)
  until they reconnect once.
- `generate_release_notifications`'s nightly Celery task referenced an
  undefined variable in its return statement, meaning it crashed after
  every run (its actual work still happened and got logged - the crash
  was purely in the return value).

## [0.18.0] - 2026-07-19

### Added

- Settings → Danger Zone: a red-treatment card at the bottom of the
  page for permanent, destructive data actions. "Clear watch history"
  wipes every watch event and rating (and in-progress "watching" state)
  for your profile in one go - your lists and watchlist are untouched,
  since history and curation are kept conceptually separate everywhere
  else in this app. Each connected provider (Trakt/Simkl) also gets a
  "Disconnect & wipe" action alongside the existing plain "Disconnect" -
  it removes the integration the same way, plus your own watch history
  for titles that provider matched. That match is approximated by
  "this title carries that provider's external id" (per-watch-event
  provenance isn't tracked), so a title also tracked another way keeps
  losing its full history here, not just the provider-specific slice -
  documented as a known limitation rather than silently glossed over.
  Both actions only ever touch your own profile's watch data; shared
  library rows (Titles/Episodes) and other profiles' history are never
  touched.

This is the third of the planned Settings rounds. Account deletion
itself was deliberately left out - it's a better fit for My Profile
than here, and wasn't part of this round's scope.

## [0.17.0] - 2026-07-19

### Added

- In-app notifications: a bell in the header with an unread badge and a
  dropdown feed. No email or push - purely in-app for now. Three
  sources, each its own toggle on Settings → Notifications: new
  episode/season alerts for what you're actively watching, calendar
  reminders for anything you're watching or have watchlisted (including
  shared lists), and Trakt/Simkl sync failure alerts. The two release-
  based sources run as a nightly background job right after the
  existing release-schedule sync; sync failures notify immediately,
  the moment a sync actually fails.

This is the second of the planned Settings rounds - a Danger Zone
(destructive account/data actions) is still deliberately left for a
follow-up round.

### Added

- Settings & Import: the "Import your history" card is now "Import &
  Export" - export your full watch history as CSV (round-trips with the
  existing CSV import) or as Trakt-compatible JSON.
- A new Privacy card (only shown with more than one profile on the
  server): a "Show my activity to other profiles on this server"
  toggle. Off, a profile's watches/ratings/list-adds are entirely
  absent from the household Activity feed, not just unlabeled.
- Appearance gained two more preferences: a default landing page (where
  logging in takes you - Dashboard, Movies & TV, Anime, History,
  Calendar, Lists, or Stats), and a preferred language, which pre-fills
  Movies & TV/Anime's own language filter instead of "Any language"
  (not full TMDB response localization - titles/overviews stay in
  TMDB's own language).

This is the first of a few Settings rounds - Notifications (in-app,
no email/push planned yet) and a Danger Zone (destructive data/account
actions) are deliberately left for follow-up rounds rather than
bundled into this one.

### Added

- A "Collections" tab on the Movies & TV page, alongside Trending/
  Popular/Upcoming/Top Rated - browse movie franchises (John Wick,
  Toy Story, Indiana Jones, ...) and click into one to see every movie
  in it. TMDB has no dedicated endpoint for this, so the list is
  derived from what's currently popular on TMDB rather than a
  hand-maintained list, and refreshes naturally as that does. Movie-
  only for now (not shown on the Anime page); studio/network browsing
  (Marvel Studios, A24, Pixar, ...) is a separate, not-yet-built
  feature. Turned off by default for now (not enough distinct
  collections surfacing yet to feel worth a permanent nav tab) - the
  feature itself is fully built and one flag away from coming back.

### Added

- The member-profile popup (clicking another household profile's avatar
  in the header) now shows real stats instead of a handful of plain
  boxes: the same circular streak ring, a Last 30 Days/All Time watch-
  time breakdown, and genre chips the main Stats page uses, plus a
  dimmed/blurred backdrop so the popup reads as focused. A "View full
  stats →" link deep-links to that member's own full Stats page.
- The Stats and History pages can now be viewed scoped to any household
  profile, not just your own (`/profile/<id>/stats/`,
  `/profile/<id>/history/`) - read-only when viewing someone else (no
  bulk-select/delete on their History). The Stats page also gained a
  "View History" link through to the matching History view.

### Added

- The Watchlist is now a real, auto-managed watchlist instead of just a
  regular list that happened to be named "Watchlist": a title comes off
  it automatically once it's finished - a movie watched at least once,
  or every episode of a show/anime watched - the same behavior Trakt
  and Simkl's own watchlists have. This only ever affects the one list
  flagged as the Watchlist; custom lists are never touched, even if a
  movie on one gets watched, and even if a custom list happens to be
  named "Watchlist" too. Existing installs get their current "Watchlist"
  list flagged automatically via a data migration.

### Fixed

- Watching every episode of a show manually via the episode browser
  (added in 0.10.0) never updated its "completed" status - only
  Trakt/Simkl sync and CSV import did. The per-episode watched button
  now runs the same completion check they do.

### Changed

- Added proportional breathing room to the main content area on
  desktop/tablet (`lg:` and up) - an extra 10% of the viewport width on
  each side, on top of the existing base padding, so wide pages don't
  stretch page content edge-to-edge. The header/navbar itself stays
  full-width and is unaffected; mobile is unaffected too.

## [0.12.0] - 2026-07-17

### Changed

- Reworked the app's primary navigation: the collapsible left sidebar is
  now a mobile-only drawer (opened via the header's hamburger button),
  and desktop/tablet screens get the nav links folded directly into the
  header as a single row instead - logo, search, nav links, and the
  add-title/profile controls all in one bar, rather than a separate
  strip underneath. The "Dashboard" nav item is gone - the SPOOL logo in
  the header is now itself a link back to the dashboard, on every screen
  size. "Settings & Import" and "Admin Dashboard" moved out of the main
  nav (they no longer need their own always-visible slot) and into the
  profile dropdown menu, alongside "My Profile" and "Log out".

## [0.11.0] - 2026-07-17

### Added

- A "mark as watched" button on each episode tile in the title detail
  page's episode browser, in the same style as the round check button
  used on Movies & TV/Anime poster cards. Logs a `WatchEvent` for that
  specific episode (materializing a local `Episode` row with its TMDB
  name if one doesn't exist yet); clicking again logs a rewatch, same
  no-unwatch behavior as every other watched button in the app.

## [0.10.1] - 2026-07-16

### Fixed

- The backdrop's "Directed by X" text (removed in 0.10.0 when the
  director moved into the Cast row) was supposed to stay - the two
  aren't mutually exclusive. Restored it alongside the Cast row entry.

## [0.10.0] - 2026-07-16

### Added

- Title detail pages for TV shows/anime now have an episode browser: a
  season dropdown (not every season on one page) and a grid of episode
  tiles (thumbnail, name, "SEASON FINALE" badge on the last episode of
  a season) with a green checkmark on episodes you've already watched.
  Opens on the season you're currently partway through by default (the
  highest season you have any watched episode in), or Season 1 if
  you haven't started the show yet.

### Changed

- The director now appears as the first entry in the Cast row (with a
  divider before the rest of the cast), instead of small text overlaid
  on the backdrop image - movies only, since TMDB doesn't credit a
  single director at the series level for TV/anime.

## [0.9.3] - 2026-07-16

### Fixed

- Poster cards (Dashboard, Discover, History, Calendar) had a faint
  white border baked into every poster image, meant to be an almost-
  invisible edge definition - on bright/light-colored posters it showed
  up as an obvious pale rim around the whole card instead of blending
  in. Removed it; the rounded-corner clipping alone is enough to define
  the card's edge.

## [0.9.2] - 2026-07-16

### Changed

- Watch activity's heatmap now fills the full width of its card - it
  was a fixed-pixel-size grid that left blank space in a wide card and
  needed horizontal scrolling in a narrow one. Cells now scale
  fluidly with the container (staying square) instead of a fixed 11px.
- The year selector next to it is now a dropdown instead of a row of
  tabs, which used to overflow on smaller screens.

## [0.9.1] - 2026-07-16

### Fixed

- "Your top genres" showed illegible truncated labels ("R..", "8...")
  for the long tail of minor genres, whose segments are too narrow to
  fit a name + count. Labels are now suppressed below a minimum share
  (~3%) - the segment still renders at its correct proportional width
  and color, it just doesn't try to cram text into a sliver too thin to
  hold it.

## [0.9.0] - 2026-07-16

### Fixed

- "Your top genres" always showed "No genre data yet" - it turned out no
  import path (Trakt, Simkl, CSV) has ever fetched or attached genre
  data to a title, for anyone, ever, since the feature was first built.
  Genre-fetching is now wired into all three import paths (and the
  discover/preview "materialize" flow), via TMDB's title-details
  endpoint, which already returns genre names alongside the runtime/
  poster data those paths already fetch.

### Added

- `backfill_genres` management command - a one-time pass over existing
  titles that already have a TMDB id but no genres yet, fetching and
  attaching them the same way new imports now do. Run it after
  upgrading to pick up genre data for your existing library.

### Changed

- Release years is temporarily disabled ("Coming soon") while its
  underlying data gets double-checked - it's still a reserved tile, not
  removed.

## [0.8.0] - 2026-07-16

### Added

- Redesigned Stats' genre panel into a new full-width "Your top genres"
  section, styled after Simkl's own genre chart: a single segmented bar
  proportioned by each genre's share, with alternating above/below
  labels so narrow segments still get room for a name, MOST/LEAST
  callouts, and a toggle between sizing by title/episode count or by
  total watch time (a new per-genre watch-time aggregation).
- Release years is now its own tile (previously bundled into the same
  panel as genres), unchanged in content.

### Changed

- The Movies/TV Shows/Anime genre-type selector moved from plain text
  tabs to pill buttons inside the new genre panel.

## [0.7.2] - 2026-07-16

### Fixed

- Split by type's pie was rendering with its edge visibly flattened/cut
  off in places - the wedges' radius exactly touched the SVG's own clip
  boundary, and anti-aliasing right at that edge was clipping it. Pulled
  the radius in slightly so the circle has margin to render cleanly.

### Changed

- The hover/default readout under the pie now shows just the percentage
  - dropped the duration ("88d 1h 53m TV"), since that's already shown
  per-type in the All time panel above and was redundant here.

## [0.7.1] - 2026-07-16

### Changed

- Split by type is a full pie again (no center hole) per feedback -
  rebuilt as three filled SVG wedges instead of stroked ring arcs. The
  hover readout (percentage + duration + type) that used to live in the
  donut's center hole now sits just below the chart instead, with the
  legend staying put as a horizontal row beneath that. Hover/tap
  behavior (pop the slice, bold the legend entry) is unchanged.

## [0.7.0] - 2026-07-16

### Added

- Stats' "Split by type" donut is now interactive: hovering (or tapping)
  a slice pops it outward slightly, bolds its legend row, and swaps the
  previously-empty center hole from dead space to that segment's own
  readout ("73% · 87d 17h TV"). With nothing hovered, the center shows
  your largest category by default instead of sitting blank.

### Changed

- Rebuilt the donut as three individually-hoverable SVG arcs (replacing
  the single flat CSS conic-gradient) so each slice can respond on its
  own - this also meant staying a donut rather than a full pie, since the
  hole is what gives the hover readout somewhere to live.
- The legend moved from three stacked rows to one horizontal row
  (Movies · TV shows · Anime) under the chart, and each entry is now
  itself hoverable/tappable too, mirroring whichever slice it represents.

## [0.6.7] - 2026-07-16

### Changed

- Refined Stats' row-alignment pass from 0.6.6: row 2's first and third
  boxes (Genres & release years / Watch activity) are now equal width
  with a narrower middle box (Split by type, whose donut+legend never
  needed as much room); row 3's three boxes (Daily breakdown / Daily
  average / Peak hours) are now a plain equal three-way split. Row 1 is
  unchanged.

## [0.6.6] - 2026-07-16

### Changed

- Stats' three rows each used their own column-width ratio (the top row
  1.7:1:1, the second 1.3:0.8:1, the third a plain equal three-way
  split), so box edges didn't line up between rows even though each row
  was internally fine - the page read as "all over the place" looking
  down it. All three rows now share the same 1.7:1:1 column template, so
  every box's left/right edge lines up cleanly with the row above and
  below it.

## [0.6.5] - 2026-07-16

### Changed

- Per a hand-drawn mockup: Stats' top row (streak / Last 30 days / All
  time) is now one combined bordered card with thin divider lines
  between its three sections, instead of three separate boxes with gaps
  between them - the gaps made the row read as misaligned even though
  each box's own height matched.

## [0.6.4] - 2026-07-16

### Added

- Calendar sidebar now keeps showing a release for 30 days after it
  airs, instead of dropping it from the agenda the instant its release
  time passes - a weekly show no longer disappears the moment Thursday
  ticks over.

### Changed

- Clicking a calendar date with releases now also highlights that
  date's matching entry in the sidebar (violet underline + a subtle
  tint on the whole block), not just the grid cell you clicked -
  previously the grid and sidebar had no visual link between them.

## [0.6.3] - 2026-07-16

### Fixed

- Navigating the Calendar to any past month always rendered an empty
  grid, even for months that genuinely had releases - `calendar_releases()`
  was hardcoded to `release_date >= now`, a filter meant for the sidebar's
  "what's upcoming" agenda, but the month grid reused the same query. The
  grid now queries the specific month being viewed instead, so past
  months show their own releases again (ReleaseSchedule rows aren't
  deleted once their date passes - the data was always there, it just
  wasn't being asked for). The sidebar's agenda is unaffected and still
  only ever shows what's upcoming from now, regardless of which month the
  grid is showing.

## [0.6.2] - 2026-07-16

### Changed

- Calendar sidebar's date groups (Jul 18, Jul 19, ...) were just bold
  text the same size as everything else, so the list of upcoming
  releases read as one undifferentiated block. Restyled each date to
  match History's own day-group header (font-display, underlined),
  making each date's releases read as a clearly separated group -
  today's date header is also now tinted primary, matching the main
  grid's own "today" highlight.

## [0.6.1] - 2026-07-16

### Fixed

- Clicking a date on the Calendar gave no indication it had been
  selected. Today's date now stays selected (violet border) until you
  click a different date, at which point the border moves there instead
  - today keeps its filled circle number regardless, since that's a
  fixed "this is today" marker, not the selection state.

## [0.6.0] - 2026-07-15

### Changed

- History's binge-group tile no longer expands into a dropdown. Per
  feedback, the episode list (previously an expand/collapse chevron
  revealing a chip list) is now a single always-visible segmented
  timeline bar under the poster - one thin segment per episode, in the
  order they were actually watched (not the page's own newest/oldest
  sort), with a hover tooltip on each segment showing that episode's
  number and watched time. No expand/collapse state at all.

## [0.5.4] - 2026-07-15

### Changed

- Reverted the collapsed sidebar's icon image back to a plain "S" letter
  (per feedback preferring that over the app icon) - still in its own
  centered row from the 0.5.3 fix, so it no longer suffers the original
  clipping/placement problem. The browser tab favicon (0.5.2) is
  unaffected and still uses the app icon.

## [0.5.3] - 2026-07-15

### Fixed

- The collapsed sidebar's icon (added in 0.5.2) was crammed into the same
  padded, baseline-aligned row as the expanded "SPOOL" wordmark, leaving
  it only ~20px of space for a 28px image - it rendered clipped and
  undersized. It now has its own centered row sized for the collapsed
  rail, rendering at its full 36px.

## [0.5.2] - 2026-07-15

### Added

- A real browser tab icon (favicon.ico + 16/32px PNGs + an apple-touch-
  icon) - there wasn't one before, so tabs just showed a generic blank
  page icon.

### Changed

- The collapsed sidebar's brand mark was a bare "S" rendered in the
  display font, sitting at the same baseline/padding as the full "SPOOL"
  wordmark it replaces - it looked like stray text, not a logo. It's now
  the same app icon used for the favicon, sized as a proper small badge.

## [0.5.1] - 2026-07-15

### Changed

- History's binge-group tile (the collapsed "S1E215–S1E221 · 7×" card)
  now shows total watch time on the card itself ("7 episodes · 2h 48m"),
  not just the episode count - previously you'd have to expand it and
  sum seven rows yourself to know how long a session actually was.
- The expanded episode list was redesigned from tight table-like rows
  (an episode number, a runtime-looking time column, and a delete button
  each in their own column) to wrapping, lighter pill chips ("S1E221 ×").
  The old time column was actually each episode's watched-at clock time,
  not its runtime, which read as a confusing, easily-misread duration
  figure sitting right next to the real per-episode data - it's now a
  hover tooltip on each chip instead of a persistent column.

## [0.5.0] - 2026-07-15

### Added

- History's tiles can now be removed in bulk: a "Select" toggle in the
  filter bar puts every tile into checkbox mode, and a floating bar
  ("N selected · Delete selected") appears once at least one is checked.
  A collapsed binge-group tile's single checkbox stands in for every
  episode it collapses - checking it counts and deletes all of them at
  once, not just the group card itself.

## [0.4.0] - 2026-07-14

### Added

- Three new Stats panels, per a hand-drawn mockup: "Daily breakdown" (a
  7-day bar chart, today included, peak day labeled with its duration),
  "Daily average" (average watch time per day over the last 7 days, with
  a delta vs. the preceding 7-day period), and "Peak hours" (lifetime
  distribution of watch events across Morning/Afternoon/Evening/Night,
  bucketed by local time of day).

## [0.3.4] - 2026-07-14

### Fixed

- Stats page's bottom row ("Genres & release years" / "Split by type" /
  "Watch activity") wasn't respecting its intended column-width ratio -
  a classic CSS Grid gotcha where a bare `Nfr` track's implicit minimum
  is its content's min-content size, not zero, so a box with wide
  content (the genre pills, the heatmap) could grow past its intended
  share regardless of the fr ratio. Fixed by giving every grid item
  `min-w-0` so the ratio actually governs the layout.

### Changed

- Per a hand-drawn layout request: the streak/stats box now sits in the
  same row as "Last 30 days" and "All time" (previously its own row
  above them), visibly wider than the other two, which are equal width.
  "Total watch time" and "Episodes logged" - previously shown standalone
  in that box - were dropped as redundant now that "All time"'s Combined
  line sits right next to it showing the same totals.

## [0.3.3] - 2026-07-14

### Fixed

- Extended the 0.3.1 badge restyle (solid background, bold text, drop
  shadow) to History's tiles too - the episode/date-range badge and
  media-type badge on single-episode and binge-group cards, and the
  existing episode-count "3×" badge, now all match.

## [0.3.2] - 2026-07-14

### Changed

- Stats page reorganized to a requested layout: "Genres & release years",
  "Split by type", and "Watch activity" now sit side by side in one row
  (in that order) instead of three separate stacked full-width sections,
  matching the streak/watch-time box and the Last 30 days/All time row
  above it. "Split by type"'s donut+legend now stacks vertically to fit
  its narrower column.

## [0.3.1] - 2026-07-14

### Fixed

- The MOVIE/TV/ANIME badge on poster cards was a low-contrast, tiny
  label that blended into the poster art, unlike the rating badge next
  to it. Restyled to match the rating badge's solid background/shadow/
  weight, and bumped both badges up slightly in size.

## [0.3.0] - 2026-07-14

### Added

- Discover grid (Movies & TV / Anime, and "If you like this") poster
  tiles now get the same watched/add-to-list quick actions as library
  poster cards. Since these are TMDB previews with no local Title row
  yet, the first click materializes the title (get-or-create by TMDB id,
  same idiom the existing "Add to Watchlist" button already used) before
  acting - every action after that flows through the normal endpoints.

### Fixed

- Poster cards (Dashboard's carousels especially) could render at wildly
  inconsistent sizes - a short title like "Bleach" as small as 40px wide,
  a long one like "Berserk: The Golden Age Arc - Memorial Edition" as
  wide as 287px. Root cause: `poster_card.html`'s width fallback used
  Django's `default_if_none` filter (changed in 0.2.0 to accommodate one
  call site's `width_class=""`), but an *omitted* template variable
  resolves to an empty string, not `None` - so `default_if_none` never
  substituted the fallback width, and cards without one fell back to
  sizing themselves from their own unconstrained overlaid title text.
  Reverted to `default` (the original, correct filter for this), and
  the one call site that genuinely wants to defer to its grid's own
  sizing now passes a real class (`w-full`) instead of an empty string.
- Strengthened the bottom-of-poster gradient (reaching about halfway up
  the card instead of a thin sliver at the very bottom) so the action
  buttons stay legible against bright poster art.

## [0.2.1] - 2026-07-14

### Fixed

- The poster card list-picker popover (added in 0.2.0) rendered
  overlapping/inside the poster art in a garbled, hard-to-read way on
  small cards — it was positioned against its own tiny trigger button,
  which left it nowhere to go but on top of the ~250px-tall card. Now
  positioned via the button's actual screen coordinates so it floats
  freely above the page instead.
- The sidebar could render fully off-screen (not just collapsed to
  icon-only) at browser widths between roughly 768–900px, a pre-existing
  bug from a mismatch between Tailwind's `md:` breakpoint (768px) and a
  separate hardcoded 900px threshold in the sidebar's own show/hide
  logic — the two are now consistent.

## [0.2.0] - 2026-07-14

### Added

- Poster cards (Dashboard's carousels, Lists) now have a persistent quick-action
  bar — mark as watched, or add/remove from any list — without leaving the
  grid, styled after Trakt's own poster cards. Cards are also a bit bigger
  and grids a bit denser across Dashboard, Lists, Discover, and "If you
  like this."

### Fixed

- `list_detail_items.html`'s `width_class=""` override (meant to defer
  poster card sizing entirely to its grid) never actually worked — Django's
  `default` filter treats an empty string as falsy too, not just a missing
  value, so it was silently ignored.

## [0.1.0] - 2026-07-14

First tracked release — baseline for version tracking itself, covering
everything shipped so far.

### Added

- Milestone celebration banner on the Dashboard (streak/movie-count
  thresholds) and warmer, less generic empty-state copy across
  Dashboard, Calendar, History, and Activity.
- Redesigned title detail rating control: a single draggable fill gauge
  instead of ten individual stars.
- Film-strip perforation accents on the title detail hero and Dashboard
  header, and heavier icon stroke weight across the sidebar/topbar.
- An Ongoing / Ended / Cancelled status badge on the title detail hero,
  sourced from TMDB.
- Calendar and Dashboard's "Up Next" now actually populate with upcoming
  episodes, season premieres, and movie release dates — a nightly job
  syncs this from TMDB for anything you're watching, have watchlisted,
  or have any watch history for.
- App version, shown in the sidebar footer and on the Settings page.

### Fixed

- Calendar/Up Next were scoped to `WatchProgress.status == WATCHING`,
  a status nothing in the app ever actually sets outside Django admin —
  broadened to also cover plain watch history, so shows synced via
  Trakt/Simkl/CSV import that you're mid-way through are correctly
  included.
