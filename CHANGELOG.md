# Changelog

All notable changes to Spool are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`) — patch
for fixes, minor for new features, major for anything requiring a manual
migration/env step or breaking an existing workflow.

## [Unreleased]

## [0.133.0] - 2026-09-24

### Added

- TV and anime episode tiles now show each episode's air date, on both the
  mobile and desktop layouts. It's omitted when TMDB has no date for the
  episode.

## [0.132.2] - 2026-09-24

### Fixed

- Removing a show's only watched episode from History no longer leaves its
  upcoming episodes in Up Next and Calendar. Marking an episode watched
  adds the show to the Watching row, and that entry now goes away with the
  last watched episode. Entries with real player progress are kept.
- Episodes in the Dashboard's Recently Watched row now open the show's page
  on that episode and highlight it, like the History page does.
- Toast notifications now appear below the top navbar instead of over it,
  on both mobile and desktop.

## [0.132.1] - 2026-09-20

### Fixed

- Marking an episode watched in Spool now puts the show on the Dashboard's
  Watching row, pointing at the latest episode watched. Previously only
  shows with progress reported by a player such as Nuvio appeared there.
- Movie cards in the Watching row now use the same watched button style as
  TV and anime cards.
- The Settings → Notifications sync failure toggle is now labeled
  "Trakt/Simkl/Nuvio". Nuvio sync failures already sent a notification
  under that toggle; only the label was missing.

## [0.132.0] - 2026-09-20

### Added

- Movie Details panel now shows the US digital release date from TMDB,
  marked "(upcoming)" while it's still in the future. It's omitted when
  TMDB has no digital date yet, which is common for new releases.

## [0.131.2] - 2026-09-19

### Fixed

- The Dashboard's Watchlist Queue, "Start Watching" row, and "Surprise
  me" only draw from the real, auto-managed Watchlist now - a custom
  list (a chronological marathon order, a curated share, ...) no
  longer leaks its titles into them as if they were watchlisted.

## [0.131.1] - 2026-09-19

### Changed

- Movie cards in the Dashboard's Watching row no longer show an
  add-to-list button next to the mark-watched button, matching the
  single-button layout TV and anime cards already use.

## [0.131.0] - 2026-09-19

### Changed

- Movies in the Dashboard's Watching row now use the same landscape card
  layout as TV and anime, with backdrop artwork, progress information,
  and a mark-watched action, instead of the old portrait poster.

## [0.130.0] - 2026-09-19

### Added

- Person (actor/director) pages now show a "Best Works" row with that
  person's highest-rated credits, above the Acting/Directing/Writing
  sections.

### Fixed

- The filmography grid's clamped view now fades out at the bottom
  instead of cutting off sharply, and a "Show less" button lets you
  collapse an expanded section again.
- The "Your Top Genres" toggle on the Stats page now updates in place
  instead of reloading the page and resetting scroll position, matching
  the Household Leaderboard fix below.

## [0.129.0] - 2026-09-19

### Added

- The Dashboard's mobile header stats and Up Next row now use an edge
  fade with previous/next arrows instead of a visible scrollbar when
  scrolling horizontally.

### Fixed

- The topbar avatar's border now reflects the profile's chosen avatar
  color instead of always showing the theme's orange.
- A TV/anime title's "Watched" button now distinguishes an in-progress
  show (blue "Watching") from a fully completed one (green "Watched"),
  matching the poster card's own watched button.
- Peak Hours on the Stats page no longer shows a redundant percentage
  next to each time bucket's play count.
- The Household Leaderboard's This Week/This Year toggle now updates in
  place instead of reloading the page and resetting scroll position.
- Deleting a title's entire watch history no longer leaves it stuck
  showing as watched in Up Next and Calendar; `WatchProgress` completion
  now re-validates immediately after any History deletion.
- `<button>` and `<select>` elements now consistently show a pointer
  cursor on hover across the app.

## [0.128.0] - 2026-09-18

### Added

- The Dashboard's Watching row now shows a landscape still of the
  in-progress episode instead of the show's cover poster, with a
  "Season Finale" badge when applicable, a mark-watched button on the
  card, and a caption showing episodes and time remaining.

## [0.127.0] - 2026-09-18

### Added

- The Dashboard's "For You" and "Because you watched" rows are replaced
  by three "Recommended for You" rows (Movies, TV, Anime), each based on
  recent watch history for that type via TMDB's similar-title
  recommendations, falling back to genre/provider preferences for
  profiles with no watch history yet.

## [0.126.1] - 2026-09-17

### Fixed

- A show's completion status could stay stuck as Completed even after
  new episodes aired, since it was only re-checked on a fresh watch
  action. The nightly release sync now also re-validates completion and
  downgrades a show back to Watching if it's fallen behind.

## [0.126.0] - 2026-09-17

### Added

- The watched checkmark now distinguishes a TV/anime title that's
  watched but not finished (blue play icon) from one that's fully
  complete (green checkmark), everywhere that button appears.
- The mobile "More" sheet is reorganized, and the Friends section
  collapses into a single toggle row instead of always listing everyone.
- The active tab in the mobile bottom navbar now gets a filled pill
  around its icon and label, matching the desktop nav; the bar itself is
  slightly taller.
- The mobile Movies/TV/Anime type switcher is now centered with an icon
  per option.

### Fixed

- "Series Completed" could incorrectly show on an earlier History/
  Activity entry after a show was later finished - it's now based on
  whether that specific entry actually ended on the real series finale,
  not the show's current state.

## [0.125.0] - 2026-09-17

### Added

- The title Details panel now shows original language and country as a
  full name with a flag, each linking to Discover pre-filtered to that
  language/country; Status now reuses the same colored badge shown in
  the header.
- TV/anime status badges now all get a real color instead of some
  rendering as plain gray.
- A not-yet-tracked TV/anime title's page now offers a "+ Mark as
  Watched" button in its header, matching what movies already had.
- History's binge/catch-up group tiles now show a "Series completed"
  badge and an episode count total, matching the Activity feed.

### Fixed

- Clickable elements built without a real button or link now show a
  pointer cursor on hover.
- Marking only one season of a multi-season show watched no longer
  incorrectly shows as "Series Completed" in History/Activity.
- A not-yet-tracked TV/anime title's "Mark as Watched" popover no longer
  renders clipped on titles with a shorter hero backdrop.
- Discover's `origin_country` filter (previously hardcoded to Japan for
  Anime) is now also available on Movies/TV via a URL parameter, with a
  dismissible "Filtered to X" banner.

## [0.124.0] - 2026-09-15

### Added

- Two-factor authentication (Settings → Security): TOTP via any
  authenticator app, QR code and manual setup, one-time backup codes,
  and a login challenge for any profile with it enabled.
- Sessions now expire after a period of inactivity (`SESSION_COOKIE_AGE`,
  14 days by default) instead of staying signed in indefinitely.
- Settings is reorganized: Import and Export are merged into one tab, a
  new Security tab holds password change and 2FA, Integrations is split
  into Connected Apps and Advanced, and the Admin Server tab is folded
  into Maintenance. Mobile's settings nav is now a dropdown.
- A not-yet-tracked TV/anime title's page now offers a "+ Mark as
  Watched" button in its header, matching movies.
- History's binge/catch-up group tiles now show a "Series completed"
  badge and an episode count total.
- Activity page profile avatars are now clickable, opening the profile
  popup.
- The Dashboard's mobile "Up Next" cards and stat pills now scroll
  horizontally in a single row instead of wrapping onto multiple lines.

### Fixed

- Two CodeQL findings: an insecure temp-file helper in test fixtures,
  and unvalidated `?next=` redirect targets in notification views.
- A Discover tile's watched/list-add popovers no longer get clipped
  inside the tile's own reveal panel.
- On touch devices, tapping a Discover tile now reveals its info/actions
  on the first tap and navigates on the second, instead of always
  navigating straight through.
- The top navbar's mobile/desktop breakpoint moved from 768px to
  1280px, fixing cut-off content on several real tablet widths.
- Removed the unused AI Recommendations settings card and its Gemini
  API key field.

## [0.123.0] - 2026-09-14

### Added

- Movies & TV / Anime discover tiles are redesigned: hovering (or
  keyboard-focusing) a tile reveals title, year, genres, and the
  watched/list actions, with the poster zooming in slightly. Genre names
  are now resolved and shown. The grid is a column wider with tighter
  gaps.
- "Surprise me" jumps to a random title from your visible watchlists,
  available on the Dashboard's Watchlist Queue and the Lists page.

### Fixed

- A discover tile's hover-reveal panel could get stuck visually
  "hovered" after navigating away and back; the reveal no longer depends
  on CSS `:hover`/`:focus-within`.

## [0.122.0] - 2026-09-13

### Fixed

- Movies & TV / Anime pages (and a person's filmography grid) now check
  watched/watchlist state in two batched queries instead of up to ~2 per
  tile, cutting query count and load time substantially on large pages.
- A title's detail/preview page now runs its TMDB lookups (cast, similar
  titles, watch providers) concurrently instead of sequentially,
  reducing time spent outside the database on a cold cache.

## [0.121.0] - 2026-09-08

### Added

- TV/anime titles now get their own header "Watched" button (previously
  movie-only), showing the same rewatch count as the poster card badge,
  with a popover for whole-show and per-season actions. The episode
  browser's own watched checkmark also gained a rewatch count and an
  updated "manage plays" popover.

## [0.120.0] - 2026-09-07

### Added

- Outbound requests to Tenrai (the MyAnimeList data source) are now
  rate-limited to stay under its public-tier request cap, with an
  automatic retry on a 429 response.

## [0.119.0] - 2026-09-07

### Changed

- The MyAnimeList integration (anime filler/recap flags, MAL enrichment,
  season-reconciliation lookups) migrated from Jikan, which has shut
  down, to Tenrai, a schema-compatible successor.

### Fixed

- The season-reconciliation MAL episode-count offset added in 0.118.0
  was silently never finding a match; it's now fixed as part of the
  Tenrai migration.

## [0.118.1] - 2026-09-07

### Fixed

- A "no match" result from the MyAnimeList lookup is now cached for an
  hour, preventing repeated live requests (and hitting rate limits) when
  reconciling a title with several affected episodes.

## [0.118.0] - 2026-09-07

### Added

- `manage.py reconcile_episode_seasons` - a one-time backfill command
  (dry run by default, `--commit` to apply) that remaps episodes
  affected by the season-numbering mismatch fixed below.

### Fixed

- A player reporting its own season split that doesn't match TMDB's
  (e.g. Nuvio splitting one TMDB season into two) could create an
  orphaned episode invisible to the episode browser. Scrobbles and
  Nuvio sync now reconcile the reported season/episode against TMDB's
  real structure, using MyAnimeList's per-cour episode counts to resolve
  the correct episode.

## [0.117.0] - 2026-09-04

### Added

- Dashboard redesign: header stats are now pills, Up Next cards show a
  day pill, "Recommended by Friends" moved into the sidebar as a compact
  carousel, and On This Day/Social Activity rows show a poster
  thumbnail.
- Stats page redesign: the hero card is now four separate cards with a
  computed Completion Efficiency figure; the genre breakdown gained a
  colored legend; Achievements show real progress bars; Taste
  Compatibility is a circular percentage ring; Release Years is now a
  real decade histogram.
- Household Activity page redesign: a day-grouped timeline replaces the
  flat list, with per-member filter pills, pagination, and poster-card
  entries. Binge sessions and bulk list-adds render as their own cards
  with total runtime; a binge ending on a show's finale is called out
  as "Series Completed". Added a Household Leaderboard and a Household
  Top 5 sidebar.
- `seed_demo` now seeds additional household profiles with their own
  watch history, pending recommendations, and on-this-day history.

### Fixed

- The top navbar's center nav could overlap the logo/search and
  notification/profile icons at certain viewport widths.
- TV/Anime detail pages: the season-poster row and episode list are now
  separately labeled "Seasons" and "Episodes".

## [0.116.1] - 2026-09-01

### Fixed

- The full notifications page is now centered in the content area
  instead of sitting flush left.
- That page's "Mark all read"/"Clear all" actions moved into the page
  itself as labeled buttons.

## [0.116.0] - 2026-09-01

### Added

- The notifications panel is redesigned: rows show a poster thumbnail
  and a per-kind icon, unread rows get an accent bar and a dismiss
  button, and rows are grouped under date headers. A new "View all
  notifications" page lists everything beyond what fits in the dropdown.

### Fixed

- The test suite's cache backend switched from `LocMemCache` back to
  `DummyCache`, fixing a cross-test data leak that caused intermittent
  test failures.

## [0.115.1] - 2026-09-01

### Fixed

- A show that drops several episodes at once no longer floods the
  notification bell with one notification per episode; they're now
  collapsed into a single notification per title per day.

## [0.115.0] - 2026-08-31

### Added

- The episode browser's season selector is now a horizontal poster-card
  row (season poster, rating, progress bar) instead of a dropdown.

### Fixed

- The trailer modal's embedded YouTube player no longer fails with
  "Error 153".
- Clicking an episode from Continue Watching, History, or Social
  Activity now lands directly on that episode and briefly highlights
  it.
- Discover pages show 3 posters per row on mobile instead of 2.
- Dashboard rows (Watchlist, Start Watching, Recently Watched, Social
  Activity, On This Day) can now be scrolled horizontally on mobile.
- The episode number badge on episode cards is legible again in some
  conditions where it had blended into the background.

## [0.114.0] - 2026-08-30

### Added

- Movie/TV/Anime detail pages now offer a trailer and a media gallery,
  with a "Watch trailer" button and a "Media" section showing the
  trailer and backdrop stills; clicking a still opens a lightbox.

## [0.113.3] - 2026-08-26

### Fixed

- Dependency security updates: Django 5.2.16 → 5.2.17 and sqlparse
  0.5.5 → 0.6.0, addressing several DoS/ReDoS advisories.
- The Docker image is now a multi-stage build - Node/npm (needed only
  to compile CSS) no longer ships in the final runtime image, removing
  a large tree of outdated JS tooling and its CVEs.
- The Dockerfile now runs `apt-get upgrade` before installing packages,
  so Debian security patches land on every build.

## [0.113.2] - 2026-08-25

### Fixed

- `merge_duplicate_titles --commit` no longer crashes when two
  colliding episodes each already have their own release-schedule row.
- The Maintenance tab's merge-preview/commit toast no longer dumps one
  line per duplicate group into a fixed-width notification; long output
  is now capped with a count.

## [0.113.1] - 2026-08-25

### Fixed

- Production errors (`DEBUG=False`) are now logged to stdout, so they
  show up in `docker compose logs` instead of disappearing silently.

## [0.113.0] - 2026-08-25

### Added

- Every nightly scheduled task now shows up in Settings → Logs with
  what it actually did, not just the worker's own log.

### Fixed

- Every sync/import path could create a duplicate title for a show
  already reclassified as Anime; titles are now matched by TMDB's own
  kind instead of local media type. `merge_duplicate_titles` can clean
  up any duplicates this already created.

## [0.112.0] - 2026-08-24

### Added

- `reclassify_anime_titles` now also runs as a nightly scheduled task,
  not just as a manually-run command.

### Fixed

- The episode browser's per-episode watched button and "Mark episodes"
  popover are no longer hidden on a title's preview page - marking an
  episode watched now materializes the title on the spot.

## [0.111.0] - 2026-08-24

### Added

- Settings → Logs' "Items" column is now a collapsible list of what was
  actually imported, not just a count, covering all sync and import
  paths.

## [0.110.1] - 2026-08-24

### Changed

- The episode browser's "Mark season watched" and "Mark all watched"
  buttons are combined into one "Mark episodes" popover, with a
  "(canon only)" option for anime that skips filler/recap episodes.

## [0.110.0] - 2026-08-23

### Added

- Anime filler/recap badges now fall back to AniFiller for anything
  MyAnimeList can't answer, used strictly as a fallback and never
  overriding a MyAnimeList answer.

## [0.109.1] - 2026-08-23

### Fixed

- Anime added via Discover's Anime tab now correctly materializes as an
  anime title instead of a plain TV title. Run
  `manage.py reclassify_anime_titles` once to fix anime added before
  this fix.

## [0.109.0] - 2026-08-18

### Added

- A read-only Reports API (`/api/reports/`) for external services to
  generate reports from watch history, built for the companion app
  [spool-wrapped](https://github.com/aljaz-h/spool-wrapped). Opt-in per
  profile via Settings → Integrations → Wrapped, authorized by a new
  `ServiceAPIKey` credential type.
- `Title.country`/`studio`/`network`/`cast`/`directors`/`writers` -
  production metadata that powers the Reports API, fetched from TMDB on
  import. Run `manage.py enrich_titles_reports_metadata` once after
  upgrading to backfill it for existing titles.

## [0.108.0] - 2026-08-18

### Changed

- The streaming-service picker no longer dumps TMDB's full ~50-100 entry
  catalog on screen; it shows the 10 most popular with a "Show N more"
  toggle.

### Fixed

- Every fixed-position popover now flips to whichever side of its
  button has room, instead of clipping off-screen near the top or
  bottom edge of the viewport.

## [0.107.0] - 2026-08-18

### Changed

- Title detail's Lists section and the Dashboard's "Recommended to you"
  cards now use the same list-picker popover every poster card has,
  instead of their own simplified controls.

### Fixed

- Every fixed-position popover now closes when the page is scrolled
  instead of staying visually pinned to its original screen position.

## [0.106.0] - 2026-08-15

### Added

- Settings → Integrations' "Custom Player" card now supports up to 5
  named API tokens per profile instead of exactly one.

## [0.105.0] - 2026-08-14

### Changed

- Redesigned the recommendation reply UI: a compact reply button opens
  a small popover instead of a permanently-expanded reaction row. The
  reaction set changed to four options focused on what's actually
  useful to hear back.

## [0.104.0] - 2026-08-14

### Added

- Optional production request/SQL/Python profiling via django-silk, off
  by default (`SILK_ENABLED` in `.env`).

### Fixed

- Startup now fails fast with a clear error if `DJANGO_SECRET_KEY` is
  missing or a placeholder while `DEBUG=False`.
- A handful of accessibility gaps: unlabeled icon-only buttons, missing
  page headings, and a missing nav landmark label.

## [0.103.1] - 2026-08-14

### Fixed

- Mobile search now reliably raises the keyboard on the first tap.
- Opening and closing mobile search no longer desyncs the bottom nav's
  fixed position on scroll.
- Calendar's day grid no longer runs narrower than the page on mobile.

## [0.103.0] - 2026-08-13

### Added

- Recommendations are now a two-way exchange: a recipient can reply
  with a quick reaction chip and/or a short message from the
  Dashboard's "Recommended to you" card, and the sender gets notified.

## [0.102.1] - 2026-08-13

### Fixed

- Four mobile-viewport bugs: Settings' Danger Zone buttons no longer
  wrap mid-word, the Calendar's day-of-week header abbreviates on
  narrow screens, the bottom nav's "More" sheet no longer drags the
  page behind it, and the mobile search button now raises the keyboard
  on the first tap.

## [0.102.0] - 2026-08-13

### Added

- Design-consistency pass: a shared dismissible toast stack replaces
  several templates' own inline banners, a themed tooltip replaces
  native browser tooltips across Settings/Stats/Person/Title Detail,
  and a shared loading spinner replaces ad hoc per-element indicators.
- Interface animations: an off-by-default Settings → Appearance toggle
  enables subtle transitions on actions, popovers, and pagination,
  respecting `prefers-reduced-motion`.

### Fixed

- History's multi-select checkboxes now use the shared themed checkbox
  style.
- `change_credentials`'s success message, and every other page's
  messages, now render consistently through the shared toast.

## [0.100.0] - 2026-08-13

### Added

- Discover preferences: Settings → Preferences gains favorite genres, a
  streaming-services picker, and a region setting, which pre-fill
  Discover's filters and power a personalized "For You" row on the
  Dashboard.

## [0.99.0] - 2026-08-13

### Added

- Blind recommendations: an optional "Send as mystery" toggle hides the
  title behind a mystery card until the recipient opens it.

## [0.98.0] - 2026-08-13

### Added

- Achievements: a Stats page badge grid checked against existing watch
  history and persisted once earned.

## [0.97.0] - 2026-08-13

### Added

- Taste Compatibility: a Stats page panel showing genre overlap between
  you and each other household profile.

## [0.96.0] - 2026-08-13

### Added

- "On This Day": a Dashboard row surfacing titles you watched on
  today's date in a previous year.

## [0.95.0] - 2026-08-13

### Added

- Watchlist time capsule: a nightly job nudges you about titles that
  have sat unwatched on your Watchlist for 6+ months.

## [0.94.0] - 2026-08-13

### Added

- Watch-roulette: a "Spin" button on any list page picks a random
  title, with optional type and max-runtime filters.

## [0.93.2] - 2026-08-12

### Changed

- Mobile bottom nav: swapped Stats for Search, moved Stats into the
  More sheet, and added a Friends list to More.

### Fixed

- Calendar's upcoming-releases panel now has a proper height limit on
  mobile instead of growing the page indefinitely.

## [0.93.1] - 2026-08-12

### Fixed

- Discover pagination now merges up to 9 TMDB pages in parallel instead
  of one after another, reducing worst-case latency. TMDB requests now
  use a shared, connection-pooled session.

## [0.93.0] - 2026-08-12

### Added

- A floating mobile-only bottom navigation bar (Home, Discover,
  Calendar, Stats, More), alongside the existing sidebar/topbar on
  desktop.

## [0.92.5] - 2026-08-12

### Fixed

- List/Watchlist views are now paginated (60 items/page) instead of
  loading every item at once. Drag-reordering still works across pages.
- The nightly release-sync job now fans out per-title instead of
  looping through every title synchronously.

## [0.92.4] - 2026-08-12

### Fixed

- Docker Compose's `web` service now respects `GUNICORN_WORKERS`/
  `GUNICORN_THREADS` instead of silently overriding them with a
  hardcoded worker count.
- Added `CELERY_WORKER_CONCURRENCY` (default 4) so a slow sync task no
  longer serializes behind other queued work.

## [0.92.3] - 2026-08-12

### Fixed

- Removed `sync_all_connected_accounts`, a leftover blanket daily-sync
  task superseded by per-account scheduled tasks.

## [0.92.2] - 2026-08-12

### Fixed

- Added composite database indexes for the four highest-traffic
  profile-scoped lookups.
- Batched `continue_watching()`'s per-show episode-count lookup into a
  single query instead of one per row.

## [0.92.1] - 2026-08-12

### Fixed

- Enabled GZip compression for dynamic HTML responses, not just static
  assets.

## [0.92.0] - 2026-08-12

### Changed

- Split the combined Movies & TV page into separate Movies and TV
  pages/nav entries, each with its own categories and filters.

## [0.91.0] - 2026-08-12

### Fixed

- An accessibility pass: popovers now close on Escape and expose
  `aria-expanded`; search inputs get a visible focus ring; decorative
  icons are hidden from screen readers; several unlabeled form controls
  now have accessible names.

## [0.90.0] - 2026-08-12

### Added

- A generic Scrobble API (`POST /api/scrobble`) - any player or script
  can report watches with a per-profile bearer token.

## [0.89.0] - 2026-08-12

### Added

- Free-text tags on Lists, set at creation or edited any time, with a
  tag filter row on the Lists overview page.

## [0.88.0] - 2026-08-12

### Added

- A "Dropped" watch status - quitting a show partway through can now be
  recorded instead of only being able to delete its progress entirely.

## [0.87.0] - 2026-08-12

### Changed

- The web process now reuses Postgres connections across requests
  instead of opening a fresh one per request.
- Added a GIN index on `Title.external_ids`.
- gunicorn's worker/thread counts are now configurable via
  `GUNICORN_WORKERS`/`GUNICORN_THREADS` and now use threads.

## [0.86.0] - 2026-08-12

### Added

- PWA support - Spool can be installed to a phone's home screen and
  runs in a standalone window. A conservative service worker only ever
  caches static assets; page/API data always hits the network.

## [0.85.0] - 2026-08-12

### Fixed

- Movies & TV and Anime's "hide watched/watchlisted" Display filter no
  longer leaves near-empty pages as you paginate.

## [0.84.3] - 2026-08-12

### Changed

- The Calendar's month switcher now sits centered in the same row as
  the type filter.

## [0.84.2] - 2026-08-12

### Changed

- The Calendar's Both/Watching/Watchlist source filter is temporarily
  disabled and always shows everything.

### Fixed

- The Calendar's agenda sidebar now scrolls to today's date on load.
- Clicking a date on the Calendar grid no longer scrolls the agenda
  sidebar past the target date's entries.

## [0.84.1] - 2026-08-12

### Changed

- The Import Data file picker is now custom-styled to match the rest
  of the site.

## [0.84.0] - 2026-08-11

### Added

- A movie/TV/anime's TMDB rating now shows on its preview page too, not
  only after it's tracked.

### Changed

- Dashboard's "Up Next" card now collapses multiple episodes of the
  same show releasing on the same day into one card.
- Dashboard's "Recently Watched" cards are bigger and fall back to
  TMDB's episode name when the local one is blank.

## [0.83.2] - 2026-08-11

### Changed

- The profile photo upload's file picker is now custom-styled to match
  the rest of the site.
- A friend's profile popup now shows "Member since" next to their role.

## [0.83.1] - 2026-08-11

### Changed

- The profile dropdown's Settings and Log out entries now show an icon
  next to their label.

## [0.83.0] - 2026-08-11

### Added

- History's binge-grouped episode tiles now show a sync-source badge,
  covering Simkl and Trakt rows too, not just Nuvio.

### Changed

- Removed the redundant MOVIE/TV badge from Movies & TV/Anime's own
  grid tiles, since every tile there is already the one type the page
  picked.

### Fixed

- The streak counter no longer resets to 0 just because you haven't
  watched anything yet today.

## [0.82.3] - 2026-08-11

### Fixed

- The Movies & TV/Anime and History Filters panels now close when the
  page is scrolled instead of staying visually stuck at their original
  position.

## [0.82.2] - 2026-08-09

### Fixed

- The header bell's unread-count badge now updates immediately after
  marking a notification read or clearing them all.

## [0.82.1] - 2026-08-09

### Changed

- Runtime bucket labels shortened for clarity.
- Genres in the Filters panel no longer scroll in a boxed
  sub-container; they wrap freely.

## [0.82.0] - 2026-08-09

### Added

- Redesigned the sign-in page: a "Keep me signed in" checkbox, a
  password visibility toggle, self-service "locked out" guidance in
  place of an email reset flow, and a footer showing the instance's
  version.

## [0.81.1] - 2026-08-09

### Changed

- Genre/Year/Runtime pills in the Filters panel use a darker unselected
  background, matching the other filter controls.

## [0.81.0] - 2026-08-09

### Added

- Marking something as watched for the first time now asks when:
  "Watched now", "On release date", or "Other date".
- `tmdb.discover_by_decades()` - merges one `/discover` call per
  selected decade so picking several decades at once shows a genuine
  interleaved mix instead of one burying the other.

### Changed

- The Movies & TV/Anime Filters panel: Runtime is now toggle buttons
  instead of a slider, and Year is now a multi-select decade chip row
  instead of a slider.
- History's Filters button now opens the same dropdown popover style as
  every other page's Filters panel.

## [0.79.1] - 2026-08-09

### Changed

- The Collection row now includes the movie you're currently viewing
  alongside its siblings, instead of omitting it.

## [0.79.0] - 2026-08-09

### Added

- A "Collection" row on a movie's detail/preview page, sourced from
  TMDB, shown whenever the movie belongs to a franchise with other
  entries.

## [0.78.1] - 2026-08-07

### Changed

- Rating pills are bigger and show each service's actual logo instead
  of a plain color dot.

## [0.78.0] - 2026-08-07

### Added

- The rating row now fills itself in automatically once a title's first
  MDBList fetch completes.

### Changed

- Rating pills redesigned to match the rest of the page; each provider
  now shows on its own native scale instead of a normalized score.

## [0.77.0] - 2026-08-07

### Added

- A "Clear" link next to each configured Trakt/Simkl/TMDB/MDBList
  credential in Server Integrations, for removing a server-stored
  override.

### Changed

- A title's rating row now shows only IMDb/Rotten Tomatoes/Metacritic/
  Trakt as icons, moved above the description.

### Fixed

- Pressing Enter in a Server Integrations field no longer submits the
  wrong "Test connection" button.
- Saving or testing a credential no longer re-renders the field blank.

## [0.76.2] - 2026-08-07

### Changed

- Server Integrations' inline "Test" buttons now match their input
  field's height and use the app's regular button style.

## [0.76.1] - 2026-08-07

### Changed

- Server Integrations' "Test connection" buttons moved inline next to
  each field, and secret/API key fields gained a reveal toggle.

## [0.76.0] - 2026-08-07

### Added

- Optional supplementary ratings from MDBList (IMDb, Rotten Tomatoes,
  Metacritic, and more) alongside the TMDB rating. Configure a free
  MDBList API key in Admin Dashboard → Server Integrations to enable
  it.

## [0.75.1] - 2026-08-06

### Fixed

- A title imported before this app tracked which TMDB catalog it came
  from could permanently show as untracked on Trending/Popular/similar
  rows, even though it was correctly marked watched in History.

## [0.75.0] - 2026-08-06

### Added

- The episode browser's watched checkmark now offers a menu once an
  episode is watched (view plays, watch again, remove last/all
  watched), instead of always logging another play.
- "Mark season watched"/"Mark all watched" flip to "Unmark" once every
  episode in that scope is already watched.

## [0.74.0] - 2026-08-06

### Added

- Notifications get a "For you"/"System" toggle to separate release and
  recommendation notifications from sync-failure/update-available ones.

### Changed

- Settings → Logs now updates itself live instead of requiring a
  manual refresh.
- Marking or unmarking a watch now updates the title page's "Your
  history" card immediately.

## [0.73.0] - 2026-08-05

### Changed

- Settings' left-hand nav now has an icon next to every item.
- "Keep logs for" moved from the Logs tab header into the Maintenance
  tab.

## [0.72.0] - 2026-08-05

### Added

- Settings → Logs gets a proper filter panel: Profile / Action Type /
  Provider / Status pill filters and a date range, replacing the old
  plain dropdown pair.

## [0.71.0] - 2026-08-05

### Added

- Settings → Logs: a "Keep logs for N days" field - a nightly job
  prunes old sync/import log rows past that age.

### Changed

- Settings → Logs now paginates at 20 entries per page instead of 50.

## [0.70.0] - 2026-08-05

### Added

- Settings → Server Integrations: a "Test connection" button next to
  each credential field, reporting success/failure without saving.
- Settings → Profiles: the server owner can now reset another
  profile's password directly.
- Settings → Maintenance: one-click buttons for `merge_duplicate_titles`
  and the TMDB backfill management commands, previously reachable only
  via the command line.
- Search now has a trigram index on title name for Postgres
  deployments, speeding up substring matching on larger libraries.

### Changed

- Images across the app now render as `<img loading="lazy">` instead
  of a CSS background image, so images below the fold no longer all
  load eagerly.

### Fixed

- Saving Server Integrations credentials, or running any Maintenance
  action, no longer bounces the admin back to the Profiles tab.

## [0.68.0] - 2026-08-05

### Fixed

- History's day-grouped pagination no longer loads a profile's entire
  watch history into memory before paginating.
- TMDB runtime/episode-count lookups now go through the same cache
  every other TMDB lookup uses.
- Stats page queries for streak and watch-time breakdown are now
  batched instead of issuing one query per type.
- Poster images in grid/carousel tiles now request smaller TMDB image
  sizes, cutting page weight.

## [0.67.0] - 2026-08-05

### Changed

- Upgraded Django 5.1.15 → 5.2.16 LTS.

## [0.66.0] - 2026-08-05

### Added

- Rate limiting on login and password/credential-change endpoints.
- CSV/JSON/zip import now enforces a 50MB upload size limit.

### Changed

- Trakt/Simkl OAuth tokens and server-entered credentials are now
  encrypted at rest.
- `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_SSL_REDIRECT`,
  and HSTS are now configurable via `.env`.
- CSV export neutralizes formula-injection characters in exported
  title names; CSV/JSON/zip import validates and truncates fields
  before they reach the database.
- Bumped `cryptography` to 50.0.0.

### Security

- Full access-control/IDOR review of owner-only pages and
  profile-scoped data - no issues found.

## [0.65.3] - 2026-08-05

### Fixed

- Trakt/Simkl import now uses the TMDB id Trakt/Simkl already provide
  instead of re-deriving it with a fuzzy search, fixing titles that
  showed as unwatched on the Discover grid despite being in History.

## [0.65.2] - 2026-08-02

### Fixed

- Movies & TV / Anime's results grid no longer leaves a stray tile or
  two dangling alone on the last row.

## [0.65.1] - 2026-08-02

### Fixed

- Calendar's agenda sidebar no longer grows unbounded when it has more
  releases than the grid has room for.
- The refresh button's tooltip now makes clear the sync runs in the
  background and won't be reflected until the page is reloaded.

## [0.65.0] - 2026-08-02

### Added

- Calendar: a manual refresh button kicks off the release sync
  immediately instead of waiting for its nightly run.

### Changed

- Calendar's source filter is now a segmented tab control matching the
  rest of the app.
- The agenda sidebar now stretches to match the calendar grid's actual
  height.

## [0.64.1] - 2026-08-02

### Added

- Episode browser: an upcoming episode now shows a countdown pill
  based on its TMDB air date.

## [0.64.0] - 2026-08-02

### Changed

- Calendar's release sync now pulls a TV show's whole current season
  instead of only TMDB's single "next episode". Movie releases are now
  synced regardless of whether the date has already passed.

## [0.63.1] - 2026-08-02

### Fixed

- Nuvio sync now backfills the History "N" source marker onto rows
  logged before the marker existed.

## [0.63.0] - 2026-08-02

### Added

- History rows synced from Nuvio now carry a small "N" marker so
  they're visually distinguishable from other sources.
- Dashboard's Social Activity row: episode watches now show a
  season/episode pill on the poster.

## [0.62.3] - 2026-08-02

### Changed

- Dashboard's Watching row: the progress bar now sits above the title
  as a filled pill with its caption centered inside it.

## [0.62.2] - 2026-08-02

### Changed

- Person detail page redesign: header and personal stats now live in
  one unified card. Each filmography section shows only its first two
  rows by default, with a "Show all N credits" button to reveal the
  rest.

## [0.62.1] - 2026-08-02

### Fixed

- A movie and a TV/anime title sharing the same raw TMDB numeric id
  could get treated as the same title when matched against the local
  library; matching now also checks the stored TMDB kind (movie vs.
  TV).

## [0.62.0] - 2026-08-02

### Added

- Person detail page (`/person/<tmdb_id>/`) - click through from any
  cast or director credit to see their bio/photo alongside
  household-specific stats and a filmography grouped by
  Acting/Directing/Writing.

### Changed

- Cast/director entries on the title detail page are now links when
  TMDB has a person id for them.

## [0.61.6] - 2026-08-02

### Changed

- Removed the redundant watched checkmark on episode thumbnails in the
  episode browser.
- The "Details" panel on a show/anime's title page now sits between
  Cast and Episodes.

## [0.61.5] - 2026-08-01

### Fixed

- Horizontally-scrolling card rows now use a thin, theme-colored
  scrollbar instead of the raw OS one.

## [0.61.4] - 2026-08-01

### Removed

- The "Not in your library yet" banner on preview title pages.

## [0.61.3] - 2026-08-01

### Changed

- Title detail hero now sits flush against the navbar and no longer
  has film-strip perforation borders.
- "If you like this, check out" is now a single horizontally scrolling
  row instead of a wrapping grid.

## [0.61.2] - 2026-08-01

### Fixed

- Title detail hero's full-bleed edges left a growing gap on wide
  monitors; corrected the margin so it cancels out at every width.

## [0.61.1] - 2026-08-01

### Added

- A "Details" panel below Cast (status, original language, budget,
  revenue, production companies, country - whichever the title
  actually has).

### Changed

- Title detail page's hero backdrop is now full-bleed instead of
  sitting in a padded panel.
- "If you like this, check out" now follows the new Details panel
  immediately.
- Sidebar's "Recommend to" list now caps at 4 visible people with a
  "+N more" toggle.

## [0.61.0] - 2026-08-01

### Added

- Title detail page's synopsis now has a "Description" heading above
  it.

### Fixed

- Trakt/Simkl/Nuvio sync could each create a duplicate title for one
  already synced through a different provider; all three now check for
  an existing title matched by TMDB id first. A new
  `manage.py merge_duplicate_titles` command cleans up any duplicates
  already created.

## [0.60.5] - 2026-07-31

### Changed

- The episode browser's "Mark season watched"/"Mark all watched"
  buttons now match the season dropdown's height.
- An episode card's per-episode watched button is now a box that fills
  the available height, matching the poster grid's own watched button.

## [0.60.4] - 2026-07-31

### Added

- History tiles' delete button is now always visible instead of
  hover-only. Binge-group tiles get their own delete button, guarded
  by a confirm dialog stating the exact play count.

## [0.60.3] - 2026-07-31

### Changed

- History now paginates by calendar date (10 dates per page) instead
  of by rendered tile count.

## [0.60.2] - 2026-07-31

### Fixed

- History paginated by raw watch-event rows instead of the tiles
  actually rendered, so a heavy binge day could eat an entire page's
  budget. Pagination now operates on the grouped tiles themselves.

## [0.60.1] - 2026-07-31

### Added

- A dismiss option on Dashboard Watching tiles for entries abandoned
  partway through, which nothing can auto-detect. Removes only the
  progress row; watch history is untouched.

### Fixed

- Nuvio-synced "Watching" entries could sit stuck near-finished
  indefinitely; an item within 2 minutes of its own duration is now
  treated as finished and marked completed.

## [0.60.0] - 2026-07-31

### Added

- Nuvio Cloud integration - a third sync source alongside Trakt/Simkl,
  per-profile, pulling watch history and continue-watching progress via
  email/password login.
- New `tmdb.find_by_imdb_id` - matches a title by IMDb id directly,
  used when Nuvio hands over an IMDb id.

### Note

- Nuvio has no documented API; this integration is built from a
  third-party open-source reference implementation and is unverified
  against a live account.

## [0.59.1] - 2026-07-31

### Added

- Import Data now accepts `.json` and `.zip` files, not just `.csv`,
  including Trakt's own export zip.
- Rows sourced from Trakt-shaped JSON/zip imports now carry a Trakt id,
  so they dedupe against a title already synced via Trakt OAuth.

### Fixed

- Importing a real Trakt export zip could fail partway through and
  miss most rewatch history. Non-history JSON files in the zip are now
  skipped entirely, and imports over 500 rows now run in the background
  instead of inside the request, avoiding request timeouts.

### Note

- The exact JSON structure inside a real Trakt export zip is
  unverified against a live export; a mismatched file will show as
  parse errors in the preview step rather than importing wrong data.

## [0.58.1] - 2026-07-31

### Changed

- Settings → Logs is wider now and gained a profile filter and a
  newest/oldest sort toggle.
- The merged sync/import/export/connect feed now looks back over the
  most recent 1000 rows per table instead of 200.

## [0.58.0] - 2026-07-30

### Added

- Settings → Admin → Logs - a new tab that broadens the old Sync Log
  page into one paginated feed across every profile, covering
  Trakt/Simkl syncs and connect attempts, and CSV import/export, each
  with status and an expandable error message.
- `/settings/sync-log/` now redirects to Settings → Logs.

## [0.57.0] - 2026-07-30

### Added

- `manage.py seed_demo` - populates a disposable dev database with
  realistic demo data.
- `setup.sh`/`setup.ps1` - one-shot install scripts that write a
  working `.env` and bring the Docker Compose stack up.
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`.
- A GitHub Pages landing page and real screenshots.

