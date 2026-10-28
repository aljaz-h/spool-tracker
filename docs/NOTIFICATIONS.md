← [Back to README](../README.md)

# Push notifications

The header bell is always there, but it only helps if you're actually
looking at Spool. Settings → Notifications → **Push Notifications**
mirrors a subset of it out to a hosted service of your choice, via
[Apprise](https://github.com/caronc/apprise), so a new episode or a
failed sync can reach you without the app open.

No server-owner setup needed — unlike Trakt/Simkl/WeTrakr/MyAnimeList
and the rest of Server Integrations, this is entirely self-service per
profile. Paste a URL, turn it on, done.

## Supported services

Only a fixed set of hosted services, not Apprise's full catalog:

| Service | URL scheme |
|---|---|
| Discord | `discord://webhook_id/webhook_token` |
| Slack | `slack://TokenA/TokenB/TokenC/` |
| Telegram | `tgram://bot_token/chat_id` |
| Pushover | `pover://user_key@app_token` |
| Microsoft Teams | `o365://...` |
| Webex Teams | `wxteams://api_key/` |

Each service's own setup page (linked from its row in
[Apprise's own README](https://github.com/caronc/apprise#popular-notification-services))
explains how to get the exact values for its URL — a Discord webhook
URL, a Telegram bot token, and so on.

You can add more than one — put each URL on its own line, and every one
gets notified.

## Why not the rest of Apprise (ntfy, email, self-hosted services, generic webhooks)?

Every URL you configure here runs *on the server*, and any profile in
the household can set one up, not just the owner. A generic webhook
scheme (`json://`, `xml://`, `form://`, ...) — or a self-hostable named
service given a custom host (`ntfy://`, `matrix://`, a self-hosted
Gotify/Rocket.Chat/...) — lets that URL's own host be anything at all,
including internal network addresses or (if Spool happens to be hosted
on EC2/GCP/etc.) a cloud metadata endpoint that can hand back real
credentials. That's a server-side request forgery hole, not a
hypothetical one.

Every scheme on the supported list above resolves to a fixed, hardcoded
host baked into Apprise's own plugin code for that service — never
something the URL itself can redirect elsewhere — which is what makes
them safe to let any profile configure. `mailto://` (an arbitrary SMTP
server), `ntfy://`, self-hosted `matrix://`, and generic webhook schemes
are deliberately not supported yet, not an oversight — safely allowing
them would need per-connection destination validation or a sandboxed
egress path, which is a bigger piece of work for a future pass if it
turns out to matter to you.

Pasting a URL with an unsupported scheme is rejected with a clear
message naming the URL and what *is* supported — both when you save and
when you hit **Send test**.

## Choosing what you get, and where

Settings → Notifications has one row per type of notification, each with
two checkboxes: **In app** (the header bell) and **Push** (the services
above). They are independent, so you can have sync failures in both,
the watchlist time capsule in the bell only, or digital releases as a
push only. Turning both off silences that type completely.

| Type | In app | Push |
|---|---|---|
| New episodes & seasons | on | on |
| Upcoming release reminders | on | on |
| Digital releases | **off** | **off** |
| Recommendations (received / watched / replied) | on | on |
| Watchlist time capsule | on | on |
| Sync failures | on | on |
| Backups | on | off |
| Imports | on | off |
| App updates (server owner only) | on | on |

Push only does anything once push is turned on and has a URL saved (the
card below the table); until then your Push choices are remembered but
inactive.

- **Digital releases** tells you when a movie on your watchlist becomes
  available to buy, rent or stream at home (TMDB's US digital release
  date). TMDB often has no date until shortly before it happens, so the
  alert arrives on the day the date passes, and only for movies you have
  not already watched.
- **Backups** and **Imports** default to in-app only because you are
  usually watching them happen. Tick Push to also get the result of a
  manual backup or import, and the first failure of a scheduled backup,
  when you are away from the app.
- A type that is push-only leaves no entry in the bell and does not count
  towards its unread badge.

## Testing a URL

**Send test** sends immediately, using whatever's currently in the
Apprise URL box — even if you haven't hit Save yet, so you can iterate
on a URL without a round-trip first. A failed delivery is reported as a
plain "couldn't deliver" either way, without saying why (a malformed
URL, an expired token, and a temporarily-down service all read the
same) — the detail that *would* help you fix it (which specific line
failed) only matters for an unsupported scheme, which is exactly what
the error message tells you.
