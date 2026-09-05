# Design System: Spool

Spool is a self-hosted household watch tracker — movies, TV, and anime —
built as the private, ad-free alternative to Trakt/Simkl: your data on
your own Docker Compose stack, not a third party's. It's a Django +
HTMX + Alpine.js server-rendered app (Tailwind 4 + daisyUI), and every
value below is lifted directly from its shipping `app.css` theme and the
component patterns repeated across its templates — nothing here is
invented. Use this file as the source of truth when prompting Stitch for
new Spool screens (Dashboard, Stats, Library, Calendar, Activity, Title
detail) so generated screens read as more of the same app, not a
different product bolted on.

## 1. Visual Theme & Atmosphere

A dark, cockpit-dense home-media console — closer to a NAS admin panel
or a flight-deck instrument cluster than a consumer streaming app. It's
built by and for someone tracking dozens of shows and hundreds of watch
events at once, so the UI is unapologetically information-dense: small
precise type, `font-mono` numerals everywhere a figure appears (streak
counts, runtimes, percentages, timestamps), and pill-shaped badges
carrying metadata (media type, episode code, rating source, list count)
rather than long descriptive sentences.

The palette is a single true-black-adjacent dark theme (`prefersdark:
true`, `color-scheme: dark` — there is no light mode) warmed by one
amber accent, with two additional *semantic* accents (teal, violet)
reserved specifically for distinguishing Anime and TV from Movies in
data displays — not decorative color, informational color. Motion is
deliberately restrained: Spool ships with interface animation **off by
default** (a user-facing Settings toggle), and even switched on, the
house rule is 150–300ms ease/ease-out opacity or transform only — "no
bounce, no scale past a couple percent, nothing that calls attention to
itself." This is a calm, legible control surface, not a marketing site.

- **Density:** 7/10 — Cockpit Dense. Cards run `p-4`/`p-5` with tight
  internal spacing; rows of stats, badges, and small poster tiles are
  the default unit, not generous whitespace.
- **Variance:** 5/10 — Offset Asymmetric. Dashboard and Stats use
  deliberate unequal-width splits (`1fr_320px`, `1fr_0.6fr_1fr`), but
  within a card, layout is orderly grid/flex, never scattered.
- **Motion:** 2/10 — Static Restrained, by explicit product decision
  (see above), not an oversight to fix.

## 2. Color Palette & Roles

All values are the live `--color-*` tokens from `app.css`'s `spool`
daisyUI theme — do not substitute Tailwind defaults or invent new hexes.

- **Void Ink** (`#14161c`, `--color-base-100`) — Page background.
- **Panel** (`#1b1e26`, `--color-base-200`) — Card/panel fill (the
  single most common surface — every card is `bg-base-200 border
  border-line rounded-2xl`).
- **Raised Panel** (`#232732`, `--color-base-300`) — Hover states,
  nested/inset surfaces (progress-track backgrounds, pill toggle rails).
- **Ink** (`#eceef2`, `--color-base-content` / `--color-ink`) — Primary
  text and icon color.
- **Ink Dim** (`#9195a6`) — Secondary text: labels, captions, metadata.
- **Ink Faint** (`#5c606f`) — Tertiary text: placeholders, disabled,
  least-important numbers.
- **Line** (`#2b2f3b`) — The one border color used everywhere (`border
  border-line`); also the progress-bar track fill and divider rule
  (`.dashboard-rule`).
- **Marquee Amber** (`#e8a63c`, `--color-primary` / `--color-warning`)
  — The single true accent: primary buttons, active/selected states,
  streak counters, the Movies slice of any Movie/TV/Anime split. Also
  doubles as the semantic "warning" color — there is no separate
  amber-vs-orange distinction to preserve.
- **Teal** (`#3fa9a0`, `--color-secondary`) — Semantic accent for
  **Anime** everywhere media-type is color-coded (charts, split bars,
  section theming via `.section-anime`, which remaps `primary` to this
  color for that whole page subtree).
- **Violet** (`#8b85d6`, `--color-accent`) — Semantic accent for **TV**
  in the same contexts. Muted/dusty, not a saturated "AI purple" — never
  brighten this.
- **Success** (`#5bd58a`) — Completed states, positive deltas, watched
  checkmarks.
- **Error** (`#fa5a3d`) — Destructive actions, negative deltas.
- **Info** (`#6fb7ce`) — Informational badges (distinct from Teal/Violet
  — reserve Info for non-media-type contexts only).
