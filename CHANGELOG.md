# Changelog

All notable changes to Spool are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`) — patch
for fixes, minor for new features, major for anything requiring a manual
migration/env step or breaking an existing workflow.

## [Unreleased]

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
