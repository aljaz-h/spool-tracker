"""Builds a profile's Wrapped recap for a period: the stats, the card HTML,
the PNG, and the delivery (a bell/push notification and, when set, a Discord
post). One code path shared by the scheduled Celery tasks and the page's
"Create image" button."""

import calendar
import logging
from datetime import date, timedelta

import requests
from django.urls import reverse
from django.utils import timezone

from tracker import notifications
from tracker.models import Notification, WrappedReport

from . import artwork, render, trends
from .entries import history_entries, profile_tz
from .stats import compute_stats
from .year_stats import compute_all_time, compute_year_in_review

logger = logging.getLogger(__name__)

# What a profile can leave off the share image (Profile.wrapped_hidden_stats
# holds the keys that are off). "name" is the owner's name in the corner.
CARD_STATS = (
    ("first_last", "First and last play"),
    ("top_titles", "Top 3 shows and movies"),
    ("split", "Movies, TV and anime split"),
    ("name", "Show my name"),
)
CARD_KEYS = tuple(key for key, _label in CARD_STATS)


def month_range(year, month):
    since = date(year, month, 1)
    until = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return since, until


def year_range(year):
    return date(year, 1, 1), date(year + 1, 1, 1)


def period_label(kind, year, month=None):
    if kind == WrappedReport.Kind.MONTHLY and month:
        return f"{calendar.month_name[month]} {year}"
    return str(year)


def period_bounds(kind, year, month=None):
    if kind == WrappedReport.Kind.MONTHLY:
        return month_range(year, month)
    return year_range(year)


# Artwork is fetched here, on the server, and inlined into the card so the
# screenshot never waits on (or is blocked from) a browser loading images.
# Only the image hosts Spool itself stores URLs for.
_IMAGE_HOSTS = ("image.tmdb.org", "assets.fanart.tv")
_IMAGE_MAX_BYTES = 4 * 1024 * 1024
_image_cache = {}


def inline_image(url):
    """A data: URI for a poster/backdrop URL, or "" when it isn't one of the
    known image hosts or can't be fetched (the card falls back to its
    gradient)."""
    import base64
    from urllib.parse import urlparse

    if not url:
        return ""
    if url in _image_cache:
        return _image_cache[url]
    parts = urlparse(url)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in _IMAGE_HOSTS:
        return ""
    try:
        resp = requests.get(url, timeout=8, allow_redirects=False, stream=True)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        body = resp.raw.read(_IMAGE_MAX_BYTES + 1, decode_content=True)
        if not content_type.startswith("image/") or len(body) > _IMAGE_MAX_BYTES:
            return ""
    except Exception:
        return ""
    data = f"data:{content_type};base64,{base64.b64encode(body).decode()}"
    if len(_image_cache) > 64:
        _image_cache.clear()
    _image_cache[url] = data
    return data


def hidden_from_profile(profile):
    return {key for key in (profile.wrapped_hidden_stats or []) if key in CARD_KEYS}


def card_context(profile, kind, year, month=None, fmt=render.DEFAULT_FORMAT, hidden=None, with_images=True):
    """(context for the card template, number of watches in the period).
    `hidden` overrides the profile's saved choices of what to leave off."""
    since, until = period_bounds(kind, year, month)
    entries = history_entries(profile, since, until)
    stats = compute_stats(entries, profile_tz(profile).key)
    hidden = hidden_from_profile(profile) if hidden is None else set(hidden)
    monthly = kind == WrappedReport.Kind.MONTHLY
    today = date.today()
    partial = (year == today.year and (not monthly or month == today.month))
    word = calendar.month_name[month].upper() if monthly else str(year)
    hours = str(round(stats.total_watch_minutes / 60))
    if monthly:
        pills = [(hours, "hours"), (str(stats.total_titles), "titles"), (str(stats.longest_streak_days), "day streak")]
    else:
        pills = [(hours, "hours"), (str(stats.total_plays), "plays"), (str(stats.total_titles), "titles")]

    def banner(moment):
        if not moment or not with_images:
            return ""
        return inline_image(moment.backdrop_url) or inline_image(moment.poster_url)

    context = {
        "profile_display_name": profile.display_name,
        "period_label": period_label(kind, year, month),
        "period_type": "monthly" if monthly else "yearly",
        "period_title": word,
        "period_sub": f"{year} in review" if monthly else "Year in review",
        "period_word": word,
        "last_word": f"{word} so far" if partial and not monthly else word,
        "pills": pills,
        "stats": stats,
        "fmt": fmt if fmt in render.FORMATS else render.DEFAULT_FORMAT,
        "show_counts": not monthly,
        "first_image": banner(stats.first_play),
        "last_image": banner(stats.last_play),
        "hidden": sorted(hidden),
    }
    for key in CARD_KEYS:
        context[f"show_{key}"] = key not in hidden
    return context, len(entries)


def year_in_review_context(profile, year):
    since, until = year_range(year)
    entries = history_entries(profile, since, until)
    stats = compute_year_in_review(entries, year, profile_tz(profile).key)
    return {"profile_display_name": profile.display_name, "stats": stats}, len(entries)


