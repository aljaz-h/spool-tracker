# Adds Title.search_name (the normalised name the library search matches
# against - see tracker/search_text.py) and fills it in for existing rows.

from django.db import migrations, models


def backfill_search_name(apps, schema_editor):
    from tracker import search_text

    Title = apps.get_model("tracker", "Title")
    for title in Title.objects.only("id", "name").iterator(chunk_size=500):
        Title.objects.filter(pk=title.pk).update(search_name=search_text.normalize(title.name))


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0071_encrypt_push_notification_urls"),
    ]

    operations = [
        migrations.AddField(
            model_name="title",
            name="search_name",
            field=models.CharField(blank=True, default="", editable=False, max_length=255),
        ),
        migrations.RunPython(backfill_search_name, migrations.RunPython.noop),
    ]
