# Spool UI/UX audit

Audited 2026-11-01 at v0.172.1. Read-only: no application file was changed and nothing was installed.

**Method.** The shipping `DESIGN.md` was treated as the locked system and the app was measured against it. Hallmark's `sloplint` was run over `static/src/app.css` and `tracker/templates/`. The real app was rendered in Chromium (local copy, seeded library) at 1440x900 and 390x844 across Home, Movies, TV, Anime, History, Calendar, Lists, Stats, Settings, Search, Wrapped, Notifications and a title page. Contrast, tap-target size, tiny text, horizontal overflow, focus rings and landmarks were measured in the page. Screenshots are in the scratchpad (`aud/`); they are not committed.

**What was not covered.** The `/activity/` route returned a 404 in the preview environment, so the Activity page is unaudited. Admin pages, login/TOTP, import review, push preview, the schedule pane and every modal/popover state were only read in source, not rendered. The seeded library is small (1 watchlist, 0 shows completed), so dense-data states (long lists, 100+ history days) are inferred, not seen. A tab-through of the full page was done only for focus-ring visibility, not for a complete keyboard-order review.

**Linter caveat.** About 1,000 sloplint lines come from linting partials as standalone files (missing `:focus-visible`, `overflow-x: clip`, `:active` states). Those are false positives, because the rules live in `app.css`. Only findings confirmed in `app.css` or in the render are used below.

---

## 1. Executive summary

Spool is already a disciplined product. It has a real written system, a single hairline colour, one accent, spacing doing the grouping work, posters carrying the colour, and almost no card-in-card clutter. Home, History, Movies and Search are the strongest pages. The audit does not argue for a redesign.

The problems are about **drift from its own rules**, **accessibility floors**, and a **handful of screens that were built later and look like a different product** (Wrapped, parts of Settings, the title-page action row).

The five things that matter most:

