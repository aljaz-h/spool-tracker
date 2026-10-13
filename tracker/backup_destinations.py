"""Uploads a Spool backup (tracker/backup.py) to wherever a profile's
BackupDestination points, and prunes old ones there.

Every kind implements the same three operations - upload(name, data),
list_backups() -> [(name, file_ref)] and delete(file_ref) - so run_backup
below doesn't care where the file goes:

- Google Drive: OAuth (drive.file scope - Spool only ever sees the files
  it created), into a "Spool Backups" folder.
- Dropbox: OAuth (offline refresh token), into /Spool Backups (inside the
  app's own folder when the Dropbox app is an "App folder" app).
- WebDAV: any WebDAV folder URL - Nextcloud/ownCloud, Synology, QNAP,
  TrueNAS, Apache/nginx dav - with a username and (app) password.
- Server folder: a subfolder of settings.BACKUP_ROOT on the machine Spool
  runs on, typically a mounted NAS share or a Docker volume.

Backups are named spool-backup-<profile>-<YYYY-MM-DD-HHMM>.json, and
pruning only ever touches files with this profile's prefix, so several
profiles (or a hand-made file) can share one folder safely.
"""

import ipaddress
import json
import os
import re
import socket
from urllib.parse import quote, unquote, urlencode, urlsplit
from xml.etree import ElementTree

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

TIMEOUT = 60
REMOTE_FOLDER_NAME = "Spool Backups"


class BackupError(Exception):
    """A destination refused or failed - the message is shown in Settings."""


def backup_prefix(profile):
    return f"spool-backup-{slugify(profile.display_name) or 'profile'}-{profile.id}-"


def backup_filename(profile, when=None):
    when = timezone.localtime(when or timezone.now())
    return f"{backup_prefix(profile)}{when:%Y-%m-%d-%H%M}.json"


def _raise_for(resp, what, include_body=True):
    """include_body=False for a server the user chose (WebDAV), so an
    error can't be used to read back what some other address answered."""
    if resp.status_code >= 400:
        detail = (resp.text or "").strip().replace("\n", " ")[:200] if include_body else ""
        raise BackupError(f"{what} failed (HTTP {resp.status_code}){': ' + detail if detail else ''}")


# --------------------------------------------------------------------
# OAuth (Google Drive / Dropbox)
# --------------------------------------------------------------------

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/drive.file"
DROPBOX_AUTH_URL = "https://www.dropbox.com/oauth2/authorize"
DROPBOX_TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"


def authorize_url(kind, client_id, redirect_uri, state):
    if kind == "gdrive":
        params = {
            "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": GOOGLE_SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    params = {
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "token_access_type": "offline", "state": state,
    }
    return f"{DROPBOX_AUTH_URL}?{urlencode(params)}"


def exchange_code(kind, client_id, client_secret, code, redirect_uri):
    """The refresh token (and, when the provider says, the account's
    email/name) for an authorization code."""
    url = GOOGLE_TOKEN_URL if kind == "gdrive" else DROPBOX_TOKEN_URL
    resp = requests.post(url, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "client_id": client_id, "client_secret": client_secret,
    }, timeout=TIMEOUT)
    _raise_for(resp, "Authorization")
    payload = resp.json()
    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        raise BackupError("The provider didn't return a refresh token - try connecting again.")
    label = ""
    access_token = payload.get("access_token")
    try:
        if kind == "gdrive":
            about = requests.get(
                "https://www.googleapis.com/drive/v3/about", params={"fields": "user(emailAddress)"},
                headers={"Authorization": f"Bearer {access_token}"}, timeout=TIMEOUT,
            )
            label = about.json().get("user", {}).get("emailAddress", "") if about.ok else ""
        else:
            account = requests.post(
                "https://api.dropboxapi.com/2/users/get_current_account",
                headers={"Authorization": f"Bearer {access_token}"}, timeout=TIMEOUT,
            )
            label = account.json().get("email", "") if account.ok else ""
    except (requests.RequestException, ValueError):
        label = ""
    return refresh_token, label