### Changed

- `README.md` rewritten with a banner, badge row, table of contents,
  and a Screenshots section.

## [0.56.8] - 2026-07-29

### Changed

- Stats page hero card's streak/movies/shows items now spread across
  the full card width instead of clumping on the left.

## [0.56.7] - 2026-07-29

### Changed

- Movies-watched and shows-completed on the Stats page hero card are
  now two separate icon-badged items instead of one combined line.

## [0.56.6] - 2026-07-29

### Changed

- The Stats page's hero card now uses a bold typographic headline for
  the streak instead of a dial.

## [0.56.5] - 2026-07-29

### Changed

- Reworked the profile popup's streak card into a bold typographic
  headline with a "Personal best N days" line and a clean stats row
  below it.

## [0.56.4] - 2026-07-29

### Changed

- Applied the same days-first display flip to the profile popup's
  "Combined" rows as the Stats page change in the previous release.

## [0.56.3] - 2026-07-29

### Changed

- Stats page's "Combined" rows now lead with a whole rounded day count
  and show the precise hour/minute figure in parentheses.

## [0.56.2] - 2026-07-29

### Changed

- "Social Activity" cards are back to the normal vertical poster
  style, with the watcher pill overlaid on the poster's corner;
  "Recently Watched" keeps the horizontal still-image cards.

