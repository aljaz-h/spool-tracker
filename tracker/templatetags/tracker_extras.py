from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def eq(value, other):
    """Django's `with` tag only accepts filter expressions, not `==`
    comparisons — this fills that gap for one-line nav-item includes."""
    return value == other


# The mockup's 8-gradient poster palette (p1..p8), used as a graceful
# fallback for titles with no poster_url yet — spool-django-handoff.md §4:
# "keep the CSS as a graceful fallback for titles missing artwork."
_GRADIENTS = [
    "from-[#3a2a1c] to-[#100d0b]",
    "from-[#1c2b3a] to-[#0a0d12]",
    "from-[#2a1c34] to-[#0d0a12]",
    "from-[#1c3a2e] to-[#0a120d]",
    "from-[#3a1c24] to-[#120a0c]",
    "from-[#3a3319] to-[#12100a]",
    "from-[#20263a] to-[#0a0c12]",
    "from-[#33241c] to-[#100b0a]",
]


@register.filter
def gradient_class(pk):
    index = int(pk) % len(_GRADIENTS) if pk is not None else 0
    return "bg-linear-to-br " + _GRADIENTS[index]


# Cycled by position (forloop.counter0), not keyed by genre name — the
# mockup's fixed 14-genre palette assumed a known genre list; ours is
# whatever genres actually appear in the profile's watch history.
_GENRE_COLORS = [
    "#e8a63c", "#3fa9a0", "#8b85d6", "#c0473a", "#5b8fd6", "#d67ab1", "#7fae5b",
    "#d6c14c", "#a67ac9", "#e08a4c", "#4ca6c9", "#9a9fb0", "#c9574c", "#5bc9a0",
]


@register.filter
def color_at_index(index):
    return _GENRE_COLORS[int(index) % len(_GENRE_COLORS)]


@register.filter
def get_item(d, key):
    """Dict lookup by a variable key — Django's `.` lookup only accepts
    literal attribute/key names, not a template variable holding the key."""
    return d.get(key)


@register.filter
def day_header(d):
    today = timezone.localdate()
    if d == today:
        return "Today"
    if d == today - timezone.timedelta(days=1):
        return "Yesterday"
    # Not %-d (platform-specific, breaks on Windows) — build the "no
    # leading zero" day number by hand instead.
    return f"{d.strftime('%A, %b')} {d.day}, {d.year}"
