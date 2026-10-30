from django.db import migrations

# Title lookups by TMDB id ("external_ids -> 'tmdb' = ...", often 100+ of
# them OR-ed together when matching a page of discover results) had no index
# to use and scanned the whole table. An expression index on exactly the
# expression Django emits for external_ids__tmdb fixes that. Postgres only:
# SQLite (dev/tests) is small enough not to care, and the index is not part
# of the model state.


def create_index(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            "CREATE INDEX IF NOT EXISTS tracker_title_tmdb_id_idx ON tracker_title ((external_ids -> 'tmdb'))"
        )


def drop_index(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("DROP INDEX IF EXISTS tracker_title_tmdb_id_idx")


class Migration(migrations.Migration):
    dependencies = [("tracker", "0081_preview_ratings_cache")]
    operations = [migrations.RunPython(create_index, drop_index)]