## [0.56.1] - 2026-07-29

### Added

- New `watch_event_card.html` partial backing Recently Watched and
  Social Activity.

### Changed

- "Recently Watched" no longer dedupes by title - a binge now shows as
  separate cards, each using that episode's own still image.
- "Recently Watched" and "Social Activity" cards are now horizontal,
  matching an episode still's aspect ratio.
- "Social Activity" cards now show a small avatar and name pill in the
  top-left corner.
- Single-row carousels now fade to the page background at the right
  edge instead of hard-clipping the boundary card.

## [0.56.0] - 2026-07-29

### Added

- Three new Dashboard rows, replacing "Because you watched" (disabled,
  not removed): "Start Watching" (watchlist titles worth starting
  now), "Recently Watched", and "Social Activity".

## [0.55.2] - 2026-07-29

### Changed

- Dashboard "Up Next" now shows 4 cards instead of 3, wrapped in one
  full-width tile.
- Dashboard's footer stats bar is now wrapped in its own tile too.
- Replaced the dotted section dividers on the Dashboard with a plain
  thin line.

## [0.55.1] - 2026-07-29

### Changed

- Dashboard's "Up Next" cards: the release-day label now sits as a
  third line under the episode caption instead of on the card's far
  right.

## [0.55.0] - 2026-07-28

