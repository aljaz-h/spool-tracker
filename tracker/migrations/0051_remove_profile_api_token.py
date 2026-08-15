from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tracker', '0050_apitoken'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='profile',
            name='api_token',
        ),
    ]
