# Creates the pg_stat_statements extension on Postgres so Settings ->
# Server -> Query performance has something to read (see
# tracker/query_stats.py). A no-op on SQLite, and it never fails the
# migration: if the database user may not create extensions, or the
# extension is not installed, it simply is not created and the panel says
# so. (The extension also has to be preloaded by the server - the compose
# files do that - or Postgres will not collect anything.)

from django.db import migrations


def create_extension(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "DO $$ BEGIN CREATE EXTENSION IF NOT EXISTS pg_stat_statements; "
            "EXCEPTION WHEN OTHERS THEN RAISE NOTICE 'pg_stat_statements could not be created'; END $$"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0072_title_search_name"),
    ]

    operations = [
        migrations.RunPython(create_extension, migrations.RunPython.noop),
    ]
