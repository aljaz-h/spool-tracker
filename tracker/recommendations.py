"""Recommendation fulfillment - see models.Recommendation for the model
and the reasoning for resolving this via an explicit call at every
WatchEvent-creation site rather than a signal."""

from .models import Notification, Recommendation


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