- Rating-source brand marks (used only as small logo/badge accents next
  to their own numbers, never as UI chrome): **IMDb** `#f5c518`,
  **Rotten Tomatoes** `#fa5a3d`, **Trakt** `#ed4a50`, **MyAnimeList**
  `#2e51a2`, **Metacritic** `#ffcc33`, **TMDB** `#01b4e4`.

Three accents exist, not one — this is a deliberate exception to
"single accent" defaults: Amber/Teal/Violet is a functioning
Movie/TV/Anime legend reused everywhere a media-type split appears
(Dashboard's monthly split bar, Stats' donut, genre charts). Never
introduce a fourth media-type color or reassign these three.

## 3. Typography Rules

- **Display:** `Bebas Neue` (weight 400, `letter-spacing: 0.02em`) —
  every `h1`/`h2`/`h3` and any element with `.font-display`. Condensed,
  all-caps-reading even in mixed case, used for page titles (`text-4xl`,
  e.g. "STATS", "HOUSEHOLD ACTIVITY"), hero numbers (`text-6xl`, streak
  counters), and section headers (`text-xl`/`text-lg` with
  `tracking-wide`) — never for body copy or data labels.
- **Body:** `Public Sans` (weights 400–800 available) — all prose,
  descriptions, and default text color `Ink`/`Ink Dim`.
- **Mono:** `JetBrains Mono` — every number that means something:
  streak days, runtimes, percentages, timestamps, episode codes
  (`S1E05`), badge pills, leaderboard figures. If it's a figure a user
  might scan down a column, it's mono.
- **Real scale in use** (do not invent a different one): page title
  `text-4xl`; hero stat `text-6xl`/`text-7xl` (sparingly — one per
  page, e.g. the streak count); section header `text-xl`/`text-lg`;
  card stat `text-2xl`; body `text-sm`/`text-[13px]`; secondary/caption
  text runs small and precise — `text-xs`, `text-[11px]`,
  `text-[10.5px]`, `text-[10px]` are all real, already-shipped sizes,
  not a mistake to round up.
- **Banned:** Inter (already avoided — good). No serif anywhere; Spool
  has no editorial/long-form reading context that would justify one.

## 4. Component Stylings

- **Cards/Panels:** `bg-base-200`, `border border-line`, `rounded-2xl`
  (16px — the dominant shape, 58+ instances), `p-4` or `p-5`. Optional
  `shadow-md shadow-black/20` for panels that sit over other content
  (sidebar cards). Nested "well" surfaces inside a card (e.g. a chart's
  own background) use `bg-base-300/30` with the same `border-line`.
- **Buttons:** daisyUI `btn` primitives almost exclusively at `btn-sm`
  (58 uses) or `btn-xs` for inline/compact contexts. `btn-primary`
  (solid Amber fill, dark `#1a1305` text) for the one primary action on
  a card; `btn-soft` (muted fill) for secondary actions. No custom
  hover glow — daisyUI's own subtle brightness shift is sufficient.
  Icon-only buttons are `btn-square` with an accessible `aria-label`.
- **Pills/Badges:** the house pattern for any small piece of metadata —
  `font-mono text-[10px]` or `text-[11px]`, `font-bold`, `uppercase`,
  `tracking-wide`, `px-1.5 py-0.5`, `rounded` (small) or `rounded-full`
  (status pills), colored as `bg-{color}/15 text-{color}` (e.g.
  `bg-primary/15 text-primary` for an "Active"/"Binge Session" pill,
  `bg-success/15 text-success` for a completed state). Never a solid
  saturated fill on a badge — always the `/15` tint over `base-200`.
- **Posters/thumbnails:** `aspect-[2/3]` for any poster image,
  `rounded-lg` (8px) at small sizes or `rounded-md` for tiny list-row
  thumbnails, `object-cover`. Missing-poster fallback is a deterministic
  per-title gradient (`gradient_class`), never a gray box or broken-image
  icon.
- **Avatars:** `rounded-full` circles are the default (topbar, profile
  popovers); a squarer `rounded-lg` avatar is used specifically in the
  dense Activity feed timeline where it visually pairs with poster
  thumbnails of the same corner radius. Colored solid fill +
  first-initial when no image is set (`avatar_color`, assigned
  per-profile, never re-used across profiles in the same household).
- **Progress bars:** `h-1.5` or `h-2`, `rounded-full`, `bg-base-300`
  track, solid accent-color fill (`bg-primary`, or the row's own
  semantic color). No gradient fills, no glow.
