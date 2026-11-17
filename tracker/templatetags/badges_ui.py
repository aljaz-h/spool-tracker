"""Template pieces for badges and levels: the level circle on an avatar, the
badge icon (a shape per category), and the unlock toasts / level-up popup."""

import math

from django import template
from django.utils.safestring import mark_safe

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


def _points(pairs):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in pairs)


def medal_svg(level, size="sm"):
    """One flat inline SVG medal for a level, built from circles, rounded rects
    and polygons. size "lg" shows 1-3 film frames for the step; any smaller size
    shows the level number with three pips under it (filled = step). Colour is
    currentColor, set per stage by .medal-stage-N in app.css."""
    level = max(1, min(level, len(badges.LEVELS)))
    stage, step = badges.medal_stage(level)
    legend = stage == 3
    cx, cy = 60, 56
    out = []
    if legend:  # ribbon tail, behind the medal
        out.append(f'<polygon points="{_points([(49, 82), (71, 82), (71, 109), (60, 102), (49, 109)])}" fill="currentColor" opacity=".7"/>')
    if stage >= 1:  # film-strip wings: two blades each side (three from white up)
        blades = 2 if stage == 1 else 3
        for k in range(blades):
            length = 22 - k * 3 + (6 if stage >= 2 else 0)
            y = 50 + k * 7
            left = [(31, y), (31 - length, y - 10), (31 - length, y - 5), (31, y + 5)]
            right = [(120 - x, yy) for x, yy in left]
            paint = 'fill="currentColor"' if legend else 'fill="none"'
            for pts in (left, right):
                out.append(f'<polygon points="{_points(pts)}" {paint} stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>')
    if stage == 2:  # short light rays above
        for deg in (-125, -107, -90, -73, -55):
            a = math.radians(deg)
            r2 = 45 if deg == -90 else 42
            out.append(
                f'<line x1="{cx + 37 * math.cos(a):.1f}" y1="{cy + 37 * math.sin(a):.1f}" '
                f'x2="{cx + r2 * math.cos(a):.1f}" y2="{cy + r2 * math.sin(a):.1f}" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>'
            )
    if legend:  # the filled medal, dark star, reel crown
        out.append(f'<circle cx="{cx}" cy="{cy}" r="31" fill="currentColor"/>')
        out.append(f'<circle cx="{cx}" cy="{cy}" r="25" fill="none" stroke="var(--color-base-100)" stroke-width="1.5" opacity=".6"/>')
        star = []
        for i in range(10):
            r = 15 if i % 2 == 0 else 6.5
            a = math.radians(-90 + i * 36)
            star.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        out.append(f'<polygon points="{_points(star)}" fill="var(--color-base-100)"/>')
        out.append('<rect x="58" y="21" width="4" height="6" fill="currentColor"/>')
        out.append('<circle cx="60" cy="16" r="7.5" fill="currentColor"/>')
        for dx, dy in ((-3, -2), (3, -2), (0, 2.5)):
            out.append(f'<circle cx="{60 + dx}" cy="{16 + dy}" r="1.4" fill="var(--color-base-100)"/>')
    else:
        out.append(f'<circle cx="{cx}" cy="{cy}" r="31" fill="none" stroke="currentColor" stroke-width="3.5"/>')
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{23.5 if stage else 25}" fill="none" stroke="currentColor" stroke-width="1.5" opacity=".6"/>')
        if stage >= 1:  # sprocket holes in the band
            for i in range(12):
                a = math.radians(i * 30)
                out.append(f'<circle cx="{cx + 27 * math.cos(a):.1f}" cy="{cy + 27 * math.sin(a):.1f}" r="1.6" fill="currentColor"/>')
        if size == "lg":
            width = step * 9 + (step - 1) * 4
            x0 = cx - width / 2
            for i in range(step):
                out.append(f'<rect x="{x0 + i * 13:.1f}" y="49" width="9" height="14" rx="2.2" fill="currentColor"/>')
        else:
            out.append(
                f'<text x="{cx}" y="{cy + 1}" text-anchor="middle" dominant-baseline="central" '
                f'font-size="40" font-weight="700" fill="currentColor">{level}</text>'
            )
            for i in range(3):
                opacity = "1" if i < step else ".3"
                out.append(f'<rect x="{38 + i * 16}" y="96" width="12" height="5" rx="2" fill="currentColor" opacity="{opacity}"/>')
    name = badges.level_name(level)
    return mark_safe(
        f'<svg class="medal medal-{size} medal-stage-{stage}" viewBox="0 0 120 112" role="img" '
        f'aria-label="Level {level}, {name}" focusable="false">{"".join(out)}</svg>'
    )


@register.simple_tag
def level_medal(level, size="sm"):
    return medal_svg(level, size)


@register.inclusion_tag("tracker/badges/level_dot.html")
def level_dot(profile, own=False, size=""):
    """The small blue circle with the level number that sits on an avatar.
    Nothing is drawn for someone who turned badges off (own=True overrides
    that for the signed-in profile's own avatar)."""
    if profile is None or (not own and not getattr(profile, "show_badges", True)):
        return {"level": ""}
    info = badges.level_info(profile.badge_xp)
    return {"level": info.level, "name": info.name, "size": size, "svg": medal_svg(info.level, "sm")}


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
