import os

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from tracker.models import Profile


class Command(BaseCommand):
    help = (
        "Idempotently ensures a first admin User + Profile exist, sourced from "
        "ADMIN_USERNAME/ADMIN_PASSWORD/ADMIN_DISPLAY_NAME env vars (run by the web "
        "service on start). Without this, a fresh install has no self-service way "
        "to attach a Profile to the first account — the Settings 'Add profile' form "
        "creates unrelated new accounts, not one for whoever just logged in."
    )

    def handle(self, *args, **options):
        if Profile.objects.exists():
            self.stdout.write("A profile already exists — skipping admin bootstrap.")
            return

        username = os.environ.get("ADMIN_USERNAME", "admin")
        password = os.environ.get("ADMIN_PASSWORD")
        display_name = os.environ.get("ADMIN_DISPLAY_NAME", username.capitalize())

        if not password:
            self.stdout.write(
                self.style.WARNING(
                    "No profile exists yet and ADMIN_PASSWORD isn't set — skipping admin "
                    "bootstrap. Set ADMIN_USERNAME/ADMIN_PASSWORD in .env and restart, or "
                    "create one manually with `manage.py createsuperuser`."
                )
            )
            return

        user, created = User.objects.get_or_create(
            username=username, defaults={"is_superuser": True, "is_staff": True}
        )
        if created:
            user.set_password(password)
            user.save()
        Profile.objects.create(user=user, display_name=display_name)
        verb = "Created" if created else "Attached a profile to the existing"
        self.stdout.write(self.style.SUCCESS(f'{verb} admin account "{username}".'))
