"""Recounts every profile's badges from its whole watch history.

A profile's first rebuild is silent (no toasts, no feed entries), so running
this after deploying the badges feature backfills everyone without a flood of
notifications. Safe to re-run: earned tiers are only ever added.
"""

from django.core.management.base import BaseCommand

from tracker import badges
from tracker.models import Profile


class Command(BaseCommand):
    help = "Rebuild badge tiers, XP and levels for every profile."

    def handle(self, *args, **options):
        for profile in Profile.objects.order_by("pk"):
            result = badges.rebuild(profile)
            self.stdout.write(
                f"{profile.display_name}: +{len(result.new_tiers)} tiers, level {result.level_before} -> {result.level_after}"
            )
