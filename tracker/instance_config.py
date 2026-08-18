"""Resolves Trakt/Simkl/TMDB credentials from the database first, falling
back to the .env-sourced Django settings when the database has nothing set
- lets an admin configure these from Settings without editing .env and
restarting containers, while not breaking an existing install that already
has working .env credentials and hasn't touched the new UI yet."""

from django.conf import settings

from .models import InstanceConfig


def get_trakt_credentials():
    cfg = InstanceConfig.load()
    return (
        cfg.trakt_client_id or settings.TRAKT_CLIENT_ID,
        cfg.get_trakt_client_secret() or settings.TRAKT_CLIENT_SECRET,
    )


def get_simkl_credentials():
    cfg = InstanceConfig.load()
    return (
        cfg.simkl_client_id or settings.SIMKL_CLIENT_ID,
        cfg.get_simkl_client_secret() or settings.SIMKL_CLIENT_SECRET,
    )


def get_credentials(provider):
    if provider == "trakt":
        return get_trakt_credentials()
    if provider == "simkl":
        return get_simkl_credentials()
    raise ValueError(f"unknown provider: {provider!r}")


def get_tmdb_api_key():
    cfg = InstanceConfig.load()
    return cfg.get_tmdb_api_key() or settings.TMDB_API_KEY


def get_mdblist_api_key():
    cfg = InstanceConfig.load()
    return cfg.get_mdblist_api_key() or settings.MDBLIST_API_KEY


def get_spool_wrapped_config():
    """(spool_wrapped_url, sso_shared_secret) for the admin dashboard's
    "Manage Wrapped" button (views.manage_wrapped) - no .env fallback like
    the credentials above, since these have no corresponding Django
    setting; set via Settings' Server Integrations tab only."""
    cfg = InstanceConfig.load()
    return cfg.spool_wrapped_url, cfg.get_sso_shared_secret()
