import json

from django.core.management.base import BaseCommand, CommandError

from tracker.integrations import nuvio
from tracker.models import NuvioConnection


class Command(BaseCommand):
    help = (
        "Prints a few raw Nuvio progress and history items for one profile, "
        "to see exactly which fields Nuvio sends. Read-only: it signs in with "
        "the profile's saved Nuvio connection (saving the rotated refresh "
        "token like the normal sync does), never prints a token and never "
        "writes any watch data."
    )

    def add_arguments(self, parser):
        parser.add_argument("username", help="Sign-in username of the profile whose Nuvio connection to use.")
        parser.add_argument("--count", type=int, default=5, help="How many items of each kind to print (default 5).")

    def handle(self, *args, **options):
        try:
            connection = NuvioConnection.objects.select_related("profile").get(profile__user__username=options["username"])
        except NuvioConnection.DoesNotExist:
            raise CommandError(f"No Nuvio connection for username {options['username']!r}.")

        session = nuvio.refresh_access_token(connection.get_refresh_token())
        # Nuvio rotates the refresh token on every use - saved straight away,
        # exactly like sync_nuvio_history, so the connection keeps working.
        connection.set_refresh_token(session["refresh_token"])
        connection.save(update_fields=["encrypted_refresh_token"])

        count = max(1, options["count"])
        progress = nuvio.fetch_watch_progress(session["access_token"], connection.nuvio_profile_id) or []
        watched = nuvio.fetch_watched_items(session["access_token"], connection.nuvio_profile_id) or []
        self.stdout.write(f"Progress items returned: {len(progress)} (showing {min(count, len(progress))})")
        self.stdout.write(json.dumps(progress[:count], indent=2, default=str, ensure_ascii=False))
        self.stdout.write(f"Watched items returned: {len(watched)} (showing {min(count, len(watched))})")
        self.stdout.write(json.dumps(watched[:count], indent=2, default=str, ensure_ascii=False))
