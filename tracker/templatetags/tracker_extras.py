from django import template

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
