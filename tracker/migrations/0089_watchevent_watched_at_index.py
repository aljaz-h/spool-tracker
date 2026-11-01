# An index on WatchEvent.watched_at alone, so the household feeds (the newest
# watches by anyone who shares) can read the newest rows and stop.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tracker', '0088_wrapped_all_time_kind'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='watchevent',
            index=models.Index(fields=['watched_at'], name='watchevent_watched_at_idx'),
        ),
    ]
