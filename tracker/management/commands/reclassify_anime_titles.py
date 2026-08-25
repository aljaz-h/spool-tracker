import argparse
import time

from django.core.management.base import BaseCommand

from tracker.integrations import tmdb
from tracker.models import MediaType, Title


class Command(BaseCommand):
    help = (
        "Reclassifies existing MediaType.TV titles that are actually anime "
        "(Animation genre + Japanese original language, same heuristic "
        "tmdb.py's own is_anime detection uses) to MediaType.ANIME. Every "
        "title added through Discover's Anime tab before this fix was "
        "materialized as plain MediaType.TV - see "
        "views._get_or_create_preview_title - so the filler/recap badge "
        "and MAL score/Japanese title/studio enrichment (both gated on "
        "media_type == MediaType.ANIME) silently never applied to them. "
        "Safe to re-run; a title already ANIME (or with no TMDB id yet) is "
        "never a candidate. A caller that needs the actual result as data "
        "(tasks.reclassify_anime_titles, to log it to DataLog) rather than "
        "scraping stdout can pass call_command(..., result_sink=some_dict) - "
        "handle() fills it with {'total': N, 'reclassified': [names]}. Not a "
        "handle() return value: BaseCommand.execute() writes any truthy "
        "return straight to stdout via self.stdout.write(), which requires "
        "a string, not a dict."
    )

    def add_arguments(self, parser):
        # Python-only channel for call_command(..., result_sink=some_dict) -
        # call_command validates kwargs against declared options, so this
        # has to exist even though no real CLI invocation should ever pass
        # it (hidden from --help accordingly).
        parser.add_argument("--result-sink", default=None, help=argparse.SUPPRESS)

    def handle(self, *args, **options):
        result_sink = options.get("result_sink")
        candidates = [t for t in Title.objects.filter(media_type=MediaType.TV) if t.external_ids.get("tmdb")]
        total = len(candidates)
        if total == 0:
            self.stdout.write("Nothing to check - no TV titles with a TMDB id yet.")
            if result_sink is not None:
                result_sink.update(total=0, reclassified=[])
            return

        self.stdout.write(f"Checking {total} TV titles for anime...")
        reclassified = []
        for i, title in enumerate(candidates, start=1):
            kind = tmdb.media_type_for(title)
            tmdb_id = title.external_ids["tmdb"]
            details = tmdb.get_full_details(kind, tmdb_id)
            if details and "Animation" in details["genres"] and details.get("original_language") == "ja":
                title.media_type = MediaType.ANIME
                title.save(update_fields=["media_type"])
                reclassified.append(title.name)
            if i % 50 == 0:
                self.stdout.write(f"...{i}/{total} checked, {len(reclassified)} reclassified so far")
            time.sleep(0.1)  # stay well clear of TMDB's rate limit over a large backfill

        self.stdout.write(self.style.SUCCESS(f"Done: {len(reclassified)}/{total} titles reclassified to anime."))
        if result_sink is not None:
            result_sink.update(total=total, reclassified=reclassified)