def _access_token(destination):
    from tracker import instance_config

    client_id, client_secret = instance_config.get_backup_credentials(destination.kind)
    if not client_id or not client_secret:
        raise BackupError(f"{destination.get_kind_display()} isn't configured on this server any more.")
    url = GOOGLE_TOKEN_URL if destination.kind == "gdrive" else DROPBOX_TOKEN_URL
    resp = requests.post(url, data={
        "grant_type": "refresh_token", "refresh_token": destination.get_secret(),
        "client_id": client_id, "client_secret": client_secret,
    }, timeout=TIMEOUT)
    if resp.status_code in (400, 401):
        raise BackupError(f"{destination.get_kind_display()} access was revoked or expired - reconnect it in Settings.")
    _raise_for(resp, "Token refresh")
    return resp.json()["access_token"]


# --------------------------------------------------------------------
# Destinations
# --------------------------------------------------------------------


class GoogleDrive:
    API = "https://www.googleapis.com/drive/v3/files"
    UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"

    def __init__(self, destination):
        self.destination = destination
        self.headers = {"Authorization": f"Bearer {_access_token(destination)}"}

    def _folder_id(self):
        if self.destination.folder:
            resp = requests.get(
                f"{self.API}/{self.destination.folder}", params={"fields": "id,trashed"},
                headers=self.headers, timeout=TIMEOUT,
            )
            if resp.ok and not resp.json().get("trashed"):
                return self.destination.folder
        resp = requests.post(
            self.API, params={"fields": "id"}, headers=self.headers, timeout=TIMEOUT,
            json={"name": REMOTE_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"},
        )
        _raise_for(resp, "Creating the Google Drive folder")
        self.destination.folder = resp.json()["id"]
        self.destination.save(update_fields=["folder"])
        return self.destination.folder

    def upload(self, name, data):
        metadata = {"name": name, "parents": [self._folder_id()], "mimeType": "application/json"}
        boundary = "spool-backup-boundary"
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\nContent-Type: application/json\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--".encode()
        resp = requests.post(
            self.UPLOAD, params={"uploadType": "multipart", "fields": "id"}, data=body, timeout=TIMEOUT * 5,
            headers={**self.headers, "Content-Type": f"multipart/related; boundary={boundary}"},
        )
        _raise_for(resp, "Uploading to Google Drive")

    def list_backups(self):
        resp = requests.get(self.API, headers=self.headers, timeout=TIMEOUT, params={
            "q": f"'{self._folder_id()}' in parents and trashed = false",
            "fields": "files(id,name)", "pageSize": 1000,
        })
        _raise_for(resp, "Listing Google Drive backups")
        return [(f["name"], f["id"]) for f in resp.json().get("files", [])]

    def delete(self, ref):
        resp = requests.delete(f"{self.API}/{ref}", headers=self.headers, timeout=TIMEOUT)
        if resp.status_code != 404:
            _raise_for(resp, "Deleting an old Google Drive backup")


class Dropbox:
    def __init__(self, destination):
        self.headers = {"Authorization": f"Bearer {_access_token(destination)}"}
        self.folder = f"/{REMOTE_FOLDER_NAME}"

    def upload(self, name, data):
        resp = requests.post(
            "https://content.dropboxapi.com/2/files/upload", data=data, timeout=TIMEOUT * 5,
            headers={
                **self.headers, "Content-Type": "application/octet-stream",
                "Dropbox-API-Arg": json.dumps({"path": f"{self.folder}/{name}", "mode": "overwrite"}),
            },
        )
        _raise_for(resp, "Uploading to Dropbox")

    def list_backups(self):
        resp = requests.post(
            "https://api.dropboxapi.com/2/files/list_folder", json={"path": self.folder},
            headers=self.headers, timeout=TIMEOUT,
        )
        if resp.status_code == 409:  # path/not_found - nothing uploaded yet
            return []
        _raise_for(resp, "Listing Dropbox backups")
        payload = resp.json()
        entries = payload.get("entries", [])
        while payload.get("has_more"):
            resp = requests.post(
                "https://api.dropboxapi.com/2/files/list_folder/continue", json={"cursor": payload["cursor"]},
                headers=self.headers, timeout=TIMEOUT,
            )
            _raise_for(resp, "Listing Dropbox backups")
            payload = resp.json()
            entries += payload.get("entries", [])
        return [(e["name"], e["path_lower"]) for e in entries if e.get(".tag") == "file"]

    def delete(self, ref):
        resp = requests.post(
            "https://api.dropboxapi.com/2/files/delete_v2", json={"path": ref},
            headers=self.headers, timeout=TIMEOUT,
        )
        if resp.status_code != 409:
            _raise_for(resp, "Deleting an old Dropbox backup")


def check_webdav_url(url, allow_local_network=False):
    """Refuses a WebDAV address Spool shouldn't be talking to - the URL is
    typed in by a user and fetched by the server, so without this any
    profile could point it at the server's own internals (the database
    or Redis containers, other services on the host, a cloud metadata
    endpoint). Checked against every address the host resolves to,
    right before each request (so a DNS answer changed after saving
    doesn't get around it):

    - loopback, link-local (incl. 169.254.169.254 metadata), multicast,
      reserved and unspecified addresses: never allowed;
    - private/local-network addresses (a NAS on the LAN, 10.x, 192.168.x,
      Docker networks...): only for the server owner's own profile
      (allow_local_network) - the owner already controls the machine.

    Returns the first validated address (a plain IP string) so the
    caller (WebDAV._request) can pin the actual connection to exactly
    the address checked here - see _PinnedHostAdapter's own docstring
    for why re-resolving the hostname a second time, left to urllib3
    itself, would reopen a DNS-rebinding gap between this check and the
    real request even though this function already re-runs on every
    single call."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BackupError("Enter the WebDAV folder's full address, starting with https:// (or http://).")
    try:
        infos = socket.getaddrinfo(
            parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), proto=socket.IPPROTO_TCP
        )
    except (socket.gaierror, UnicodeError, ValueError) as exc:
        raise BackupError(f"Couldn't find the server {parts.hostname}.") from exc
    if not infos:
        raise BackupError(f"Couldn't find the server {parts.hostname}.")
    validated_address = None
    for info in infos:
        raw_address = info[4][0].split("%", 1)[0]
        ip = ipaddress.ip_address(raw_address)
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise BackupError(f"{parts.hostname} points at an address Spool won't connect to.")
        if not ip.is_global and not allow_local_network:
            raise BackupError(
                f"{parts.hostname} is on a local network - only the server owner can back up to local addresses. "
                "Use an address reachable from the internet."
            )
        if validated_address is None:
            validated_address = raw_address
    return validated_address


class _PinnedHostAdapter(requests.adapters.HTTPAdapter):
    """Makes the request's actual TCP connection go to a specific,
    already-validated IP address instead of letting urllib3 resolve the
    hostname itself a second time - closes a DNS-rebinding gap that
    would otherwise survive check_webdav_url's own re-check-every-call
    design: that function's DNS lookup and the HTTP library's own DNS
    lookup for the real connection are still two separate resolutions
    moments apart, and nothing stops a malicious/compromised DNS answer
    from differing between them (classic SSRF-via-DNS-rebinding - the
    exact gap a CodeQL py/full-ssrf scan flags, since it can't know a
    custom validator function like check_webdav_url is meant to cover
    this).

    TLS verification is unaffected by the pin: build_connection_pool_key_
    attributes (requests' own documented subclassing hook for exactly
    this) still runs first, then only the resulting host_params["host"]
    is swapped for the pinned IP - server_hostname (SNI) and
    assert_hostname (certificate hostname check) are both explicitly set
    to the real hostname, so an HTTPS connection still verifies the
    certificate against the hostname the user actually typed, not the
    IP - a wrong/absent cert for that hostname still fails exactly as it
    would without this adapter.

    send() also pins the HTTP Host header to the real hostname - without
    this, confirmed via a real local-socket test, http.client's own
    Host-header default kicks in and sends whatever the connection
    actually connected to (now the pinned IP) instead, which would
    silently break any WebDAV server reached through name-based virtual
    hosting (a reverse proxy routing by hostname, which most self-hosted
    WebDAV setups - Nextcloud/ownCloud behind nginx or Traefik - are)."""

    def __init__(self, resolved_ip, *args, **kwargs):
        self._resolved_ip = resolved_ip
        super().__init__(*args, **kwargs)

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(request, verify, cert)
        hostname = host_params["host"]
        host_params["host"] = self._resolved_ip
        if host_params.get("scheme") == "https":
            pool_kwargs["server_hostname"] = hostname
            pool_kwargs["assert_hostname"] = hostname
        return host_params, pool_kwargs

    def send(self, request, **kwargs):
        if "Host" not in request.headers:
            parts = urlsplit(request.url)
            default_port = {"http": 80, "https": 443}.get(parts.scheme)
            host_header = parts.hostname if parts.port in (None, default_port) else f"{parts.hostname}:{parts.port}"
            request.headers["Host"] = host_header
        return super().send(request, **kwargs)


class WebDAV:
    def __init__(self, destination):
        self.base = destination.url.rstrip("/") + "/"
        self.auth = (destination.username, destination.get_secret()) if destination.username else None
        self.allow_local_network = destination.profile.is_owner

    def _request(self, method, url, **kwargs):
        # The IP validated here is the one the actual connection below
        # is pinned to (see _PinnedHostAdapter) - not just a check that's
        # discarded once it passes.
        resolved_ip = check_webdav_url(url, self.allow_local_network)
        session = requests.Session()
        adapter = _PinnedHostAdapter(resolved_ip)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        try:
            # No redirects: a redirect could lead to an address the check
            # above would have refused.
            with session:
                resp = session.request(
                    method, url, auth=self.auth, timeout=kwargs.pop("timeout", TIMEOUT), allow_redirects=False,
                    **kwargs,
                )
        except requests.RequestException as exc:
            raise BackupError(f"Couldn't reach {urlsplit(self.base).netloc}: {exc.__class__.__name__}") from exc
        if 300 <= resp.status_code < 400:
            raise BackupError("The WebDAV server redirected the request - enter the folder's final address instead.")
        return resp

    def check(self):
        """Creates the folder if it's missing and confirms it's reachable
        with these credentials - used by Settings' Save/Test."""
        resp = self._request("PROPFIND", self.base, headers={"Depth": "0"})
        if resp.status_code == 404:
            resp = self._request("MKCOL", self.base)
            _raise_for(resp, "Creating the WebDAV folder", include_body=False)
            return
        if resp.status_code in (401, 403):
            raise BackupError("The WebDAV server rejected that username/password.")
        _raise_for(resp, "Opening the WebDAV folder", include_body=False)

    def upload(self, name, data):
        self.check()
        resp = self._request(
            "PUT", self.base + quote(name), data=data, timeout=TIMEOUT * 5,
            headers={"Content-Type": "application/json"},
        )
        _raise_for(resp, "Uploading over WebDAV", include_body=False)

    def list_backups(self):
        resp = self._request("PROPFIND", self.base, headers={"Depth": "1"})
        _raise_for(resp, "Listing WebDAV backups", include_body=False)
        try:
            root = ElementTree.fromstring(resp.content)
        except ElementTree.ParseError as exc:
            raise BackupError("The WebDAV server returned an unreadable folder listing.") from exc
        files = []
        for href in root.iter("{DAV:}href"):
            name = unquote((href.text or "").rstrip("/").rsplit("/", 1)[-1])
            if name.endswith(".json"):
                files.append((name, self.base + quote(name)))
        return files

    def delete(self, ref):
        resp = self._request("DELETE", ref)
        if resp.status_code != 404:
            _raise_for(resp, "Deleting an old WebDAV backup", include_body=False)


def safe_subfolder(value):
    """A folder name under BACKUP_ROOT - path separators and dot-segments
    are stripped so a destination can never point outside it."""
    parts = [re.sub(r"[^\w .-]", "", p).strip() for p in re.split(r"[\\/]+", value or "")]
    return "/".join(p for p in parts if p and p not in (".", ".."))


class ServerFolder:
    def __init__(self, destination):
        root = os.path.realpath(settings.BACKUP_ROOT)
        self.path = os.path.realpath(os.path.join(root, safe_subfolder(destination.folder)))
        if self.path != root and not self.path.startswith(root + os.sep):
            raise BackupError("That folder is outside the server's backup folder.")

    def upload(self, name, data):
        try:
            os.makedirs(self.path, exist_ok=True)
            tmp = os.path.join(self.path, f".{name}.part")
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, os.path.join(self.path, name))
        except OSError as exc:
            raise BackupError(f"Couldn't write to the server folder: {exc.strerror or exc}") from exc

    def list_backups(self):
        try:
            return [(n, os.path.join(self.path, n)) for n in os.listdir(self.path) if n.endswith(".json")]
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise BackupError(f"Couldn't read the server folder: {exc.strerror or exc}") from exc

    def delete(self, ref):
        try:
            os.remove(ref)
        except FileNotFoundError:
            pass


