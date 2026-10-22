"""Calendar page's "Subscribe" button - turns this profile's own
ReleaseSchedule rows (the exact same data selectors.calendar_releases
already feeds the in-app Calendar) into a plain iCalendar (RFC 5545)
feed, so Google/Apple/Outlook can poll it directly rather than Spool
needing a push integration with any of them. Hand-rolled rather than a
third-party library - the subset of the format this needs (flat VEVENTs,
no recurrence/alarms/attendees) is a few dozen lines of well-specified
text, not worth a new dependency for.

Two VEVENT shapes, matching ReleaseSchedule.has_exact_time (see
release_sync.py and the TVmaze integration that sets it): a TVmaze-
sourced episode gets a real timed event (DTSTART/DTEND, both UTC - no
VTIMEZONE block needed since every consumer understands a bare "Z"
suffix and converts it to the viewer's own zone itself); everything
else (TMDB's date-only fallback) gets an all-day event instead of a
fake midnight time, which would read as a real (and wrong) showtime in
any calendar app.
"""

from datetime import timezone as dt_timezone

from django.urls import reverse
from django.utils import timezone

_FOLD_WIDTH = 73  # octets per content line before a CRLF+space continuation (RFC 5545 §3.1 allows up to 75)
_DEFAULT_RUNTIME_MINUTES = 30


# Every C0/DEL control character except \n (0x0A) - \r is normalized into
# \n before this runs, and \n itself is escaped to the literal two-char
# sequence "\n" afterward, so stripping it here would silently delete an
# intentional line break instead.
_CONTROL_CHARS = "".join(chr(c) for c in range(0x20) if c != 0x0A) + chr(0x7F)
_STRIP_CONTROL_CHARS = str.maketrans("", "", _CONTROL_CHARS)


def _escape_text(value):
    """RFC 5545 §3.3.11 TEXT escaping - backslash first, then the rest,
    so an already-escaped backslash from one of the later substitutions
    never gets escaped a second time. Security: a raw \\r (alone, or as
    part of a \\r\\n pair) is normalized to \\n *before* escaping - title/
    episode names ultimately come from TMDB, not something this profile
    typed, but Titles are shared across every profile on the instance,
    so one profile's data still ends up in another's feed; left
    unescaped, a literal CRLF would terminate this content line early
    and let its own bytes inject a new, attacker-controlled iCalendar
    property into the file. Every other C0/DEL control character is
    stripped outright (RFC 5545 TEXT excludes them all, tab included -
    not worth preserving in a one-line SUMMARY)."""
    return (
        value.replace("\r\n", "\n")
        .replace("\r", "\n")
        .translate(_STRIP_CONTROL_CHARS)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line):
    """RFC 5545 §3.1 line folding - a content line over 75 octets must be
    split across multiple physical lines, each continuation starting
    with a single space. Folds on character count, not true UTF-8
    octets - titles are overwhelmingly ASCII-range in practice, and a
    folded-mid-multibyte-character line is still valid per the RFC
    (the unfolding step just concatenates raw bytes), so this doesn't
    need to be byte-exact to produce a file real calendar apps parse."""
    if len(line) <= _FOLD_WIDTH:
        return line
    parts = [line[:_FOLD_WIDTH]]
    rest = line[_FOLD_WIDTH:]
    while rest:
        parts.append(" " + rest[: _FOLD_WIDTH - 1])
        rest = rest[_FOLD_WIDTH - 1 :]
    return "\r\n".join(parts)


def _utc_stamp(dt):
    return timezone.localtime(dt, dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _event_summary(release):
    title_name = release.title.name
    episode = release.episode
    if episode is not None:
        suffix = f"S{episode.season:02d}E{episode.episode:02d}"
        if episode.name:
            suffix += f" - {episode.name}"
        return f"{title_name} - {suffix}"
    if release.release_type == release.ReleaseType.MOVIE_RELEASE:
        return f"{title_name} (Movie release)"
    return f"{title_name} - {release.get_release_type_display()}"


def _event_lines(release, base_url):
    lines = [
        "BEGIN:VEVENT",
        f"UID:spool-release-{release.pk}@spool",
        f"DTSTAMP:{_utc_stamp(timezone.now())}",
        f"SUMMARY:{_escape_text(_event_summary(release))}",
    ]
    if release.has_exact_time:
        start = release.release_date
        runtime = (release.episode.runtime_minutes if release.episode else None) or _DEFAULT_RUNTIME_MINUTES
        end = start + timezone.timedelta(minutes=runtime)
        lines.append(f"DTSTART:{_utc_stamp(start)}")
        lines.append(f"DTEND:{_utc_stamp(end)}")
    else:
        day = timezone.localtime(release.release_date).date()
        lines.append(f"DTSTART;VALUE=DATE:{day:%Y%m%d}")
        lines.append(f"DTEND;VALUE=DATE:{(day + timezone.timedelta(days=1)):%Y%m%d}")
    if base_url:
        lines.append(f"URL:{base_url}{reverse('title_detail', args=[release.title.pk])}")
    lines.append("END:VEVENT")
    return lines


def build_feed(profile, releases, base_url=""):
    """releases: an iterable of ReleaseSchedule rows (select_related
    title/episode already applied by the caller, same as
    selectors.calendar_releases's own callers do). base_url: scheme+host
    (e.g. request.build_absolute_uri('/').rstrip('/')) used to build each
    event's URL: back to that title's page - omitted entirely (no URL:
    line at all) when not given, since a relative link is meaningless in
    a calendar app that has no notion of "this app's own origin".
    Returns the full .ics file as a str, CRLF line endings throughout
    per RFC 5545."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Spool//Calendar Feed//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape_text(f'Spool - {profile.display_name}')}",
    ]
    for release in releases:
        lines.extend(_event_lines(release, base_url))
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
