"""Badges page, badge detail popup and pinning (the engine is tracker/badges.py)."""

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_POST

from . import badges
from .models import Profile

FILTERS = (
    ("all", "All"),
    ("earned", "Earned"),
    ("progress", "In progress"),
    ("complete", "Completed"),
    ("locked", "Not earned"),
)
SORTS = (("progress", "Progress"), ("rarity", "Rarity"), ("name", "Name"))


def _me(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    return profile


def _target(request, me):
    """The profile whose badges are shown: ?profile=, defaulting to the signed-in one."""
    raw = request.GET.get("profile") or request.POST.get("profile")
    if raw and raw.isdigit() and int(raw) != me.pk:
        other = Profile.objects.filter(pk=int(raw)).first()
        if other is None:
            raise Http404
        return other
    return me


def _matches(card, flt):
    done = card["tiers_done"]
    if flt == "earned":
        return done > 0
    if flt == "progress":
        return not card["complete"] and card["count"] > 0
    if flt == "complete":
        return card["complete"]
    if flt == "locked":
        return done == 0
    return True


def _sort_key(sort):
    if sort == "name":
        return lambda c: c["name"].lower()
    if sort == "rarity":
        # The rarest tier held first; badges with nothing earned last.
        return lambda c: (c["rarest_holders"] is None, c["rarest_holders"] or 0, c["name"].lower())
    # Furthest along first: tiers done, then progress toward the next one.
    return lambda c: (-c["tiers_done"], -c["percent"], c["name"].lower())


@login_required
def badges_page(request):
    me = _me(request)
    target = _target(request, me)
    flt = request.GET.get("filter", "all")
    sort = request.GET.get("sort", "progress")
    if flt not in dict(FILTERS):
        flt = "all"
    if sort not in dict(SORTS):
        sort = "progress"
    hidden = target.pk != me.pk and not target.show_badges
    data = None if hidden else badges.profile_cards(target)
    if data:
        key = _sort_key(sort)
        for group in data["categories"]:
            group["visible"] = sorted((c for c in group["cards"] if _matches(c, flt)), key=key)
        data["categories"] = [g for g in data["categories"] if g["visible"]]
    query_base = f"profile={target.pk}&" if target.pk != me.pk else ""

    def link(**changes):
        params = {"filter": flt, "sort": sort, **changes}
        return f"?{query_base}filter={params['filter']}&sort={params['sort']}"

    return render(
        request,
        "tracker/badges.html",
        {
            "target": target,
            "is_self": target.pk == me.pk,
            "hidden": hidden,
            "hidden_title": f"{target.display_name} keeps their badges private.",
            "data": data,
            "filters": [{"label": label, "url": link(filter=key), "active": key == flt} for key, label in FILTERS],
            "sorts": [{"label": label, "url": link(sort=key), "active": key == sort} for key, label in SORTS],
            "filter": flt,
            "household": Profile.objects.order_by("display_name"),
        },
    )


def _detail_response(request, me, target, key):
    detail = badges.badge_detail(target, key)
    if detail is None:
        raise Http404
    is_self = target.pk == me.pk
    pinned = key in (me.pinned_badges or [])
    limit = badges.pin_limit(badges.level_info(me.badge_xp).level)
    used = len([k for k in (me.pinned_badges or []) if k in badges.BADGE_BY_KEY])
    return render(
        request,
        "tracker/partials/badge_detail.html",
        {
            "d": detail,
            "target": target,
            "is_self": is_self,
            "can_pin": is_self and detail["card"]["tiers_done"] > 0,
            "pinned": pinned,
            "pins_free": max(0, limit - used),
            "pin_limit": limit,
        },
    )


@login_required
def badge_detail(request, key):
    me = _me(request)
    target = _target(request, me)
    if target.pk != me.pk and not target.show_badges:
        raise Http404
    return _detail_response(request, me, target, key)


@login_required
@require_POST
def badge_pin(request, key):
    me = _me(request)
    badges.pin_badge(me, key, request.POST.get("pinned") == "1")
    return _detail_response(request, me, me, key)