### Changed

- Redesigned the top of the Dashboard: removed the 4 stat tiles, day
  streak now shows as a pill, "Up Next" is its own full-width row,
  "Recommended to you" now renders as poster cards, Watchlist shows as
  many newest items as fit one row, and movies/shows/watch-time moved
  into a closing footer bar.

## [0.54.6] - 2026-07-28

### Changed

- Title detail page: added a bold divider dot and bolder styling
  between the release date and the runtime/season count.

## [0.54.5] - 2026-07-28

### Changed

- Title detail page: genres now render as individual colored badges
  instead of a comma-separated list.
- Dropped the redundant Ended/Cancelled/Ongoing text from the air-date
  row for shows, since that status is already shown as its own badge.

## [0.54.4] - 2026-07-28

### Changed

- Title detail page: "N seasons · N episodes" is now "N seasons (N
  EP)".

## [0.54.3] - 2026-07-28

### Fixed

- Moved a misplaced divider dot on the title detail page so it sits
  between the tags and the genre list, not between the tags
  themselves.

## [0.54.2] - 2026-07-28

### Changed

- Added a divider dot between the age rating and language tags on the
  title detail page.

## [0.54.1] - 2026-07-28

### Changed

- Split the title detail page's metadata into two rows to reduce
  clutter: age rating/language/genres in one row, release info in the
  row below.

