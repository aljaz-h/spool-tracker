"""Mirrors a subset of in-app Notification rows out through Apprise
(https://github.com/caronc/apprise) to a fixed allowlist of hosted
services - Discord, Slack, Telegram, Pushover, Microsoft Teams, Webex
Teams. Entirely self-service per profile - no server-owner setup,
unlike every OAuth-based integration in this app.

Deliberately NOT "any of the 100+ services Apprise supports", despite
that being the original premise of this feature - every URL a profile
configures runs the server itself, and a non-owner profile on a shared
household Spool instance can set this up too, not just the owner. A
generic webhook scheme (json://, xml://, form://, ...) or a self-
hostable named service given a custom host (ntfy://, matrix://, a self-
hosted Gotify/Rocket.Chat/...) lets the URL's own host be anything at
all, including internal infrastructure or a cloud metadata endpoint
(169.254.169.254) if Spool happens to be hosted on EC2/GCP/etc, which
can hand back real cloud credentials - that's a server-side request
forgery hole, not a hypothetical one.

A connection-level guard (resolve the host, block private/loopback/
link-local ranges right before the real request) was tried first and
rejected: DNS rebinding can change what a hostname resolves to between
the check and the actual connection; several Apprise transports (SMTP
for mailto://, syslog, MQTT, XMPP) never go through requests.Session.send
at all, so a requests-level hook doesn't cover them; and a global
monkeypatch around one send() call isn't safe if two push sends ever
run concurrently in the same worker.

ALLOWED_SCHEMES is a strict allowlist instead, checked before a URL is
ever handed to Apprise at all - every scheme on it resolves to a
hardcoded host baked into that Apprise plugin's own source, never
something the URL itself can redirect elsewhere (confirmed by reading
each plugin's source, not assumed from its name - matrix/matrixs looks
like a similarly "named" service but was excluded after confirming its
own host *is* user-suppliable, since Matrix is a federated/self-
hostable protocol, the exact same shape of risk as ntfy). mailto://,
ntfy://, and generic webhook schemes are a real, deliberate narrowing
of what Apprise itself can do, not a temporary gap - supporting them
safely would need pinned-IP connection validation or a sandboxed
egress path, a bigger piece of work for later if it turns out to
matter to someone.

The allowlist check itself does NOT work by urlsplit()-ing the raw
string and reading off its scheme - Apprise's own URL parsing can
split a single string into *more* URLs than that naive check ever
sees (comma-separated is one of several conventions it understands),
so a crafted single "line" like "discord://valid/webhook,
json://169.254.169.254/evil" could pass a per-candidate scheme check
on what looks like its only scheme while Apprise's own .add() still
walked the comma and added the second, disallowed target too.
_instantiate_if_allowed closes this structurally rather than chasing
Apprise's exact splitting rules: apprise.utils.parse_urls(candidate)
is used to ask Apprise itself how many URLs it derives from the
string - exactly one is required, anything else (zero, or more than
one) is rejected outright - and only then does apprise.Apprise.
instantiate() turn it into the actual plugin OBJECT, whose concrete
class (never its scheme name) is checked against the allowlist.
send() adds that object to the Apprise client directly, never the raw
string, so the thing that was validated is guaranteed to be the thing
that gets contacted - there's no second parsing pass for a smuggled
piece to slip through on.

tracker/apps.py's post_save signal on Notification decides *when* this
fires (created=True, an in-scope Kind, this profile has it configured);
this module only does the actual send, so it's directly testable without
any signal/Celery machinery in the way - same split tracker/notifications.py
itself already uses for its own "eligibility logic vs the thing it
triggers" separation.
"""

import re
from urllib.parse import urlsplit

import apprise
import apprise.plugins as apprise_plugins
import apprise.utils.parse as apprise_parse

from .models import Notification

# Discord's @everyone/@here and <@user>/<@&role>/<#channel> mention
# syntax, and Slack's <!here>/<!channel>/<!everyone>/<@U123> syntax -
# matched so _defuse_mentions (see send()) can break each one with an
# invisible character before it ever reaches a real webhook.
_MENTION_PATTERN = re.compile(r"@(?:everyone|here)\b|<[@#!]", re.IGNORECASE)


def _defuse_mentions(text):
    """A body/title passed to send() can contain another profile's own
    free text, not just this server's fixed strings - a recommendation
    reply (tracker/recommendations.py's add_reply, up to 160 chars) is
    stored and shown as-is in the in-app bell, but here it's about to
    leave the app through *this* profile's own configured webhook, so
    one profile typing "@everyone" (or Discord's raw <@&roleid> mention
    syntax) into a reply must not actually page an entire external
    Discord/Slack channel. Breaks the trigger with a zero-width space
    rather than stripping it outright - the text still reads the same to
    a human, it just stops matching the exact prefix Discord/Slack's own
    parsers require."""
    return _MENTION_PATTERN.sub(lambda m: m.group(0)[0] + "​" + m.group(0)[1:], text)

