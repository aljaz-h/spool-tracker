"""The shared UI components (Components 1 and 2 boards): one tag per
component, the markup in tracker/templates/tracker/ui/, the styling in
app.css's "Spool UI kit" block. A page that needs a button, tab, chip,
checkbox, field, pager, header, meta line or badge takes it from here and
does not restyle its own. /ui-kit/ renders every one of them.

`attrs` on a tag is extra HTML attributes written by the template author
(hx-*, Alpine, data-*, aria-*): it is output as-is, so it must never carry
user input.
"""

import itertools

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from tracker.formatting import episode_code

register = template.Library()

MEDIA_LABELS = {"movie": "Movie", "tv": "TV", "anime": "Anime"}
MEDIA_DOTS = {"movie": "bg-movie", "tv": "bg-tv", "anime": "bg-anime"}
BUTTON_VARIANTS = {
    "primary": "btn-primary",
    "secondary": "btn-soft",
    "ghost": "btn-ghost",
    "danger": "btn-danger",
}
BADGE_TONES = {
    "": "",
    "neutral": "",
    "attention": "chip-attention",
    "success": "chip-success",
    "error": "chip-error",
    "outline": "chip-outline",
}


@register.filter
def epcode_text(episode):
    """An Episode (or anything with .season / .episode) as plain "S1E9" text,
    for attributes and sentences where the mono span doesn't belong."""
    if episode is None:
        return ""
    return episode_code(getattr(episode, "season", None), getattr(episode, "episode", None))


@register.simple_tag
def epcode(season, episode, episode_to=None, season_to=None, plain=False):
    """The one episode format, in mono: {% epcode 1 9 %} -> S1E9,
    {% epcode 1 1 5 %} -> S1E1–S1E5. plain=True gives the bare text."""
    code = episode_code(season, episode, episode_to, season_to)
    if plain or not code:
        return code
    return format_html('<span class="epcode">{}</span>', code)


@register.inclusion_tag("tracker/ui/button.html")
def ui_button(label="", variant="secondary", icon="", href="", type="button", compact=False, disabled=False, icon_only=False, attrs="", extra_class=""):
    """Primary (one per page), secondary, ghost or danger. 44px tall, 36px
    with compact=True (dense tables). icon_only=True is the 44px icon button:
    `label` is then its accessible name."""
    return {
        "label": label, "variant_class": BUTTON_VARIANTS.get(variant, "btn-soft"), "icon": icon, "href": href,
        "type": type, "compact": compact, "disabled": disabled, "icon_only": icon_only,
        "attrs": mark_safe(attrs), "extra_class": extra_class,
    }


@register.inclusion_tag("tracker/ui/tabs.html")
def ui_tabs(items, style="underline", label="", extra_class=""):
    """Three patterns, one job each: "underline" switches sections of a page,
    "segmented" switches the view or range of the same data, "chips" filters
    a list (single select). items: dicts with label, url, active and
    optionally count."""
    return {"items": items, "style": style, "label": label, "extra_class": extra_class}


@register.inclusion_tag("tracker/ui/chip.html")
def ui_chip(label, selected=False, removable=False, dot="", count=None, href="", check=False, attrs="", remove_label=""):
    """A filter chip. check=True is the multi-select kind (a check when on),
    removable=True the applied-filter kind (an X), dot="#rrggbb" or a media
    type ("movie"/"tv"/"anime") the person or type kind."""
    dot_class = MEDIA_DOTS.get(dot, "")
    return {
        "label": label, "selected": selected, "removable": removable, "count": count, "href": href, "check": check,
        "dot_class": dot_class, "dot_color": "" if dot_class else dot, "attrs": mark_safe(attrs),
        "remove_label": remove_label or f"Remove {label}",
    }


@register.inclusion_tag("tracker/ui/check.html")
def ui_checkbox(label, name="", value="", checked=False, indeterminate=False, attrs="", hide_label=False):
    return {"kind": "checkbox", "label": label, "name": name, "value": value, "checked": checked,
            "indeterminate": indeterminate, "attrs": mark_safe(attrs), "hide_label": hide_label}


@register.inclusion_tag("tracker/ui/check.html")
def ui_radio(label, name="", value="", checked=False, attrs="", hide_label=False):
    return {"kind": "radio", "label": label, "name": name, "value": value, "checked": checked,
            "attrs": mark_safe(attrs), "hide_label": hide_label}


@register.inclusion_tag("tracker/ui/toggle.html")
def ui_toggle(label, name="", value="", checked=False, attrs="", hide_label=False, extra_class=""):
    """The on/off switch: amber when on, a 44px tap area."""
    return {"label": label, "name": name, "value": value, "checked": checked, "attrs": mark_safe(attrs),
            "hide_label": hide_label, "extra_class": extra_class}


@register.inclusion_tag("tracker/ui/mark_watched.html")
def ui_mark_watched(watched=False, label="", attrs=""):
    """Green when watched: it is a result, not a setting."""
    return {"watched": watched, "label": label or ("Watched" if watched else "Mark watched"), "attrs": mark_safe(attrs)}


@register.inclusion_tag("tracker/ui/search.html")
def ui_search(name="q", placeholder="Search", value="", shortcut="", label="", attrs=""):
    """The search field. shortcut="/" shows the key and focuses the field
    when it is pressed outside another input."""
    return {"name": name, "placeholder": placeholder, "value": value, "shortcut": shortcut,
            "label": label or placeholder, "attrs": mark_safe(attrs)}