def year_in_review_page_context(profile, year):
    """Everything the Year in Review page (and its image export) needs."""
    context, count = year_in_review_context(profile, year)
    stats = context["stats"]
    if count:
        artwork.decorate_review(stats)
    sections = [
        (key, section) for key, section in (("shows", stats.shows), ("movies", stats.movies)) if section.total_plays
    ]
    return {
        "profile": profile,
        "year": year,
        "year_str": str(year),
        "next_year": year + 1,
        "stats": stats,
        "sections": sections,
        "chart_data": {key: chart_payload(section) for key, section in sections},
        "cover_tiles": [
            ("Plays", f"{stats.total_plays:,}"), ("Hours", stats.total_hours_display), ("Titles", f"{stats.total_titles:,}"),
            ("Movies", f"{stats.movies.total_titles:,}"), ("Shows", f"{stats.shows.total_titles:,}"),
        ],
        "has_history": bool(count),
        "trends": trends.trends_for(profile, year) if count else {},
    }


def all_time_context(profile):
    """Everything the All Time page needs: the whole watch history, not one period."""
    entries = history_entries(profile, date(1900, 1, 1), date.today() + timedelta(days=1))
    stats = compute_all_time(entries, profile_tz(profile).key)
    if stats.has_data:
        artwork.decorate_all_time(stats)
    sections = [(key, s) for key, s in (("shows", stats.shows), ("movies", stats.movies)) if s.total_plays]
    chart = {key: {"genres": [{"name": g.name, "count": g.count} for g in s.genres]} for key, s in sections}
    chart["all"] = {"countries": [{"name": c.name, "count": c.count} for c in stats.countries]}
    return {
        "stats": stats,
        "sections": sections,
        "chart_data": chart,
        "since_line": f"Since {stats.first_year} · {stats.days_on_spool:,} days on Spool" if stats.has_data else "",
        "cover_tiles": [
            ("Movies", f"{stats.movie_titles:,}"), ("Shows", f"{stats.show_titles:,}"),
            ("Episodes", f"{stats.episodes:,}"), ("Time watched", stats.time_display),
        ],
    }


def chart_payload(section):
    """A section's numbers as plain data for the page's charts."""

    def points(items):
        return [{"label": p.label, "plays": p.plays, "minutes": p.minutes} for p in items]

    def named(items):
        return [{"name": n.name, "count": n.count} for n in items]

    return {
        "weekly": points(section.weekly_activity),
        "monthly": points(section.monthly_activity),
        "secondary": named(section.secondary),
        "genres": named(section.genres),
        "countries": named(section.countries),
    }


def parse_options(query):
    """(format, hidden keys or None) from a request's query/form:
    ?fmt=square&hide=name,split. An unknown format falls back to the default;
    no `hide` at all means "use the profile's saved choices" (None)."""
    fmt = query.get("fmt", "")
    hide = None
    if "hide" in query:  # absent = the profile's saved choices
        hide = {k for k in (query.get("hide", "") or "").split(",") if k in CARD_KEYS}
    return (fmt if fmt in render.FORMATS else render.DEFAULT_FORMAT), hide


def page_url(kind, year, month=None):
    if kind == WrappedReport.Kind.MONTHLY:
        return reverse("wrapped_month", args=[year, month])
    if kind == WrappedReport.Kind.YEAR_IN_REVIEW:
        return reverse("wrapped_review", args=[year])
    return reverse("wrapped_year", args=[year])


DISCORD_HOSTS = frozenset({"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"})


def is_discord_webhook(url):
    """True only for a real Discord webhook address (https, one of Discord's
    own hosts, /api/webhooks/...). The URL is typed in by the profile and the
    server posts to it, so anything else - an internal address, another
    host - must never be contacted."""
    from urllib.parse import urlparse

    try:
        parts = urlparse(url or "")
        host = (parts.hostname or "").rstrip(".").lower()
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and host in DISCORD_HOSTS
        and port in (None, 443)
        and not parts.username
        and parts.path.startswith("/api/webhooks/")
    )


def safe_error(exc):
    """What's logged and stored about a failed delivery: the HTTP status or
    the exception type, never the message - a requests error message
    contains the full webhook URL, and the token in it is a secret."""
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None):
        return f"HTTP {response.status_code}"
    return type(exc).__name__


def _post_to_discord(webhook_url, path, profile, label):
    """Uploads the PNG itself, so nothing needs a public URL for Spool."""
    if not is_discord_webhook(webhook_url):
        raise ValueError("not a Discord webhook address")
    payload = {
        "embeds": [
            {
                "title": f"{profile.display_name}'s Spool Wrapped",
                "description": label,
                "color": 0xE8A63C,
                "image": {"url": "attachment://wrapped.png"},
            }
        ]
    }
    import json

    with open(path, "rb") as f:
        resp = requests.post(
            webhook_url,
            data={"payload_json": json.dumps(payload)},
            files={"file": ("wrapped.png", f, "image/png")},
            timeout=20,
            allow_redirects=False,
        )
    resp.raise_for_status()


