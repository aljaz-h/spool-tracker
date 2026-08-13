from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def eq(value, other):
    """Django's `with` tag only accepts filter expressions, not `==`
    comparisons — this fills that gap for one-line nav-item includes."""
    return value == other


@register.filter
def animated_swap(base_swap, animations_enabled):
    """hx-swap="{{ "outerHTML"|animated_swap:active_profile.animations_enabled }}" -
    the one place the animation system's swap:/settle: timing is decided,
    so every opted-in hx-swap in the app goes through here instead of
    repeating the same {% if %} at each call site (see static/src/app.css's
    "Animation system" section for the actual transition rules these
    classes/timings drive - htmx only adds its .htmx-swapping/.htmx-settling
    classes when a swap has an explicit delay, which is exactly what this
    appends).

    Deliberately keyed on the profile flag alone, not prefers-reduced-motion -
    that's a client-only signal Django can't see, so it's left entirely to
    the CSS media query on the *visual* side. The tradeoff: someone with
    the profile toggle on and OS-level reduced motion on gets a still-
    instant swap with a brief invisible pause (the delay fires, the fade
    the CSS would have shown doesn't) rather than a truly instant one -
    judged an acceptable, narrow edge case rather than reaching for a
    client-side reduced-motion check per element, which is exactly the
    scattered-JS-toggle pattern this system is trying to avoid."""
    return f"{base_swap} swap:150ms settle:150ms" if animations_enabled else base_swap


@register.filter
def in_list(value, arg):
    """Same gap as eq (see its own docstring), for "is this one of
    several route names" - arg is a comma-separated string, e.g.
    url_name|in_list:"movies,tv,anime"."""
    return str(value) in arg.split(",")


@register.filter
def poster_size(url, width):
    """Re-points a stored TMDB poster URL at a smaller size than the w500
    tmdb.py always fetches/stores (IMAGE_BASE) - grid/carousel tiles
    render at ~110-190px CSS width (discover_tile.html's own grid-cols
    minmax), so downloading the full w500 wastes bandwidth for the
    smallest, highest-volume contexts (a browse grid can be dozens of
    tiles). TMDB's image path is just a fixed-width path segment
    (/t/p/{size}/...), so this is a URL string swap, not a second fetch
    or a stored-data change. No-ops (returns url unchanged) for anything
    that isn't a recognized w500 TMDB URL, so a None/blank/already-
    resized url never raises or breaks."""
    if not url or "/t/p/w500/" not in url:
        return url
    return url.replace("/t/p/w500/", f"/t/p/{width}/", 1)


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
    literal attribute/key names, not a template variable holding the key.
    Tolerates a missing/non-dict `d` (returns None) rather than raising -
    discover_tile.html is included from several views, and a context
    var that's merely absent (vs. an empty dict) shouldn't be a hard
    template error."""
    if not isinstance(d, dict):
        return None
    return d.get(key)


@register.filter
def tmdb_key(item):
    """discover_action_context()'s lookup key for a discover_tile.html
    item dict - "media_type:tmdb_id", matching how that selector builds
    discover_watched/discover_list_membership/discover_title_by_key."""
    return f"{item['media_type']}:{item['tmdb_id']}"


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


@register.filter
def format_money(amount):
    """$50,000,000 - the title detail Details panel's budget/revenue
    rows. amount is already None (not 0) for "unknown" by the time it
    reaches here (see tmdb.get_full_details), so this only ever runs on
    a real figure."""
    return f"${amount:,}"
