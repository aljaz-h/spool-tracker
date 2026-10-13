from django.db import migrations


def ensure_watchlists(apps, schema_editor):
    """Gives every existing profile its auto-managed Watchlist - adopting
    a list already named "Watchlist" when there is one (same rule as
    tracker.watchlists.ensure_watchlist, inlined since migrations use the
    historical models)."""
    Profile = apps.get_model("tracker", "Profile")
    WatchList = apps.get_model("tracker", "WatchList")
    for profile in Profile.objects.all():
        if WatchList.objects.filter(profile=profile, is_watchlist=True).exists():
            continue
        existing = WatchList.objects.filter(profile=profile, name__iexact="Watchlist").order_by("id").first()
        if existing:
            existing.is_watchlist = True
            existing.save(update_fields=["is_watchlist"])
        else:
            WatchList.objects.create(profile=profile, name="Watchlist", is_watchlist=True)


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0058_importsession_importcandidate"),
    ]

    operations = [
        migrations.RunPython(ensure_watchlists, migrations.RunPython.noop),
    ]