- **Inputs:** daisyUI `input`/`select`, dark-mode-aware native controls
  (date pickers render dark via `color-scheme: dark`). Label above,
  helper/error text below, standard gap.
- **Tooltips:** CSS-only via `.spool-tooltip`/`data-tooltip` — small
  `bg-base-300` pill above the trigger, `border border-line`, shown on
  hover and focus alike. Use this pattern, not a JS tooltip library.
- **Loading:** a single shared spinner (`.spool-spinner`, a bordered
  circle with a transparent segment, `currentColor`), sized via
  `font-size` to drop into any button or form footer. No skeleton
  screens exist yet in the shipped product — prefer the spinner
  pattern for consistency unless a screen is explicitly a new
  direction.
- **Empty states:** short, specific, slightly wry copy in `text-sm
  text-ink-faint` centered in the card (e.g. "Quiet so far — watch
  something to get this started") — never a generic "No data" with an
  illustration.

## 5. Layout Principles

- Page container follows a `mb-6` header block (title `text-4xl` +
  one-line `text-ink-dim text-sm` subtitle, optional action button(s)
  top-right) then stacked/gridded content below.
- Two dominant multi-column shapes, both real: a **main + sidebar**
  split (`grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6`, sidebar
  collapses below `lg:`) for Dashboard/Activity, and an **even hero
  strip** (`grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4`) for
  top-of-page stat cards. Both are legitimate — pick by content shape,
  not by rule.
- Horizontally-scrolling card rows (`flex gap-4 overflow-x-auto
  scroll-row`) are the standard way to show "more than fits" without
  pagination — used for poster rows, season pickers, cast. Pair with a
  themed thin scrollbar (`.scroll-row`), not the raw OS one.
- CSS Grid for tile grids (`grid-cols-[repeat(auto-fill,minmax(190px,1fr))]`),
  Flexbox for everything linear.
- Mobile collapse is real and already implemented: multi-column grids
  drop to one column below `sm:`/`md:`/`lg:` per-breakpoint as
  appropriate; the topbar's center nav is `hidden md:flex`, replaced by
  a bottom nav bar on mobile — don't redesign that pattern, extend it.

## 6. Motion & Interaction

Match the shipped restraint — this is the one place to actively resist
a generic "make it feel alive" instinct:

- Everything animatable must work identically, instantly, with **no**
  transition — animation is a progressive enhancement gated behind a
  user setting that defaults off, and `prefers-reduced-motion` always
  wins regardless of that setting.
- When motion is on: 150–300ms, `ease`/`ease-out` only, `opacity` and
  `transform` only. No spring physics, no bounce, no scale beyond a
  couple percent, no perpetual/looping micro-interactions on idle UI.
- The one intentional "notice me" moment in the whole app is a 2-second
  amber ring-flash on a card the user just navigated to (episode
  deep-links) — that's the ceiling for how much attention any animation
  should draw, not a floor to build up from.
- Drag interactions (list reordering) get a real physical response —
  slight scale-up, rotation, and shadow while dragging — because it's
  functionally necessary feedback, not decoration.

## 7. Anti-Patterns (Banned)

- No light mode / no light-theme screens — Spool is dark-only.
- No emojis in UI chrome (fine inside user-authored reply text/reactions,
  which already use a couple deliberately, e.g. reaction buttons — but
  never in labels, headers, or system copy).
- No Inter, no generic system-font fallback as the *display* face.
- No serif anywhere.
- No pure black — the darkest surface is `#14161c` (Void Ink), never
  `#000000`.
- No fourth media-type color, no reassigning Amber/Teal/Violet's roles.
- No neon/saturated glow on buttons or focus rings — focus states use a
  soft `rgba(accent, 0.28)` outer ring at most (see the range-slider
  thumb's own focus treatment), never a bright halo.
- No idle/looping animation on dashboard widgets — this product's
  motion is opt-in and momentary, not ambient.
- No generic "3 equal cards" filler row where the real data has an
  actual shape (a binge session, a leaderboard, a decade histogram) —
  let that shape drive the layout instead.
- No placeholder copy, fake usernames, or invented stats when mocking a
  screen — reuse the actual demo dataset's vocabulary (profile names
  like "Demo"/"Alex", real title names, real genre names) so generated
  screens read as this app, not a template.
- No AI copywriting clichés ("Elevate", "Seamless", "Unleash",
  "Next-Gen") — Spool's real copy is plain and specific ("Track what
  your household watches... on your own server, not someone else's").
