from django.db import migrations


def flag_existing_watchlists(apps, schema_editor):
    """Retroactively flags every pre-existing WatchList named exactly
    "Watchlist" (the name the app has always get-or-created by, before
    is_watchlist existed) as the profile's real auto-managed watchlist."""
    WatchList = apps.get_model("tracker", "WatchList")
    WatchList.objects.filter(name="Watchlist").update(is_watchlist=True)


def unflag_watchlists(apps, schema_editor):
    WatchList = apps.get_model("tracker", "WatchList")
    WatchList.objects.filter(name="Watchlist").update(is_watchlist=False)


class Migration(migrations.Migration):

    dependencies = [
        ('tracker', '0010_watchlist_is_watchlist'),
    ]

    operations = [
        migrations.RunPython(flag_existing_watchlists, unflag_watchlists),
    ]
