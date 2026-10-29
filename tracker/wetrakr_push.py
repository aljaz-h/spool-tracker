"""Sending Spool's watch history to WeTrakr (POST /sync/tracking).

What goes out: this profile's WatchEvents - movies, and episodes grouped
under their show - except

- events that came FROM WeTrakr (source = wetrakr): they are already there,
  and sending them back would be an echo;
- rewatches (is_rewatch) and whole-show events: WeTrakr is asked to log a
  title once. Every write carries allow_rewatch=false, which is WeTrakr's
  documented mode for re-sending a library: anything the account has
  already watched comes back as "Already watched!" and nothing is written,
  so a full send can never inflate anyone's counters;
- titles Spool has no usable id for (WeTrakr matches on its own id, or on
  tmdb/imdb ids).

Every event gets a PushRecord saying what happened, and an event with a
terminal record is never sent again - so the daily send carries only what
is new, and an interrupted one (the daily request quota) simply carries on
the next run. A first send needs the person's confirmation after a preview
(ExternalAccount.push_confirmed_at); see tasks.push_wetrakr_history.

Checked against WeTrakr's API reference (a Tracking Update answers with
added/removed/notFound/errored); the parts of a response that name WHICH
item was refused are read defensively - anything that cannot be attributed
is recorded as an error and retried, never assumed sent.
"""

import time
from datetime import timezone as dt_timezone

from .models import MediaType, PushRecord, WatchEvent

PROVIDER = "wetrakr"

MOVIE_BATCH = 100
SHOW_BATCH = 20
EPISODES_PER_REQUEST = 400
# WeTrakr allows 60 writes a minute and 2 at once per user.
SECONDS_BETWEEN_WRITES = 1.1
MAX_ATTEMPTS = 3
PREVIEW_SAMPLE = 8

# Records after which an event is never looked at again. ERROR is not one of
# them: it is retried (until MAX_ATTEMPTS turns it into FAILED).
TERMINAL = (
    PushRecord.Status.SENT, PushRecord.Status.ALREADY, PushRecord.Status.NOT_FOUND,
    PushRecord.Status.NO_IDS, PushRecord.Status.SKIPPED, PushRecord.Status.FAILED,
)


