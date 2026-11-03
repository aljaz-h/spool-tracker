# Design System: Spool

Spool is a self-hosted household watch tracker — movies, TV, and anime —
built as the private, ad-free alternative to Trakt/Simkl. It's a Django +
HTMX + Alpine.js server-rendered app (Tailwind 4 + daisyUI). Every value
below is the shipping theme in `static/src/app.css`, and the component
classes named here are defined there too — reuse them rather than
re-spelling utility strings in templates.

## 1. Direction

A dark, cinematic, media-first tracker. Artwork carries the visual
character; the interface around it stays quiet. Spool is a **tracker
first** and a browser second, so watch state, progress, ratings and
history stay easy to scan at a glance.

- **Content sits on the page.** Sections are separated by spacing,
  headings and the occasional hairline — not by wrapping everything in a
  bordered card. A card is used only when the thing *is* a card (a
  poster, a continue-watching tile, a recommendation, a sign-in form).
- **No cards inside cards**, no generic "four KPI boxes" rows, no
  glassmorphism, glow, neon, decorative gradients or blobs.
- **Layout follows content:** media → poster grids, episodes → compact
  rows, history → day-grouped poster wall (the classic Trakt history),
  activity → timeline rows, continue watching → 16:9
  landscape cards, stats → grouped figures and charts, settings →
  restrained forms.
- **Motion is opt-in** (Settings → Appearance, off by default) and
  `prefers-reduced-motion` always wins. When on: 150–250ms, ease/ease-out,
  opacity/small translate/scale ≤ 1.02 only.

## 2. Color

Tokens live in the `spool` daisyUI theme and `@theme` block in `app.css`.
Never hard-code hexes in templates.

| Token | Value | Use |
|---|---|---|
| `base-100` | `#0b0d11` | Page background (never pure black) |
| `base-200` | `#13151a` | Surfaces: real cards, inputs, menus |
| `base-300` | `#1b1e25` | Raised/hover, progress tracks, chips |
| `line` | `#23262d` | The one hairline color |
| `ink` / `base-content` | `#eceef2` | Primary text |
| `ink-dim` | `#a6aab8` | Secondary text, metadata |
| `ink-faint` | `#8a8fa0` | Tertiary text, placeholders (5.2:1 on `base-200`, AA) |
| `primary` | `#e8a63c` | Spool amber — active nav, primary CTA, progress, focus, selection |
| `movie` / `tv` / `anime` | amber / `#8b85d6` / `#3fa9a0` | Media type — dots, chart series, split bars |
| `success` / `error` / `info` | `#5bd58a` / `#f0604a` / `#6fb7ce` | Watched/complete, destructive, informational/in progress |

Amber is used **selectively** so it stays important: the active nav item,
the one primary action, progress fills, focus rings, today/selected
states. It is not a decoration color. Movie/TV/Anime colors communicate
type, never decorate large areas. Rating-source brand marks (IMDb, RT,
Trakt, MAL, Metacritic, TMDB) appear only as small marks next to their
own scores.

## 3. Typography

- **Public Sans** for the whole interface, headings included
  (`--font-display` is Public Sans now). Page title `.page-title`
  28px phone / 33px desktop, 700; section heading `.section-title`
  19/21px, 650; media/card title 15px/600; body 14–15px; metadata 13px
  `ink-dim`. `text-xs` is remapped to 12.5px, so tiny text (11–12px) is
  reserved for chips, badges and keyboard hints.
- Hierarchy comes from size *and* tone: titles in `base-content`,
  metadata in `ink-dim`, only truly tertiary text in `ink-faint`.
- **Bebas Neue** (`.font-brand`) only for the SPOOL wordmark.
- **JetBrains Mono** only for episode codes (`S01E04`), timestamps where
  alignment matters, and technical values (API keys, versions). Ordinary
  numbers use Public Sans with `tabular-nums`.
- Title case over UPPERCASE; no letter-spaced uppercase labels (the
  Bebas Neue wordmark and display figures are the only uppercase).

## 4. Shape & depth

Radius scale (remapped in `@theme` so old and new markup agree): posters,
buttons, inputs 8px (`rounded-lg`); cards 10–12px (`rounded-xl`/`2xl`);
large panels ≤ 14px; full pills only for chips, filter chips, segmented
controls and avatars. Shadows only on floating things — menus/popovers
(`.menu-surface`), modals, the poster in the title hero.

## 5. Shell

- **Application grid** (tokens in `app.css` `@theme`): top bar
  `--spacing-topbar` 60px desktop / 56px phone, content gutters 16 / 24 /
  40px (phone / tablet / desktop, set once on `.page`), content capped at
  1920px. `.bleed` breaks a band out to the full window (title hero and
  its tab bar) with a `.page` inside to keep the content on the grid. Main + rail layouts use one shape everywhere: `minmax(0,1fr)`
  plus a 288–340px rail with a 48–64px gap (Home, title pages, Activity).
  Sections are 44px apart on phones, 56px on desktop. A page header sits
  32px above its first section, 24px when a tab/filter row follows it;
  a section heading sits 16px above its content.
