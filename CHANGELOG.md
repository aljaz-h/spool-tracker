# Changelog

All notable changes to Spool are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`) — patch
for fixes, minor for new features, major for anything requiring a manual
migration/env step or breaking an existing workflow.

## [Unreleased]

## [0.188.0] - 2026-11-08

### Changed
- Moving around the main navigation (the top bar, the phone's bottom bar and
  the Movies / TV / Anime switcher) no longer reloads the whole document: the
  fonts, styles and scripts stay loaded and only the page body is swapped,
  with the address, title and back button still working. Back and forward now
  refetch the page instead of restoring a saved snapshot (htmx's snapshot
  cache is turned off: it stored household data in the browser and restored
  Alpine-driven pages badly).
- Hovering a nav link for a moment (or touching it) asks the server for that
  page once, ahead of the click, so what it needs from the server's caches -
  mostly TMDB lists - is warm when you open it. It does not hand the page to
  the click, and it is skipped on data-saver and 2g connections.

### Fixed
- Pages that had to survive a swapped body now clean up after themselves: the
  Movies / TV / Anime hero stops its rotation timer when you leave, the
  Discover grid's resize handling is removed with the grid, and the Calendar's
  month-sync listener is registered once however often the page is opened.

## [0.187.0] - 2026-11-08

### Changed
- A tracked title's page now goes out with its header, poster, synopsis and
  your own watch state straight away, and loads the slow TMDB-backed parts
  right after behind skeletons of the same size: the cast and director(s)
  (hero row, "Directed by" and the Cast & Crew tab), the trailer and media
  gallery, the collection, where to watch and similar titles. They arrive in
  one request (`/titles/<id>/extras/`, signed-in profiles only, like the page
  itself); if it fails, each placeholder says what couldn't load with a Try
  again button. Pages for titles not yet tracked (the preview page) are
  unchanged. Fan art was already filled in from a background fetch, so it
  is untouched.

### Fixed
- "Directed by" no longer shows a stray space before the comma between two
  directors.

## [0.186.0] - 2026-11-08

### Changed
- The Dashboard now shows its frame and the fast local rows (stats, Up Next,
  Continue Watching, the watchlist, Start Watching) straight away, and loads
  the slower blocks afterwards behind skeletons of the same size: the three
  Recommended rows, Recently Watched, On This Day and Social Activity. Each is
  its own small request with the same login and profile checks as the page,
  and a block that fails shows "Couldn't load ..." with a Try again button. A
  block with nothing to show disappears once it has loaded.

## [0.185.1] - 2026-11-06

### Fixed
- Server Integrations: blanking the WeTrakr Redirect URI field now clears it
  (it used to mean "leave unchanged", so it could only be removed together
  with the client ID and secret). The docs now say to add Spool's callback to
  the WeTrakr app instead of relying on its default `urn:` redirect.
- The "Code from WeTrakr" box no longer stays stuck on Connected apps: it
  disappears once the Redirect URI is cleared or changed, and it has a Cancel
  button.

## [0.185.0] - 2026-11-06

### Added
- WeTrakr can now be connected when its app is locked to a Redirect URI other
  than Spool's own callback, such as the out-of-band `urn:ietf:wg:oauth:2.0:oob`.
  Server Integrations has a new optional Redirect URI field (or set
  `WETRAKR_REDIRECT_URI`); leave it blank to keep using Spool's callback. With
  an out-of-band URI, WeTrakr shows a code after you approve, and Connected
  apps shows a "Code from WeTrakr" box to paste it into and finish connecting.
  Needs `python manage.py migrate` (one new column).

## [0.184.2] - 2026-11-05

### Fixed
- Title lookups by an external id (Nuvio/Trakt/Simkl sync, CSV import,
  scrobbling, Discover and search) now use Postgres JSON containment, which
  the existing GIN index serves. The previous per-key form could not use that
  index and scanned the whole titles table on every call, which added up to
  minutes of database time across a big sync.
- The Schedule page's "Finish line" estimate ran its own watched-episode
  query for every show and movie on your lists (Sentry flagged it as an N+1
  query on /calendar/schedule/estimate/). It now asks once for the whole list.

## [0.184.1] - 2026-11-05

### Changed
- The discover hero rotates every 6 seconds (was 8) and keeps rotating even when the system's "reduce motion"
  setting is on.

## [0.184.0] - 2026-11-05

### Changed
- The Movies, TV shows and Anime hero is now full width and much taller
  (about 70% of the window height), running edge to edge under the top bar.
  The page title and its one-line description sit in its top-left corner, and
  the synopsis gets three lines with a "Directed by" line underneath (shows
  without a credited director say "Created by" instead). Only page 1 has the
  hero; later pages keep the plain title row.
- Title pages: the main cast row now starts with the director(s), then a
  vertical divider, then the cast. Films with more than one director show all
  of them (in the row, the hero's "Directed by" fact and the Cast & Crew tab).

### Fixed
- A stale Discover test that asserted History's scroll-away markup against the
  Movies page.

## [0.183.0] - 2026-11-05

### Changed
- Movies, TV shows and Anime pages rebuilt to the new mockups: a 340px hero
  for the top trending titles (TMDB backdrop, left-to-right gradient, meta
  line, two-line synopsis, View details / Add to list / Trailer, four
  rotating progress dashes), underline tabs (Trending, Popular, Upcoming /
  Airing today / Airing now, Top rated), a "Hide watched" toggle beside the
  Filters button, an active-filter row with "Clear all" and the sort label,
  and a 7-column poster grid with 35 titles per page (5 rows).
- Tiles show the full title (wrapping to two lines) and a coloured type dot,
  type, year and rating; pagination buttons are 44px.
- "Airing today" (TV) and "Airing now" (anime, the last 7 days) are new tabs.

## [0.182.0] - 2026-11-05

### Changed
- The Filters drawer on Movies, TV shows and Anime is rebuilt: 440px wide, "Filters · N active" header, and sections for Genres, Year, Runtime, Rating, Language and availability, and Display (Genres and Year open, the rest collapsed, each showing its current value). Picked chips are raised with a check, the rating slider has white handles and a neutral fill with the range in mono, "Show N more services" is a neutral underlined link, and the Filters button shows a count instead of an amber dot. Amber is only the Apply button, which sits in a fixed footer with Clear all. The stray "Discover" and "Access" headings are gone.
- Display has a Show / Dim / Hide control with text labels for Watched and Watchlisted.
- Runtime options read "Under 90 min", "90–120 min", "120–150 min" and "Over 150 min".

### Fixed
- The Filters drawer stays attached to the Filters button when the page scrolls, and scrolling never closes it. It closes on the X, Escape, a click outside, the button again, or Apply. Scrolling inside it no longer scrolls the page behind it, and it fits the space under the button (above the tab bar on small screens).

## [0.181.3] - 2026-11-04

### Fixed
- A show with several Nuvio progress entries (one per episode, for example S1E8 and an older S1E6) no longer ends up on the oldest one. The most recently played in-progress episode wins, so the Continue Watching card, its order and the Friends "Watching" status follow what you actually played last.

## [0.181.2] - 2026-11-04

### Fixed
- "Watching" in the Friends menu and popup no longer shows for something that was played long ago. A title that first appears in progress, or a different one than last time, only counts as watching if Nuvio's own last-watched time is within the last 45 minutes; the time shown is that real play time. The status also drops after 2 hours instead of 6.

## [0.181.1] - 2026-11-04

### Fixed
- Continue Watching points an anime at its next canon episode instead of an unwatched filler or recap episode, and an anime whose canon episodes are all watched is completed and leaves the row. The same skip applies when an episode is marked watched in the app.
- Continue Watching rows that an older version stamped with a sync's time re-sort once from Nuvio's own play time on the next sync.

## [0.181.0] - 2026-11-04

### Added
- `manage.py nuvio_dump <username>` prints a few raw Nuvio progress and history items so the fields Nuvio sends can be checked. Read-only; never prints a token.
- An anime now counts as completed once every canon episode is watched; filler and recap episodes aren't needed. Checked on every watch, sync and scrobble, and when you open the anime's episode list. Only for anime with known filler data, and a dropped show stays dropped. The percentage shown is unchanged.

### Fixed
- Continue Watching is ordered by when an item was really played (Nuvio's own last-watched time on the item when it sends one) instead of when a sync noticed the change, so a movie you just watched no longer sits below older items.

## [0.180.1] - 2026-11-04

### Added
- The Friends menu (and the mobile More sheet) has a "Me" row above Friends that opens your own popup, including what you are currently watching.

### Changed
- Filters popovers (Discover, History, Logs) no longer close the moment you scroll. They stay under the Filters button on screen and close when you click the button again, click outside, press Esc, or scroll the button far enough away that only about 15% of the panel would still show.
- Settings > Profiles has an Owners and a Members section (each with a count) in one panel. Rows show just "Username <username>", the signed-in profile gets a "You" badge and no buttons, and the buttons are labelled "Make owner", "Demote to member", "Reset password" and "Delete profile". The Members section is hidden when there are none.
- Settings > Notifications indents each setting under its group heading, with a divider line before each group.

## [0.180.0] - 2026-11-03

### Added
- Friends menu shows what a friend is watching: a wider menu (full width on small screens) with the title, a dot and "Watching · Movie / TV S4 E2 / Anime S14 E13" on the right of each row. The row opens the friend popup; the title is its own link to that title's page.
- Friend popup has a "Currently watching" card (poster, title, type and episode, how long ago) that links to the title. Hidden when they aren't watching or have "Share my activity" off.
- "Watching" comes from Nuvio only. Each Nuvio sync compares the profile's latest in-progress item with the previous sync's snapshot: progress up or a different episode means watching; nothing moved, finished, marked watched or gone from continue-watching means not. A title that appears in progress shows as watching right away, and stops once a later sync sees the same percentage. Stored on the profile, never computed on page load, and dropped after six hours without a change.

### Changed
- "Full stats →" in the friend popup is light grey instead of amber.

## [0.179.3] - 2026-11-03

### Changed

- Settings > Profiles shows each profile's sign-in username next to its display name (for example "Member · Username maya.k"), so an owner can tell who signs in as whom.

## [0.179.2] - 2026-11-03

### Fixed

- A streaming service is shown once. TMDB lists each plan and storefront as its own provider, so a title's "Where to watch" could show Paramount Plus five times (Paramount Plus, Premium, Essential and its Amazon and Apple TV channels), and Settings > Preferences offered Paramount Plus Premium and Paramount Plus Essential, and Peacock Premium and Peacock Premium Plus, as separate services. They are now one entry each ("Paramount Plus", "Peacock"): in the Streaming services chips, in the Discover filter and in "Where to watch".
- Picking a service filters on all of its plans, so choosing Paramount Plus still finds titles on any Paramount Plus tier, and a service you picked before this (saved as one plan's id) shows as picked. Products that only share a name stay separate (YouTube Premium is not YouTube, Netflix Kids is not Netflix).

## [0.179.1] - 2026-11-03

### Fixed

- The date filters on Settings > Logs (`log_date_from`, `log_date_to`) were put into a script expression as typed in the address, so a crafted link could run script on an owner's Logs page. They are now only kept when they are a real date (and written back out as one), and the script expression escapes them as well.

## [0.179.0] - 2026-11-03

### Changed

- Settings is rebuilt from one set of shared pieces (documented in DESIGN.md > Settings), so every page now has the same frame, widths, rows, buttons, badges and wording.
  - **Page frame:** every page opens with a title and a one-line description (no icon tile), then its panels. Two content widths: narrow (720px) and wide (1040px, for Profile, Notifications, Connected apps, Import and export, Logs and Query performance). Logs is therefore no longer full width.
  - **Controls:** buttons, selects and fields are 44px tall (a 36px compact size exists for dense desktop tables only), and small grey text is the lighter grey. Each page has at most one amber button.
  - **Badges:** one status badge (a dot and a short word) replaces the "Connected", "Configured", "Not configured" and plain "On" variants. "Needs attention" is coral (Logs too), not amber.
  - **Saving:** toggles, selects and chips show "Saved" beside the control when they save, or say they couldn't. The Integrations form uses the unsaved-changes bar instead of an always-visible Save.
  - **Wording:** sentence case everywhere, including the sidebar (Connected apps, Import and export, API and sharing, Activity log, Danger zone), and links are underlined light grey, never amber.
- **Preferences:** sections instead of icon-tile cards, toggles instead of checkboxes, and Favorite genres and Streaming services as chips (the chosen ones are raised and checked, not amber).
- **Security:** labels above the fields, 44px show/hide buttons, a check that the new password is at least 8 characters and that the two match, "Change password" as the page's one amber button, and two-factor authentication with the standard "On" badge and a red outlined "Turn off".
- **Connected apps:** only accounts that keep syncing. A "Connected" section (each with its direction, schedule, last synced and Disconnect) and an "Available" list with a Connect button and a badge (Not connected, or Not set up when the server owner hasn't set it up). AniList and Kitsu ask for the username in the row; Nuvio opens its sign-in under the row. Sync now is the amber button only when a single account is connected.
- **Import and export:** file-based only, with a drop area, a Choose file button, the chosen file (name and size, with a remove button) and an Upload button for Trakt, Simkl, Letterboxd, IMDb, TV Time, Anime list, Spool backup and Other tracker, then Export and a link to automatic backups. The provider Connect buttons are gone from here (they live in Connected apps); Trakt, Simkl and Anime list link to "Connect an account instead". Notifications, My schedule, Wrapped, API and sharing, Backups, Danger zone, Profiles, Integrations, Maintenance, Activity log and Logs follow the same pieces.

### Fixed

- The Favorite genres chips on Preferences stayed as grey placeholders when the genre list failed to load or the section never scrolled into view. They now load as soon as the tab is shown, a failing TMDB lookup shows a message instead of an error, and there is a Try again button.
- Small buttons (Run, Test, Preview, Back up now, Send test and the like) were 24 to 32px tall; they are 44px now.

## [0.178.1] - 2026-11-02

### Fixed

- The Share my activity switch no longer says "Saved" when the save didn't happen. If your session had expired, the request was bounced to the sign-in page and followed, which looked like a success. It now counts only the endpoint's own 204 as saved, and an expired session (a redirect to sign-in, or the 403 the CSRF check gives once the session is gone) puts the switch back and says the session has ended, with a link to sign in.

## [0.178.0] - 2026-11-02

### Changed

- Settings > Account > Profile is reworked.
  - **Header:** "Member since <month year> · N apps connected · Manage apps" (the link goes to Connected Apps) replaces the Trakt/Simkl/WeTrakr "not connected" strip; only what is connected is counted.
  - **Photo:** one 96px round avatar with a camera button on it and Change photo / Remove buttons, instead of the native file input and the red trash button. Click the avatar, the camera or Change photo to choose a file, or drop one on the avatar; the new photo shows in the avatar before you save. Remove only appears when there is a photo, and is undone by Discard. The same limits apply (JPG, PNG or WebP, up to 5 MB), now also checked in the browser.
  - **Fields:** every label sits above its field. Display name is checked for uniqueness as you type (about 300 ms after you stop) and shows "Available" or why not; the server enforces it too (ignoring case; a name that is already a duplicate can stay while it is unchanged) and the 50-character limit. Bio has a live counter and is limited to 80 characters on the server too (an older, longer bio is kept until it is edited).
  - **Avatar color:** eight clearly different colours as 44px round buttons with a visible check and a name for screen readers (the other palette colours that read as duplicates are gone from the picker, but a profile that already has one keeps it). New profiles start with one of the eight.
  - **Saving:** the always-visible "Save profile" button is gone. A bar appears at the bottom of the form only when something changed ("You have unsaved changes", Discard, Save changes); saving says "Saved." and leaves the page clean, and leaving with unsaved changes asks first.
  - **Right-hand cards** (on a server with more than one profile): "How housemates see you" shows your avatar, name, bio and what you watched in the last two weeks, and updates as you type; and "Share my activity", now a switch that saves the moment you flip it and puts itself back with a message if the save fails. Two columns on wide screens, one on narrow ones.
- One avatar component (`partials/avatar.html`) is used by the Activity feed and the Settings preview: the photo, or the initial in dark ink on a light colour and white on a dark one. The Settings avatar no longer uses white on every colour.

## [0.177.2] - 2026-11-02

### Changed

- A person's page opens with a full-width header like a movie, TV or anime page's, instead of a bordered box: the portrait, name, departments, bio, actions and your stats sit on the page grid over a dimmed collage of their best-known posters, which stays to the right (behind the stats) and fades into the page at the bottom. The "Add unwatched to a list" menu is no longer at risk of being clipped by the header.

## [0.177.1] - 2026-11-02

### Fixed

- The longest streak (Stats, Dashboard) was too low for anyone who watched more than one thing on a day. The query that lists the days you watched something returned a day once per watch instead of once, so a second watch on the same day looked like a break in the streak. Each day is now counted once. It also reads far fewer rows, since it was returning one per watch (about 25,000 for a long history) instead of one per day (a thousand or so). The same default-ordering mistake made three "which of these titles has this profile watched" lookups read every watch row; they now read only the distinct ids.
- The seven tests that had been failing are fixed, none of them an app bug except the streak one above: a test that failed on the word "Filters" appearing in a script comment, four release-sync tests that depended on the machine's timezone and (with a TMDB key set) made real TMDB and TVmaze requests, a backfill test that hardcoded a date that has since passed, a stale expectation that a TV show's countries are ISO codes (they are resolved to names), and the demo-seed test, which failed through the streak bug.

## [0.177.0] - 2026-11-02

Run `python manage.py migrate` (migrations 0090 and 0091 add a profile setting, an account field and a few new choices; no data changes). This release adds one dependency, `defusedxml`, to `requirements.txt`, so rebuild the image.

### Added

- Settings > Logs is grouped into runs. Entries fold into one collapsible row per category per day under Today / Yesterday / date dividers: Nightly maintenance, Hourly jobs (MDBList refresh), account syncs (per profile and provider, with a cadence such as "every 30 min") and manual actions (single rows). Each row shows a status pill (green `6/6 ok`, amber `Warning`, red `Failed`, blue `running`), a "1 failed · 1 not run" note and a square per job. Groups with a failure, a missing job or a running entry open by default.
- Each nightly run is checked against the fixed list of six jobs (Release Sync, Release Notifications, Update Check, Watchlist Reminders, Log Retention, Reclassify Anime). A job with no log entry shows as a hatched "not run" row and counts against the total, and a night with no entries at all shows as `Failed · 0/6`. A job isn't called missed until 05:30 (an hour after the last one is due), and the check is off while a profile, action, provider or status filter is set.
- A Grouped / Flat list toggle (Flat is the old table), category chips with counts, Expand all / Collapse all, and a "Needs attention" strip with a Show problems button. The failure-streak banners, the filters and the 4-second polling while something runs are unchanged.
- Settings > Preferences > Appearance has **Blur unwatched episodes**, off by default and per profile. When it is on, the thumbnail of an episode you haven't watched yet is blurred: the next episode on the Continue Watching card, and every unwatched row in a show's episode list (a thumbnail shows itself while the pointer is on it, on devices with a mouse). Watched episodes, movies and titles with no episode still are untouched.
- AniList and Kitsu import and sync, by public username. Settings > Connected Apps (and Import & Export) has a card for each: type the username, Connect, choose what it may do, review the import, and Spool then re-reads the list on the card's schedule. No app registration or server setup is needed. The name is checked on connect (a typo or a private list says so at once); AniList lists are read in chunks of 500 and Kitsu's follows its own page links. Titles are matched by their MyAnimeList id when the service has one, otherwise by name and year. Plan to watch goes on the Watchlist. Sync logs and notifications work like the other providers'.
- Anime list file (XML) import: upload a MyAnimeList "Export my list" file (`.xml` or `.xml.gz`), or an AniList/Kitsu list converted to one, straight into Import Review. Entries with no dates are dated the day of the import. The file is parsed with `defusedxml`, so a crafted file can't expand entities or read local files.
- MyAnimeList's own sync was already there (it needs the server owner to register a MyAnimeList app); the new importers share its code, so statuses, scores, "watched N episodes" and the watchlist behave the same everywhere. `docs/IMPORTING.md` covers all three.

### Changed

- The Logs tab uses the full width of the page instead of stopping at 80rem (1585px of table at a 1920px window, up from 1040px), and the Detail column gets more room on very wide screens. The other Settings tabs keep their widths.
- The log row is its own include (`settings_logs_row.html`) and the table another (`settings_logs_entries.html`), shared by the flat view and every group. The grouped view pages by run (10 per page).

## [0.176.1] - 2026-11-01

### Changed

- The phone tab bar is back to the floating frosted-glass capsule (rounded, blurred and translucent, with a rim and soft shadow). DESIGN.md now names it as the one deliberate glass surface.

## [0.176.0] - 2026-11-01

Run `python manage.py migrate` (migration 0089 adds an index; nothing else changes in the database).

### Changed

- Faster household feeds. The Activity page and the Dashboard's Social Activity row sort every shared watch by time, and the only index started with the profile, so Postgres sorted the whole table to return a dozen rows. `WatchEvent.watched_at` now has its own index (migration 0089), which lets those queries read the newest rows and stop.
- The same feeds no longer read columns they never use: the profile's TOTP secret and backup codes, push URLs, webhook, preferences and bio, and the title's large cast, crew and gender lists. This makes each row lighter and keeps secrets out of feed queries.
- The "recently watched titles" lookup behind Recommended for you now asks the database for the newest N distinct titles (two queries) instead of loading a profile's entire watch history for that media type and stopping part-way through the loop.

## [0.175.3] - 2026-11-01

### Security

- The Wrapped year and month pickers no longer navigate to text read from the page. The options now hold plain numbers and the script builds the address from fixed text plus those numbers (anything that isn't a whole number is ignored). Fixes the remaining CodeQL "DOM text reinterpreted as HTML" alert on `wrapped-actions.js`.

## [0.175.2] - 2026-11-01

### Changed

- Dependency updates (patch and minor releases that fit the versions Celery, Kombu and Playwright require): django-ninja 1.7.3, pydantic 2.14.2 / pydantic_core 2.50.2, oauthlib 4.0.1, python-crontab 3.4.0, amqp 5.4.1, billiard 4.3.1, click-repl 0.4.1, annotated-types 0.8.0, wcwidth 0.9.4; Tailwind CSS 4.3.3 (build tools). Left alone on purpose: Django 6 (5.2 is the long-term-support line), redis 8 (Kombu needs below 6.5), Playwright (needs a matching browser reinstall), daisyUI 5.7 (a large jump that can shift component styles), and pyee (Playwright pins it).

## [0.175.1] - 2026-11-01

### Security

- Wrapped's Discord upload now posts to an address rebuilt from known-safe parts (one of Discord's own hosts from a fixed table, a numeric webhook id, a URL-safe token) instead of the typed-in text, so the server never connects to anything the user typed; a query string or extra path is dropped. Fixes the CodeQL "full server-side request forgery" alert.
- The saved-recap image view resolves the real file path and refuses anything outside the recaps folder, on top of the token check. Fixes the two CodeQL "uncontrolled data used in path expression" alerts.
- The Wrapped year and month pickers no longer use an inline `onchange="window.location = this.value"`; a small script follows the chosen address only if it is on this site. Fixes the two CodeQL "DOM text reinterpreted as HTML" alerts.

## [0.175.0] - 2026-11-01

### Changed

- The person (cast and crew) page is redone. A header with a dimmed backdrop of their best-known posters, a bigger portrait, department chips (Actor, Director, Producer...), born date, age and place, a short bio with "Read more", and a stats card: "X of Y watched" with a green progress bar, total watch time, a bar per department, and which housemates have watched how many of their titles (and how many you share).
- "Known for": six larger posters with the part they played ("as Gus Fring"), ranked by TMDB popularity and skipping talk-show, archive and one-or-two-episode guest appearances, with the usual watched, watching and rewatch badges.
- "Career by year": credits per release year as a bar chart drawn in the page, watched titles in amber and unwatched in grey.
- One Filmography replaces the separate "You've Watched", "Best Works", Acting and Directing grids, so a title appears once however many departments it is under. Tabs for All, Acting, Directing, Producing (and Writing when they have it) with counts; Watched/Unwatched and Movies/TV filters; sort by newest, highest rated or most popular; a grid/list switch that is remembered per browser. The grid groups credits by decade (8 across; 3 on phones) with the watched, watching and x2 badges; the list is a table with year, poster, title, role, type, rating, your status (with the episode while you are watching) and an add-to-list button, stacking into rows on phones. "Show all N credits" replaces the faded loading row.
- Poster tiles use w342 with a 1x/2x `srcset` and the list rows w185; the first row is high priority.
- Producing credits are now included (TMDB's Production department), each credit carries its role (character or job), and the per-department cap is 60 credits instead of 40.

### Added

- "Pick something unwatched" (a random released credit you haven't watched, taken to the title's page or its TMDB preview) and "Add unwatched to a list" (puts their released, unwatched credits on a list you choose, looking up at most 20 new titles per click and saying when to run it again).

### Fixed

- The person page's stats did one query per watched title and per housemate; they are now a fixed number of queries however many credits there are (covered by a query-count test).

## [0.174.0] - 2026-11-01

### Changed

- Faster pages, fewer bytes. Poster tiles in grids and rows now offer the browser two sizes (`srcset`: w185 for normal screens, w342 for 2x and up) instead of always sending w342, so a 1x screen downloads roughly half the image data on Home and Browse; the big poster on a title page stays w500. Posters are still stored at w500 and resized at render time, so existing titles pick this up with no migration or re-sync.
- The streaming-provider logos in the Browse filter panel and in Settings > Preferences (about 150 of them, shown at 18-20px, loaded eagerly even while hidden) are now `w45`, lazy, and have a width and height. Movies/TV/Anime pages no longer start about 150 extra requests.
- The UI fonts (Bebas Neue, Public Sans, JetBrains Mono) are bundled with the app (`static/fonts`, woff2, served with a long-lived immutable cache) instead of loading from Google Fonts: no third-party request or render-blocking stylesheet, and the app works offline. Public Sans and JetBrains Mono are variable files, so every weight in use comes from one file each. Latin-extended files (Slovenian, Polish, Czech... letters) are declared by unicode-range and only download when a page shows such a letter. The body face and the wordmark are preloaded. The Wrapped PNG renderer embeds the same files, so recaps render without network access.
- The first row of posters on Home (Continue Watching, Recently Watched), the first row of Movies/TV/Anime, and the title page's poster and backdrop load with `fetchpriority="high"` and are never lazy; everything below the fold stays lazy.
- Late-loading blocks show a skeleton in the shape of the real content instead of a "Loading..." line: the Dashboard's My Schedule card, Calendar > My schedule, the schedule's Finish line card, the title page's Household panel, Settings > Preferences (favorite genres) and Settings > Query performance. One shared `.skel` style, built from surface tokens; the faint sweep stops under `prefers-reduced-motion`.
- The household avatar on a title page now picks dark or white initials from the avatar colour, like the other avatars.

### Added

- `tmdb_size` and `poster_srcset` template filters.

## [0.173.0] - 2026-11-01

### Changed

- UI/UX audit pass (see `UI_UX_AUDIT.md`). The palette is a touch darker (page `#0b0d11`, surfaces and hairlines stepped to match) with the amber unchanged, and the faint text colour is lifted to `#8a8fa0` so captions, placeholders and empty states meet WCAG AA contrast.
- The phone tab bar is now a solid bar docked to the bottom edge instead of a floating frosted-glass capsule, so it no longer covers the last row of content or breaks the no-glassmorphism rule.
- The title page's main action ("Watching") is now the amber primary instead of cyan, its labels are sentence case, and the hero status chip ("Ended", "Returning") is neutral instead of amber. Labels across the app (title facts, schedule, Wrapped, Year in Review, Settings tables) are sentence case without letter-spacing, as `DESIGN.md` already required.
- Profile avatar initials pick dark or white ink from the avatar colour, so the letter stays readable on light colours (it was white on gold, 1.8:1).
- Settings → Connected Apps: "Connect" buttons are the quiet style rather than amber, so the list no longer implies an order. "Not available yet - ask" copy fixed; the empty watchlist line is plain; a falling daily average or month-over-month figure is no longer shown in error red.
- The page's right edge no longer shifts 15px between long and short pages (the scrollbar lane is always reserved).
- Small controls (calendar month arrows, History remove buttons, small buttons and segmented controls on touch screens) get a 44px hit area without changing how they look.
- Headings balance their line breaks; schedule day numbers use tabular figures; one `transition-all` removed.

