"""Fernet symmetric encryption for secrets that need to be stored (not
just hashed) - currently just NuvioConnection.refresh_token. No such
convention existed before this (Trakt/Simkl tokens are plaintext,
matching their own OAuth apps' revocable/scoped nature - a Nuvio refresh
token is closer to a password-equivalent, worth the extra bar). Key is
derived from DJANGO_SECRET_KEY via SHA-256 rather than requiring a new
env var - every existing install already has a secret key, so this needs
no new .env/setup step and no migration path for instances that update
without setting a new var.
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
