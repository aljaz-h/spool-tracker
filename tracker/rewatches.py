"""Keeps WatchEvent.is_rewatch correct after an import adds new events for
a (profile, title, episode) group. The chronologically first watch is
never a rewatch, every later one is - recomputed for the whole group
rather than inferred from insertion order, since Trakt/Simkl history and
CSV rows can arrive in any order (Trakt's own /sync/history is newest-
first, so naively marking "first one processed = not a rewatch" would get
this exactly backwards)."""

from .models import WatchEvent


def recompute_is_rewatch(profile, title, episode):
    events = WatchEvent.objects.filter(profile=profile, title=title, episode=episode).order_by("watched_at")
    for i, event in enumerate(events):
        should_be_rewatch = i > 0
        if event.is_rewatch != should_be_rewatch:
            event.is_rewatch = should_be_rewatch
            event.save(update_fields=["is_rewatch"])
