from django import template

register = template.Library()


@register.filter
def eq(value, other):
    """Django's `with` tag only accepts filter expressions, not `==`
    comparisons — this fills that gap for one-line nav-item includes."""
    return value == other
