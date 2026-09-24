from django.db import migrations
from django.db.models import Exists, OuterRef


def remove_stale_watch_progress(apps, schema_editor):
    """Deletes WATCHING/COMPLETED WatchProgress rows for a show with no
    watched episodes and no playback position - what's left behind when
    an episode is marked in-app (which adds the show to the Watching row)
    and then removed from History before that cleanup existed. Any
    WatchProgress row keeps a show's upcoming episodes in Up Next and
    Calendar, so these made shows with no history at all appear there. A
    row carrying a real player position, or a DROPPED one, is kept."""
    WatchProgress = apps.get_model("tracker", "WatchProgress")
    WatchEvent = apps.get_model("tracker", "WatchEvent")
    has_watched_episode = WatchEvent.objects.filter(
        profile_id=OuterRef("profile_id"), title_id=OuterRef("title_id"), episode__isnull=False
    )
    WatchProgress.objects.filter(
        status__in=["watching", "completed"],
        position_seconds=0,
        title__media_type__in=["tv", "anime"],
    ).exclude(Exists(has_watched_episode)).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0056_add_profile_totp_2fa"),
    ]

    operations = [
        migrations.RunPython(remove_stale_watch_progress, migrations.RunPython.noop),
    ]
