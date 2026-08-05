from django.db import migrations, models


def _encrypt_existing_secrets(apps, schema_editor):
    """Forward: encrypt whatever plaintext values already exist into the
    new encrypted_* fields, before the old plaintext fields are dropped
    below - see tracker/crypto.py for the Fernet helper this uses (same
    one NuvioConnection.encrypted_refresh_token already relied on)."""
    from tracker import crypto

    ExternalAccount = apps.get_model('tracker', 'ExternalAccount')
    for account in ExternalAccount.objects.all():
        update_fields = []
        if account.access_token:
            account.encrypted_access_token = crypto.encrypt(account.access_token)
            update_fields.append('encrypted_access_token')
        if account.refresh_token:
            account.encrypted_refresh_token = crypto.encrypt(account.refresh_token)
            update_fields.append('encrypted_refresh_token')
        if update_fields:
            account.save(update_fields=update_fields)

    InstanceConfig = apps.get_model('tracker', 'InstanceConfig')
    for cfg in InstanceConfig.objects.all():
        update_fields = []
        if cfg.trakt_client_secret:
            cfg.encrypted_trakt_client_secret = crypto.encrypt(cfg.trakt_client_secret)
            update_fields.append('encrypted_trakt_client_secret')
        if cfg.simkl_client_secret:
            cfg.encrypted_simkl_client_secret = crypto.encrypt(cfg.simkl_client_secret)
            update_fields.append('encrypted_simkl_client_secret')
        if cfg.tmdb_api_key:
            cfg.encrypted_tmdb_api_key = crypto.encrypt(cfg.tmdb_api_key)
            update_fields.append('encrypted_tmdb_api_key')
        if update_fields:
            cfg.save(update_fields=update_fields)


def _decrypt_back_to_plaintext(apps, schema_editor):
    """Reverse of the above, for `migrate` backwards - decrypts back into
    the plaintext fields this migration's own RemoveField operations
    below re-add on the way down, so rolling back doesn't just silently
    lose every connected account's tokens."""
    from tracker import crypto

    ExternalAccount = apps.get_model('tracker', 'ExternalAccount')
    for account in ExternalAccount.objects.all():
        update_fields = []
        if account.encrypted_access_token:
            account.access_token = crypto.decrypt(account.encrypted_access_token)
            update_fields.append('access_token')
        if account.encrypted_refresh_token:
            account.refresh_token = crypto.decrypt(account.encrypted_refresh_token)
            update_fields.append('refresh_token')
        if update_fields:
            account.save(update_fields=update_fields)

    InstanceConfig = apps.get_model('tracker', 'InstanceConfig')
    for cfg in InstanceConfig.objects.all():
        update_fields = []
        if cfg.encrypted_trakt_client_secret:
            cfg.trakt_client_secret = crypto.decrypt(cfg.encrypted_trakt_client_secret)
            update_fields.append('trakt_client_secret')
        if cfg.encrypted_simkl_client_secret:
            cfg.simkl_client_secret = crypto.decrypt(cfg.encrypted_simkl_client_secret)
            update_fields.append('simkl_client_secret')
        if cfg.encrypted_tmdb_api_key:
            cfg.tmdb_api_key = crypto.decrypt(cfg.encrypted_tmdb_api_key)
            update_fields.append('tmdb_api_key')
        if update_fields:
            cfg.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ('tracker', '0031_watchevent_source'),
    ]

    operations = [
        migrations.AddField(
            model_name='externalaccount',
            name='encrypted_access_token',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='externalaccount',
            name='encrypted_refresh_token',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='instanceconfig',
            name='encrypted_simkl_client_secret',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='instanceconfig',
            name='encrypted_tmdb_api_key',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='instanceconfig',
            name='encrypted_trakt_client_secret',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.RunPython(_encrypt_existing_secrets, _decrypt_back_to_plaintext),
        migrations.RemoveField(
            model_name='externalaccount',
            name='access_token',
        ),
        migrations.RemoveField(
            model_name='externalaccount',
            name='refresh_token',
        ),
        migrations.RemoveField(
            model_name='instanceconfig',
            name='simkl_client_secret',
        ),
        migrations.RemoveField(
            model_name='instanceconfig',
            name='tmdb_api_key',
        ),
        migrations.RemoveField(
            model_name='instanceconfig',
            name='trakt_client_secret',
        ),
    ]
