"""Settings -> Notifications: which notifications a profile gets and where.

Every notification Spool creates belongs to one *setting* (a row in the
Notifications table), and each setting has two independent channels - the
in-app bell and push (tracker/push.py). A profile only stores the choices
that differ from the defaults below (Profile.notification_prefs), so
changing a default later reaches everyone who never touched it.

Pure data and lookups, no model imports - models.py and notifications.py
both import this, and the kinds are plain strings (Notification.Kind
values) for that reason.
"""

from dataclasses import dataclass

APP = "app"
PUSH = "push"

GROUP_RELEASES = "Releases"
GROUP_SOCIAL = "Recommendations"
GROUP_LIBRARY = "Your library"
GROUP_SYSTEM = "System & data"


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    hint: str
    group: str
    kinds: tuple
    app: bool = True
    push: bool = True
    owner_only: bool = False


SETTINGS = (
    Setting(
        "new_release", "New episodes & seasons",
        "A title you're watching has a new episode, season or movie out.",
        GROUP_RELEASES, ("new_release",),
    ),
    Setting(
        "upcoming_release", "Upcoming release reminders",
        "A few days before something on your lists or that you watch is released.",
        GROUP_RELEASES, ("upcoming_release",),
    ),
    Setting(
        "digital_release", "Digital releases",
        "A movie on your watchlist becomes available to buy, rent or stream at home.",
        GROUP_RELEASES, ("digital_release",), app=False, push=False,
    ),
    Setting(
        "recommendations", "Recommendations",
        "A housemate recommends something, watches or replies to one of yours.",
        GROUP_SOCIAL, ("recommendation_received", "recommendation_watched", "recommendation_replied"),
    ),
    Setting(
        "watchlist_stale", "Watchlist time capsule",
        "A nudge for titles that have sat on your watchlist for months.",
        GROUP_LIBRARY, ("watchlist_stale",),
    ),
    Setting(
        "sync_failed", "Sync failures",
        "Trakt, Simkl or Nuvio sync failed.",
        GROUP_SYSTEM, ("sync_failed",),
    ),
    Setting(
        "backup_status", "Backups",
        "A manual backup finishes, or a scheduled one starts failing.",
        GROUP_SYSTEM, ("backup_status",), push=False,
    ),
    Setting(
        "import_status", "Imports",
        "An import is ready to review, finishes or fails.",
        GROUP_SYSTEM, ("import_status",), push=False,
    ),
    Setting(
        "system_update", "App updates",
        "A newer version of Spool has been released. Only the server owner gets these.",
        GROUP_SYSTEM, ("system_update",), owner_only=True,
    ),
)

BY_KEY = {s.key: s for s in SETTINGS}
BY_KIND = {kind: s for s in SETTINGS for kind in s.kinds}


def setting_for_kind(kind):
    return BY_KIND.get(kind)


def value(profile, setting, channel):
    """One channel of one setting for this profile: the stored choice, else
    the default."""
    stored = (profile.notification_prefs or {}).get(setting.key)
    if isinstance(stored, dict) and isinstance(stored.get(channel), bool):
        return stored[channel]
    return getattr(setting, channel)


def channels(profile, kind):
    """(in_app, push) wanted for a Notification.Kind. A kind outside the
    table (none today) gets both, which is what it did before this existed."""
    setting = BY_KIND.get(kind)
    if setting is None:
        return True, True
    return value(profile, setting, APP), value(profile, setting, PUSH)


def visible_settings(profile):
    """The rows this profile's table shows - owner-only ones are left out
    for everyone else, since they could never receive them."""
    owner = bool(getattr(profile.user, "is_superuser", False))
    return [s for s in SETTINGS if owner or not s.owner_only]


def table(profile):
    """Rows for the template, grouped in table order:
    [(group, [(setting, app_on, push_on), ...]), ...]"""
    groups = {}
    for setting in visible_settings(profile):
        groups.setdefault(setting.group, []).append(
            (setting, value(profile, setting, APP), value(profile, setting, PUSH))
        )
    return list(groups.items())


def parse_post(profile, post):
    """The submitted table -> the dict to store: only settings this profile
    can see are read (an owner-only row a member never sees keeps whatever
    it had), and only choices that differ from the default are kept."""
    prefs = dict(profile.notification_prefs or {})
    for setting in visible_settings(profile):
        chosen = {
            APP: bool(post.get(f"{setting.key}__{APP}")),
            PUSH: bool(post.get(f"{setting.key}__{PUSH}")),
        }
        diff = {ch: on for ch, on in chosen.items() if on != getattr(setting, ch)}
        if diff:
            prefs[setting.key] = diff
        else:
            prefs.pop(setting.key, None)
    return prefs