## [0.54.0] - 2026-07-28

### Added

- Movie and TV/Anime detail pages now show release/air date info next
  to the title, with a status-aware summary for shows (first-aired
  date, full aired span, or scheduled premiere).

## [0.53.0] - 2026-07-28

### Changed

- Moved the Watched/Watchlisted "Display" preference into the Filters
  panel as a 3-way eye-icon toggle instead of Settings → Preferences.

## [0.52.2] - 2026-07-28

### Fixed

- The Filters dropdown (and the topbar's notification/friends/profile
  dropdowns) no longer closes itself the instant you click anything
  inside it.

## [0.52.1] - 2026-07-28

### Changed

- Genres, Year, Runtime, and Rating are now each their own row inside
  a "Discover" section; Language/Availability/Age Rating/Status
  collapse into an "Access" section.

### Fixed

- The Filters panel is now a dropdown popover instead of a sliding
  sidebar drawer, fixing blurry text rendering on Windows Chrome/Edge.

## [0.52.0] - 2026-07-28

### Changed

- Redesigned the Movies & TV/Anime Filters drawer into two collapsible
  "Discover"/"Access" sections, with Apply/Clear pinned to the bottom
  while scrolling.
- Moved the "Display" controls out of the Filters drawer into Settings
  → Preferences as a persisted per-profile preference.

