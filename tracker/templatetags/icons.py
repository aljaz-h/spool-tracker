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
    return mark_safe(svg.replace("<svg", f'<svg class="{css_class}"', 1))