# Monthly recaps are kept for a year after they were made; yearly recaps and
# Year in Review reports are kept for good. A recap that found nothing, or
# failed, is only a note that it was tried, so it goes after 90 days.
MONTHLY_KEEP = timedelta(days=365)
NOTE_KEEP = timedelta(days=90)


def remove_reports(queryset):
    """Deletes reports and their image files. Returns how many rows went."""
    from pathlib import Path

    count = 0
    for report in list(queryset):
        if report.token:
            try:
                (render.generated_dir() / f"{report.token}.png").unlink(missing_ok=True)
            except OSError:
                logger.warning("Couldn't remove Wrapped image %s", Path(report.token).name)
        report.delete()
        count += 1
    return count


def prune_expired(now=None):
    """Applies the retention above. Run daily by tasks.generate_wrapped_recaps."""
    now = now or timezone.now()
    gone = remove_reports(
        WrappedReport.objects.filter(kind=WrappedReport.Kind.MONTHLY, created_at__lt=now - MONTHLY_KEEP)
    )
    gone += remove_reports(
        WrappedReport.objects.exclude(status=WrappedReport.Status.SUCCESS).filter(created_at__lt=now - NOTE_KEEP)
    )
    return gone


def saved_reports(profile):
    """The profile's kept recaps, newest period first, for the page's list."""
    reports = WrappedReport.objects.filter(profile=profile, status=WrappedReport.Status.SUCCESS).order_by(
        "-since", "-created_at"
    )
    return [
        {
            "report": r,
            "url": page_url(r.kind, r.since.year, r.since.month),
            "expires": r.created_at + MONTHLY_KEEP if r.kind == WrappedReport.Kind.MONTHLY else None,
        }
        for r in reports
    ]


def generate(profile, kind, year, month=None, deliver=True, fmt=render.DEFAULT_FORMAT, hidden=None):
    """Renders and stores one recap. Always returns a WrappedReport row
    (status "skipped_no_history" when nothing was watched, "failed" when
    rendering blew up) instead of raising, so a scheduled run over many
    profiles doesn't die on one bad one."""
    since, until = period_bounds(kind, year, month)
    label = period_label(kind, year, month)
    review = kind == WrappedReport.Kind.YEAR_IN_REVIEW
    template_name = "" if review else (fmt if fmt in render.FORMATS else render.DEFAULT_FORMAT)
    base = dict(profile=profile, kind=kind, period_label=label, since=since, until=until, template_name=template_name)

    if review:
        context, count = year_in_review_context(profile, year)
    else:
        context, count = card_context(profile, kind, year, month, fmt=template_name, hidden=hidden)
    if not count:
        return WrappedReport.objects.create(**base, status=WrappedReport.Status.SKIPPED)

    if review:
        # The Year in Review is a page first (it's always there to read at
        # its own URL); the record marks it as made so the daily job doesn't
        # repeat it, and the image is made the same way as the cards.
        try:
            from . import export

            token, _path = export.render_review_png(year_in_review_page_context(profile, year))
        except Exception as exc:
            logger.exception("Year in Review image failed for profile %s (%s)", profile.id, label)
            token = None
            error = str(exc)[:500]
        else:
            error = ""
        remove_reports(
            WrappedReport.objects.filter(profile=profile, kind=kind, since=since, status=WrappedReport.Status.SUCCESS)
        )
        report = WrappedReport.objects.create(
            **base, token=token, status=WrappedReport.Status.SUCCESS, error_message=error
        )
        if deliver:
            notifications.notify(
                profile,
                Notification.Kind.WRAPPED_READY,
                lookup={"link": page_url(kind, year, month)},
                message=f"Your {label} Year in Review is ready.",
            )
        return report

    try:
        token, path = render.render_png("card", context, fmt=template_name)
    except Exception as exc:
        logger.exception("Wrapped render failed for profile %s (%s)", profile.id, label)
        return WrappedReport.objects.create(**base, status=WrappedReport.Status.FAILED, error_message=str(exc)[:500])

    # One kept copy per period: a re-created image replaces the old one.
    remove_reports(
        WrappedReport.objects.filter(profile=profile, kind=kind, since=since, status=WrappedReport.Status.SUCCESS)
    )
    report = WrappedReport.objects.create(**base, token=token, status=WrappedReport.Status.SUCCESS)
    if deliver:
        _deliver(profile, report, path, kind, year, month)
    return report


def _deliver(profile, report, path, kind, year, month):
    errors = []
    if profile.wrapped_webhook_url:
        try:
            _post_to_discord(profile.wrapped_webhook_url, path, profile, report.period_label)
            report.delivered_discord = True
        except Exception as exc:
            reason = safe_error(exc)
            logger.warning("Wrapped Discord delivery failed for profile %s: %s", profile.id, reason)
            errors.append(f"Discord: {reason}")
    link = page_url(kind, year, month)
    notifications.notify(
        profile,
        Notification.Kind.WRAPPED_READY,
        lookup={"link": link},
        message=f"Your {report.period_label} Wrapped is ready.",
    )
    if errors:
        report.error_message = "; ".join(errors)[:500]
    report.save(update_fields=["delivered_discord", "error_message"])
