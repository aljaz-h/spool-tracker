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

from . import render
from .entries import history_entries, profile_tz
from .stats import compute_stats
from .year_stats import compute_year_in_review

logger = logging.getLogger(__name__)

# The stats a profile can leave off the card (Profile.wrapped_hidden_stats).
CARD_STATS = (
    ("top_genre", "Top genre"),
    ("total_watch_time", "Total watch time"),
    ("longest_streak", "Longest streak"),
    ("top_rated_title", "Top-rated title"),
    ("type_split", "Movies / TV / anime split"),
    ("total_titles", "Total titles"),
)


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


def card_context(profile, kind, year, month=None):
    """(context for the card template, number of watches in the period)."""
    since, until = period_bounds(kind, year, month)
    entries = history_entries(profile, since, until)
    stats = compute_stats(entries, profile_tz(profile).key)
    hidden = set(profile.wrapped_hidden_stats or [])
    context = {
        "profile_display_name": profile.display_name,
        "period_label": period_label(kind, year, month),
        "period_type": "monthly" if kind == WrappedReport.Kind.MONTHLY else "yearly",
        "stats": stats,
    }
    for key, _label in CARD_STATS:
        context[f"show_{key}"] = key not in hidden
    return context, len(entries)


def year_in_review_context(profile, year):
    since, until = year_range(year)
    entries = history_entries(profile, since, until)
    stats = compute_year_in_review(entries, year, profile_tz(profile).key)
    return {"profile_display_name": profile.display_name, "stats": stats}, len(entries)


def template_for(profile):
    return profile.wrapped_template if profile.wrapped_template in render.AVAILABLE_TEMPLATES else "spool"


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


def generate(profile, kind, year, month=None, deliver=True):
    """Renders and stores one recap. Always returns a WrappedReport row
    (status "skipped_no_history" when nothing was watched, "failed" when
    rendering blew up) instead of raising, so a scheduled run over many
    profiles doesn't die on one bad one."""
    since, until = period_bounds(kind, year, month)
    label = period_label(kind, year, month)
    template_name = "year_in_review" if kind == WrappedReport.Kind.YEAR_IN_REVIEW else template_for(profile)
    base = dict(profile=profile, kind=kind, period_label=label, since=since, until=until, template_name=template_name)

    if kind == WrappedReport.Kind.YEAR_IN_REVIEW:
        context, count = year_in_review_context(profile, year)
    else:
        context, count = card_context(profile, kind, year, month)
    if not count:
        return WrappedReport.objects.create(**base, status=WrappedReport.Status.SKIPPED)

    try:
        token, path = render.render_png(
            template_name, context, full_page=kind == WrappedReport.Kind.YEAR_IN_REVIEW
        )
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
