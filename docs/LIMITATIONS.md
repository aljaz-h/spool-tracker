← [Back to README](../README.md)

# Known limitations

- **Simkl history sync is unverified against a live account** — see the
  caveat in [Importing your data](IMPORTING.md#connecting-trakt--simkl--nuvio).
- **Nuvio sync is against an undocumented, reverse-engineered API** (see
  `tracker/integrations/nuvio.py`) — built from a third-party open-source
  reference implementation, not official docs, and unverified against a
  live account from this environment. Could change or break without
  notice; failures show up in Settings & Import → Logs.
- **Import Review only covers watch history, not ratings/lists/progress
  yet.** A first Trakt/Simkl/Nuvio connection scans and stages watch
  history for review before writing anything - but Trakt's own list/
  watchlist import (the `import_lists` sync-schedule checkbox) and
  Nuvio's continue-watching progress aren't reviewable yet; both still
  only ever arrive via the normal daily sync (automatic, not gated
  behind review), so they start showing up the day after a first
  reviewed import, not as part of it.
- **Nuvio's Import Review page can show a slightly different season/
  episode than what actually gets imported**, in one narrow case: a
  title already reclassified as anime, with a TMDB season-count/MAL
  relation-chain remap (see `episode_matching.resolve_episode_season`).
  The review page shows the as-reported season/episode; commit still
  applies the same reconciliation routine Nuvio sync always has, so
  what's actually written is unaffected - only the preview label could
  briefly disagree with it.
- **CSV import** has no TMDB/IMDB-based matching (unlike Trakt/Simkl/
  Nuvio, which now all dedupe against each other by TMDB id — see
  [Duplicate titles from multiple sync sources](IMPORTING.md#duplicate-titles-from-multiple-sync-sources))
  — same-title-different-spelling across a CSV import and any of those
  syncs can still create a duplicate Title.
- **No light theme** — the Settings → Appearance light-mode swatch is
  decorative; only the dark theme is implemented.
- **Poster matching is title+year search, not ID-based** — an unusual
  title, an off-by-one release year, or a title TMDB just doesn't have
  will silently keep the gradient-placeholder fallback rather than error.
- **Single Django project, no multi-tenancy** — profiles share one
  instance/database by design (this is a household tracker, not a
  multi-user SaaS); anyone with a login can see every shared list and the
  Activity feed.