### Added

- A "Skip to content" link as the first tab stop, and a default meta description.
- Colour literals in `app.css` (scrims, shadows, white-on-artwork, the soft button fill) now go through tokens.

## [0.172.2] - 2026-11-01

### Changed

- It's now clear how a recap gets saved: making the image (the first Download PNG or Copy image click for a period) saves it under **Saved recaps**. The buttons say "Saved ✓" / "Copied and saved ✓", the page updates in place (no reload) so the new recap appears there, and the hint under the preview says so. Monthly recaps are kept for a year, yearly ones for good; making another image for the same period replaces the saved one.

## [0.172.1] - 2026-11-01

### Added

- **Copy image** next to **Download PNG** on every Wrapped recap (Year, Month and Year in Review): one click copies the picture so it can be pasted straight into a chat or a post. Both buttons make the image first if it doesn't exist yet, so it's a single click either way, without leaving the page. Saved recaps get a copy button too. Copying needs https or localhost; elsewhere the Copy buttons hide and Download remains. Downloaded files say what they are and when they were made: `spool-wrapped-yearly-2026-20261101-1443.png`, `spool-wrapped-monthly-2026-10-...`, `spool-wrapped-year-in-review-2026-...`, `spool-wrapped-all-time-...`.
- **All Time can be downloaded and copied as an image too** (the same one-click buttons; the latest copy is kept). New migration `0088`.