## [0.51.0] - 2026-07-27

### Added

- Anime title pages now show a MAL score badge, the native Japanese
  title, and the studio/source material alongside TMDB's own details.

## [0.50.0] - 2026-07-27

### Added

- Anime episodes now show a Filler or Recap badge in the episode
  browser, sourced from Jikan (an unofficial MyAnimeList API).
- The sidebar now credits TMDB and Jikan/MyAnimeList as data sources.

## [0.49.0] - 2026-07-27

### Added

- A list's own creator can now share/unshare it with the household at
  any time instead of only at creation.

### Changed

- Dragging a title to reorder a list now reorders live as you drag,
  with a smooth slide animation.

## [0.48.1] - 2026-07-27

### Fixed

- A stray developer comment above the Lists detail page was rendering
  as visible text at the top of the page.

## [0.48.0] - 2026-07-27

### Added

- Lists gained the same All/Movies/TV/Anime toggle and Filters drawer
  as History, plus a "Manual order" sort option.
- Titles in a list can now be manually reordered by dragging them
  (while unfiltered).
- The instance owner can feature any shared list on the Dashboard,
  surfacing it in a new "Featured Lists" rail.

## [0.47.1] - 2026-07-27

### Fixed

- History's Filters button now shows a live active-filter dot.
- Switching the All/Movies/TV/Anime toggle no longer silently drops an
  applied Period/Sort filter.

## [0.47.0] - 2026-07-27

### Added

- History gained a search box next to the All/Movies/TV/Anime toggle.
- History's Period and Sort dropdowns moved into a Filters drawer.
- The Sort filter gained "Most watched"/"Least watched", ranking
  titles by play count.

## [0.46.0] - 2026-07-27

### Added

- The notifications panel gained a "Clear all" action alongside "Mark
  all read".

### Changed

- The notifications panel is slightly wider.

## [0.45.0] - 2026-07-27

### Added

- TV shows and anime now get the same "watched ×N" rewatch counter
  movies already had, based on the least-rewatched episode.
- Clicking into any untracked movie/show/anime now shows the same
  Lists chip picker a tracked title's page uses.
- The episode browser now shows each episode's own runtime, and the
  season's total runtime.

## [0.44.1] - 2026-07-27

### Fixed

- The Year/Runtime/Rating range sliders' handle-crossing fix from
  0.44.0 didn't actually work in practice; fixed properly by binding
  each handle's min/max to the other's live value.
- The Display panel's "Dim" opacity was too subtle; lowered further.

## [0.44.0] - 2026-07-27

### Added

- The Movies & TV/Anime filter panel gained Availability, Status
  (TV/Anime only), and a Display section controlling how
  already-watched/watchlisted titles show up in results.

### Fixed

- The Year/Runtime/Rating range sliders let you drag one handle past
  the other, producing an inverted range; each handle now clamps
  against the other.

## [0.43.0] - 2026-07-26

### Added

- Title detail pages now show an age rating badge next to the language
  badge.
- The Movies & TV filter panel has an Age Rating filter (movies only).

### Fixed

- Anime browsing could surface explicit content; browsing now excludes
  TMDB's hentai/ecchi/adult keyword tags server-side. This reduces but
  doesn't guarantee against it, since some titles carry no matching
  tag.

## [0.42.0] - 2026-07-26

### Added

- Search now tolerates typos via a spelling-corrected retry merged in
  behind direct results.
- Search now understands a trailing year to disambiguate a same-named
  movie/show.
- The search results page has an All/Movie/TV/Anime tab filter.

### Changed

- Added a new dependency, `pyspellchecker`, for the typo-correction
  above.

## [0.41.0] - 2026-07-26

### Changed

- Reworked the title detail page for mobile: the poster/title header
  now stacks instead of squeezing side by side, episodes render as a
  compact row, the rating control shrinks and wraps, and content
  padding is reduced.

## [0.40.0] - 2026-07-26

### Added

- A search button now shows up on mobile/tablet, dropping a full-width
  search bar under the header.

### Fixed

- The topbar's icon cluster no longer drifts to the middle of the
  header on mobile.
- Tightened the gap between the topbar's icons on mobile/tablet.

## [0.39.1] - 2026-07-26

### Fixed

- A show watched to completion entirely through the episode browser
  now correctly shows the green watched checkmark on poster cards.

## [0.39.0] - 2026-07-25

### Added

- The episode browser can now mark a whole season, or a whole show, as
  watched in one click.
- The season picker is now a custom dropdown showing every season's
  own TMDB rating.

### Changed

- A show's title page no longer has the single "+ Mark as Watched"
  header button a movie gets; shows use the new season/whole-show
  actions in the episode browser instead.

## [0.38.0] - 2026-07-25

### Added

- TV/anime episode tiles now show TMDB's own rating for that episode,
  and the season header shows the average.

### Changed

- The episode browser now shows up on a TV/anime title's preview page
  too, read-only until the title is added to your library.

## [0.37.0] - 2026-07-25

### Changed

- The topbar's Friends dropdown "Active X ago" badge now reflects real
  app usage (`Profile.last_seen_at`) instead of last watch timestamp,
  which could be a backdated import. **New migration** - run it as
  usual on upgrade.
- The title detail page's "Watched" header button now opens the same
  rewatch/undo/history menu the poster card's watched button has.

## [0.36.0] - 2026-07-25

### Added

- The poster card's watched checkmark now shows a "×N" play-count
  badge once watched more than once.
- Clicking an already-watched title's checkmark now opens a menu (view
  plays, watch again, remove last/all watched) instead of silently
  logging another play.
- The History page can now be filtered to a single title via
  `?title=<id>`.

## [0.35.1] - 2026-07-25

### Changed

- Constrained and centered the Settings page instead of leaving it
  hugging the left edge with a large empty gap on wide screens.

## [0.35.0] - 2026-07-25

### Changed

- Merged My Profile, Settings & Import, and Admin Dashboard into a
  single Settings page with a left sidebar, switching between sections
  instantly.
- The "share my activity" privacy toggle moved from its own card into
  the Account tab.

## [0.34.1] - 2026-07-24

### Changed

- Dropped the "self-hosted" subtitle next to the SPOOL wordmark and
  sized the wordmark up slightly.

## [0.34.0] - 2026-07-24

### Changed

- Removed the "AI Pick" Gemini mood-search box from the Dashboard,
  since it needed a configured API key to do anything.
- "Recommended to you" is now its own standalone section with poster
  art, sender avatar, a relative timestamp, and a one-click "+ Add to
  Watchlist" action alongside dismiss.
- Adding a recommended title to the Watchlist no longer dismisses the
  recommendation - it stays pending until the title is actually
  watched.

## [0.33.1] - 2026-07-24

### Fixed

- Docking "Up next" beside the Watchlist carousel broke horizontal
  scrolling for large watchlists; constrained the column so the
  carousel scrolls in place again.

## [0.33.0] - 2026-07-24

### Changed

- Reorganized the Dashboard into purpose-driven sections separated by
  a visible divider.
- Merged "What should I watch?" and "Recommended to you" into a single
  AI Pick module.
- Removed "Recently added to lists", which duplicated the Watchlist
  row above it.
- The "Watching" section now disappears entirely when nothing's in
  progress.
- "Up next" now sits docked beside the Watchlist carousel.

## [0.32.7] - 2026-07-24

### Changed

- The poster card action bar's "marked" state now shows a tinted
  background pill behind the icon (green for watched, amber for
  on-a-list) instead of only a color change.

## [0.32.6] - 2026-07-24

### Changed

- The poster card action bar is now opaque dark gray and sits below
  the poster instead of floating translucent over the art. Removed the
  redundant title caption that used to sit on the poster.

## [0.32.5] - 2026-07-24

### Changed

- Poster cards traded their floating circular icon buttons for a
  full-width flat action bar along the poster's bottom edge.

## [0.32.4] - 2026-07-24

### Changed

- Stats' Peak Hours widget now has a caption explaining what its
  counts mean, and a hover tooltip on each number.

## [0.32.3] - 2026-07-24

### Fixed

- TV/anime watch time was silently undercounted whenever TMDB's
  show-level episode length was missing (common for anime and foreign
  shows). Added a fallback that pulls each episode's own runtime from
  TMDB's season/episode endpoint. **Re-run
  `python manage.py backfill_completion`** if your TV/anime total
  watch time looks too low.

