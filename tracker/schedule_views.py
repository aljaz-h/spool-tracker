"""Calendar -> My schedule: the pane, its settings form, the Tonight card
and the slot buttons. The logic lives in tracker/planner.py; these views
only parse requests and hand it the profile."""

import datetime
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.timesince import timesince
from django.views.decorators.http import require_POST

from . import planner, selectors
from .models import ApiToken, ExternalAccount, NuvioConnection, Profile, ScheduledSlot, SchedulePlan

DAY_CHOICES = {"1": 1, "3": 3, "7": 7}


def _profile(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    return profile


def _time_format_str(profile):
    return "H:i" if profile.time_format == Profile.TimeFormat.H24 else "g:i A"


def _sync_sources(profile):
    """The "Watch sync" card: where watches arrive from and how recently."""
    rows = []
    now = timezone.now()
    for account in ExternalAccount.objects.filter(profile=profile, provider__in=planner.SYNC_PROVIDERS):
        rows.append(
            {
                "name": account.get_provider_display(),
                "detail": f"synced {timesince(account.last_synced_at, now)} ago" if account.last_synced_at else "not synced yet",
                "live": account.last_synced_at is not None,
            }
        )
    nuvio = NuvioConnection.objects.filter(profile=profile, sync_enabled=True).first()
    if nuvio:
        rows.append(
            {
                "name": "Nuvio",
                "detail": f"{timesince(nuvio.last_synced_at, now)} ago" if nuvio.last_synced_at else "not synced yet",
                "live": nuvio.last_synced_at is not None,
            }
        )
    if ApiToken.objects.filter(profile=profile).exists():
        rows.append({"name": "Scrobble API", "detail": "live", "live": True})
    return rows


def _settings_context(profile, plan):
    day_windows = plan.day_windows or {}
    return {
        "plan": plan,
        "mode_choices": SchedulePlan.Mode.choices,
        "list_choices": planner.list_choices(profile, plan),
        "media_choices": SchedulePlan.Media.choices,
        "order_choices": SchedulePlan.Order.choices,
        "weekday_rows": [
            {
                "index": index,
                "name": name,
                "free": index in (plan.free_days or []),
                "start": (day_windows.get(str(index)) or ["", ""])[0],
                "end": (day_windows.get(str(index)) or ["", ""])[1],
            }
            for index, name in enumerate(planner.WEEKDAYS)
        ],
    }


def _parse_start(raw, days, today):
    try:
        start = date.fromisoformat(raw)
    except (TypeError, ValueError):
        start = None
    if start is None:
        return today - timedelta(days=today.weekday()) if days == 7 else today
    return start


def _add_form_context(profile, plan, tz, now):
    """The manual plan's "Add to schedule" card."""
    titles = [
        {"id": item.title_id, "name": item.title.name, "kind": item.title.media_type}
        for item in planner.candidates(profile, plan, now)
    ]
    return {
        "titles": titles,
        "date": now.astimezone(tz).date(),
        "time": plan.window_start.strftime("%H:%M"),
    }


def _pane_context(request, profile, error=""):
    days = DAY_CHOICES.get(request.GET.get("days") or request.POST.get("days") or "7", 7)
    now = timezone.now()
    tz = selectors.profile_zoneinfo(profile)
    today = now.astimezone(tz).date()
    plan = planner.ensure_plan(profile, now)
    start = _parse_start(request.GET.get("start") or request.POST.get("start"), days, today)
    step = timedelta(days=7 if days == 7 else 1)
    home = today - timedelta(days=today.weekday()) if days == 7 else today
    context = {
        "profile": profile,
        "settings": _settings_context(profile, plan),
        "plan": plan,
        "days": days,
        "day_options": [("7", "Week"), ("3", "3 days"), ("1", "Day")],
        "start": start,
        "prev_start": start - step,
        "next_start": start + step,
        "home_start": home,
        "is_home": start == home,
        "time_format_str": _time_format_str(profile),
        "sync_sources": _sync_sources(profile),
    }
    if plan.enabled:
        context["week"] = planner.week_context(profile, plan, start, days, now)
        context["tonight"] = planner.tonight_context(profile, plan, now)
        if plan.mode == SchedulePlan.Mode.MANUAL:
            context["add_form"] = _add_form_context(profile, plan, tz, now)
            context["add_error"] = error
    return context


@login_required
def schedule_pane(request):
    profile = _profile(request)
    return render(request, "tracker/partials/schedule_pane.html", _pane_context(request, profile))


def _parse_settings(plan, post, profile):
    """Writes the posted settings onto plan; unknown or malformed values
    keep what was there."""
    visible = {str(wl.id): wl for wl, _selected, _count in planner.list_choices(profile, plan)}
    chosen = [visible[i] for i in post.getlist("lists") if i in visible]
    plan.source_lists.set(chosen)
    if post.get("mode") in SchedulePlan.Mode.values:
        plan.mode = post["mode"]
    if post.get("media") in SchedulePlan.Media.values:
        plan.media = post["media"]
    if post.get("order") in SchedulePlan.Order.values:
        plan.order = post["order"]
    plan.free_days = sorted({int(d) for d in post.getlist("free_days") if d in {str(i) for i in range(7)}})
    start, end = planner.parse_time(post.get("window_start")), planner.parse_time(post.get("window_end"))
    if start and end and end > start:
        plan.window_start, plan.window_end = start, end
    overrides = {}
    for index in range(7):
        a, b = planner.parse_time(post.get(f"day_start_{index}")), planner.parse_time(post.get(f"day_end_{index}"))
        if a and b and b > a:
            overrides[str(index)] = [a.strftime("%H:%M"), b.strftime("%H:%M")]
    plan.day_windows = overrides
    plan.dirty = True
    plan.save()


@login_required
@require_POST
def schedule_save(request):
    """The settings form. From the sidebar it re-renders the pane with the
    replanned week; from the Settings tab it saves (including the on/off
    switch) and goes back there with a message."""
    profile = _profile(request)
    plan = planner.get_plan(profile)
    _parse_settings(plan, request.POST, profile)
    if request.POST.get("from") == "settings":
        plan.enabled = request.POST.get("enabled") == "1"
        plan.save(update_fields=["enabled"])
        planner.invalidate(profile)
        messages.success(request, "Schedule settings saved.")
        return redirect(f"{reverse('settings')}?tab=schedule")
    planner.invalidate(profile)
    return render(request, "tracker/partials/schedule_pane.html", _pane_context(request, profile))


@login_required
@require_POST
def schedule_toggle(request):
    """Turns My schedule on or off. The first-time setup form posts its
    lists, mode and free time along with it (setup=1)."""
    profile = _profile(request)
    plan = planner.get_plan(profile)
    if request.POST.get("setup"):
        _parse_settings(plan, request.POST, profile)
    plan.enabled = request.POST.get("enabled") == "1"
    plan.dirty = True
    plan.save()
    planner.invalidate(profile)
    return render(request, "tracker/partials/schedule_pane.html", _pane_context(request, profile))


@login_required
@require_POST
def schedule_rebuild(request):
    profile = _profile(request)
    plan = planner.get_plan(profile)
    if plan.enabled:
        plan.dirty = True
        plan.save(update_fields=["dirty"])
    return render(request, "tracker/partials/schedule_pane.html", _pane_context(request, profile))


@login_required
@require_POST
def schedule_slot_action(request, pk, action):
    profile = _profile(request)
    if action not in ("watched", "skip"):
        raise Http404
    slot = get_object_or_404(
        ScheduledSlot.objects.select_related("title").prefetch_related("episodes"), pk=pk, profile=profile
    )
    if slot.status == ScheduledSlot.Status.PLANNED:
        if action == "watched":
            planner.mark_slot_watched(profile, slot)
        else:
            planner.skip_slot(profile, slot)
    if request.POST.get("target") == "tonight":
        return _tonight_response(request, profile)
    return render(request, "tracker/partials/schedule_pane.html", _pane_context(request, profile))


@login_required
@require_POST
def schedule_add(request):
    """Manual plans: one block added at a chosen day and time."""
    profile = _profile(request)
    plan = planner.get_plan(profile)
    error = ""
    if not plan.enabled or plan.mode != SchedulePlan.Mode.MANUAL:
        raise Http404
    tz = selectors.profile_zoneinfo(profile)
    try:
        title_id = int(request.POST.get("title", ""))
        day = date.fromisoformat(request.POST.get("date", ""))
        at = planner.parse_time(request.POST.get("time"))
        count = int(request.POST.get("count") or 1)
    except (TypeError, ValueError):
        title_id = day = at = count = None
    if not (title_id and day and at):
        error = "Choose a title, a day and a time."
    else:
        _slot, error = planner.add_manual_slot(
            profile, plan, title_id, datetime.datetime.combine(day, at, tzinfo=tz), count
        )
        error = error or ""
    return render(request, "tracker/partials/schedule_pane.html", _pane_context(request, profile, error))


def _tonight_response(request, profile):
    now = timezone.now()
    plan = planner.ensure_plan(profile, now)
    context = {
        "plan": plan,
        "tonight": planner.tonight_context(profile, plan, now) if plan.enabled else None,
        "time_format_str": _time_format_str(profile),
        "target": "tonight",
    }
    return render(request, "tracker/partials/schedule_tonight.html", context)


@login_required
def schedule_tonight(request):
    """The Dashboard's Tonight card."""
    return _tonight_response(request, _profile(request))


@login_required
def schedule_estimate(request):
    profile = _profile(request)
    plan = planner.get_plan(profile)
    context = {"plan": plan}
    if plan.enabled:
        context["estimate"] = planner.estimate(profile, plan)
    return render(request, "tracker/partials/schedule_estimate.html", context)
