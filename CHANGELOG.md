# Changelog

All notable changes to Spool are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`) — patch
for fixes, minor for new features, major for anything requiring a manual
migration/env step or breaking an existing workflow.

## [Unreleased]

## [0.14.0] - 2026-07-17

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
