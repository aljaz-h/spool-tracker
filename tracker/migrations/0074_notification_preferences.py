from django.db import migrations, models

# The three per-source booleans become entries in Profile.notification_prefs
# (see tracker/notification_prefs.py). They were all-or-nothing - a source
# that was off never created a row, so never pushed either - so an "off"
# turns both channels off. "On" is the default and stores nothing.
LEGACY = {
    "notify_new_releases": "new_release",
    "notify_upcoming_releases": "upcoming_release",
    "notify_sync_failures": "sync_failed",
}


def move_flags_into_prefs(apps, schema_editor):
    Profile = apps.get_model("tracker", "Profile")
    for profile in Profile.objects.all():
        prefs = dict(profile.notification_prefs or {})
        for field, key in LEGACY.items():
            if not getattr(profile, field):
                prefs[key] = {"app": False, "push": False}
        if prefs != (profile.notification_prefs or {}):
            profile.notification_prefs = prefs
            profile.save(update_fields=["notification_prefs"])


def move_prefs_into_flags(apps, schema_editor):
    Profile = apps.get_model("tracker", "Profile")
    for profile in Profile.objects.all():
        prefs = profile.notification_prefs or {}
        for field, key in LEGACY.items():
            setattr(profile, field, (prefs.get(key) or {}).get("app", True))
        profile.save(update_fields=list(LEGACY))


class Migration(migrations.Migration):

    dependencies = [
        ('tracker', '0073_pg_stat_statements'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='notification_prefs',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(move_flags_into_prefs, move_prefs_into_flags),
        migrations.RemoveField(
            model_name='profile',
            name='notify_new_releases',
        ),
        migrations.RemoveField(
            model_name='profile',
            name='notify_sync_failures',
        ),
        migrations.RemoveField(
            model_name='profile',
            name='notify_upcoming_releases',
        ),
        migrations.AddField(
            model_name='notification',
            name='in_app',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='notification',
            name='kind',
            field=models.CharField(choices=[('new_release', 'New release'), ('upcoming_release', 'Upcoming release'), ('digital_release', 'Digital release'), ('sync_failed', 'Sync failed'), ('system_update', 'System update'), ('recommendation_received', 'Recommendation received'), ('recommendation_watched', 'Recommendation watched'), ('recommendation_replied', 'Recommendation replied'), ('watchlist_stale', 'Watchlist time capsule'), ('import_status', 'Import'), ('backup_status', 'Backup')], max_length=25),
        ),
        migrations.AlterField(
            model_name='releaseschedule',
            name='release_type',
            field=models.CharField(choices=[('episode', 'New episode'), ('season_premiere', 'Season premiere'), ('movie_release', 'Movie release'), ('digital_release', 'Digital release')], max_length=20),
        ),
    ]
