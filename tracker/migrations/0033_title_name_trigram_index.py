import django.contrib.postgres.indexes
from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    """Speeds up the search bar's name__icontains scan on Postgres via a
    trigram GIN index - see tracker/models.py's Title.Meta.indexes
    comment. TrigramExtension activates the pg_trgm extension the index's
    gin_trgm_ops opclass needs; both this and AddIndex(GinIndex(...))
    degrade gracefully on SQLite (confirmed via sqlmigrate: SQLite's
    schema editor silently creates a plain non-GIN index instead, and
    TrigramExtension is a documented no-op there), so the app's SQLite
    dev-fallback keeps migrating cleanly without pg_trgm - it just
    doesn't get the accelerated search path Postgres does."""

    dependencies = [
        ('tracker', '0032_encrypt_provider_credentials'),
    ]

    operations = [
        TrigramExtension(),
        migrations.AddIndex(
            model_name='title',
            index=django.contrib.postgres.indexes.GinIndex(fields=['name'], name='tracker_title_name_trgm', opclasses=['gin_trgm_ops']),
        ),
    ]
