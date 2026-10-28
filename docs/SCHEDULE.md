← [Back to README](../README.md)

# My schedule

A personal "what should I watch today?" planner. It turns your lists into
an evening-by-evening plan so things stop piling up unwatched. Off by
default and per profile - nothing nags you until you turn it on.

Turn it on from **Calendar → My schedule**, or from **Settings → My
schedule**.

## Where it shows up

- **Calendar → My schedule** - a second tab next to Releases. A week view
  in the style of a TV guide: one column per day, your free window shaded,
  and the movies and episodes placed in it by time. The week shares the
  width between its seven days, so it fits without a sideways scrollbar;
  only a screen too narrow for that scrolls sideways. **Week / 3 days /
  Day** shortens the view (phones open on Day), and the arrows move by a
  week, or by a day in the shorter views. The card is as tall as the sidebar
  beside it and shows the day from 07:00 to midnight, opened on your free
  time; scrolling up and down reaches any other hour.
- **Tonight** - a card at the top of the Dashboard's side column and in the
  schedule tab's sidebar: today's remaining blocks, with the time left
  open. After the last block of the day it shows the next day with a plan.
- **Finish line** - when your current lists could be finished at this pace.
  It counts released episodes and movies only (nothing unaired), and
  recalculates when you watch something, change a list or change your free
  time.
- **Settings → My schedule** - the same options as the sidebar's Plan
  settings, plus the on/off switch.

## Setting it up

The first time, My schedule asks which lists to use and whether it should be:

- **Automatic** - it fits titles from those lists into your free time and
  replans what you miss (below).
- **Manual** - it starts empty and nothing is added for you. Use **Add to
  schedule** (a title from your lists, a day and time, and for a show how many
  episodes) to put things in. A slot you remove is gone; one you do not watch is
  marked Moved and left as history. Slots still tick themselves off from your
  watch history.

You can switch later in Plan settings (switching to Automatic replans the slots
that have not started). Plan settings are applied together with **Apply** (or
**Save** in Settings -> My schedule).

## How the plan is built (automatic)

1. **Pull from** - the lists to draw from (yours and shared ones). With none
   picked it uses your Watchlist.
2. **Free days and window** - for example Mon-Fri 19:00-22:30, with
   different times on particular days if you want them.
3. **Media** - Mixed, Movies, TV or Anime.
4. **Order** - Oldest first, List order, or Shuffle (stable within a week).

Blocks are fitted into the free windows by runtime, two weeks ahead.
Titles take turns, so an evening mixes your lists instead of finishing one
show first. A show gets one or more consecutive episodes per block (up to
about 100 minutes); a movie only gets a block it fits in. A title with
nothing released and unwatched is skipped. Missing runtimes fall back to
110 minutes for a movie, 45 for an episode and 24 for an anime episode.

The plan is stored, so it does not move around between visits. It is
rebuilt when you change the settings or press **Rebuild week**, and topped
up as time passes, a title is added or a block moves.

## How it knows what you watched

Slots complete themselves from your real watch history, whichever way the
watch got there - Trakt, Simkl, Nuvio, the Scrobble API, an import, or
logging it in Spool. A slot is done once every episode in it (or the
movie) has a watch, matched by title and episode, using the time of the
watch and not when a sync ran.

A slot that was not watched is only **moved** once your connected sync
sources have run since the slot ended, so a watch that is still on its way
is not mistaken for a miss (after 36 hours it moves regardless). The
**Waiting** tag marks a slot in that state. What was left of a moved slot is
planned again into the next open slot.

Hover a slot (or tap, on a phone) for **Mark as watched** and **Skip**:
the manual fallback.

## Notes

- Dates and times follow the timezone and 12/24-hour setting in Settings →
  Appearance.
- Episode lists and air dates come from TMDB (cached), so a show TMDB cannot
  resolve is left out of the plan.
- The plan lives in the database (`SchedulePlan`, `ScheduledSlot`); the
  logic is in `tracker/planner.py`.
