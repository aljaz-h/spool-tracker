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

1. In Google Cloud Console, create a project and enable the **Google Drive API**.
2. On the OAuth consent screen, add the scope
   `https://www.googleapis.com/auth/drive.file`. This scope only lets Spool see
   files it created itself, and it needs no Google verification.
3. Set **Publishing status** to *In production*. In *Testing* mode, Google
   expires the connection after 7 days.
4. Create an OAuth client of type **Web application**. Add the redirect URI
   `https://<your spool>/settings/backups/gdrive/callback/`.

Backups go into a **Spool Backups** folder in the account's Drive.

**Dropbox**

1. At dropbox.com/developers/apps, create an app with **Scoped access** and an
   **App folder**.
2. Under Permissions, enable `files.content.write`, `files.content.read`,
   `files.metadata.read` and `account_info.read`.
3. Add the redirect URI `https://<your spool>/settings/backups/dropbox/callback/`.
4. Until the app is approved for production, Dropbox lets up to 500 users
   connect to it, which is plenty for a household.

Backups go into `Apps/<your app>/Spool Backups`.
