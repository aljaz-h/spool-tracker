"""Shared building blocks for Settings panels (the SettingsCard /
SettingsRow pattern): one panel, a header, then rows with the label and help
text on the left and the control on the right. Styles live in app.css under
`.s-*`; this module only supplies the wrapper markup so each card doesn't
copy it."""

from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_block_tag
def settings_card(content, extra_class=""):
    """The panel: dark background, 1px border, 16px radius."""
    return format_html('<div class="s-card {}">{}</div>', extra_class, content)


@register.simple_block_tag
def settings_row(content, title, help=""):
    """One setting: title + one line of help on the left, the block's content
    (the control) on the right."""
    return format_html(
        '<div class="s-row"><div class="s-row-text"><div class="s-row-title">{}</div>{}</div>'
        '<div class="s-row-control">{}</div></div>',
        title,
        format_html('<p class="s-row-help">{}</p>', help) if help else "",
        content,
    )


@register.simple_tag
def direction_help(provider, connection):
    """The help line for a connection's current sync direction."""
    from tracker import sync_direction

    for value, _label, help_text in sync_direction.choices_for(provider):
        if value == connection.sync_direction:
            return help_text
    return ""


_SCHEDULE_PRESETS = [
    ("m30", "Every 30 minutes"),
    ("m60", "Every hour"),
    ("d1", "Every day"),
    ("d2", "Every 2 days"),
    ("d3", "Every 3 days"),
    ("d7", "Every week"),
]


@register.simple_tag
def schedule_options(connection):
    """(current value, [(value, label)]) for the schedule dropdown - the
    sub-daily presets only for a connection that supports them (Nuvio)."""
    every = getattr(connection, "sync_every_minutes", None)
    options = [o for o in _SCHEDULE_PRESETS if every is not None or not o[0].startswith("m")]
    if every:
        current = "m30" if every < 60 else "m60"
    else:
        current = f"d{connection.sync_interval_days}"
    if current not in {value for value, _ in options}:
        options.append((current, f"Every {connection.sync_interval_days} days"))
    return current, options


@register.simple_tag
def sync_is_stale(connection):
    """True when the last sync is more than a few intervals overdue - the
    status strip turns amber."""
    from datetime import timedelta

    from django.utils import timezone

    last = connection.last_synced_at
    if last is None:
        return False
    every = getattr(connection, "sync_every_minutes", 0)
    interval = timedelta(minutes=every) if every else timedelta(days=connection.sync_interval_days)
    return timezone.now() - last > max(interval * 3, timedelta(hours=1)) + timedelta(hours=1)