CLIENTS = {"gdrive": GoogleDrive, "dropbox": Dropbox, "webdav": WebDAV, "folder": ServerFolder}


def client_for(destination):
    return CLIENTS[destination.kind](destination)


# --------------------------------------------------------------------
# Scheduling + running
# --------------------------------------------------------------------

INTERVALS = {"daily": 1, "weekly": 7, "monthly": 30}
# Hours to wait before retrying after a failed run, so a broken
# destination doesn't get hammered by every hourly check.
RETRY_AFTER_HOURS = 6


def next_run_at(destination):
    """When this destination's next backup is due (None = due now)."""
    from datetime import timedelta

    candidates = []
    if destination.last_success_at:
        # An hour of slack so a daily backup doesn't creep later by the
        # hourly check's own offset every day.
        candidates.append(destination.last_success_at + timedelta(days=INTERVALS[destination.frequency], hours=-1))
    if destination.last_attempt_at and destination.last_error:
        candidates.append(destination.last_attempt_at + timedelta(hours=RETRY_AFTER_HOURS))
    return max(candidates) if candidates else None


def is_due(destination, now=None):
    if not destination.enabled:
        return False
    due_at = next_run_at(destination)
    return due_at is None or (now or timezone.now()) >= due_at


def prune(client, profile, keep_last):
    """Deletes this profile's oldest backups beyond keep_last. Names sort
    chronologically (the timestamp is the suffix), and only files with
    this profile's own prefix are considered."""
    if not keep_last:
        return 0
    prefix = backup_prefix(profile)
    mine = sorted((name, ref) for name, ref in client.list_backups() if name.startswith(prefix))
    stale = mine[:-keep_last] if len(mine) > keep_last else []
    for _, ref in stale:
        client.delete(ref)
    return len(stale)


def run_backup(destination):
    """Builds, uploads and prunes one backup. Records the outcome on the
    destination and returns (ok, message)."""
    from tracker import backup

    now = timezone.now()
    destination.last_attempt_at = now
    data = backup.build_backup(destination.profile)
    payload = json.dumps(data, separators=(",", ":")).encode()
    name = backup_filename(destination.profile, now)
    try:
        client = client_for(destination)
        client.upload(name, payload)
        pruned = prune(client, destination.profile, destination.keep_last)
    except (BackupError, requests.RequestException) as exc:
        destination.last_error = str(exc)[:500]
        destination.save(update_fields=["last_attempt_at", "last_error"])
        return False, destination.last_error
    destination.last_success_at = now
    destination.last_error = ""
    destination.last_filename = name
    destination.save(update_fields=["last_attempt_at", "last_success_at", "last_error", "last_filename"])
    message = f"{name} ({len(data['history'])} plays, {len(data['lists'])} lists)"
    if pruned:
        message += f", removed {pruned} older backup{'s' if pruned != 1 else ''}"
    return True, message
