from django.apps import AppConfig


def _invalidate_stats_cache_on_watchevent_change(sender, instance, **kwargs):
    # Module-level, not a closure inside ready() below - Signal.connect()
    # keeps only a *weak* reference to its receiver by default, so a
    # locally-defined function with nothing else holding a strong
    # reference to it gets garbage-collected almost immediately after
    # ready() returns, silently dropping the connection (confirmed live:
    # post_save._live_receivers(WatchEvent) came back empty even though
    # connect() itself never raised). A module-level function stays alive
    # for as long as the module does, same as every other Django signal
    # receiver in the wild that isn't a bound method.
    from .selectors import invalidate_profile_stats_cache

    invalidate_profile_stats_cache(instance.profile_id)


class TrackerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tracker'

    def ready(self):
        # Invalidates the Dashboard/Stats computed-data cache (see
        # selectors._cache_get_or_set/invalidate_profile_stats_cache)
        # whenever a profile's watch history actually changes. Every
        # production WatchEvent write goes through .objects.create() or a
        # queryset .delete() - both fire post_save/post_delete once a
        # receiver is connected (Django disables the fast-delete SQL-only
        # path automatically whenever a receiver exists), so this one
        # signal covers every real call site (mark/unmark watched, bulk
        # season/show catch-up, History deletes, CSV import, Trakt/Simkl/
        # Nuvio/webhook sync) without needing to touch any of them.
        from django.db.models.signals import post_delete, post_save

        from .models import WatchEvent

        post_save.connect(
            _invalidate_stats_cache_on_watchevent_change,
            sender=WatchEvent,
            dispatch_uid="watchevent_stats_cache_invalidate_save",
        )
        post_delete.connect(
            _invalidate_stats_cache_on_watchevent_change,
            sender=WatchEvent,
            dispatch_uid="watchevent_stats_cache_invalidate_delete",
        )
