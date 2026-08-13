"""Recommendation lifecycle - see models.Recommendation for the model
and the reasoning for resolving fulfillment via an explicit call at every
WatchEvent-creation site rather than a signal."""

from .models import Notification, Recommendation, WatchEvent


def send(from_profile, to_profile, title, is_blind=False):
    """The 'Recommend to' card's one write action - shared by the real
    title_detail page (send_recommendation) and the not-yet-tracked
    preview page (title_preview_send_recommendation), so both notify the
    recipient identically. No-ops (no row, no notification) when the
    target has already watched it - _recommend_context already reflects
    that state as a disabled "Already watched" button; this is the same
    rule enforced server-side, not just cosmetically hidden. get_or_create
    means a second click while one's still pending doesn't double-notify
    (is_blind isn't part of the lookup, so a second click can't flip an
    already-pending rec's mystery status either way).

    is_blind=True omits the title from both the notification's message
    and its title FK - the whole point of a blind recommendation is that
    it stays a surprise until opened on the Dashboard card itself
    (dashboard_recommendations.html); notifications_panel.html links
    straight to the title page whenever a notification carries a title,
    which would give it away instantly otherwise."""
    if WatchEvent.objects.filter(profile=to_profile, title=title).exists():
        return
    _, created = Recommendation.objects.get_or_create(
        from_profile=from_profile,
        to_profile=to_profile,
        title=title,
        status=Recommendation.Status.PENDING,
        defaults={"is_blind": is_blind},
    )
    if created:
        if is_blind:
            Notification.objects.create(
                profile=to_profile,
                kind=Notification.Kind.RECOMMENDATION_RECEIVED,
                message=f"{from_profile.display_name} sent you a mystery recommendation \U0001f381",
            )
        else:
            Notification.objects.create(
                profile=to_profile,
                kind=Notification.Kind.RECOMMENDATION_RECEIVED,
                title=title,
                message=f"{from_profile.display_name} recommended: {title.name}",
            )


def mark_title_watched(profile, title):
    """Resolves every pending Recommendation of this exact title sent TO
    this profile - watching any part of it (a movie, or a single episode
    of a show) fulfills it; "watch this" doesn't require finishing a
    whole series. Notifies each sender. Cheap when nothing's pending -
    one query, no writes."""
    pending = Recommendation.objects.filter(
        to_profile=profile, title=title, status=Recommendation.Status.PENDING
    ).select_related("from_profile")
    for rec in pending:
        rec.status = Recommendation.Status.WATCHED
        rec.save(update_fields=["status"])
        Notification.objects.create(
            profile=rec.from_profile,
            kind=Notification.Kind.RECOMMENDATION_WATCHED,
            title=title,
            message=f"{profile.display_name} watched your recommendation: {title.name}",
        )
