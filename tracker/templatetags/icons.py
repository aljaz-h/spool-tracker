from pathlib import Path

from django import template
from django.conf import settings
from django.utils.safestring import mark_safe

register = template.Library()

_ICON_DIR = Path(settings.BASE_DIR) / "static" / "icons"


@register.simple_tag
def icon(name, css_class="w-4 h-4"):
    svg = (_ICON_DIR / f"{name}.svg").read_text()
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