1. **The tertiary text colour fails contrast.** `--color-ink-faint` (#737887) measures 4.32:1 on the page and 4.03:1 on surfaces. It is used at 11 to 13px for placeholders, footers, empty states, calendar rail text and Stats captions. That fails WCAG AA everywhere it is used. Stats alone has 40+ instances.
2. **The system forbids uppercase letter-spaced labels and the product ships them.** `DESIGN.md` section 3 says "no letter-spaced uppercase labels". The title hero (AIRED, SEASONS, RATED, STATUS, MAIN CAST), the title action row (WATCHING, ADD TO LIST, TRAILER), Wrapped (FORMAT, SHOW ON THE IMAGE, HOURS, PLAYS) and about 15 more selectors do it. `DESIGN.md` section 6 then describes the hero facts as "small caps label", so the document contradicts itself.
3. **The mobile bottom nav is the one glassmorphic surface in a product that bans glassmorphism.** `.glass-bar` is a blurred translucent pill that floats over content and covers the last row of every mobile page.
4. **Layout shifts between pages.** There is no `scrollbar-gutter: stable`, so the top-bar search, bell and avatar jump 15px sideways when you move from a long page (Movies) to a short one (Lists). Lists also lets the page action sit at x=1400 while other pages end at x=1385.
5. **Accent usage is no longer "selective".** Amber appears as primary button, active nav, progress, focus ring, status chip, heatmap fill, the Wrapped format selector and the avatar. The title-page primary action ("Watching") is a pale cyan, not amber, so the page's most important button is off-system.

Overall rating by area: Information design B+, Visual consistency B-, Accessibility C+, Responsive behaviour B, Component discipline B-, Polish and copy B.

---

## 2. Severity-ranked findings

Severity: **High** = fails an accessibility floor or visibly breaks the system on a main path. **Medium** = inconsistency a regular user will notice. **Low** = polish.

### High

**H1. `ink-faint` fails WCAG AA contrast.**
- Evidence: `static/src/app.css:57` defines #737887. Measured 4.32:1 on `base-100` (#0e1014), 4.03:1 on `base-200` (#16181d); AA needs 4.5:1 for text under 24px.
- Where: Calendar rail ("Nothing upcoming..."), calendar day numbers, footer credits, Stats captions and heatmap legend, settings `.s-foot` notes, "Not available yet" lines, placeholders, title-page cast subtitles.
- Fix: lift to about #8a8fa0 (about 5.6:1 on base-100, about 5.2:1 on base-200). One token change. Keep it distinct from `ink-dim` (#a6aab8) by letting the gap be about 1.3x rather than the current 1.6x.

**H2. Avatar initial is white on a gold fill.**
- Evidence: the profile avatar in the top bar and bottom nav measures 1.81:1 (white on rgb(214,193,76)). It is also the single most repeated element in the shell.
- Fix: dark ink (`#1a1305`, the colour the login page already uses on amber) on any light avatar colour; or compute the text colour from the avatar's luminance.

**H3. No skip link, no meta description, 35 unlabelled links on Home.**
- Evidence: no `skip` anchor in `base.html`; `document.querySelector('meta[name=description]')` is null; 35 `<a>` elements on Home have no text, `aria-label`, `title` or alt-bearing image (poster links are the likely bulk). Landmarks are otherwise fine (one `main`, one `footer`, two `header`, four `nav`).
- Impact: keyboard users tab through 12+ top-bar controls on every page; screen-reader users hear "link" 35 times on Home.
- Fix: a visually-hidden-until-focused "Skip to content" as the first element, a description per page, `aria-label="{{ title }}"` on poster links (the title is already in the card's text, so a `sr-only` span is enough).

**H4. Tap targets under 44px are the norm, and under 32px in places.**
- Evidence: `btn-sm` renders 30px tall; `.seg-item` 30px; calendar month arrows about 20px; History select checkbox and filter 21px; the card remove "x" on History is 24x24; the wordmark link is 53x26; footer links 15px tall; mobile "popup-handle" 23px. The in-page count of controls under 32px (about 50 on Movies) includes visually hidden 1x1 inputs, so treat it as an upper bound; the named items above are the visible ones.
- Fix: keep the visual size, grow the hit area: `min-height: 44px` on mobile via padding or a pseudo-element, `@media (pointer: coarse)` for `btn-sm`, `seg-item`, calendar arrows and the card actions.

**H5. Mobile bottom nav covers content and is the wrong material.**
- Evidence: `partials/bottom_nav.html:6` uses `.glass-bar` (`app.css:1542-1551`: translucent fill, blur, inner highlight, two shadows). On every mobile screenshot it sits over the final row of posters/text. `DESIGN.md` section 7 forbids glassmorphism.
- Fix: a solid `base-200` bar docked to the bottom edge with a 1px `line` top border, full width, safe-area padding, and `padding-bottom` on `<main>` equal to its height. The pill shape and floating inset can go.

### Medium

**M1. Title-page primary action is cyan, and four equal-weight buttons compete.**
- The row "WATCHING / ADD TO LIST / 9/10 / TRAILER" gives all four the same size and surface. The one that changes state (Watching) is pale cyan (`info`), which `DESIGN.md` reserves for informational/in-progress. The amber primary is not used anywhere in the hero.
- Fix: one primary (amber, "Mark watched" or "Continue S1E8"), one secondary (list), tertiary icon-only buttons (rating, trailer). Sentence case labels.

**M2. Uppercase tracked labels contradict the system (see Executive summary 2).**
- CSS: `.hero-label` (`app.css:1517`), `.sch-eyebrow`, `.sch-day-name`, `.sch-tag` (also 9px and mono), `.sprog-label`, `.atile`, `.wp-options-title`, `.yr-*` x9. Templates: `settings.html:462-464`, `settings_logs_table.html:62` (9.5px uppercase), `title_hero_heading.html`, `title_rating_tile.html:72`, `tv_watched_menu_panel.html:61`.
- Fix: drop `text-transform`/`letter-spacing`, keep the label a step smaller and in `ink-dim`. Update `DESIGN.md` section 6 to match ("labelled facts - sentence-case label, bright value").

**M3. The page edge moves between pages (no `scrollbar-gutter`).**
- `getComputedStyle(documentElement).scrollbarGutter` is `auto`. Top-bar icons sit at x=1235 on long pages and x=1250 on short ones. Content right edge is 1385 vs 1400.
- Fix: `html { scrollbar-gutter: stable; }`. One line.

**M4. Wrapped looks like a separate product.**
- Different top bar (back link, boxed "SPOOL WRAPPED" lockup in Bebas), a centred 1080px column, bordered option panels, uppercase labels (FORMAT, SHOW ON THE IMAGE), a rotating-stack of five button styles (`.wp-btn`, `.wp-btn-primary`, `.btn-soft`, `.s-btn-*`, outlined Subscribe on Calendar), and an amber-bordered selected state on the format picker. Within the app the "Wrapped" button on Stats is a fourth border/fill treatment next to Export and View History.
- Fix: put Wrapped inside the app shell (same top bar, same `.page` grid). Keep the preview card centred inside it. Reuse `.seg` for Year / Month / Year in Review / All Time and `btn btn-primary btn-sm` for Download.

**M5. Button recipes have multiplied.**
- sloplint F4: four filled-primary recipes (`.btn-soft`, `.wp-btn`, `.wp-btn-primary`, `.s-btn-prim…`). Screens show: solid amber (Connect, New list, Download), dark soft (Surprise me, Wrapped), white outline (Subscribe), ghost text (Export, Refresh), and uppercase tiles (title hero).
- Fix: define exactly three (`primary`, `soft`, `ghost`) and one danger variant; map `.wp-btn` and `.s-btn-*` onto them.

**M6. Two amber "Connect" buttons in a five-row list read as a recommendation.**
- Settings, Connected Apps: Trakt and TMDB are amber because they are configured; Simkl, WeTrakr and MyAnimeList are grey because they are not. The eye reads amber as "do this first", but the order is arbitrary.
- Fix: all available "Connect" actions use `btn-soft`; amber is reserved for the one action the page wants (none here). Disabled providers should be removed from the primary list or collapsed under "Not set up on this server".

**M7. Settings and Calendar leave a third of the window empty.**
- Settings content ends at x=1048 of 1440 with an empty right column; Calendar's grid is 990px wide and the right rail holds one grey sentence.
- Fix: Settings is fine as a reading column, but align it to the page grid (left) and let the sidebar stay sticky. Calendar: let the grid take the full width when the rail is empty, and collapse the rail to a one-line empty state above the grid.

**M8. Stats opens with a five-up KPI strip.**
- "2 day streak / 28 / 0 / 75 days / 1h 15m" is exactly the pattern `DESIGN.md` bans (generic KPI row), even though it uses hairlines instead of boxes. "0 Shows completed" and "Daily average -5m" (a negative, in red) read as errors, and "75 days · 1792h 31m" says the same thing twice.
- Fix: lead with one sentence ("75 days watched across 467 plays") and the Watch Activity heatmap; move the other figures into a plain definition list. Clamp the daily average at 0 or hide it when negative.

**M9. Colour literals and off-token values sit outside the token block.**
- sloplint F48: 70+ literals in `app.css` (`.chip-scrim`, `.art-scrim`, `.glass-bar`, `.hero-backdrop-scrim`, `.card-actions`, `.yr-*`, `.sprog-*`, and `#272a32` in `.btn-soft`), plus inline `#fff`, `#14161c`, `#f4f2ec` in templates, and Tailwind arbitrary `text-[#c4c7d2]` / `shadow-[0_12px_32px_rgba(...)]`. Pure `#000000` for the Simkl brand colour (`app.css:79`).
- Impact: a theme tweak (for example the darker treatment below) has to touch 70+ places.
- Fix: add `--scrim-*`, `--shadow-pop`, `--on-accent`, `--on-media` tokens and replace literals mechanically.

**M10. Copy and typography slips.**
- "Not available yet -ask your server owner" renders with the dash glued to "ask" (the `&mdash;` in `import_source_card.html:58` is fine; the plain-hyphen strings in `settings.html:595-598+` are not).
- "Watchlist's empty - future you will thank present you for adding something" uses an em dash and a joke, against the system's "plain and specific" rule. 32 arrow-glued links ("History →", "Calendar →", "All lists →").
- "TV Shows" in nav vs "TV" in tabs and filters vs "TV series" on the title page.

### Low

- **L1.** Tiny text: 118 elements at 11px and 14 at 10px on Movies; 26 at 11px on Settings. The system says 11-12px is for chips and kbd hints, but it is used for metadata.
- **L2.** `transition-all` on a button in the shell and a `width` transition on `.sprog-fill` and the top progress bar (`base.html:437`); `app.css:2240`. Use transform/opacity.
- **L3.** Two sticky `top: 0` elements stack (`.sch-head` and `.wp-bar`, `app.css:2447`).
- **L4.** Side stripes: `.sch-slot` and `.sch-tonight-row` use a 3px coloured left border; the system prefers a hairline or dot.
- **L5.** `.glass-bar` and trailer-modal backdrop use `backdrop-filter`.
- **L6.** `text-wrap: balance` is missing on headings; long titles break unevenly ("Spider-Man: Brand Ne...").
- **L7.** Lazy-loaded first image on 24 templates (likely LCP); add `fetchpriority="high"` on the first row.
- **L8.** `.sch-day-num` and similar large numerals lack `tabular-nums`.
- **L9.** Heatmap on Stats clips the last month label ("N") and relies on a horizontal scroller with no visible affordance.
- **L11.** The Stats genre bar uses 14+ hues, which breaks "no fourth media-type colour". It reads as a legend lookup, not a chart.
- **L12.** `DESIGN.md` is out of date against the CSS in three places (hero facts as small caps; Wrapped not mentioned; `glass-bar` not listed as an exception).

---

## 3. Page-by-page findings

**Shell (top bar, footer, bottom nav).**
Strong: one bar, current page with a 2px amber underline, search on `/`. Weak: nav labels carry icons at 18px beside 14px text, so the bar feels busy for nine items; "Anime" icon is a sparkle that is also used for "Surprise me" and "Wrapped" (three meanings, one glyph); the wordmark link is 26px tall. Footer shows the version in monospace, which is fine, but the credit links sit at 12.5px `ink-faint` (fails contrast, H1). No skip link (H3). Layout jumps between pages (M3). Bottom nav is glass (H5).

**Home.**
Best page in the product. Recently Watched as 16:9 cards, then posters, is exactly right. Issues: the empty "Watchlist Queue" takes a full heading and a joke sentence where it could take one line; the right rail repeats a headline "0.0 hrs / 0 eps" with a red "100% vs last mo" when nothing happened; "On This Day" items show a star and a number with no label. The three recommendation rows (Movies, TV, Anime) are identical in shape and in weight, so the page scrolls 1,800px with no change of pace. Consider limiting Home to one recommendation row and moving the others to the type pages. Mobile: Up Next precedes Recently Watched, which puts an empty state above real content.

**Movies / TV / Anime (Discover).**
Clean grid, tabs with an underline, a single "Filters" button. Metadata line "2026 · ★ 8.7 · Animation" is good. Issues: titles truncate in two columns of 7 ("Spider-Man: Brand Ne..."), which hurts at 175px poster width; tabs and the type nav duplicate each other's role (Movies tab strip inside the Movies page); 118 elements under 12px; "Filters" is the only action and is 31px tall.

**Title detail.**
The backdrop-to-page fade is well done. Issues: overview text runs across the bright part of the backdrop (to x=1100) with no extra scrim, so legibility depends on the image; the poster shows the logo and the caption "Breaking Bad · TV · 2008" repeats the title directly under it; the action row (M1); the status chip "Ended" is amber; the label row is uppercase tracked (M2); the progress block "11% 5h 15m · 7 ep / LEFT 41h 15m · 55 ep" mixes four numbers in one small tile and uses a purple fill that exists nowhere else on the page; cast row is good. On mobile the overview scrolls under the bottom bar.

**History.**
Day-grouped poster wall with sticky day headings is a good, distinctive choice. Issues: three plays leave 60% of the row empty, so short days look unfinished; the 24px remove "x" on every poster is small for a destructive control (the confirmation flow was not exercised); "Select" and "Filters" are different button styles side by side; the search field is 21px tall inside a 40px wrapper.

**Calendar.**
Grid is legible and today is marked with an amber outline. Issues: the right rail shows only "Nothing upcoming matches these filters - try widening them" in `ink-faint` (H1); three button styles in the header (segmented, outlined Subscribe, ghost Refresh); day numbers are 12.5px in `ink-faint`; month arrows are a 20px hit area (H4); out-of-month cells have almost no contrast against the page (intentional, but verging on invisible).

**Lists.**
Minimal, which suits one list. Issues: a 16:9 empty collage tile with an icon and "No titles yet" is the biggest object on the page and says nothing; "+ New list" mixes a plus glyph with the label; the page has no scrollbar so its right edge shifts (M3).

**Stats.**
Information is good; presentation is the most "dashboard-like" screen in the app. Issues: M8, L9, L11; Peak Hours and Daily Breakdown use dim brown bars that read as disabled; the pie chart under "Split by Type" duplicates the stacked bar above it; Achievements shows a lock/unlock list in the same weight as the charts. Mobile page is 3,638px tall.

**Settings.**
Good information architecture (Personal / Data / Server). The sidebar groups collapse, the content column is readable, and cards were recently shrunk to match. Issues: M6, M7, copy slips (M10), 26 elements at 11px on the first tab, `s-foot` notes at 4.03:1. On mobile the section picker is a "Connected Apps" dropdown that does not look like a control until tapped.

**Search.**
Strong: one field, three result groups, clear headings. Minor: "In your library" shows a heading and a grey sentence when empty (fine); the type filter sits below the field rather than with it.

**Wrapped.**
See M4 and M2. Additional: the saved-recaps section and the preview card compete for the centre column; the preview image has text at 6-7px effective size, which is fine as a preview but means the page shows illegible copy at rest; "Make these automatically" is an underlined inline link inside the subtitle.

**Notifications.**
Short page, same shell. Empty-state only in the preview data, so not rated.

---

## 4. Recommendations

### 4.1 A darker, more professional treatment (keeps the orange)

The amber (#e8a63c) is already a muted gold-orange and works. The product does not need a new palette. It needs fewer places where colour is spent.

| Token | Now | Proposed | Why |
|---|---|---|---|
| `base-100` | #0e1014 | #0b0d11 | slightly deeper, still not pure black |
| `base-200` | #16181d | #13151a | keeps the 1.3x step to base-100 |
| `base-300` | #1f2229 | #1b1e25 | |
| `line` | #262930 | #23262d | |
| `ink-faint` | #737887 | #8a8fa0 | AA on both bases (H1) |
| `primary` | #e8a63c | #e8a63c | unchanged |
| `on-primary` | scattered #1a1305 | `--color-on-primary: #1a1305` | one token, used by avatar too (H2) |

Rules for accent use: amber appears on at most one filled control per screen, the active nav underline, progress fills, the focus ring and the selected state of a segmented control. It stops appearing on status chips (use `ink-dim` text with a dot), heatmap-level-1, and format pickers.

### 4.2 Remove or demote

- The glass bottom bar (H5).
- Uppercase tracked labels (M2).
- The KPI strip on Stats (M8).
- The twelve-colour genre bar. Use the three media colours plus `ink-dim` tints for rank, with the label inline.
- The pie chart. The stacked bar already says it.
- The empty 16:9 tile on Lists when a list has no titles; show a one-line row with "Add titles".
- The "Wrapped" boxed lockup in Bebas inside the Wrapped page bar.

### 4.3 Structural fixes

1. Add `scrollbar-gutter: stable` to `html` (M3).
2. Add a skip link, per-page meta descriptions, and labels on poster links (H3).
3. Make pointer-coarse hit areas 44px without changing the visual size (H4).
4. Collapse the button set to primary / soft / ghost / danger (M5), and map the Wrapped, Settings and Schedule button classes onto them.
5. Move every colour literal into tokens (M9); `grep -n "rgba\|#[0-9a-f]\{3,6\}" static/src/app.css` after the `:root` block should return nothing.
6. Put Wrapped inside the shell (M4).

### 4.4 Documentation

Update `DESIGN.md`: section 3 and 6 (labels), section 7 (glass exception removed, not added), a "Buttons" paragraph naming the three recipes, and the contrast values for `ink-faint`. The doc is currently the best asset in the repo; it should not contradict the code.

---

## 5. Implementation priorities

**P0: one sitting, low risk, high payoff.**
1. `ink-faint` to #8a8fa0 (H1).
2. Avatar initial colour (H2).
3. `scrollbar-gutter: stable` (M3).
4. Skip link, meta description, poster-link labels (H3).
5. Fix the "yet -ask" strings and replace the em-dash joke on the empty watchlist (M10).

**P1: a day or two.**
6. Solid docked bottom nav and bottom padding on `<main>` (H5).
7. Coarse-pointer hit areas (H4).
8. Title-page action row: one amber primary, two quiet secondaries (M1).
9. Delete uppercase/tracking from labels and update `DESIGN.md` (M2).
10. Amber to `btn-soft` for non-primary Connect buttons (M6).

**P2: a week.**
11. Collapse button recipes (M5) and token-lift colour literals (M9).
12. Move Wrapped into the shell (M4).
13. Rework the top of Stats (M8) and the genre chart (L11).
14. Calendar rail/width and Lists empty state (M7).

**P3: polish.**
L1 to L12, plus a rendered review of Activity, admin pages, modals and the schedule pane, which this audit did not cover.

---

## 6. Subjective improvements

These are opinions, not defects.

- **Make the poster wall the brand.** The best moments (Recently Watched, History, Movies) are when the artwork is large and the interface is quiet. The weaker moments are the ones where Spool draws its own graphics (pie, KPI strip, Wrapped lockup). Spend design effort on typography and spacing of the lists, not on new chart styles.
- **Give Home a point of view.** It is three identical recommendation shelves under the useful part. A single "Up next for you" row with progress, followed by one shelf that changes (new episodes this week, unfinished movies, or a random watchlist pick), would feel like a tracker instead of a streaming homepage.
- **Use the wordmark less decoratively.** Bebas Neue is excellent at 26px in the bar. Reusing it in Wrapped and in saved images makes the brand louder than the content.
- **Let the title page breathe.** The hero carries poster, title, facts, genres, ratings, tagline, overview, cast, four buttons and a progress tile above the fold. Moving cast below the fold and shortening the overview to three lines would make the backdrop do more work.
- **Reconsider the icon set.** Every nav item has an icon plus a label; on a nine-item bar either would do. Dropping the icons on desktop would calm the bar and free about 170px.
- **Empty states should be one line.** Several pages answer "nothing here" with a heading, a sentence and sometimes a joke. A single line in `ink-dim` reads as more confident.
- **Name things once.** TV / TV Shows / TV Series, Alerts / Notifications, Browse / Movies-TV-Anime, Stats / Wrapped. Pick one word each and use it in nav, title, tab and button.
