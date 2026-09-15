"""TOTP two-factor authentication (Settings → Security) - a thin wrapper
around pyotp/qrcode so views.py only ever deals with plain data (a
secret string, a data: URI, a list of plaintext codes), never the
underlying crypto/encoding calls directly. Optional and per-profile,
same bring-your-own-key spirit as this app's other opt-in credentials,
except the secret and backup codes are generated here (not pasted in by
the user) and the backup codes are hashed at rest via Django's own
password hasher - unlike every other credential this app stores
(cleartext, e.g. ApiToken, InstanceConfig's provider keys), these are a
full authentication-bypass secret, the same class of thing a real
account password is, not a low-stakes third-party integration token."""

import base64
import io
import secrets

import pyotp
import qrcode
from django.contrib.auth.hashers import check_password, make_password

BACKUP_CODE_COUNT = 8


def generate_secret():
    """A fresh base32 secret - not yet saved as this profile's *enabled*
    secret until they prove they can actually generate a matching code
    with it (see verify_code) - see Profile.totp_secret's own docstring
    for why enabling stays gated on that instead of trusting the QR scan
    blindly."""
    return pyotp.random_base32()


def provisioning_qr_data_uri(secret, username):
    """A data: URI PNG of the otpauth:// QR code an authenticator app
    scans to add this secret - generated on the fly per request, never
    written to disk, since the secret itself is only ever "pending"
    (not yet the profile's real totp_secret) at the point this is shown."""
    uri = pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name="Spool")
    image = qrcode.make(uri)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def verify_code(secret, code):
    """valid_window=1 tolerates the code from one 30s step either side of
    now - without it, a device with even a few seconds of clock drift
    (common enough on real phones) would intermittently fail to verify a
    code that's genuinely correct by the time it's typed in and submitted."""
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)


def generate_backup_codes():
    """Plaintext codes to show the user exactly once (setup, or a
    regenerate) - the caller is responsible for hashing these via
    hash_backup_codes before persisting; this function never touches the
    database itself."""
    return ["-".join([secrets.token_hex(2), secrets.token_hex(2)]) for _ in range(BACKUP_CODE_COUNT)]


def hash_backup_codes(plain_codes):
    return [make_password(code) for code in plain_codes]


def consume_backup_code(hashed_codes, submitted_code):
    """Checks submitted_code against every still-unused hash and, on a
    match, returns the remaining list with that one hash removed (single-
    use - the caller persists this returned list back onto the profile).
    Returns None if nothing matched, leaving the caller's existing list
    untouched."""
    submitted_code = submitted_code.strip()
    for hashed in hashed_codes:
        if check_password(submitted_code, hashed):
            return [h for h in hashed_codes if h != hashed]
    return None
