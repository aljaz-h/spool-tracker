from django.db import migrations, models


def nuvio_flag_into_direction(apps, schema_editor):
    """Nuvio's old on/off flag becomes the direction: off stays off, on is
    the pull it always was."""
    NuvioConnection = apps.get_model("tracker", "NuvioConnection")
    NuvioConnection.objects.filter(sync_enabled=False).update(sync_direction="off")


def direction_into_nuvio_flag(apps, schema_editor):
    NuvioConnection = apps.get_model("tracker", "NuvioConnection")
    NuvioConnection.objects.exclude(sync_direction__in=("pull", "both")).update(sync_enabled=False)


DIRECTIONS = [("pull", "Import into Spool"), ("push", "Send from Spool"), ("both", "Both ways"), ("off", "Connected, not syncing")]


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0076_schedule_plan_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="externalaccount",
            name="sync_direction",
            field=models.CharField(choices=DIRECTIONS, default="pull", max_length=4),
        ),
        migrations.AddField(
            model_name="nuvioconnection",
            name="sync_direction",
            field=models.CharField(choices=DIRECTIONS, default="pull", max_length=4),
        ),
        migrations.RunPython(nuvio_flag_into_direction, direction_into_nuvio_flag),
        migrations.RemoveField(
            model_name="nuvioconnection",
            name="sync_enabled",
        ),
    ]