## [0.32.2] - 2026-07-24

### Changed

- Reverted the SPOOL logo back to its single-line layout, removing the
  live clock.
- Moved the topbar's vertical divider to sit between the logo and the
  search bar.

### Fixed

- Topbar's center nav pills were visibly off-center; rebuilt the
  header as a 3-column grid so the center column is genuinely
  centered.

## [0.32.1] - 2026-07-24

### Changed

- Desktop topbar's profile trigger is now a pill (avatar, name,
  chevron) instead of a bare avatar circle.

## [0.32.0] - 2026-07-24

### Changed

- Redesigned the desktop topbar's nav into a centered pill/segmented
  control, with a plain rounded search bar and a live clock under the
  logo.

## [0.31.3] - 2026-07-22

### Changed

- Neutral secondary buttons switched from a bordered/outlined look to
  a soft filled style.

## [0.31.2] - 2026-07-22

### Changed

- Moved Notifications and Privacy from Settings & Import to My
  Profile.

## [0.31.1] - 2026-07-22

### Added

- Settings & Import: a "Sync now" button on Trakt/Simkl for an
  immediate one-off sync.
- Admin Dashboard: profiles can now be demoted back from owner to
  member.

### Changed

- Admin Dashboard's Profiles card: the Promote/Remove text links are
  now icon buttons with tooltips.

## [0.31.0] - 2026-07-21

### Added

- Settings & Import: a personal Timezone dropdown, so household
  members in a different timezone see their own local times.
- Admin Dashboard: an Activity Log recording profile
  creation/removal/promotion, and a "Promote" control for owner-level
  access.
- My Profile: a Danger Zone with self-service account deletion for
  Members.

### Changed

- Settings & Import's "Import & Export" card is renamed to "Connected
  Apps".
- Trakt/Simkl's "Connect" button is disabled with an explanatory note
  when the server owner hasn't configured credentials yet.
- Moved the version footer from Settings & Import to Admin Dashboard's
  Server card.

## [0.30.0] - 2026-07-21

### Added

- My Profile: an optional bio field, a "Member since" date, and
  Trakt/Simkl connected-status badges.
- My Profile: a live thumbnail preview of a chosen photo before
  saving.
- Change password: a show/hide toggle on all three password fields.

### Changed

- My Profile's "Remove" photo button is now a red trash-can icon.

## [0.29.8] - 2026-07-21

### Changed

- Moved the desktop topbar's search bar back next to the logo and
  widened it slightly.

## [0.29.7] - 2026-07-21

### Changed

- The desktop topbar's search bar now shows a search icon and a
  divider before the input text.

## [0.29.6] - 2026-07-21

### Changed

- Reordered the desktop topbar: the search bar now sits between the
  nav links and the icon cluster.

## [0.29.5] - 2026-07-21

### Fixed

- Topbar's icon cluster sat left-of-center on mobile instead of
  hugging the right edge.

## [0.29.4] - 2026-07-21

### Fixed

- Topbar's Notifications and Friends dropdowns ran off the left edge
  of the screen on mobile; switched to viewport-clamped positioning.
- Several discover/similar grids showed only a single oversized card
  per row on mobile; added a smaller mobile-first floor width.
- Calendar's month grid was cramped and illegible on mobile; cells
  below `sm:` now show a single presence dot instead of thumbnails.

## [0.29.3] - 2026-07-21

### Fixed

- Uneven spacing between the topbar's bell/Friends/avatar icons.

## [0.29.2] - 2026-07-21

### Added

- History's day-group headers now show total watch time next to the
  item count.

## [0.29.1] - 2026-07-21

### Changed

- The topbar's household-member avatar circles are now a single
  Friends icon that opens a dropdown listing each person's avatar,
  name, and last-active time.

## [0.29.0] - 2026-07-20

### Added

- The detail page's "Mark as Watched" header button is now a real
  watched/unwatched status toggle instead of a static call-to-action.
- A not-yet-tracked preview page now offers "Mark as Watched"
  independently of "Add to Watchlist".

## [0.28.1] - 2026-07-20

### Changed

- Redesigned the title detail page's Lists and Recommend To cards with
  filled/outlined chip toggles and real profile avatars.

## [0.28.0] - 2026-07-20

### Fixed

- Recommending a title no longer requires adding it to a watchlist
  first.
- Sending a recommendation now actually notifies the recipient via the
  header bell.
- Discovery grids now correctly show the watched checkmark and list
  membership for a title you've already watched or listed elsewhere.
- Marking a title watched again once it's already marked now asks for
  confirmation first.
- Replaced the browser's native confirm popup with a styled in-app
  dialog everywhere the app asks for confirmation.

## [0.27.1] - 2026-07-20

### Removed

- The topbar's non-functional "+ Add title" button, superseded by the
  search bar.

## [0.27.0] - 2026-07-20

### Added

- Profile pictures: My Profile now has a photo uploader that takes
  priority over the color-circle avatar everywhere one is shown.
- New profiles now get a random starting avatar color instead of a
  shared default.
- The navbar avatar circles are a bit bigger.

### Fixed

- Added a missing default file-storage backend, discovered while
  adding the avatar-upload feature above.

## [0.26.1] - 2026-07-20

### Added

- "Last 30 days" now gets its own Combined row, matching "All time",
  on both the Stats page and the profile popup.

## [0.26.0] - 2026-07-20

### Added

- Send a household member a movie/TV/anime recommendation via a
  "Recommend to" card on a title's page. It shows up on their
  Dashboard until watched or dismissed, and you get notified once they
  watch any part of it.

## [0.25.0] - 2026-07-19

### Added

- History's binge-group tiles now open a dropdown listing every
  episode in the group, each with its own delete action.

### Changed

- History's poster tiles are slightly bigger.

## [0.24.1] - 2026-07-19

### Fixed

- Activity's per-row explanatory comment was leaking onto the page as
  literal text above every entry.

## [0.24.0] - 2026-07-19

### Changed

- Activity is back to being a lightweight household glance: a group
  now shows only its collapsed summary line, with full episode-level
  detail left to History.
- Each row now carries a left-border color by activity type.

### Fixed

- A binge summary could silently merge two real, hours-apart viewing
  sessions into one group; consecutive watches now need to be within 6
  hours of each other to stay grouped.

## [0.23.0] - 2026-07-19

### Added

- Spool now tells you when a newer version is out via a notification
  and a Settings banner, both linking to the GitHub changelog.
  Owner-only, and self-correcting once you upgrade.

## [0.22.0] - 2026-07-19

### Added

- A "Because you watched X" discovery row on the Dashboard, using
  TMDB's own recommendations for your most recently watched title.

### Fixed

- "Recently added to lists" no longer duplicates the Watchlist
  carousel above it; it now only shows adds to custom lists.
- Dashboard's "Total watch time" stat now matches the Stats page's own
  watch-time format.

## [0.21.0] - 2026-07-19

### Added

- The navbar search box now actually works - type and press Enter to
  jump to a results page covering both your library and TMDB.
- A "What should I watch?" box on the Dashboard, powered by Gemini and
  optional/bring-your-own-key per profile.

## [0.20.0] - 2026-07-19

### Added

- Sync Log now surfaces problems: a banner appears when a provider's
  syncs are consecutively failing, with a direct reconnect link when
  it looks auth-related.
- Error messages can now be expanded into a full, copyable block
  instead of a truncated hover tooltip.
- Status now pairs a check/x icon with the existing color.
- Failures under a second get a "fast fail" badge, usually indicating
  an auth problem rather than a timeout.

## [0.19.0] - 2026-07-19

### Fixed

- Trakt/Simkl syncs now recover from an expired or revoked access
  token by refreshing it and retrying, instead of failing every run
  forever. Requires the account's original redirect URI; accounts
  connected before this ships fall back to manual reconnect until
  reconnected once.
- `generate_release_notifications`'s nightly task no longer crashes on
  its return statement.

## [0.18.0] - 2026-07-19

### Added

- Settings → Danger Zone: "Clear watch history" wipes every watch
  event/rating for your profile; each connected provider also gets a
  "Disconnect & wipe" action. Both only ever touch your own profile's
  watch data.

## [0.17.0] - 2026-07-19

### Added

- In-app notifications: a bell in the header with an unread badge and
  a dropdown feed, covering new episode/season alerts, calendar
  reminders, and Trakt/Simkl sync failures - each with its own toggle
  in Settings → Notifications.
- Settings & Import: "Import your history" is now "Import & Export",
  adding CSV/Trakt-JSON export.
- A new Privacy card: a "Show my activity to other profiles" toggle -
  when off, your watches, ratings, and list-adds are left out of the
  household Activity feed entirely, not just unlabeled.
- Appearance gained a default landing page preference and a preferred
  language filter default.
- A "Collections" tab on the Movies & TV page - browse movie
  franchises, derived from what's currently popular on TMDB. Movie
  only, turned off by default for now.