# The only Notification.Kind values this ever fires for - every one of
# these is created via a single Notification.objects.create()/
# get_or_create() call (a real .save(), so post_save sees it), unlike
# IMPORT_STATUS/BACKUP_STATUS, which get their *useful* terminal state
# via a QuerySet.update() call instead (tracker/transfer_tracking.py) -
# .update() never fires post_save at all, so a signal-based design can
# only ever see those two kinds' first ("scanning started"/"backing
# up...") row, typically the least useful moment to push. Covering their
# real finished/failed state would need an explicit dispatch call at
# those update() sites instead of relying on this signal - a reasonable
# follow-up, deliberately not done here. Both are also already something
# the profile is actively watching happen in the app when they trigger
# it, not a "fire and forget while I'm away" case the way a release or a
# sync failure is.
PUSH_ELIGIBLE_KINDS = frozenset(
    {
        Notification.Kind.NEW_RELEASE,
        Notification.Kind.UPCOMING_RELEASE,
        Notification.Kind.SYNC_FAILED,
        Notification.Kind.SYSTEM_UPDATE,
        Notification.Kind.RECOMMENDATION_RECEIVED,
        Notification.Kind.RECOMMENDATION_WATCHED,
        Notification.Kind.RECOMMENDATION_REPLIED,
        Notification.Kind.WATCHLIST_STALE,
    }
)

# scheme -> display name, for error messages and Settings UI copy - see
# this module's own docstring for exactly why each one is safe (a fixed,
# hardcoded host in the Apprise plugin itself) and why look-alikes like
# matrix/ntfy/mailto aren't on this list despite being named services.
ALLOWED_SCHEMES = {
    "discord": "Discord",
    "slack": "Slack",
    "tgram": "Telegram",
    "pover": "Pushover",
    "o365": "Microsoft Teams",
    "wxteams": "Webex Teams",
    "webex": "Webex Teams",
}

# Class identity, not scheme-name string matching - see the module
# docstring's explanation of why a scheme-string check on a raw
# candidate isn't enough on its own.
_ALLOWED_CLASSES = {apprise_plugins.N_MGR[scheme] for scheme in ALLOWED_SCHEMES}


def _urls(raw):
    """profile.get_push_notification_urls() (one Apprise URL per line) ->
    a clean list, blank lines and surrounding whitespace dropped."""
    return [line.strip() for line in (raw or "").splitlines() if line.strip()]


def _instantiate_if_allowed(candidate):
    """One line from the textarea -> (plugin_object, error). error is
    None on success. Rejects a candidate that Apprise's own parsing
    would derive anything other than exactly one real URL from (a comma
    or other Apprise multi-URL delimiter embedded in what's meant to be
    a single line) before ever instantiating it, then rejects the
    instantiated object's class if it isn't on the allowlist - see the
    module docstring for why both steps matter."""
    sub_urls = apprise_parse.parse_urls(candidate)
    if len(sub_urls) != 1:
        return None, f'"{candidate}" looks like more than one URL - put each on its own line.'
    obj = apprise.Apprise.instantiate(candidate, suppress_exceptions=True)
    if obj is None or type(obj) not in _ALLOWED_CLASSES:
        scheme = urlsplit(candidate).scheme.lower() or "unrecognized"
        return None, f'"{candidate}" uses an unsupported scheme ("{scheme}").'
    return obj, None


def validate_urls(raw):
    """raw: profile.get_push_notification_urls() (or the Settings form's
    submitted-but-maybe-unsaved value) -> (valid_urls, errors).
    valid_urls are each allowed candidate's own normalized .url() string
    (safe to persist and to re-instantiate later - verified to round-
    trip through a second instantiate() with no further splitting).
    errors is a list of human-readable strings, one per rejected line,
    naming the URL and why it got rejected - used by both
    views.save_notifications (drop the line outright, see its own
    docstring) and views.test_push_notification (same rule, same
    wording, so a profile never discovers the difference between "saved
    fine" and "works" the hard way)."""
    valid, errors = [], []
    for candidate in _urls(raw):
        obj, error = _instantiate_if_allowed(candidate)
        if obj is not None:
            valid.append(obj.url())
        else:
            errors.append(error)
    return valid, errors


def send(profile, title, message, url=None):
    """Sends one push via every *allowlisted* URL configured on profile -
    see validate_urls/ALLOWED_SCHEMES. Any URL using a scheme outside the
    allowlist is silently skipped here (not an error at send time - by
    this point the profile already had a chance to fix it with a clear
    message at save time; this is just the defense-in-depth backstop in
    case something bad ever ends up stored some other way). The
    instantiated plugin OBJECT is what gets added to the Apprise client,
    never the raw string - see the module docstring for why that's the
    part that actually closes the multi-URL-smuggling bypass, not just
    the validation step on its own. Returns True only if every
    allowlisted target succeeded (Apprise's own AppriseResult is falsy
    on any failure, including a partial one across several URLs). False
    when there's nothing configured, or nothing configured passes the
    allowlist, or delivery itself failed - Apprise's own add()/notify()
    never raise for a bad target, they just don't count it (confirmed
    empirically, not assumed).

    url: an optional click-through link (Notification.link, e.g. a
    title's detail page) - Apprise's own notify() has no generic
    "clickable link" parameter (a handful of individual services support
    one via their own target-URL query params, not a runtime argument),
    so this just appends it to the message body, which every service
    renders as plain text at minimum.

    title/message are defused of Discord/Slack broadcast-mention syntax
    (see _defuse_mentions) before being sent - message in particular can
    be another profile's own free text (a recommendation reply), not
    just a fixed string this app wrote itself."""
    objects = []
    for candidate in _urls(profile.get_push_notification_urls()):
        obj, _error = _instantiate_if_allowed(candidate)
        if obj is not None:
            objects.append(obj)
    if not objects:
        return False
    client = apprise.Apprise()
    for obj in objects:
        client.add(obj)
    title = _defuse_mentions(title)
    message = _defuse_mentions(message)
    body = f"{message}\n{url}" if url else message
    return bool(client.notify(title=title, body=body, body_format=apprise.NotifyFormat.TEXT))