- **Pinned UI** is at most: top bar + one sticky strip (title tabs or a
  day heading) + the phone bottom nav — under 20% of the viewport. On
  short viewports (landscape phones, under 520px tall) only the bottom
  nav stays fixed.
- **Desktop (lg+):** one top bar (`topbar.html`) — wordmark, then text
  links Home · Movies / TV Shows / Anime · Calendar / History / Lists /
  Stats / Activity (`nav_top_item.html`; the current page gets full-
  brightness text and a 2px amber underline on the bar's bottom edge),
  then search (a field from xl, an icon from lg; press `/` to focus),
  notifications, household and the profile menu, which holds Settings.
- **Footer:** version and the TMDB / Jikan / AniFiller credit on every
  page (TMDB's terms require the credit).
- **Below lg:** a solid bottom nav docked to the screen edge (Home, Browse,
  Search, Alerts, You; hairline on top, no blur, no floating capsule) plus
  the You sheet; the top bar keeps the wordmark and account icons.
- **Fonts:** Bebas Neue, Public Sans and JetBrains Mono are bundled in
  `static/fonts` (woff2, `@font-face` at the top of `app.css`); nothing is
  loaded from Google Fonts. Preload only what shows above the fold.
- **Content width:** `.page` caps content at 1920px and owns the gutters;
  reading-heavy pages (Activity, Notifications, Settings)
  constrain themselves further. Pages never re-add their own side
  padding.

## 6. Components (app.css)

- **Page/section headers:** `.page-head`, `.page-title`, `.page-subtitle`,
  `.section`, `.section-head`, `.section-title`, `.section-count`,
  `.section-link` ("View all →"), `.subhead`.
- **Media cards:** `discover_tile.html` (TMDB items) and
  `poster_card.html` (library titles) share one structure — `.poster`
  artwork, `.media-title` + one `.meta` line underneath, a persistent
  `.card-state` marker for watched/in-progress, and a `.card-actions` bar
  (watched / list) revealed over the poster on hover/focus or first tap.
  `.card-actions` must stay a sibling of the overflow-hidden poster —
  its popovers are `position: fixed`.
- **Landscape cards:** Continue Watching (`poster_card.html` with
  `progress`) and Recently Watched (`watch_event_card.html`) — 16:9 art,
  bottom scrim with title/episode, 3px amber progress line.
- **Title page:** poster-led hero spanning the window (`.bleed`): the
  backdrop shows on the right and fades into the page on the left; a
  large poster on the left; then the type label, title, labelled facts
  (`.hero-facts` - sentence-case label in `ink-dim`, bright value: Directed by /
  Released / Runtime / Seasons / Rated / Status), genre chips, ratings, overview,
  actions, show progress and the main cast as portrait cards
  (`title_person.html`). Below: a main panel with tabs (Episodes /
  Overview = media, details, collection / Cast & Crew / Your activity)
  beside a rail of panels (`.title-rail > section`: your rating, where
  to watch, recommend, More Like This). Tabs are Alpine state
  mirrored to the URL hash; every panel stays in the DOM so HTMX targets
  keep working. Shows open on Episodes, movies on Overview. Long seasons
  show 12 episodes plus "Show all"; when "up next" (or a linked episode)
  is further in, only a window around it is shown. On phones the poster
  sits beside the title, the main panel loses its card background and
  the rail only shows under Overview.
- **Person page:** a header panel (dimmed collage of their best-known
  posters, big portrait, department chips, born line, bio, two actions and
  a stats card), then Known for (six `discover_tile`s with a role line),
  Career by year (bars drawn in the page: amber watched, grey not) and one
  Filmography - tabs by department, watched and type filters, sort, and a
  grid (by decade) or list (table) view that is remembered. Filtering and
  sorting run in the browser over what the server rendered
  (`static/js/person-filmography.js`); a title appears once however many
  departments it is under. Chips here are plain: no amber.
- **History:** each day is a heading (with its totals, pinned while you
  scroll it) over a `.media-grid` of plays — `history_tile.html` (poster
  with the episode code, title, episode name or year · time) and
  `history_group_tile.html` (one card per binge: episode range on the
  poster, count badge with a per-episode remove menu, session strip).
  Kept deliberately different from Activity's timeline rows.
- **Lists:** `list_card.html` — 16:9 collage of the first four posters,
  name, then count · sharing · last updated · owner. Three columns on
  desktop; creating a list is the page's primary button, never a card.
- **Grids/rows:** `.media-grid` (responsive auto-fill posters),
  `.media-row` / `.landscape-row` (one row: sideways scroll on phones,
  with the next item deliberately peeking; clamped single grid row from
  sm), `.media-row-sm` for rows inside a narrower column. Poster density
  is a tracker's, not a streaming homepage's: 3 columns on phones, ~170px
  posters on desktop (5 across at 1366px, 6 at 1440px). `.rows` (a
  list grouped by spacing, hover wash per row — no hairlines).
- **Metadata:** `.meta` renders "2024 · Movie · 2h 14m" from plain spans
  and never shows a dot at the start of a wrapped line (each item's dot
  sits in a slot that `clip-path` trims at the line start);
  `.type-dot` + `media_label`/`media_dot_class` filters for media type.
  Prefer this over badges.
- **Focus:** a 2px amber ring everywhere; inside clipping containers
  (tab strips, sideways rows) it is drawn inset so it is never cut off.
- **Chips:** `.chip` (12px) (+ `-primary/-success/-error/-info/-tv/-anime/
  -movie/-scrim`) only for state worth isolating: status, filler/recap,
  season finale, sync state. `.filter-chip` for removable active filters.
- **Buttons:** one filled amber `btn-primary` per screen at most; everything
  else is `btn-soft` or `btn-ghost`. A list of equivalent actions (Connect
  buttons, provider rows) uses `btn-soft` throughout. On touch screens
  small controls keep their look but get a 44px hit area (`.hit-44`,
  `@media (pointer: coarse)`).
- **Controls:** `.seg`/`.seg-item` segmented control (type switches),
  `.tabs-line`/`.tab-line` underline tabs (in-page categories),
  `.toolbar`, `.field` (icon + input), daisyUI `btn` at `btn-sm`
  (`btn-primary` solid amber, `btn-soft` dark surface, `btn-ghost` quiet).
- **Ratings:** `pill_badges.html` — your rating first in amber, then each
  source's mark + score on one line, no boxed pills.
- **Episodes:** `title_episodes.html` — season poster picker, then one
  compact row per episode; watched rows recede, the first unwatched
  episode after a watched one is marked "Up next" (`.ep-row` CSS).
- **Loading:** a block that arrives after first paint shows a skeleton the
  size and shape of the real thing (`.skel`, `partials/skeleton_*.html`),
  never a spinner or a bare "Loading...". The faint sweep is a loading
  signal, not decoration, so it is the one looping animation allowed; it
  stops under `prefers-reduced-motion`. An empty result is not a skeleton:
  it stays the one-line `.empty` message.
- **Empty states:** `.empty` + `.empty-title` — short heading, one plain
  sentence, optional action.
- **Figures:** `.figure` / `.figure-label` for headline numbers (Stats,
  This Month).

## 7. Anti-patterns

- No light mode, no pure black, no fourth media-type color.
- No bordered card around every section, no cards inside cards, no
  uppercase metadata pills where a `.meta` line would do.
- No hairline between every row or section — spacing groups; a border
  is for real navigation edges (tabs, the shell) or form grouping.
- No glow, neon, glassmorphism, large decorative gradients or blobs. The one
  exception is the phone tab bar, a floating frosted-glass capsule.
- No idle/looping animation; nothing that moves unless the user acted
  (the loading skeleton's sweep is the one exception).
- No AI-copy clichés; Spool's copy stays plain and specific.

## 8. Settings

Every Settings page is built from nine shared pieces (`app.css`, the `.s-*`
classes; templates under `tracker/templates/tracker/settings/` and `partials/`).
Small grey text is `#a6aab8` or lighter, labels are sentence case, and amber is
only for the one primary action and for things that are switched on.

1. **Page frame.** `settings_page_head.html`: a title and a one-line
   description, then panels. The same header on every page, no icon tile on the
   page itself. Two content widths only: narrow (720px) for forms, toggles and
   lists, wide (1040px) for Profile, Notifications, Connected apps, Import and
   export, Logs and Query performance (`wideTabs` in `settings.html`).
2. **Setting rows.** `settings_card` + `settings_row`: label and one-line help
   on the left, the control on the right, one panel per section, hairline
   dividers, at least 64px tall.
3. **Form fields.** `.s-label` above a 44px `.s-field` with `.s-help` under it; an
   error (`.s-error`) replaces the help text and turns the border red.
4. **Buttons.** One amber primary (`.s-btn-primary`) per page; everything else is
   secondary (`.s-btn`), ghost (`.s-btn-ghost`) or danger (`.s-btn-danger`, outlined
   red). 44px by default; 36px (`.s-btn-compact`) only inside dense tables on a
   desktop. Inside `[data-settings]` daisyUI's `.btn` is held to 44px too.
5. **Status badges.** `status_badge.html`: a dot and a short word - Connected, On,
   Off, Not connected, Not set up, Needs attention (coral, never amber), Failed,
   Coming soon. The same wording everywhere.
6. **Saving.** Toggles, selects and chips save as they change and show "Saved"
   (`settings-save.js`); forms with several text fields use the "You have unsaved
   changes" bar (`.s-unsaved`, Discard / Save changes); one-off actions show their
   result next to the button.
7. **Choosing.** A toggle (`s_toggle.html`) for on and off; a checkbox only inside
   a matrix or list of options (checked is amber); chips (`.s-chip`) for picking
   several - the selected chip is raised and checked, not amber.
8. **File upload.** `file_dropzone.html` + `file-dropzone.js`: a dashed drop area,
   a Choose file button, the chosen file's name and size with a remove button. No
   native "Choose file / No file chosen" input anywhere.
9. **Wording and links.** Sentence case for every title, label and button, the
   sidebar included. Links are underlined light grey (`.s-link`), never amber.

