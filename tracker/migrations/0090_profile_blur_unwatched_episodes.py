# A per-profile setting: blur the thumbnails of episodes not watched yet.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tracker', '0089_watchevent_watched_at_index'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='blur_unwatched_episodes',
            field=models.BooleanField(default=False),
        ),
    ]
