"""Template pieces for badges and levels: the level circle on an avatar, the
badge icon (a shape per category), and the unlock toasts / level-up popup."""

from django import template

from tracker import badges

register = template.Library()

SHAPES = {c.key: c.shape.lower() for c in badges.CATEGORIES}


@register.filter
def badge_level(profile):
    """A profile's level number, or "" when it keeps its badges to itself.
    (The signed-in profile's own circle is drawn with {% level_dot self=True %}.)"""
    if profile is None or not getattr(profile, "show_badges", True):
        return ""
    return badges.level_info(profile.badge_xp).level


@register.filter
def badge_level_name(profile):
    if profile is None or not getattr(profile, "show_badges", True):
        return ""
    return badges.level_info(profile.badge_xp).name


@register.inclusion_tag("tracker/badges/level_dot.html")
def level_dot(profile, own=False, size=""):
    """The small blue circle with the level number that sits on an avatar.
    Nothing is drawn for someone who turned badges off (own=True overrides
    that for the signed-in profile's own avatar)."""
    if profile is None or (not own and not getattr(profile, "show_badges", True)):
        return {"level": ""}
    info = badges.level_info(profile.badge_xp)
    return {"level": info.level, "name": info.name, "size": size}


@register.inclusion_tag("tracker/badges/icon.html")
def badge_icon(card, size="md"):
    """One badge, drawn in its category's shape, coloured by its state."""
    return {"card": card, "shape": SHAPES.get(card["category"], "diamond"), "size": size}


@register.inclusion_tag("tracker/badges/pips.html")
def badge_pips(card):
    return {"pips": card["pips"]}


@register.inclusion_tag("tracker/badges/unlocks.html", takes_context=True)
def badge_unlocks(context):
    """Tier toasts and the level-up popup for what was unlocked since the last
    full page: shown once, then marked shown. Skipped on partial responses
    (the next full page shows them)."""
    request = context.get("request")
    profile = context.get("active_profile")
    if request is None or profile is None or request.headers.get("HX-Request"):
        return {"tiers": [], "levels": []}
    tiers, levels = badges.pending_unlocks(profile)
    items = []
    for row in tiers:
        badge = badges.BADGE_BY_KEY.get(row.badge_key)
        if badge is None:
            continue
        card = badges.badge_card(badge, {badge.key: 0}, {row.tier: row}, {}, 1)
        card["state"] = "earned"
        card["category"] = badge.category
        items.append({"badge": badge, "tier": row.tier, "xp": row.xp, "card": card})
    ups = [
        {"level": lv.level, "name": badges.level_name(lv.level), "xp": lv.xp_total, "pins": badges.pin_limit(lv.level)}
        for lv in levels
    ]
    return {"tiers": items, "levels": ups[-1:]}
