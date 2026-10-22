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


def _ensure_watchlist_on_profile_create(sender, instance, created, raw=False, **kwargs):
    # Every profile gets its auto-managed Watchlist the moment it exists
    # (see tracker/watchlists.py) - module-level for the same weak-
    # reference reason as the receiver above. raw=True is a fixture load;
    # the fixture brings its own lists.
    if created and not raw:
        from .watchlists import ensure_watchlist

        ensure_watchlist(instance)


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

        from .models import Profile

        post_save.connect(
            _ensure_watchlist_on_profile_create,
            sender=Profile,
            dispatch_uid="profile_ensure_watchlist",
        )

        # Every ImportSession status/progress change is mirrored into the
        # admin Logs and the user's notification bell (transfer_tracking.py).
        from .models import ImportSession
        from .transfer_tracking import on_import_session_saved

        post_save.connect(
            on_import_session_saved,
            sender=ImportSession,
            dispatch_uid="import_session_transfer_tracking",
        )

        # A newly-created Notification (not import_status/backup_status -
        # see notifications.on_notification_saved's own docstring) also
        # goes out through Apprise (tracker/push.py) for any profile
        # that's configured it.
        from .models import Notification
        from .notifications import on_notification_saved

        post_save.connect(
            on_notification_saved,
            sender=Notification,
            dispatch_uid="notification_push_dispatch",
        )
