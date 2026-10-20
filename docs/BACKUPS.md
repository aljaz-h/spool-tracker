# Backups

## Full backup file

**Settings → Import & Export → Export full backup (JSON)** downloads everything
a profile owns:

- watch history, with ratings
- every list, including the Watchlist, with its sharing setting, tags and order

The file starts with `"format": "spool-backup"`. To restore it, upload it under
**Import Data**. It goes through Import Review like any other import:

- History rows are matched against what you already have.
- Lists are restored when you confirm the import.
- Titles already on a list are left alone, so importing the same backup twice is safe.

## Automatic backups

**Settings → Import & Export → Automatic Backups.** Each profile chooses for
itself whether to back up, and where to. For each destination, pick how often
(daily, weekly or monthly) and how many backups to keep (the last 5, 10, 30, or
all of them).

- Once an hour, the scheduler starts any backup that is due. **Back up now**
  runs one straight away.
- If a backup fails, the reason is shown on the card and in the Logs tab. Spool
  tries again 6 hours later.
- Files are named `spool-backup-<profile>-<id>-<YYYY-MM-DD-HHMM>.json`.
- Spool only ever deletes old files that start with *that profile's* name, so
  several profiles can share one folder.

### Server folder

Backups are written to `BACKUP_ROOT` (`/app/backups` in Docker), inside a
subfolder each profile chooses. The `worker` service mounts the `backup_data`
volume there.

To keep the backups on a NAS, replace that volume with a bind mount of a
mounted share. For example, with the share mounted on the host at
`/mnt/nas/spool`:

```yaml
  worker:
    volumes:
      - media_data:/app/media
      - /mnt/nas/spool:/app/backups
```

### WebDAV (NAS, Nextcloud, …)

Enter the full URL of the folder, plus a username and password. Spool creates
the folder if it doesn't exist and checks that it can reach it before saving.
The password is stored encrypted.

| Server | Folder URL |
| --- | --- |
| Nextcloud / ownCloud | `https://cloud.example.com/remote.php/dav/files/<user>/Spool` (use an app password) |
| Synology | WebDAV Server package, then `https://nas.local:5006/<shared folder>/Spool` |
| QNAP / TrueNAS / Unraid | enable their WebDAV service, then use the share URL |

### Google Drive and Dropbox

The server owner registers an OAuth app once. After that, every profile can
connect its own account.

The credentials go in **Settings → Server Integrations** (or in `.env` as
`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` and `DROPBOX_APP_KEY` /
`DROPBOX_APP_SECRET`). That page also shows the exact redirect URIs to register.

**Google Drive**

1. In Google Cloud Console, create a project, then separately go to **APIs &
   Services → Library**, search **Google Drive API**, and click **Enable**.
   This is its own step, not something creating OAuth credentials does for
   you — skip it and the OAuth connect flow itself still succeeds, but every
   backup then fails with `Google Drive API has not been used in project
   ... or it is disabled (HTTP 403)`, which only shows up later on the Logs
   tab, not at connect time.
2. On the OAuth consent screen (**Branding**/**Audience** pages), add the
   scope `https://www.googleapis.com/auth/drive.file` and fill in app name +
   support email. This scope only lets Spool see files it created itself.
3. Leave **Publishing status** as *Testing* and, under **Audience → Test
   users**, add the Google account(s) that will actually connect. Anyone not
   listed gets an "Access blocked" / `Error 403: access_denied` page.
   Publishing to *Production* instead avoids that allowlist, but
   `drive.file` is a sensitive scope, so production mode shows every
   connecting user Google's "unverified app" warning screen unless you go
   through full verification — not worth it for a handful of household
   accounts. Testing mode's refresh tokens expire after 7 days of *no use*,
   but Spool's own recurring backup schedule uses the token at least weekly,
   so in practice it never actually goes idle long enough to expire.
4. Create an OAuth client (**Clients** page) of type **Web application**.
   Add the exact redirect URI
   `https://<your spool>/settings/backups/gdrive/callback/` — the bare
   domain isn't enough, Google requires an exact path match.

Backups go into a **Spool Backups** folder in the account's Drive.

**Dropbox**

Simpler than Google here - there's no separate "enable the API" step and no
test-user allowlist blocking anyone from connecting.

1. At dropbox.com/developers/apps, create an app with **Scoped access** and an
   **App folder**.
2. Under Permissions, enable `files.content.write`, `files.content.read`,
   `files.metadata.read` and `account_info.read`.
3. Add the redirect URI `https://<your spool>/settings/backups/dropbox/callback/`.
4. Until the app is approved for production, Dropbox lets up to 500 users
   connect to it, which is plenty for a household.

Backups go into `Apps/<your app>/Spool Backups`.