### Fixed

- Big numbers on the cover and stat tiles (1,573 plays, 444d 21h watched) no longer run past the tile's border: each number sizes itself to the width of its tile, on Year in Review and All Time.

## [0.172.0] - 2026-11-01

### Changed

- **Wrapped has its own page.** The Year, Month and Year in Review recaps now open in their own full page inside Spool - no top nav or footer, a slim bar to switch period, and one centered column - instead of sitting in the app's dashboard layout.
- **Year in Review is a page to read through**, laid out like Trakt's but in Spool's own look: a cover with your top posters, the first play of the year, then Shows and Movies each with a weekly activity chart, hours and plays per month, the most-played titles, networks/studios as bubbles, a genre bar and a world map of the countries you watched, followed by most-watched cast, directors and writers, the last play of the year and a closing "Thanks" card. Charts are drawn in the page itself, with the map data bundled in Spool, so nothing is loaded from outside.
- **Every recap can be downloaded as a PNG**, the Year in Review included (a full-height image of the page, charts and map drawn in, made the same way as the cards and saved with the others).
- **New share image** for the Year and Month recaps: a 1080-wide card with the period as a huge title, three stat pills (hours, titles and longest streak for a month; hours, plays and titles for a year), a first-play and a last-play banner over the title's backdrop, the top 3 shows and top 3 movies, and the movies/TV/anime split with the top genre. It is drawn on the server (artwork is fetched there and inlined). The Wrapped page shows it as a scaled preview with a Format choice (Portrait 4:5, Square, Story 9:16) and tick-boxes for what appears (first and last play, top 3, split, hide my name); the choice is kept in the page address and used by "Create PNG"/"Re-create". Saved recaps show a thumbnail of their image. The four old card styles are replaced by this one design, so the Card style setting is gone.
- **Year in Review additions:** most-watched cast is split into Actors and Actresses (from TMDB's gender field, shown with a 2x2 grid with Directors and Writers, each with a title count), and **Show Trends / Movie Trends** show the most popular title that premiered each month of the year (TMDB, cached) with the ones you watched ticked. Run `manage.py enrich_titles_reports_metadata` once so existing titles get cast genders. New migration `0087`.
- **Year in Review and All Time are wider and more visual** (a 1240px page that stacks on narrow screens): a backdrop mosaic behind the cover with a fan of five posters and big stat tiles, first and last play banners over the title's backdrop, section headings, a weekly chart with gridlines and hour labels, the top five shows and movies as large poster cards with big rank numbers over a dimmed backdrop (places 6 to 10 as a list with bars), networks and studios as letter tiles with thick bars, 96px genre strips with percentages, a large world map with the ranked countries beside it, and people cards with a portrait for the featured person (looked up on TMDB). The same design is used for the Year in Review image export (now about 1320px wide).
- **All Time tab** next to Year, Month and Year in Review: totals since your first watch, where the time went, release-year histograms for shows and movies, top 10 shows and movies, genres, a countries map, networks and studios as bars and the four people cards.
- The compact Wrapped card's "Spool" style follows the app's current typography (Public Sans for headings and labels; Bebas only for the wordmark and big figures).

## [0.171.1] - 2026-11-01

### Fixed

- Turning on built-in Wrapped recaps no longer also turned on sharing your watch history with an external app through the Reports API. They are now two separate settings: recaps use their own switch (off by default, migration `0086`), and the old "share with an external Wrapped app" setting stays as it was. A small "External Wrapped app" card appears under API & Sharing only while a profile is still sharing, so it can be turned off.
- A failed Discord delivery no longer logs or stores the webhook address (its token is a secret); only the HTTP status or error type is kept.

## [0.171.0] - 2026-11-01

### Added

- **Wrapped is built in.** The monthly and yearly recap cards and the long Year in Review that used to need the separate spool-wrapped app now live in Spool, read straight from your watch history. A new **Wrapped** page (Stats -> Wrapped, or `/wrapped/`) shows any year or month in the app's own look, with a **Year in Review** tab, and **Create image** makes a PNG you can download or share. Four card styles (Spool, Classic, Minimal, Vibrant).
- **Settings -> Personal -> Wrapped** replaces the old card under API & Sharing: turn scheduled recaps on, pick monthly / yearly / Year in Review, the card style, which stats appear, and an optional Discord webhook the finished image is posted to.
- A daily job makes the month and year that just ended for everyone who has Wrapped on (once each, and it catches up after downtime) and sends a "Wrapped ready" notification (bell and push, configurable under Notifications).
- Recaps are saved: yearly recaps and Year in Review are kept for good, monthly recaps for a year after they were made (a daily job removes expired ones and their image files). A "Saved recaps" list on the Wrapped page opens or downloads any of them; re-creating a period's image replaces the old copy.
- PNG rendering uses headless Chromium (Playwright), now installed in the Docker image, so the image is larger. New migration `0085`.

### Changed

- The old "Share watch history with spool-wrapped" switch is now the master switch for the built-in Wrapped; the external Reports API is unchanged for anyone still using it.

## [0.170.1] - 2026-11-01

### Fixed

- Continue Watching now shows real progress for a movie that has no stored runtime: the percent and time left use the length Nuvio reports (stored as `duration_seconds`), then the stored runtime, then TMDB's runtime (the one the title page shows). Spool only stored a runtime once something was watched, so an in-progress movie sat at 0%. A movie that Nuvio showed as 30 minutes left no longer sits at "In progress, 0%".
- Continue Watching is ordered by when you last watched or moved each title, not by when the last sync touched it. Every sync used to re-save every row, which shuffled the whole row; now only a changed position, episode or status counts. Existing rows are ordered by their latest watch (migration `0084`).

## [0.170.0] - 2026-10-31

### Changed

- Settings panels share one pattern: a single panel with a header, then rows with the label and help text on the left and a dark dropdown/input tile on the right, dividers between, amber primary actions, neutral tiles for secondary ones and red text-only Disconnect/Clear. Built on shared `settings_card` / `settings_row` template tags and `.s-*` styles.
- Converted: Connected Apps (Nuvio, Trakt, Simkl, WeTrakr, TMDB, MyAnimeList) with a last-synced strip (amber when overdue) and Review full import / Sync now; Preferences; Notifications and Push Notifications; and the Server Integrations credential cards.
- The sync schedule is now one dropdown that saves automatically on change (with an inline "Saved"), replacing the "Sync every N day(s) at" inputs and Save button. Trakt's "import my lists" option is a row in the same form.

## [0.169.2] - 2026-10-31

### Fixed

- Anime that a player numbers differently from TMDB's own seasons now land on the right season when TMDB lists several: Bleach's Thousand-Year Blood War (Nuvio "S17", TMDB season 2) was recorded as S17E48-50 in History but never marked on the title page. A season far past TMDB's last one now maps to the latest season when the episode fits. Watches already synced under the wrong season move over on the next Nuvio sync (or via `manage.py reconcile_episode_seasons --commit`).

## [0.169.1] - 2026-10-31

### Changed

- Continue Watching cards for shows and anime now show how far through the current episode you are (position over the episode's runtime) and the time left in that episode, instead of whole-show progress, which stays on the title page. Movies already worked this way.

## [0.169.0] - 2026-10-31

### Added

- Nuvio can now sync every 30 minutes or every hour (Settings -> Nuvio -> "Sync"), not just daily, so Continue Watching positions stay current. Choosing "daily or less often" keeps the existing day/time schedule.

## [0.168.0] - 2026-10-31

### Changed

- Continue Watching cards are now one rounded panel: 120px artwork with the title and episode over it, and a single full-width footer button below. The button's background is the progress bar, with the remaining episode count (or "In progress") on the left and "63% · 2d 13h 12m" on the right, on every card including 0%. The separate progress line, percentage text and standalone check button are gone; the footer still opens the same mark-watched menu, keeps the solid green state once watched, and has a hover border and a keyboard focus ring.

## [0.167.5] - 2026-10-30

### Fixed

- The "Show all N credits" button on the person page no longer shows a double-width gap around the number.

## [0.167.4] - 2026-10-30

### Fixed

- Performance (from the query performance page): opening My Schedule no longer queues up behind another request that is mid-plan (the plan row lock was seen waiting 20s+ - a request that finds it locked now just shows the current plan); a fill no longer re-checks each movie's release date once per time window (42k identical queries); the "who is watching this title" lookup behind release notifications no longer joins watch history and progress together (150-500 ms per call); the calendar's release list and the release-sync title list no longer join watch history, progress and watchlists together and de-duplicate (100+ ms each); and title lookups by TMDB id (matching a page of discover results) use one indexed "id IN (...)" instead of an OR of 100+ pairs, plus a Postgres index (migration 0082, Postgres only).

## [0.167.3] - 2026-10-30

### Fixed

- Titles you haven't watched or listed yet now show everything a tracked title does: the IMDb / Rotten Tomatoes / Metacritic / Trakt ratings (cached by TMDB id, new table, migration 0081, and they appear on their own), and for anime the MyAnimeList score, Japanese title, studio and source, MAL's trailer, and the filler/recap badges on episodes (so "canon only" now has something to skip). Nothing is created for a title until you actually track it.

## [0.167.2] - 2026-10-30

### Fixed

- The title logo and Fanart background now show for movies, shows and anime you haven't watched or added to a list yet, not only tracked ones. They are cached by TMDB id (new table, migration 0080) and appear on their own the first time, like on tracked titles.

## [0.167.1] - 2026-10-30

### Removed

- The "Add to a list" card in the side rail of an untracked title - the "Add to list" popup under the poster does the same job (and now has a "New list…" link).

## [0.167.0] - 2026-10-30

### Changed

- Season picker on TV/anime pages redesigned: bigger posters with an "S01" chip, a green check badge once a season is complete, and under each poster a progress button (fills green as you watch, solid when complete; click to mark the season watched, or unmark it when complete) plus a chevron that opens that season's own "Mark episodes" menu (mark season, canon only for anime, mark all, and a new "Reset season progress"). Under that: season name, rating and air date. Seasons TMDB has no episodes for yet show as dimmed "Announced".
- The "Mark episodes" dropdown beside "Season N Episodes" is gone; the menu lives on each season card now.

- Trailer player redesigned: a centered lightbox on desktop and a bottom sheet on phones (drag handle, swipe down to close). Header with the poster, title and an "Open on YouTube" button; a "More videos" row (TMDB's teasers, clips and featurettes) when the title has more than one video, with "Now playing" on the current one. Plays through youtube-nocookie.com, pauses when you switch or close, locks page scroll while open and returns focus to the Trailer button. TMDB gives no video durations, so the mockup's duration chips are left out.

### Fixed

- "Add to list" on a movie/show/anime you haven't tracked yet now opens the same list popup as a tracked one (picking a list tracks the title and adds it). It used to just scroll to the side rail, so on a new title nothing visible happened - especially on phones.

## [0.166.0] - 2026-10-29

### Added

- The title logo (Fanart.tv) now appears by itself the first time you open a movie, show or anime, instead of needing a page refresh - the heading polls for it like the rating pills do, and gives up after about 30 seconds.
- On desktop, a plain "Name · Movie/TV/Anime · Year" line sits under the poster, above the action tiles, and replaces the small kind/year line above the heading (phones keep that one).

### Changed

- The title logo is a little larger.

## [0.165.0] - 2026-10-29

### Changed

- **Title page: the actions moved under the poster.** A row of four equal tiles -
  watched/status, lists, rating and trailer - now sits under the poster (and under
  the poster and title on a phone, still in one row), instead of beside the title.
  The watch-progress card is under them.
  - The status tile is neutral ("Track") until you track the title, blue while you
    are partway through a show ("Watching"), and green only once you are all caught
    up or the show has finished - it always agrees with the progress card. The lists
    tile turns amber when the title is on a list; the trailer tile only appears when
    there is a trailer.
  - Progress card states: not started ("0% - Start watching"), partway (percentage
    on the left, "Left" with the time and episodes remaining on the right), caught up
    on a show that is still airing ("All caught up"), and finished on a show that has
    ended or been cancelled ("Finished", from the show's status, never a toggle).
    The card now also shows for shows you have not started.
  - The poster column is a little wider on desktop.
- **Rating moved into a popup.** The rating tile shows your score (or "Rate") and
  opens a popup under the tiles: five stars with half-star steps, a large score
  readout, a "Clear rating" row, and a "By season" list with a rating chip for each
  season. The "Your rating" card in the side rail is gone.

### Added

- **Clear rating** - removes your score from the show while keeping your watches.
- **Season ratings** - rate each season of a show separately in the popup. They are
  part of Spool's backup export and come back when you import it (a season you have
  already rated keeps your score).

## [0.164.0] - 2026-10-29

### Added

- **Send your watch history from Spool to WeTrakr.** On the WeTrakr card (and
  when connecting) you can now choose *Send from Spool* or *Both ways*. Spool
  first shows a preview - how many movies, episodes and shows it would send,
  with examples - and nothing leaves until you confirm it. After that the daily
  sync, or **Send now**, sends only what is new.
  - Safe to repeat: WeTrakr is asked not to re-count anything the account has
    already watched, and Spool records what it sent, so nothing is counted twice.
  - Watches that came from WeTrakr are never sent back to it; rewatches,
    whole-show watches and titles WeTrakr cannot identify are not sent.
  - Respects WeTrakr's limits: writes are paced, and a send that hits the daily
    request limit carries on the next day without losing its place.
  - Turning sending off clears the go-ahead, so it asks again next time.
  - Sends show up in Settings -> Logs as "Send · WeTrakr".

## [0.163.0] - 2026-10-29

### Added

- **Choose what each connected service may do.** Every connected app (Trakt,
  Simkl, WeTrakr, Nuvio, MyAnimeList, TMDB) now has a **Sync** setting on its card
  in Settings -> Connected Apps: **Import into Spool**, or **Connected, not
  syncing**. A brand new connection now asks first - nothing is read from the
  service, and nothing in your Spool history changes, until you pick (importing
  still goes through Import Review before anything is written). "Not syncing"
  keeps the account connected without a schedule, and its Sync now / Review
  full import buttons are hidden. The setting is ready for sending data from
  Spool to a service as well; those options appear per service once that
  service's push exists.

### Changed

- Connections that already existed keep importing exactly as before. Nuvio's old
  on/off flag became this setting (off stays off).
- Reconnecting a service that is set to "not syncing" no longer starts a sync.

## [0.162.1] - 2026-10-29

### Fixed

- **WeTrakr sync now matches WeTrakr's real API.** It was written without access
  to the API and could not have worked. Checked against WeTrakr's published
  reference:
  - Every request now sends the app's client ID (`wetrakr-api-key`) and
    `wetrakr-api-version`; without them WeTrakr answered 401 to everything,
    including "Test connection".
  - Watch history is now read in WeTrakr's real shape (the WeTrakr id is the
    media object's `id`, episodes carry `season_number` with their show nested
    inside). Before, every play would have been skipped as invalid.
  - Incremental sync asks for `from_date` (it sent a parameter WeTrakr does not
    know, so each daily sync re-downloaded the whole history and burned quota).
  - The token refresh sends only what WeTrakr asks for, and pages are capped at
    WeTrakr's maximum of 100.
  - The default app-wide daily request pause is now 900 (it assumed 16,000; a
    develop key is 1,000-2,000). Raise `WETRAKR_APP_QUOTA_PAUSE_AT` once you have
    a production key.
- The docs now say that your WeTrakr app needs Spool's callback URL added as a
  redirect URI (a new app only has the out-of-band one).

## [0.162.0] - 2026-10-29

### Added

- **Watch progress on the title page** for TV shows and anime. On desktop it
  sits under the poster at the poster's width (the poster is a little larger
  now); on phones it is under the action buttons. A bar fills with how much of the series you have watched:
  the percentage (e.g. 21%), the time already watched and episodes done on the
  filled side, and the time still to go and episodes left on the other
  ("LEFT 177h 28m · 368 ep"). Only released episodes count, so an episode that
  has not aired is in neither the total nor the time left. It follows your
  watch history however a watch arrives (the buttons here, Trakt, Simkl,
  Nuvio, the Scrobble API, imports), updates straight away when you mark
  episodes watched or unwatched, and turns green with "All caught up" when
  you are level with what has aired. Time watched uses each episode's own
  runtime where it is known, otherwise the show's typical episode length.

## [0.161.0] - 2026-10-28

### Added

- **My schedule can be filled by hand.** Setting it up now asks which lists to
  use and whether it should be **Automatic** (as before) or **Manual**: a manual
  schedule starts empty, and you add a title, a day and a time (and, for a show,
  how many episodes) from an "Add to schedule" card. Slots can be removed, and
  still tick themselves off from your watch history. Switch between the two in
  Plan settings.
- A small poster next to each slot in the week view.

### Changed

- Plan settings are no longer applied the moment you tick something. Change what
  you want and press **Apply** (or **Save** in Settings -> My schedule), so
  several changes go through together. "Not applied yet" shows while there are
  unsaved changes.
- While My schedule is updating, the page dims and shows "Updating your
  schedule..." and its buttons are disabled, so it is clear something is
  happening and nothing gets clicked twice.

- The week view now fills its card down to the bottom of the sidebar beside it,
  instead of ending early with empty space underneath. It shows the whole day
  from 07:00 (earlier only if something is planned earlier) and opens on your
  free time; the slots keep their size, and the other hours are a scroll away.

### Fixed

- The week view no longer shows a vertical scrollbar when everything already
  fits (the edge hour labels were overflowing it by a few pixels).
- Two requests at once (a double click, or a refresh while it is loading) can no
  longer plan the same hours twice.

## [0.160.0] - 2026-10-28

### Added

- **My schedule** - an opt-in planner for what to watch and when. Calendar
  now has a second tab, **My schedule**: pick the lists to draw from, your
  free days and times, the media (movies, TV, anime or mixed) and the
  order, and it fits movies and episodes into your free evenings using their
  runtimes (shows get consecutive episodes per slot). Off by default and per
  profile. See docs/SCHEDULE.md.
  - A week view in the style of a TV guide: one column per day, your free
    window shaded, slots placed by time. All seven days share the width, so
    there is no sideways scrollbar unless the screen is genuinely too narrow;
    **Week / 3 days / Day** and the arrows shorten and move the view (phones
    open on Day). Vertical scrolling stays for the hours.
  - A **Tonight** card on the Dashboard and in the schedule's sidebar.
  - Slots tick themselves off from your real watch history, whichever source
    reported the watch (Trakt, Simkl, Nuvio, the Scrobble API, an import or
    Spool itself), matched by title and episode using the watch time. A slot
    you did not watch moves to the next open slot, but only after your
    connected sync sources have run and found nothing. **Mark as watched**
    and **Skip** buttons remain as a fallback.
  - A **finish line**: when your current lists could be finished at your
    schedule, counting released episodes and movies only, and updating as
    you watch, add titles or change your free time.
  - The same options live in Settings -> My schedule.

## [0.159.0] - 2026-10-28

### Added

- **Digital release notifications.** A new opt-in notification tells you when
  a movie on your watchlist becomes available to buy, rent or stream at home
  (TMDB's US digital release date). It is off by default and skips movies you
  have already watched.
- **Choose where each notification goes.** Settings → Notifications is now a
  table with one row per type and two checkboxes - In app and Push - so you
  can, for example, get sync failures in both, the watchlist time capsule in
  the bell only, and digital releases as a push only. Recommendations, the
  watchlist time capsule, backup results, import results and app updates
  (server owner) can now be switched off too; before, only new episodes,
  upcoming reminders and sync failures could.
- Backup and import results can now be sent as push notifications (a manual
  backup or import finishing or failing, and the first failure of a scheduled
  backup). They stay in-app only until you tick Push for them.

### Changed

- The three old notification toggles are carried over into the new table
  automatically: anything you had turned off stays off, in the bell and in push.
- Digital release dates are stored with the release schedule but are kept off
  the calendar, the iCal feed and Up Next.

## [0.158.2] - 2026-10-28

### Security

- Updated Pillow from 12.3.0 to 13.0.0, which fixes all 41 open Dependabot
  alerts (image-decoding memory-safety bugs - heap overflows, out-of-bounds
  reads, double frees - plus several decompression-bomb and denial-of-
  service issues in the TIFF, GIF, PDF, MIC, MSP, BMP and other decoders,
  and a bundled libopenjp2 fix). Spool only uses Pillow to check that an
  uploaded profile picture is a real image.
- Search no longer uses two regular expressions (the "&" / "and" spelling
  variants) that CodeQL flagged as potentially slow on crafted input; it
  now uses plain string handling. Behaviour is unchanged.
- Updated certifi (the trusted-certificate list used for HTTPS requests)
  to 2026.10.23, which fixes a known vulnerability in the previous pin.

### Changed

- Routine dependency updates (no breaking changes expected): cryptography
  50.0.2, gunicorn 26.2.0, psycopg 3.3.6, pydantic 2.14.1, django-ninja
  1.7.1, django-silk 5.6.0, sentry-sdk 2.73.0, apprise 2.1.0, and small
  bumps to asgiref, idna, charset-normalizer, click, Pygments, tzdata,
  tzlocal, packaging, prompt_toolkit, pyspellchecker and typing-inspection.
  Major-version upgrades (Django 6, redis 8) were deliberately left out.

## [0.158.1] - 2026-10-25

### Changed

- Query performance has its own tab in Settings (Server → Query performance),
  as wide as the Logs tab, instead of a card squeezed into Maintenance.

### Fixed

- The Query performance panel printed every query's full SQL (Django
  selects every column of every joined table) in a cramped five-column
  table, which was unreadable. Each query is now a compact entry: a
  one-line summary ("SELECT ... FROM tracker_datalog INNER JOIN ..."), its
  average / calls / total / max / cache figures underneath, and "Show SQL"
  to expand the full statement over several lines. Each query has a "Copy
  SQL" button and the panel has "Copy all as text" for pasting the whole
  list somewhere.

## [0.158.0] - 2026-10-25

### Added

- Settings → Maintenance → Query performance (owner only): the slowest
  database queries by total time, average time or call count, how much of
  each came from cache, anything averaging over 100 ms flagged, plus the
  tables Postgres mostly reads start to finish instead of through an
  index (probable missing indexes), and a "Reset statistics" button. It
  reads Postgres's `pg_stat_statements`, which the compose files now
  preload and a new migration creates.

### Changed

- **Upgrade note:** the database service in all three compose files now
  starts with `shared_preload_libraries=pg_stat_statements`, so the first
  `docker compose up -d` after updating recreates the database container
  - a few seconds of downtime while Postgres restarts. Data is untouched
  (it lives in the `db_data` volume).

## [0.157.1] - 2026-10-24

### Fixed

- You were notified "<name> watched your recommendation" even when that
  housemate keeps their activity private, which told you they had
  watched something. The notification is now skipped for private
  profiles (the recommendation is still marked as watched on their side).

## [0.157.0] - 2026-10-24

### Fixed

- Search no longer cares how a title is punctuated. "spider man",
  "spider-man" and "spiderman" now find the same things, "one punch man"
  finds One-Punch Man, "fast & furious" and "fast and furious" match,
  and "pokemon" and "pokémon" are the same search. TMDB is asked for up
  to four spellings of what you typed (the typed text, a spelling
  correction if there is one, then hyphen / no-space / no-accent /
  "&" variants), still through the existing cache.
- The "In your library" half of search missed titles whose punctuation
  or accents differed from what you typed ("One-Punch Man" for "one
  punch man"). It now ignores case, accents and punctuation and needs
  every word you typed. Titles get a new stored `search_name` for this
  (a migration fills it in for existing titles).

### Changed

- Search results are ranked: an exact title match first, then titles
  that start with what you typed, then titles containing all your words
  (ignoring a leading "the"), then by TMDB popularity. Results nobody
  has voted on go to the bottom. Library matches use the same order.
- When a spelling correction produced the top result, the page says
  "Showing results for <corrected> · Search instead for <typed>" and the
  second part runs the search exactly as typed.

## [0.156.0] - 2026-10-24

### Changed

- Redesigned the household profile popup. It now opens with "You and
  <name>" (taste match, titles you've both watched, and your shared
  favourite genre - each hidden when it can't be worked out), then
  their movies, shows finished, total watch time and streak as
  separate figures. "Where their time goes" is a single split bar with
  a 30 days | All time switch and durations in days or hours, never
  "25d 8h 26m". "Recently watched" groups plays by title ("S9E1-E8 ·
  2 sittings", or "Movie · rated 9 / 10"), each row linking to the
  title. On phones the popup is a bottom sheet with a drag handle.
- Sections of the popup and the title page's Activity tab (facts,
  timeline, plays list, household rows) are now separated by hairlines.

### Fixed

- The profile popup ignored the "Share my activity" setting and showed
  anyone's stats, counts and recent watches to the whole household. A
  profile that keeps its activity private now shows only its name and
  member-since date.
- The "Full stats" pages for another profile (Stats, History and their
  charts, at `/profile/<id>/stats/` and `/profile/<id>/history/`) had
  the same gap and could be opened for a private profile by URL. They
  now return "not found" for one; you can always open your own.

## [0.155.0] - 2026-10-24

### Added

- A redesigned "Your activity" tab on every title page. Your plays are
  grouped into sittings (plays within 6 hours of each other) and drawn
  as a timeline that fits what you did: a one-night binge shows blocks
  by time of day with break labels, a longer watch shows a proportional
  axis with one circle per sitting (nearby sittings merge) and the gaps
  between them, and a movie shows each viewing as a first watch or
  rewatch with the gap between them (empty calendar years are
  collapsed). A facts row sits above it (started / finished / took /
  longest break, or viewings / first watched / latest / longest gap) and
  an "All plays" list is grouped by sitting.
- A Household view on the same tab, shown only when another profile
  shares its activity. Shows get a row per profile with status,
  progress ("E6 of 8"), how far ahead or behind you they are, last
  watched and rating, plus a timeline of everyone's sittings on one
  axis; movies get who has watched it, viewings, ratings and a
  household average. Profiles with "Share my activity" off never get a
  row, lane, count or average. It loads only when opened. The
  sub-view is reflected in the URL as `#activity-household`.
- Recommending now lives in the Household view: a housemate who hasn't
  watched it has a Recommend button on their row, and housemates who
  keep their activity private are listed under "Recommend to" (just a
  name and a send button, no watch state). "Send as mystery" and
  housemates' replies carried over.

### Changed

- The "Recommend to" card is gone from the title page's side column.
  This also stops it showing "Already watched" for housemates who keep
  their activity private.
- The Activity tab now also appears on titles you haven't tracked yet
  (it says you haven't watched it, and is where you recommend it), and
  is called "Activity" instead of "Your activity" whenever the
  household has another profile. With only one profile nothing about
  the household or recommending is shown.
- Timeline times use the profile's own timezone (the server's when
  blank), matching every other time in the app.

## [0.154.0] - 2026-10-23

### Added

- The calendar (.ics) feed is now cached per subscription URL for 15
  minutes and sends an ETag, so calendar apps polling it get a quick
  "not modified" instead of re-running the calendar query each time.
- TMDB failures are cached for 60 seconds, and concurrent requests for
  the same uncached TMDB call now wait for one fetch instead of all
  hitting TMDB at once, so an outage no longer makes every page load
  wait out the full timeout.

### Changed

- TMDB responses are cached for different lengths depending on how
  often they change: searches 15 minutes, watch providers 12 hours,
  movie details/credits/people/collections 24 hours, genres 7 days.
  TV details and seasons stay at 6 hours since airing shows change.
- Added a test that fails if a new `stats:*` cache key isn't cleared
  when watch history changes.

## [0.153.1] - 2026-10-22

### Fixed

- The Celery worker would shut down entirely (relying on Docker to
  restart it) on any brief Redis connection blip, instead of just
  reconnecting - a setting meant to stop a *web request* from hanging
  when Redis was briefly unreachable was being applied to the
  long-running worker process too.
- The nightly release sync did 2-3 database queries per episode in a
  season instead of a handful per season, flagged by Sentry as an N+1
  query on `sync_title_release`.

## [0.153.0] - 2026-10-22

### Added

- Settings → Notifications has a new "Push notifications" card: add one
  Apprise URL per line (Discord, Slack, Telegram, Pushover, Microsoft
  Teams or Webex Teams) and new releases, upcoming releases, sync
  failures, system updates and recommendation activity are mirrored out
  through it, with a "Send test" button to check it works. Deliberately
  limited to those six hosted services rather than Apprise's full
  catalog - every other scheme (`ntfy://`, `mailto://`, `matrix://`,
  generic webhooks, ...) lets the URL's own host be anything at all,
  which is a server-side request forgery risk, not a hypothetical one.

### Fixed

- The Calendar page's "Subscribe" button could render its panel
  off-screen on mobile.
- A raw `\r`/`\r\n` in a shared Title's name could terminate an
  iCalendar content line early and inject an unescaped property into
  someone else's calendar feed.
- Settings' backup-restore links pointed at a stale tab location.

## [0.152.0] - 2026-10-22

### Added

- Calendar now has a "Subscribe" button offering a personal .ics feed
  URL - add it to Google Calendar, Apple Calendar or Outlook as a
  subscription and it stays in sync with what's on your own Calendar
  page (watching + watchlists). No login needed to fetch it - the URL
  itself is the secret, same as any other calendar subscription link -
  with a regenerate option if it ever leaks. TVmaze-enriched episodes
  carry their exact airtime; everything else is a plain all-day event
  rather than a misleading midnight time.

### Changed

- Settings' sidebar is now grouped into Personal (Account, Preferences,
  Notifications), Data (Connected Apps, Import & Export, Backups, API &
  Sharing) and, for the server owner, Server (Profiles, Integrations,
  Maintenance, Activity Log, Logs) - each collapsible, expanded by
  default. Security and Danger Zone stay as their own top-level items.
  Backups is now its own tab instead of living inside Import & Export.

## [0.151.0] - 2026-10-22

### Added

- Fanart.tv artwork support: title detail pages now show a transparent
  clearlogo over the backdrop instead of plain text where Fanart has
  one, and prefer Fanart's own backdrop over TMDB's when available.
  Optional - needs a free personal API key (Server Integrations), and
  TMDB's own poster/backdrop stay the fallback everywhere Fanart has
  nothing.
- TVmaze supplements TMDB's date-only TV air dates with an exact
  airtime and timezone, so the dashboard's Up Next card and the
  Calendar's agenda sidebar can show "Today · 9:00 PM" instead of just
  a date, using each profile's own 12h/24h time preference. No API key
  needed, movies are unaffected (TVmaze has no movie data), and the
  nightly sync stays cheap via TVmaze's own bulk "updated shows"
  endpoint rather than re-checking every tracked show individually.
  TVmaze is now credited alongside TMDB/Jikan/AniFiller in the site
  footer, per its CC BY-SA license.

## [0.150.0] - 2026-10-21

### Added

- Import & Export can now bring in IMDb ratings/watchlist CSV exports and
  Letterboxd ZIP exports (watched history, ratings and watchlist), with
  no account connection or API key needed - just upload the file. IMDb
  and Letterboxd titles match existing library titles via IMDb id or
  fuzzy name+year, the same way every other import source does, so
  re-importing or importing from a second source never creates
  duplicates. IMDb ratings use the "Date Rated" as the watched date
  (IMDb's export has no separate watched date) and skip anything without
  episode-level data - individual TV episode ratings (no season/episode
  number in the export) and whole-series ratings (Spool can only store a
  rating against one specific episode) - with a clear explanation in the
  Import Review list rather than a generic error. See
  docs/IMPORTING.md for the full breakdown of what is and isn't
  importable from each source.
- TMDB can now be connected as an account, syncing favorites and
  watchlist (movies and TV) the same way Trakt/Simkl/WeTrakr sync
  history - TMDB has no watched-history data of its own, so this only
  ever adds to lists, never creates watch events.
- MyAnimeList can now be connected as an account, syncing your anime
  list (status, score and episode progress) into watch history and the
  Watchlist. MAL only reports a cumulative episodes-watched count per
  show rather than which specific episodes, so Spool fills in the
  earliest N episodes in season/episode order to match it.
- Import & Export can now bring in a TV Time Liberator CSV export
  (movies, episodes and followed shows) - TV Time itself shut down in
  2026, so this is for migrating a historical export, not a live
  connection. Shows still missing an episode are added to the
  Watchlist; everything else matches by IMDb id or fuzzy name+year like
  every other file-based import source.

### Fixed

- Settings -> Logs' "Connect" filter was missing WeTrakr, TMDB and
  MyAnimeList connect events (only Trakt/Simkl/Nuvio were ever added to
  it) - filtering logs to "Connect" now covers every provider that can
  be connected.

## [0.149.0] - 2026-10-20

### Changed

- Extended the Import & Export card redesign to the rest of Settings:
  Preferences, Notifications, Advanced, Server Integrations and
  Maintenance are now each a list of cards with an icon badge, title and
  description, instead of plain text headers. Server Integrations splits
  what used to be grouped multi-field panels (Movie & TV data; Trakt,
  Simkl & WeTrakr; Cloud backups) into one card per provider, each with
  that provider's real logo and a status chip, matching Import & Export
  and Connected Apps exactly.

### Fixed

- A vendored icon with its own internal SVG ids (Google Drive's gradient
  mark) rendered blank whenever it appeared twice on the same page at
  once - which it now legitimately does, since Server Integrations and
  Import & Export's Automatic Backups both show it and Alpine's tabs keep
  every tab's markup in the DOM, just hidden. Two elements sharing one id
  is invalid SVG; the browser resolved both copies' `url(#id)` references
  to whichever one appeared first, leaving the other's mask empty. The
  `{% icon %}` tag now suffixes any ids a vendored SVG defines with a
  counter unique to that render, so repeats of the same icon never
  collide - a no-op for the common case, since most icons define none.



### Changed

- Redesigned Settings → Import & Export: each source (Trakt, Simkl,
  WeTrakr, Nuvio, Spool's own backup format, and a generic CSV/JSON/ZIP
  catch-all) is now its own card with the provider's real logo, a status
  chip, and inline actions, instead of one bare upload box. Unconfigured
  providers say so plainly ("ask your server owner to set up X") and show
  their mark desaturated rather than just disabling a button silently.
  IMDb, TMDB, Letterboxd, MDBList, TV Time and MyAnimeList are listed as
  "Coming soon" with their real marks, laying groundwork without
  pretending they're supported yet. Automatic Backups and Export Data got
  the same card treatment, with real icons for Google Drive, Dropbox,
  WebDAV and Server folder. Connected Apps' provider rows now show the
  same real logos instead of plain letter monograms. Danger Zone's nav
  entry and its destructive buttons now read as red at a glance instead
  of blending into the rest of the sidebar/panel.

## [0.147.0] - 2026-10-20

### Added

- Optional [Sentry](https://sentry.io) integration (`SENTRY_DSN`) for
  error tracking and performance tracing in production. Off by default —
  leave `SENTRY_DSN` unset and nothing is sent anywhere. Covers gunicorn,
  the Celery worker, and Celery beat. `SENTRY_TRACES_SAMPLE_RATE` (default
  `0`) controls what fraction of requests/tasks get a full performance
  trace (view time + DB query spans) — the thing that actually shows
  what's slow, not just that something was. See `docs/CONFIGURATION.md`.

## [0.146.1] - 2026-10-17

### Fixed

- WeTrakr's self-tracked daily request quota (see 0.146.0) only ever
  recorded one request per sync run, no matter how many pages that run
  actually fetched - a routine sync or full import that spanned several
  pages was silently undercounting its real usage against the ceiling
  the whole proactive-pacing mechanism exists to respect. Every page
  fetch, and the `/plan-usage` check, now checks and records the quota
  individually, same as the Import Review scan path already did.

## [0.146.0] - 2026-10-17

### Added

- WeTrakr connect - a fourth sync provider alongside Trakt/Simkl/Nuvio,
  same OAuth2-connect-then-Import-Review flow (see
  [Importing your data](docs/IMPORTING.md#connecting-trakt--simkl--wetrakr--nuvio)).
  WeTrakr's free plan caps a connected account at 1000 API requests/day:
  Spool checks which plan an account is on before a scan starts and
  self-paces to stay under a free account's limit. If a first-time
  import is large enough to outlast one day's quota, it pauses partway
  through - Import Review shows "Paused - waiting for quota" with
  progress so far - and resumes automatically right after the quota
  resets (shortly after midnight UTC), continuing from exactly where it
  left off rather than starting over or failing. A routine daily sync
  that hits the quota mid-run simply retries the same small delta on its
  next scheduled run. New `WETRAKR_CLIENT_ID`/`WETRAKR_CLIENT_SECRET` env
  vars (also settable under Settings → Server Integrations).

## [0.145.11] - 2026-10-17

### Fixed

- iOS launch splash was missing for iPhone 17, iPhone 17 Pro, iPhone 17
  Pro Max, and iPhone Air - their exact screen-size media queries weren't
  in the apple-touch-startup-image list yet, so none of the 28 existing
  splash images could match and the home-screen launch fell back to a
  plain black screen (the dark background with no wordmark). Added the
  3 missing size classes (6 images, both orientations).

Existing home-screen installs: iOS caches the splash/icon state from
when the app was added, not live - remove and re-add Spool from the
home screen to pick this up.

## [0.145.10] - 2026-10-17

### Added

- A real launch splash screen for the iOS home-screen install, instead
  of a blank screen: the Spool wordmark on the app's own background,
  covering every current iPhone/iPad size and both orientations (28
  images total). iOS never generates one from the web manifest the way
  Android does - it has to be an exact-pixel-match image per device
  size, or nothing shows.

### Fixed

- The PWA `<head>` metadata (icons, manifest, theme-color, and now the
  splash tags) was hand-duplicated across every standalone page -
  login, the 2FA challenge, and first-run credential setup - not just
  base.html. Confirmed live as a real gap, not just untidy: the
  color-scheme/white-flash fix from the previous release only ever
  reached base.html, so a PWA launch that opened straight to the login
  screen (the single most common cold-launch case) got none of it.
  Pulled into one shared partial every one of these pages now includes,
  so this can't drift out of sync again.

Existing home-screen installs: iOS caches the splash/icon state from
when the app was added, not live - remove and re-add Spool from the
home screen to pick this up.

## [0.145.9] - 2026-10-17

### Fixed

- iOS home-screen installs could flash white between the native launch
  splash and the page's own CSS loading (app.css is render-blocking,
  behind a Google Fonts stylesheet) - the WebView's own default canvas
  is white, and there was nothing telling it otherwise for that gap.
  Added `color-scheme: dark` plus an inline background as an earlier,
  non-network fallback, and matched the web manifest's background/theme
  color to the page's own real background (`#0e1014` - it was `#14161c`,
  a different dark shade, which stood out against the real page once
  content painted). Already-installed home-screen icons were likely
  the other half of the inconsistency - iOS captures manifest colors at
  "Add to Home Screen" time, so an existing install may need to be
  removed and re-added to pick this up.

## [0.145.8] - 2026-10-16

### Fixed

- Notifications: an "Upcoming" release reminder was never converted or
  removed once its own release date passed, so it sat under "Upcoming"
  indefinitely - and being the oldest release date in the list, it
  sorted first, making the whole section look ordered backwards.
  Reminders whose release date has already passed no longer show.

## [0.145.7] - 2026-10-16

### Fixed

- Title page's season picker: the selected season's ring still wasn't
  showing after the previous fix - `ring-inset` turned out to be a
  Tailwind v3 modifier that's a no-op in this Tailwind v4, and even
  switched to the real v4 `inset-ring-*` utilities, an inset ring on
  the same box as the full-bleed poster image was getting painted over
  by the image itself. Now rendered as its own overlay div after the
  image, which actually shows.

## [0.145.6] - 2026-10-16

### Fixed

- Activity feed: a series or season finale watched on its own (not as
  part of a binge run) showed no "completed" indication at all - only
  grouped binge entries ever got that check. Singles now get it too,
  and both singles and groups now also call out finishing just a
  *season* (previously only a full series finish was shown). History's
  own group tile gained the same season-completed indicator.
- Activity feed's binge summary read "8/37 episodes" for an 8-episode
  session of a 37-episode show, which looks like cumulative progress
  ("8 of 37 watched") rather than what it actually means ("this session
  was 8 of the show's 37 total"). Reworded to "37-episode series".

### Changed

- `episode_totals_for_group` now also returns whether the group's last
  episode completed its own season, not just the whole series.

### Fixed

- Title page's season picker: the selected season's ring looked cut off
  along the bottom edge - the picker row scrolls horizontally, which
  forces the browser to clip vertical overflow too, and the ring's
  outward box-shadow bleed was getting caught in that clip. Switched to
  an inset ring, which is drawn inside the poster instead of bleeding
  past it.

## [0.145.4] - 2026-10-16

### Fixed

- History's binge-grouped tile: the per-episode "remove" dropdown could
  render clipped to the poster's edges on some browsers despite being
  fixed-positioned - now teleported out to `<body>` so no ancestor's
  `overflow: hidden` can clip it. The group tile's sync-source badge
  (N/S/T/W) was also hidden below the `sm` breakpoint while the
  single-play tile's own badge wasn't - dropped that inconsistency now
  that it's a small icon rather than a wide text chip.

## [0.145.3] - 2026-10-16

### Changed

- History's third-party sync marker (Trakt/Simkl/Nuvio/Scrobble) is back
  to a small bluish letter badge (N/S/T/W) instead of a full-width text
  chip, on both the single-play tile and the binge-grouped tile.

## [0.145.2] - 2026-10-13

### Fixed

- WebDAV backups: `check_webdav_url`'s own validation and the actual
  HTTP connection were still two separate DNS lookups moments apart -
  a CodeQL-flagged DNS-rebinding SSRF gap even after the earlier WebDAV
  SSRF hardening pass. The connection is now pinned to the exact
  address that was validated (TLS certificate verification still checks
  the real hostname, not the pinned IP - confirmed with dedicated
  tests, including one against a real local socket).

## [0.145.1] - 2026-10-13

### Fixed

- Dashboard's Continue Watching row now scrolls horizontally in one row
  on desktop instead of wrapping into several full-height rows - a
  profile with many in-progress titles no longer pushes the rest of the
  Dashboard far down the page.

## [0.145.0] - 2026-10-13

Merges the spool-tracker-experimental redesign and features. Runs three
new migrations (0059-0061), all additive - back up the database first
as usual.

### Changed

- **Redesign.** Darker, cinematic theme on a strict application grid
  with a consistent type scale (see `DESIGN.md`). A top navigation bar
  with icons replaces the sidebar, pages are wider, and phones get a
  floating frosted-glass bottom tab bar (Home, Browse, Search, Alerts,
  You) with notifications and the profile menu in it.
- **Title pages:** poster-led hero over a full-width backdrop with
  labelled facts (Directed by, Released, Runtime, Rated, ...), genre
  chips, main cast, and a tabbed panel (Episodes / Overview / Cast &
  Crew / Your activity) beside a side rail. Long seasons show a window
  of episodes instead of every row.
- **Home:** Recommended Movies / TV / Anime as three rows under the
  Watchlist queue.
- **History** is a day-grouped poster wall again (classic Trakt style),
  distinct from Activity.
- **Lists** show 16:9 poster collages.
- **Activity:** the household leaderboard and Top 5 are always shown;
  a quiet week opens on This Year.
- **Settings → Server Integrations** is grouped into sections.

### Added

- **Person pages:** a "You've Watched" row listing the titles you've
  seen with that person.
- **Every profile always has a Watchlist** - created with the profile,
  backfilled for existing ones (an existing list named "Watchlist" is
  adopted), and it can't be deleted.
- **Full backup:** Settings → Import & Export → "Export full backup
  (JSON)" with history, ratings and every list. Importing it through
  Import Review restores the lists too.
- **Automatic backups** each profile can turn on for itself: daily,
  weekly or monthly to Google Drive, Dropbox, WebDAV (NAS, Nextcloud,
  Synology, ...) or a server folder, keeping the last N. Google Drive /
  Dropbox need an OAuth app configured once in Server Integrations (or
  `GOOGLE_CLIENT_ID`/`DROPBOX_APP_KEY` env vars) - see `docs/BACKUPS.md`.
  The worker gets a `backup_data` volume at `/app/backups`.
- **Imports and backups stay visible after leaving the page:** a live
  Activity card on Import & Export, one notification per import that
  updates as it progresses (and links to its review), and running /
  awaiting-review / finished rows in the admin Logs.
- Import Review shows progress while matching titles and a "Finishing
  up" phase after commit, instead of looking stuck.
- `docker-compose.coolify.yml` and `docs/COOLIFY.md` for running under
  Coolify behind your own Nginx Proxy Manager.

### Fixed

- Anime survives export -> import: anime series and anime films come
  back as anime (CSV, Trakt JSON and full backup); a Spool CSV's anime
  films no longer fail with "TV/anime rows need a season and episode
  number".
- Trakt JSON imports keep ratings; Trakt's own Watchlist maps onto the
  profile's Watchlist.

### Security

- WebDAV backup addresses are checked before every request: loopback,
  link-local/cloud-metadata and other special addresses are refused,
  local-network addresses are allowed only for the server owner,
  redirects aren't followed, and errors don't echo the remote response
  (CodeQL "full server-side request forgery").

## [0.144.0] - 2026-10-04

### Changed

- `InstanceConfig` (the admin-configurable Trakt/Simkl/TMDB credentials
  row) is now cached instead of hitting the database on every single
  lookup - confirmed via a real query-count probe that this alone was
  40 of 131 total queries on one Dashboard request (every TMDB call
  anywhere on the page was independently re-loading the same singleton
  row). Invalidated immediately on save, so an admin's credential
  change still takes effect right away.
- Marking an episode watched no longer recomputes your entire Continue
  Watching list (every in-progress show, TMDB calls included) just to
  refresh the one Dashboard card for the show you were watching.

Measured together on one real Dashboard request: 131 → 92 queries.

## [0.143.0] - 2026-10-04

### Changed

- Search, the person detail page, and the title detail page's trailer/
  gallery section now fire their independent TMDB calls in parallel
  instead of one after another - confirmed via django-silk profiling
  that search (up to 4 sequential calls for a query with both a year
  and a spelling correction) and person detail (2 sequential calls)
  were spending most of their load time on exactly this.

## [0.142.0] - 2026-10-04

### Changed

- `/sw.js` no longer runs through Django's template engine (and the
  profile/notification/version-check DB queries that come with it) -
  confirmed via django-silk profiling it was costing ~10 queries on
  every single call, and the browser re-registers the service worker on
  every full-page load, making it the single most-hit endpoint in the
  app. It's now served straight from an in-memory cache of the file.
- Dashboard's Continue Watching now fetches every in-progress show's
  TMDB details in parallel instead of one after another - on a cold
  cache, this was a real contributor to multi-second Dashboard loads
  (confirmed live: 9.6s for one request with several in-progress shows).

## [0.141.1] - 2026-10-04

### Fixed

- Enabling `SILK_ENABLED` at runtime 500'd on `/silk/` with "Missing
  staticfiles manifest entry" — the Dockerfile's `collectstatic` step
  runs once at build time, before any runtime `.env` is read, so Silk's
  own CSS/JS/fonts never made it into the static manifest no matter what
  you set afterward. Silk's static files are now always collected at
  build time regardless of whether it's actually turned on, so toggling
  `SILK_ENABLED` at runtime works without a rebuild, as documented.

## [0.141.0] - 2026-09-29

### Added

- A nightly cleanup job now expires abandoned Import Review sessions
  (started but never reviewed/confirmed) and discards their staged data,
  so a review nobody came back to doesn't sit around indefinitely.

## [0.140.0] - 2026-09-29

### Changed

- Connecting Nuvio for the first time now goes through **Import Review**
  too - watch history is scanned and staged for review before anything's
  written. Continue-watching progress isn't reviewable yet and still
  arrives via the normal daily sync, one day after a first reviewed
  import. Reconnecting an existing profile is unchanged. Every source
  Import Review currently supports (CSV/JSON/ZIP, Trakt, Simkl, Nuvio)
  is now wired in.

### Changed

- Connecting Simkl for the first time now goes through **Import Review**
  too, same as Trakt - nothing imports until you select and confirm it.
  Reconnecting an existing account and the scheduled sync are unchanged.

## [0.138.0] - 2026-09-29

### Changed

- Connecting Trakt for the first time no longer imports your history
  automatically. It now scans your account and takes you to **Import
  Review** to select what to bring in before anything's written -
  reconnecting an already-connected account still syncs immediately, and
  a new **Review full import** button lets a connected account run
  another reviewed import on demand. Simkl/Nuvio still sync immediately
  on connect for now; they're next.

## [0.137.0] - 2026-09-29

### Changed

- Importing a CSV/JSON/ZIP file (Settings & Import) no longer writes to
  Spool as soon as you confirm the preview. It now stages an **Import
  Review** session instead: a filterable, paginated breakdown of every
  row as New/Existing/Duplicate/Error, with selection controls (by
  content type or bucket, or row by row) — nothing is imported until you
  review it and click Import. Cancelling discards the staged review data
  and imports nothing. This is the first piece of a broader
  review-before-import system landing across Trakt/Simkl/Nuvio too.

## [0.136.0] - 2026-09-29

### Changed

- Dashboard and Stats now cache their computed watch-history numbers
  (streaks, watch time, genre breakdowns, achievements, and similar) for a
  few minutes instead of recalculating them from scratch on every visit,
  and update immediately the next time you mark something watched or
  remove it from History. Measured locally: about 73% fewer database
  queries on Stats, 15% fewer on Dashboard.
- Discover's Year filter (2+ decades selected) and Discover's own page
  load now fetch from TMDB concurrently instead of one request after
  another.

## [0.135.1] - 2026-09-27

### Fixed

- Marking an episode watched from a not-yet-tracked title's preview page
  no longer errors. Its per-episode watched button could route into the
  wrong internal state, which crashed while trying to build a URL that
  only applied to titles that aren't tracked yet.

## [0.135.0] - 2026-09-27

### Changed

- The Dashboard's three "Recommended for You" rows now share one TMDB
  request pool instead of running one after another, shortening how long
  a first, uncached page load takes - most noticeable right after opening
  Spool from its home screen icon, where that time shows as a plain black
  splash screen with no feedback.

## [0.134.0] - 2026-09-27

### Added

- A thin progress bar now appears at the top of the screen the moment you
  tap a link or button, before the response arrives - on a slow connection
  there was previously no sign a tap had registered at all. Covers both
  full page navigation and background htmx actions; excludes automatic
  background polling (Settings → Logs, pending MDBList ratings).

## [0.133.1] - 2026-09-24

### Fixed

- Shows with no watch history no longer linger in Up Next and Calendar.
  A migration removes leftover Watching entries, created when an episode
  was marked and later removed from History, that have no watched episodes
  and no player progress. It runs automatically on upgrade.

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
