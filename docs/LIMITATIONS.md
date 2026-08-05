← [Back to README](../README.md)

# Known limitations

- **Simkl history sync is unverified against a live account** — see the
  caveat in [Importing your data](IMPORTING.md#connecting-trakt--simkl--nuvio).
- **Nuvio sync is against an undocumented, reverse-engineered API** (see
  `tracker/integrations/nuvio.py`) — built from a third-party open-source
  reference implementation, not official docs, and unverified against a
  live account from this environment. Could change or break without
  notice; failures show up in Settings & Import → Logs.
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
