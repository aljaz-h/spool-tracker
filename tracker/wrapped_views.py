"""Wrapped: the recap page, the card/report HTML it embeds, the on-demand PNG
and the public image URL. See tracker/wrapped/ for the stats and rendering."""

import os
import re
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .models import Profile, WatchEvent, WrappedReport
from .wrapped import render as wrapped_render
from .wrapped import service

Kind = WrappedReport.Kind
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")


def _profile_or_404(request):
    profile = Profile.objects.filter(user=request.user).first()
    if profile is None:
        raise Http404
    return profile


def _years_for(profile):
    """Years worth opening: from the first watch to this year, newest first."""
    first = WatchEvent.objects.filter(profile=profile).order_by("watched_at").values_list("watched_at", flat=True).first()
    this_year = date.today().year
    start = first.year if first else this_year
    return list(range(this_year, start - 1, -1))


@login_required
def wrapped_home(request):
    """/wrapped/ - this year so far."""
    _profile_or_404(request)
    return redirect("wrapped_year", year=date.today().year)


def _page(request, kind, year, month=None):
    profile = _profile_or_404(request)
    if month is not None and not 1 <= month <= 12:
        raise Http404
    label = service.period_label(kind, year, month)
    report = (
        WrappedReport.objects.filter(
            profile=profile, kind=kind, since=service.period_bounds(kind, year, month)[0],
            status=WrappedReport.Status.SUCCESS,
        )
        .order_by("-created_at")
        .first()
    )
    common = {
        "profile": profile,
        "kind": kind,
        "year": year,
        "month": month if month else (date.today().month if year == date.today().year else 1),
        "years": _years_for(profile),
        "months": [(i, name) for i, name in enumerate(
            ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"], start=1)],
        "is_review": kind == Kind.YEAR_IN_REVIEW,
    }
    if kind == Kind.YEAR_IN_REVIEW:
        return render(
            request,
            "tracker/wrapped_review.html",
            {
                **common,
                **service.year_in_review_page_context(profile, year),
                "label": label,
                "report": report,
                "filename": service.image_filename(report) if report else "",
                "saved": service.saved_reports(profile),
                "create_fields": {"kind": kind, "year": year, "month": ""},
            },
        )
    fmt, hidden = service.parse_options(request.GET)
    context, count = service.card_context(profile, kind, year, month, fmt=fmt, hidden=hidden, with_images=False)
    hide = ",".join(context["hidden"])
    frame_url = (
        reverse("wrapped_month_frame", args=[year, month]) if kind == Kind.MONTHLY
        else reverse("wrapped_year_frame", args=[year])
    ) + f"?fmt={fmt}&hide={hide}"
    width, height = wrapped_render.format_size(fmt)
    return render(
        request,
        "tracker/wrapped.html",
        {
            "profile": profile,
            "kind": kind,
            "year": year,
            "month": month if month else (date.today().month if year == date.today().year else 1),
            "label": label,
            "years": _years_for(profile),
            "months": [(i, name) for i, name in enumerate(
                ["January", "February", "March", "April", "May", "June", "July",
                 "August", "September", "October", "November", "December"], start=1)],
            "has_history": bool(count),
            "frame_url": frame_url,
            "report": report,
            "filename": service.image_filename(report) if report else "",
            "is_review": kind == Kind.YEAR_IN_REVIEW,
            "create_fields": {"kind": kind, "year": year, "month": month or "", "fmt": fmt, "hide": hide},
            "saved": service.saved_reports(profile),
            "fmt": fmt,
            "formats": [(key, label, w, h) for key, (label, w, h) in wrapped_render.FORMATS.items()],
            "card_height": height,
            "preview_scale": round(420 / width, 5),
            "options": [
                (key, label, key not in context["hidden"]) for key, label in service.CARD_STATS if key != "name"
            ],
            "hide_name": "name" in context["hidden"],
        },
    )


@login_required
def wrapped_all_time(request):
    """Everything you've ever watched, on one page - not tied to a period."""
    profile = _profile_or_404(request)
    today = date.today()
    report = (
        WrappedReport.objects.filter(profile=profile, kind=Kind.ALL_TIME, status=WrappedReport.Status.SUCCESS)
        .order_by("-created_at")
        .first()
    )
    context = service.all_time_context(profile)
    return render(
        request,
        "tracker/wrapped_all_time.html",
        {
            "profile": profile,
            "kind": "all_time",
            "has_history": context["stats"].has_data,
            "label": "All Time",
            "report": report,
            "filename": service.image_filename(report) if report and report.token else "",
            "create_fields": {"kind": Kind.ALL_TIME, "year": "", "month": ""},
            "year": today.year,
            "month": today.month,
            "years": _years_for(profile),
            "months": [],
            "is_review": False,
            "is_all_time": True,
            **context,
        },
    )


@login_required
def wrapped_year(request, year):
    return _page(request, Kind.YEARLY, year)


@login_required
def wrapped_month(request, year, month):
    return _page(request, Kind.MONTHLY, year, month)


@login_required
def wrapped_review(request, year):
    return _page(request, Kind.YEAR_IN_REVIEW, year)


def _frame(request, kind, year, month=None):
    """The card HTML on its own, for the page's iframe - exactly what the
    PNG is a screenshot of."""
    profile = _profile_or_404(request)
    fmt, hidden = service.parse_options(request.GET)
    context, _count = service.card_context(profile, kind, year, month, fmt=fmt, hidden=hidden)
    _width, height = wrapped_render.format_size(context["fmt"])
    response = HttpResponse(wrapped_render.render_html("card", {**context, "height": height}))
    response["X-Frame-Options"] = "SAMEORIGIN"
    return response


@login_required
def wrapped_year_frame(request, year):
    return _frame(request, Kind.YEARLY, year)


@login_required
def wrapped_month_frame(request, year, month):
    if not 1 <= month <= 12:
        raise Http404
    return _frame(request, Kind.MONTHLY, year, month)


@login_required
@require_POST
def wrapped_create(request):
    """"Create image": renders the PNG for the period on the page. Not sent
    anywhere - delivery (Discord, notification) is for the scheduled runs."""
    profile = _profile_or_404(request)
    kind = request.POST.get("kind")
    if kind not in Kind.values:
        raise Http404
    try:
        year = 0 if kind == Kind.ALL_TIME else int(request.POST.get("year", ""))
        month = int(request.POST["month"]) if kind == Kind.MONTHLY else None
    except (ValueError, KeyError):
        raise Http404
    if month is not None and not 1 <= month <= 12:
        raise Http404
    fmt, hidden = service.parse_options(request.POST)
    report = service.generate(profile, kind, year, month, deliver=False, fmt=fmt, hidden=hidden)
    if "application/json" in request.headers.get("Accept", ""):
        # The page's Download / Copy buttons make the image in the background.
        if report.status == WrappedReport.Status.SUCCESS and report.token:
            return JsonResponse(
                {
                    "ok": True,
                    "url": reverse("wrapped_image", args=[report.token]),
                    "name": service.image_filename(report),
                }
            )
        message = (
            "Nothing watched in that period yet."
            if report.status == WrappedReport.Status.SKIPPED
            else "Couldn't create the image. Try again in a moment."
        )
        return JsonResponse({"ok": False, "message": message}, status=400)
    if report.status == WrappedReport.Status.FAILED or (
        report.status == WrappedReport.Status.SUCCESS and not report.token
    ):
        messages.error(request, "Couldn't create the image. Try again in a moment.")
    elif report.status == WrappedReport.Status.SKIPPED:
        messages.info(request, "Nothing watched in that period yet.")
    url = service.page_url(kind, year, month)
    if kind in (Kind.MONTHLY, Kind.YEARLY):
        # Back to the same format and options the image was made with.
        hide = ",".join(sorted(hidden if hidden is not None else service.hidden_from_profile(profile)))
        url += f"?fmt={fmt}&hide={hide}"
    return redirect(url)


def wrapped_image(request, token):
    """The PNG at an unguessable URL - no login, so it can be posted to
    Discord or shared; the token is the access control."""
    if not _TOKEN_RE.match(token):
        raise Http404
    report = WrappedReport.objects.filter(token=token).first()
    if report is None:
        raise Http404
    # The token is already limited to URL-safe characters above; resolving the
    # real path and checking it stays inside the recaps folder is the second,
    # independent guard.
    base = os.path.realpath(str(wrapped_render.generated_dir()))
    path = os.path.realpath(os.path.join(base, f"{token}.png"))
    if not path.startswith(base + os.sep) or not os.path.isfile(path):
        raise Http404
    response = FileResponse(open(path, "rb"), content_type="image/png")
    response["Cache-Control"] = "private, max-age=31536000, immutable"
    response["Content-Disposition"] = f'inline; filename="{service.image_filename(report)}"'
    return response