@register.inclusion_tag("tracker/ui/field.html")
def ui_field(label, name, value="", type="text", help="", error="", placeholder="", id="", attrs=""):
    """A text field: label above, one line of help or the error below."""
    return {"label": label, "name": name, "value": value, "type": type, "help": help, "error": error,
            "placeholder": placeholder, "id": id or f"f-{name}", "attrs": mark_safe(attrs)}


@register.inclusion_tag("tracker/ui/select.html")
def ui_select(label, name, options, value="", id="", attrs="", hide_label=False):
    """A select. options: (value, label) pairs."""
    return {"label": label, "name": name, "options": options, "value": str(value), "id": id or f"f-{name}",
            "attrs": mark_safe(attrs), "hide_label": hide_label}


@register.inclusion_tag("tracker/ui/filters_button.html")
def ui_filters_button(count=0, attrs="", label="Filters"):
    """The toolbar's Filters button: a count when filters are on, never a dot."""
    return {"count": count, "attrs": mark_safe(attrs), "label": label}


@register.inclusion_tag("tracker/ui/pagination.html")
def ui_pagination(page, total, base_query="", note="", attrs_template="", target=""):
    """"Page 1 of N", number buttons and "Next →". base_query is the rest of
    the query string (no leading ?). target is an optional hx-target: the
    links then also carry hx-get and push the URL."""
    from tracker.templatetags.tracker_extras import page_window

    page, total = int(page or 1), int(total or 1)
    prefix = f"?{base_query}&page=" if base_query else "?page="
    return {
        "page": page, "total": total, "note": note, "target": target,
        "window": [None if p is None else {"n": p, "url": f"{prefix}{p}", "current": p == page} for p in page_window(page, total)],
        "prev_url": f"{prefix}{page - 1}" if page > 1 else "",
        "next_url": f"{prefix}{page + 1}" if page < total else "",
    }


@register.simple_block_tag
def page_head(content, title, subtitle=""):
    """The page header: title, one line, and the block's content (at most one
    primary button) on the right. The tab row goes under it."""
    return format_html(
        '<div class="page-head"><div class="min-w-0"><h1 class="page-title">{}</h1>{}</div>{}</div>',
        title,
        format_html('<p class="page-subtitle">{}</p>', subtitle) if subtitle else "",
        format_html('<div class="flex flex-wrap items-center gap-2">{}</div>', content) if content.strip() else "",
    )


@register.inclusion_tag("tracker/ui/section_head.html")
def ui_section_head(title, count=None, url="", link_label="See all", id=""):
    """A section header: 18px title, optional count, one link on the right
    with an arrow."""
    return {"title": title, "count": count, "url": url, "link_label": link_label, "id": id}


@register.inclusion_tag("tracker/ui/meta.html")
def ui_meta(kind="", year="", rating=None, extra="", small=False):
    """The one meta line: ● Movie · 2026 · ★ 7.8. kind is movie/tv/anime, or
    "" to leave the type out (a page that is already one type)."""
    return {"kind": kind, "kind_label": MEDIA_LABELS.get(kind, ""), "dot_class": MEDIA_DOTS.get(kind, ""),
            "year": year, "rating": rating, "extra": extra, "small": small}


@register.simple_tag
def ui_badge(label, tone="", dot=False):
    """Neutral by default; a status colour only for status
    (attention / success / error), "outline" for the Next marker."""
    return format_html(
        '<span class="chip {}">{}{}</span>',
        BADGE_TONES.get(tone, ""),
        mark_safe('<span class="chip-dot" aria-hidden="true"></span>') if dot else "",
        label,
    )


@register.inclusion_tag("tracker/ui/stat_tile.html")
def ui_stat_tile(value, label, unit="", note="", percent=None):
    """A figure: white and mono, never amber."""
    return {"value": value, "label": label, "unit": unit, "note": note, "percent": percent}


@register.simple_block_tag
def panel(content, title="", extra_class=""):
    """A bordered box, for sidebars and widgets only."""
    return format_html(
        '<div class="panel {}">{}{}</div>',
        extra_class,
        format_html('<h3 class="panel-title">{}</h3>', title) if title else "",
        content,
    )


@register.inclusion_tag("tracker/ui/empty_state.html")
def ui_empty_state(title, text="", icon="", action_label="", action_url="", action_attrs=""):
    """What a page shows when there is nothing: what is missing and the
    action that fixes it. Never a loading block left on screen."""
    return {"title": title, "text": text, "icon": icon, "action_label": action_label, "action_url": action_url,
            "action_attrs": mark_safe(action_attrs)}


@register.inclusion_tag("tracker/ui/banner.html")
def ui_banner(text, lead="Needs attention.", action_label="", action_url="", action_attrs=""):
    return {"text": text, "lead": lead, "action_label": action_label, "action_url": action_url,
            "action_attrs": mark_safe(action_attrs)}


@register.inclusion_tag("tracker/ui/toast.html")
def ui_toast(text, tone="success"):
    return {"text": text, "tone": tone}


@register.inclusion_tag("tracker/ui/skeleton.html")
def ui_skeleton(lines=2, thumb=True, label="Loading"):
    """A loading block. It is replaced by the content or by an empty state."""
    return {"lines": range(int(lines)), "thumb": thumb, "label": label}


_UID = itertools.count(1)


@register.simple_tag
def uid():
    """A token that is unique within this process, for DOM ids that would
    otherwise repeat when the same title shows up twice on one page (two
    rails on the dashboard): "watched-btn-12-7". A page-wide-unique id keeps
    the markup valid; the pieces that need to find their own root use
    `closest` instead of the id."""
    return next(_UID)
