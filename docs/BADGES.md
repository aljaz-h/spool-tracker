# Badges and levels

Spool awards badges for what you watch. There are 29 badges in six categories (Movies, TV shows, Anime, Habits, Household and a few hidden Unique ones), each with five tiers. Everything is counted from your whole watch history (Nuvio sync, Trakt and other imports, manual logging) and rebuilt after an import. Earned tiers are kept even if a title is deleted later.

The badges feature is inspired by WeTrakr and Cinetrak.

## XP

Each tier gives XP: 5, 10, 20, 40 and 80 for tiers 1 to 5. A tier held by fewer than 25% of the household pays 1.5x (only in a household with more than one profile). XP keeps counting after the top level.

## Levels

Ten levels. The names and thresholds live in one place, `LEVELS` in `tracker/badges.py`.

| Level | Name | XP needed | Medal |
|---|---|---|---|
| 1 | Newcomer | 0 | Grey, plain ring, 1 frame |
| 2 | Explorer | 100 | Grey, 2 frames |
| 3 | Regular | 250 | Grey, 3 frames |
| 4 | Cinephile | 400 | Blue, sprocket holes and wings, 1 frame |
| 5 | Curator | 650 | Blue, 2 frames |
| 6 | Expert | 1,000 | Blue, 3 frames |
| 7 | Virtuoso | 1,500 | White, longer wings and light rays, 1 frame |
| 8 | Master | 2,200 | White, 2 frames |
| 9 | Grandmaster | 3,000 | White, 3 frames |
| 10 | Legend | 4,000 | Amber medal, star, reel crown, ribbon |

Levels are always calculated from the stored XP, so changing a threshold never resets anyone. Levels 1 to 6 are unchanged from the first version; the old Legend (1,500 XP) is now Virtuoso.

All 29 badges at tier 5 give 4,495 XP, so Legend needs about 89% of everything.

## Medals

One flat inline SVG per level, generated from the level number (`medal_svg` in `tracker/templatetags/badges_ui.py`). The stage colours are the `--color-medal-*` tokens in `static/src/app.css`. The large medal shows 1 to 3 film frames for the step; the small one shows the level number with three pips under it. All ten are on the UI kit page (`/ui-kit/`).

## Privacy

Settings, Preferences, Badges has three switches: show my badges to the household, badge notifications, and post badges to the activity feed. Someone else's badges, level and pinned badges are only shown when they show their badges and share their activity. A profile with badges turned off shows no medal.

## Rebuilding

Badges are rebuilt lazily on the next full page after the history changes, and after an import or sync. `manage.py rebuild_badges` recounts every profile at once (the first rebuild of a profile is silent: no toasts, no feed entries).
