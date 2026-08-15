import django.db.models.deletion
from django.db import migrations, models


def backfill_existing_tokens(apps, schema_editor):
    """Any profile that already had Profile.api_token set (the old single-
    token design) gets one ApiToken row carrying that exact same value,
    named "Custom Player" - so a player/script already configured with
    that token keeps working unchanged after upgrading, instead of
    silently 401ing the next time it scrobbles. Profile.api_token itself
    is dropped in the very next migration, once this has run."""
    Profile = apps.get_model("tracker", "Profile")
    ApiToken = apps.get_model("tracker", "ApiToken")
    for profile in Profile.objects.exclude(api_token__isnull=True).exclude(api_token=""):
        ApiToken.objects.create(profile=profile, name="Custom Player", token=profile.api_token)


class Migration(migrations.Migration):

    dependencies = [
        ('tracker', '0049_alter_recommendation_reply_reaction'),
    ]

    operations = [
        migrations.CreateModel(
            name='ApiToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=60)),
                ('token', models.CharField(max_length=64, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='api_tokens', to='tracker.profile')),
            ],
            options={
                'ordering': ['created_at'],
            },
        ),
        migrations.RunPython(backfill_existing_tokens, migrations.RunPython.noop),
    ]
