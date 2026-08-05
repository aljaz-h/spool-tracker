"""Fernet symmetric encryption for secrets that need to be stored (not
just hashed): NuvioConnection.encrypted_refresh_token,
ExternalAccount.encrypted_access_token/encrypted_refresh_token (Trakt/
Simkl OAuth), and InstanceConfig's encrypted_trakt_client_secret/
encrypted_simkl_client_secret/encrypted_tmdb_api_key. Key is derived
from DJANGO_SECRET_KEY via SHA-256 rather than requiring a new env var -
every existing install already has a secret key, so this needs no new
.env/setup step and no migration path for instances that update without
setting a new var. A consequence worth knowing: rotating SECRET_KEY
without also re-encrypting these fields makes them undecryptable (see
SECURITY.md's rotation guidance) - the same secret key both signs
sessions/CSRF tokens *and* derives this encryption key.
"""

import base64
import hashlib

from cryptography.fernet import Fernet
from django.conf import settings


def _fernet():
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def encrypt(plaintext):
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext):
    return _fernet().decrypt(ciphertext.encode()).decode()