def _iso(moment):
    moment = moment.astimezone(dt_timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def title_ref(title):
    """How to name a title to WeTrakr: its own id when Spool has one (exact),
    else the external ids it accepts; None when there is nothing to go on."""
    external = title.external_ids or {}
    wetrakr_id = str(external.get("wetrakr") or "")
    if wetrakr_id.isdigit():
        return {"id": int(wetrakr_id)}
    ids = {}
    tmdb = str(external.get("tmdb") or "")
    if tmdb.isdigit():
        ids["tmdb"] = int(tmdb)
    imdb = str(external.get("imdb") or "")
    if imdb.startswith("tt"):
        ids["imdb"] = imdb
    return {"ids": ids} if ids else None


def _ref_keys(ref):
    if "id" in ref:
        return {("id", str(ref["id"]))}
    return {(key, str(value)) for key, value in ref["ids"].items()}


def _entry_keys(entry):
    """The keys an item in a response's notFound/errored lists answers to -
    WeTrakr echoes the item as it was sent."""
    keys = set()
    if not isinstance(entry, dict):
        return keys
    if entry.get("id") is not None:
        keys.add(("id", str(entry["id"])))
    for key, value in (entry.get("ids") or {}).items():
        keys.add((key, str(value)))
    nested = entry.get("show")
    if isinstance(nested, dict):
        keys |= _entry_keys(nested)
    return keys


def _is_movie(event):
    if event.episode_id is not None:
        return False
    external = event.title.external_ids or {}
    return event.title.media_type == MediaType.MOVIE or external.get("tmdb_kind") == "movie"


def label(event):
    if event.episode_id:
        return f"{event.title.name} S{event.episode.season}E{event.episode.episode}"
    return event.title.name


def pending_events(profile):
    """Events still to be looked at: not from WeTrakr, and without a terminal
    record."""
    return (
        WatchEvent.objects.filter(profile=profile)
        .exclude(source=WatchEvent.Source.WETRAKR)
        .exclude(push_records__provider=PROVIDER, push_records__status__in=TERMINAL)
        .select_related("title", "episode")
        .order_by("watched_at", "id")
    )


class Plan:
    """What the pending events turn into: movies to send, episodes grouped
    by show, and the ones that will not be sent (with why)."""

    def __init__(self):
        self.movies = []  # [(event, ref)]
        self.shows = {}  # show title id -> {"ref", "title", "events": [event]}
        self.skipped = []  # [(event, status, detail)]

    @property
    def episode_count(self):
        return sum(len(group["events"]) for group in self.shows.values())

    def counts(self):
        by_status = {}
        for _event, status, _detail in self.skipped:
            by_status[status] = by_status.get(status, 0) + 1
        return {
            "movies": len(self.movies),
            "episodes": self.episode_count,
            "shows": len(self.shows),
            "rewatches": sum(1 for _e, _s, d in self.skipped if d == "rewatch"),
            "no_ids": by_status.get(PushRecord.Status.NO_IDS, 0),
            "other_skipped": sum(1 for _e, s, d in self.skipped if s == PushRecord.Status.SKIPPED and d != "rewatch"),
        }


def build_plan(profile):
    plan = Plan()
    for event in pending_events(profile):
        if event.is_rewatch:
            plan.skipped.append((event, PushRecord.Status.SKIPPED, "rewatch"))
            continue
        ref = title_ref(event.title)
        if event.episode_id is None and not _is_movie(event):
            plan.skipped.append((event, PushRecord.Status.SKIPPED, "whole show"))
        elif ref is None:
            plan.skipped.append((event, PushRecord.Status.NO_IDS, "no id WeTrakr understands"))
        elif event.episode_id is None:
            plan.movies.append((event, ref))
        else:
            group = plan.shows.setdefault(event.title_id, {"ref": ref, "title": event.title, "events": []})
            group["events"].append(event)
    return plan


def preview(profile):
    """What a send would do, from the database alone: counts and a few
    examples, plus what has already gone."""
    plan = build_plan(profile)
    records = PushRecord.objects.filter(profile=profile, provider=PROVIDER)
    return {
        **plan.counts(),
        "to_send": len(plan.movies) + plan.episode_count,
        "samples": [label(e) for e, _r in plan.movies[:PREVIEW_SAMPLE]]
        + [label(e) for g in list(plan.shows.values())[:PREVIEW_SAMPLE] for e in g["events"][:1]],
        "sent_before": records.filter(status__in=(PushRecord.Status.SENT, PushRecord.Status.ALREADY)).count(),
        "failed": records.filter(status=PushRecord.Status.FAILED).count(),
    }


# --- payloads ---------------------------------------------------------------


def movie_item(event, ref):
    return {**ref, "status": "watched", "tracked_at": _iso(event.watched_at)}


def show_item(group, events):
    seasons = {}
    for event in events:
        seasons.setdefault(event.episode.season, []).append(
            {"number": event.episode.episode, "status": "watched", "tracked_at": _iso(event.watched_at)}
        )
    return {
        **group["ref"],
        "status": "watched",
        "seasons": [{"number": number, "episodes": eps} for number, eps in sorted(seasons.items())],
    }


# --- results ----------------------------------------------------------------


def _classify(error):
    return PushRecord.Status.ALREADY if "already" in (error or "").lower() else PushRecord.Status.ERROR


def _failures(response):
    """[(entry, status, detail)] from a Tracking Update's notFound/errored."""
    found = []
    for entries in (response.get("notFound") or {}).values():
        for entry in entries or []:
            found.append((entry, PushRecord.Status.NOT_FOUND, "not in WeTrakr's catalog"))
    for entries in (response.get("errored") or {}).values():
        for entry in entries or []:
            error = entry.get("error") if isinstance(entry, dict) else str(entry)
            found.append((entry, _classify(error), (error or "refused")[:255]))
    return found


def _record(profile, event, status, detail=""):
    record = PushRecord.objects.filter(provider=PROVIDER, watch_event=event).first()
    attempts = record.attempts if record else 0
    if status == PushRecord.Status.ERROR:
        attempts += 1
        if attempts >= MAX_ATTEMPTS:
            status = PushRecord.Status.FAILED
    PushRecord.objects.update_or_create(
        provider=PROVIDER, watch_event=event,
        defaults={"profile": profile, "status": status, "detail": detail[:255], "attempts": attempts},
    )
    return status


def apply_movie_response(profile, batch, response):
    """batch: [(event, ref)]. Marks each event from a Tracking Update."""
    by_key = {}
    for event, ref in batch:
        for key in _ref_keys(ref):
            by_key.setdefault(key, []).append(event)
    outcome = {event.id: (PushRecord.Status.SENT, "") for event, _ref in batch}
    for entry, status, detail in _failures(response):
        for key in _entry_keys(entry):
            for event in by_key.get(key, []):
                outcome[event.id] = (status, detail)
    return _save(profile, [e for e, _r in batch], outcome)


def apply_show_response(profile, groups, response):
    """groups: [(group, events)] sent in one request."""
    by_key = {}
    for index, (group, _events) in enumerate(groups):
        for key in _ref_keys(group["ref"]):
            by_key.setdefault(key, set()).add(index)
    outcome = {}
    for _group, events in groups:
        for event in events:
            outcome[event.id] = (PushRecord.Status.SENT, "")
    unattributed = []
    for entry, status, detail in _failures(response):
        indexes = set()
        for key in _entry_keys(entry):
            indexes |= by_key.get(key, set())
        if not indexes:
            unattributed.append(detail)
            continue
        for index in indexes:
            for event in groups[index][1]:
                outcome[event.id] = (status, detail)
    if unattributed:
        # WeTrakr refused something we cannot tie to a show: do not call the
        # batch sent - keep it to be tried again.
        for _group, events in groups:
            for event in events:
                if outcome[event.id][0] == PushRecord.Status.SENT:
                    outcome[event.id] = (PushRecord.Status.ERROR, ("unmatched: " + unattributed[0])[:255])
    return _save(profile, [e for _g, events in groups for e in events], outcome)


def _save(profile, events, outcome):
    counts = {}
    for event in events:
        status, detail = outcome[event.id]
        final = _record(profile, event, status, detail)
        counts[final] = counts.get(final, 0) + 1
    return counts


# --- the send ---------------------------------------------------------------


def _chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _show_requests(plan):
    """Shows grouped into requests of at most SHOW_BATCH shows and
    EPISODES_PER_REQUEST episodes."""
    current, episodes = [], 0
    for group in plan.shows.values():
        size = len(group["events"])
        if current and (len(current) >= SHOW_BATCH or episodes + size > EPISODES_PER_REQUEST):
            yield current
            current, episodes = [], 0
        current.append((group, group["events"]))
        episodes += size
    if current:
        yield current


def run(profile, post, sleep=time.sleep, max_requests=None):
    """Sends everything pending. post(body) -> the Tracking Update dict; it
    may raise (a daily quota, a network failure) - what was already saved
    stays saved and the rest is simply still pending next time.
    Returns {"sent", "already", "not_found", "errors", "skipped", "requests",
    "labels"}."""
    plan = build_plan(profile)
    totals = {"sent": 0, "already": 0, "not_found": 0, "errors": 0, "skipped": 0, "requests": 0, "labels": []}

    for event, status, detail in plan.skipped:
        _record(profile, event, status, detail)
        totals["skipped"] += 1

    def add(counts):
        totals["sent"] += counts.get(PushRecord.Status.SENT, 0)
        totals["already"] += counts.get(PushRecord.Status.ALREADY, 0)
        totals["not_found"] += counts.get(PushRecord.Status.NOT_FOUND, 0)
        totals["errors"] += counts.get(PushRecord.Status.ERROR, 0) + counts.get(PushRecord.Status.FAILED, 0)

    def send(body):
        if max_requests is not None and totals["requests"] >= max_requests:
            return None
        if totals["requests"]:
            sleep(SECONDS_BETWEEN_WRITES)
        totals["requests"] += 1
        return post({**body, "allow_rewatch": False})

    for batch in _chunks(plan.movies, MOVIE_BATCH):
        response = send({"movies": [movie_item(e, r) for e, r in batch]})
        if response is None:
            return totals
        add(apply_movie_response(profile, batch, response))
        totals["labels"] += [label(e) for e, _r in batch][: PREVIEW_SAMPLE * 4]

    for groups in _show_requests(plan):
        response = send({"shows": [show_item(group, events) for group, events in groups]})
        if response is None:
            return totals
        add(apply_show_response(profile, groups, response))
        totals["labels"] += [label(events[0]) for _g, events in groups]
    return totals
