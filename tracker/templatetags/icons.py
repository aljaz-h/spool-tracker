import itertools
import re
from pathlib import Path

from django import template
from django.conf import settings
from django.utils.safestring import mark_safe

register = template.Library()

_ICON_DIR = Path(settings.BASE_DIR) / "static" / "icons"
_ID_COUNTER = itertools.count()
_ID_RE = re.compile(r'\bid="([^"]+)"')


def _uniquify_ids(svg):
    # A handful of vendored marks (e.g. google-drive.svg) carry their own
    # <mask>/<linearGradient> ids referenced via url(#id) - fine the one
    # time they're inlined, but {% icon %} inlines the raw SVG verbatim on
    # every call, and this same icon legitimately appears more than once
    # on one page at once (e.g. Server Integrations' badge and Import &
    # Export -> Automatic Backups' badge, both always in the DOM since
    # Alpine tabs hide with CSS rather than removing markup). Two elements
    # sharing one id is invalid HTML/SVG, and the browser resolves every
    # url(#id) reference to whichever element got that id first - so the
    # second inlined copy's mask/gradient points at the first copy's,
    # which usually renders it blank. Suffixing every id (and the
    # references to it) with a counter unique to this call sidesteps that
    # - a no-op for the common case (most icons have no ids at all).
    ids = set(_ID_RE.findall(svg))
    if not ids:
        return svg
    suffix = f"-{next(_ID_COUNTER)}"
    for raw_id in ids:
        svg = svg.replace(f'id="{raw_id}"', f'id="{raw_id}{suffix}"')
        svg = svg.replace(f"#{raw_id})", f"#{raw_id}{suffix})")
    return svg


@register.simple_tag
def icon(name, css_class="w-4 h-4"):
    svg = (_ICON_DIR / f"{name}.svg").read_text()
    svg = _uniquify_ids(svg)
    # Vendored Lucide source is multi-line ("<svg\n  xmlns=...>"), not the
    # single-line "<svg ...>" the stack addendum's snippet assumed — match
    # bare "<svg" so the class lands right regardless of what follows.
    # aria-hidden/focusable="false" once here instead of at each of the
    # ~100 call sites - every one of these is decorative (the accessible
    # name, where one's needed, always comes from the enclosing button's
    # own title/aria-label, never from the icon itself), so screen
    # readers should skip straight past it instead of announcing the raw
    # SVG or double-announcing alongside adjacent visible text.
    svg = svg.replace("<svg", f'<svg class="{css_class}" aria-hidden="true" focusable="false"', 1)
    # Bump from Lucide's default stroke-width="2" to a slightly heavier
    # 2.5 across every vendored icon, to match the bold font-display type
    # (visual-identity brief, phase 2) - centralized here instead of
    # repeating a stroke-width override at each of the ~9 call sites.
    return mark_safe(svg.replace('stroke-width="2"', 'stroke-width="2.5"', 1))