- The member-profile popup now shows real stats (streak, watch-time
  breakdown, genre chips) instead of a handful of plain boxes.
- The Stats and History pages can now be viewed scoped to any
  household profile (`/profile/<id>/stats/`, `/profile/<id>/history/`),
  read-only for others.
- The Watchlist is now a real, auto-managed watchlist: a title comes
  off it automatically once finished, the same behavior Trakt and
  Simkl have. Existing installs get their current "Watchlist" list
  flagged automatically via a data migration.

### Changed

- Added proportional breathing room to the main content area on
  desktop/tablet.

### Fixed

- Watching every episode of a show manually via the episode browser
  now correctly updates its completed status, matching Trakt/Simkl
  sync and CSV import.

## [0.12.0] - 2026-07-17

### Changed

- Reworked the app's primary navigation: the sidebar is now a
  mobile-only drawer, and desktop/tablet get nav links folded into the
  header as a single row. The SPOOL logo now links back to the
  dashboard on every screen size.

## [0.11.0] - 2026-07-17

### Added

- A "mark as watched" button on each episode tile in the episode
  browser, logging a `WatchEvent` for that specific episode.

## [0.10.1] - 2026-07-16

### Fixed

- Restored the backdrop's "Directed by X" text alongside the Cast row
  entry, which shouldn't have been removed in 0.10.0.

## [0.10.0] - 2026-07-16

### Added

- Title detail pages for TV shows/anime now have an episode browser: a
  season dropdown and a grid of episode tiles with a checkmark on
  episodes you've already watched.

### Changed

- The director now appears as the first entry in the Cast row instead
  of small text overlaid on the backdrop image (movies only).

## [0.9.3] - 2026-07-16

### Fixed

- Removed a faint white border baked into poster images that showed up
  as an obvious pale rim on bright/light-colored posters.

## [0.9.2] - 2026-07-16

### Changed

- Watch activity's heatmap now fills the full width of its card
  instead of a fixed-pixel-size grid.
- The year selector next to it is now a dropdown instead of a row of
  tabs.

## [0.9.1] - 2026-07-16

### Fixed

- "Your top genres" showed illegible truncated labels for minor
  genres; labels are now suppressed below a minimum share.

## [0.9.0] - 2026-07-16

### Added

- `backfill_genres` management command - a one-time pass over existing
  titles to fetch and attach genre data.

### Changed

- Release years is temporarily disabled ("Coming soon") while its
  underlying data gets double-checked.

### Fixed

- "Your top genres" always showed "No genre data yet", since no import
  path had ever fetched genre data. Genre-fetching is now wired into
  all import paths via TMDB.

## [0.8.0] - 2026-07-16

### Added

- Redesigned Stats' genre panel into a full-width "Your top genres"
  section: a single segmented bar with MOST/LEAST callouts and a
  toggle between sizing by title/episode count or by total watch time.
- Release years is now its own tile.

### Changed

- The Movies/TV Shows/Anime genre-type selector moved from plain text
  tabs to pill buttons.

## [0.7.2] - 2026-07-16

### Changed

- The hover/default readout under the "Split by type" pie now shows
  just the percentage, dropping the redundant duration.

### Fixed

- Split by type's pie was rendering with its edge visibly clipped;
  pulled the radius in slightly.

## [0.7.1] - 2026-07-16

### Changed

- Split by type is a full pie again (no center hole), rebuilt as three
  filled SVG wedges. The hover readout moved below the chart.

## [0.7.0] - 2026-07-16

### Added

- Stats' "Split by type" donut is now interactive: hovering a slice
  pops it outward and shows its own readout in the center hole.

### Changed

- Rebuilt the donut as three individually-hoverable SVG arcs. The
  legend moved to one horizontal row, each entry also
  hoverable/tappable.

## [0.6.7] - 2026-07-16

### Changed

- Refined Stats' row-alignment: row 2's outer boxes are now equal
  width with a narrower middle box; row 3 is a plain equal three-way
  split.

## [0.6.6] - 2026-07-16

### Changed

- All three Stats rows now share the same column-width ratio, so box
  edges line up cleanly across rows.

## [0.6.5] - 2026-07-16

### Changed

- Stats' top row (streak / Last 30 days / All time) is now one
  combined bordered card with thin divider lines instead of three
  separate boxes.

## [0.6.4] - 2026-07-16

### Added

- Calendar sidebar now keeps showing a release for 30 days after it
  airs instead of dropping it the instant its release time passes.

### Changed

- Clicking a calendar date with releases now also highlights that
  date's matching sidebar entry.

## [0.6.3] - 2026-07-16

### Fixed

- Navigating the Calendar to any past month always rendered an empty
  grid, even for months that had releases; the grid now queries the
  specific month being viewed.

## [0.6.2] - 2026-07-16

### Changed

- Calendar sidebar's date groups are now styled to match History's own
  day-group header, with today's date tinted primary.

## [0.6.1] - 2026-07-16

### Fixed

- Clicking a date on the Calendar now visibly stays selected until you
  click a different date.

## [0.6.0] - 2026-07-15

### Changed

- History's binge-group tile no longer expands into a dropdown; the
  episode list is now a single always-visible segmented timeline bar
  with a hover tooltip per segment.

## [0.5.4] - 2026-07-15

### Changed

- Reverted the collapsed sidebar's icon back to a plain "S" letter, in
  its own centered row.

## [0.5.3] - 2026-07-15

### Fixed

- The collapsed sidebar's icon was cramped into too little space; it
  now has its own centered row at full size.

## [0.5.2] - 2026-07-15

### Added

- A real browser tab icon (favicon + apple-touch-icon).

### Changed

- The collapsed sidebar's brand mark is now the same app icon used for
  the favicon, sized as a proper small badge.

## [0.5.1] - 2026-07-15

### Changed

- History's binge-group tile now shows total watch time on the card
  itself, not just the episode count.
- The expanded episode list was redesigned from table-like rows into
  wrapping, lighter pill chips.

## [0.5.0] - 2026-07-15

### Added

- History's tiles can now be removed in bulk via a "Select" toggle and
  a floating "N selected · Delete selected" bar. A binge-group tile's
  checkbox stands in for every episode in it.

## [0.4.0] - 2026-07-14

### Added

- Three new Stats panels: "Daily breakdown" (7-day bar chart), "Daily
  average" (with a delta vs. the preceding period), and "Peak hours"
  (lifetime distribution by time of day).

## [0.3.4] - 2026-07-14

### Changed

- The streak/stats box now sits in the same row as "Last 30 days" and
  "All time", visibly wider than the other two.

### Fixed

- Stats page's bottom row wasn't respecting its intended column-width
  ratio due to a CSS Grid min-width quirk; fixed by giving every grid
  item `min-w-0`.

## [0.3.3] - 2026-07-14

### Fixed

- Extended the 0.3.1 badge restyle to History's tiles too, so every
  badge type now matches.

## [0.3.2] - 2026-07-14

### Changed

- Stats page reorganized: "Genres & release years", "Split by type",
  and "Watch activity" now sit side by side in one row.

## [0.3.1] - 2026-07-14

### Fixed

- The MOVIE/TV/ANIME badge on poster cards was low-contrast and
  blended into the poster art; restyled to match the rating badge.

## [0.3.0] - 2026-07-14

### Added

- Discover grid poster tiles now get the same watched/add-to-list
  quick actions as library poster cards, materializing the title on
  first click.

### Fixed

- Poster cards could render at wildly inconsistent sizes due to a
  template filter bug; fixed the width fallback.
- Strengthened the bottom-of-poster gradient so action buttons stay
  legible against bright poster art.

## [0.2.1] - 2026-07-14

### Fixed

- The poster card list-picker popover rendered overlapping/garbled on
  small cards; now positioned via the button's actual screen
  coordinates.
- The sidebar could render fully off-screen at certain browser widths
  due to a breakpoint mismatch.

## [0.2.0] - 2026-07-14

### Added

- Poster cards now have a persistent quick-action bar (mark as
  watched, or add/remove from any list) without leaving the grid.
  Cards are also a bit bigger and grids a bit denser.

### Fixed

- A width-override meant to defer poster card sizing to its grid never
  actually worked due to a template filter issue.

## [0.1.0] - 2026-07-14

First tracked release - baseline for version tracking itself, covering
everything shipped so far.

### Added

- Milestone celebration banner on the Dashboard and warmer empty-state
  copy across Dashboard, Calendar, History, and Activity.
- Redesigned title detail rating control: a single draggable fill
  gauge instead of ten individual stars.
- Film-strip perforation accents on the title detail hero and
  Dashboard header, and heavier icon stroke weight across the
  sidebar/topbar.
- An Ongoing/Ended/Cancelled status badge on the title detail hero,
  sourced from TMDB.
- Calendar and Dashboard's "Up Next" now actually populate with
  upcoming episodes, season premieres, and movie release dates.
- App version, shown in the sidebar footer and on the Settings page.

### Fixed

- Calendar/Up Next were scoped to a status nothing in the app ever
  actually set; broadened to also cover plain watch history.
