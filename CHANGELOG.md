# Changelog

All notable changes to Spool are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`) — patch
for fixes, minor for new features, major for anything requiring a manual
migration/env step or breaking an existing workflow.

## [Unreleased]

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
