import io

from django.contrib.auth.models import User
from django.test import TestCase

from . import csv_import
from .models import MediaType, Profile, Title, WatchEvent


class CsvImportMappingTests(TestCase):
    def test_detects_trakt_style_headers(self):
        mapping = csv_import.detect_mapping(["Title", "Type", "Year", "WatchedAt".lower(), "watched_at", "Rating"])
        self.assertEqual(mapping["title"], "Title")
        self.assertEqual(mapping["media_type"], "Type")
        self.assertEqual(mapping["year"], "Year")
        self.assertEqual(mapping["watched_at"], "watched_at")
        self.assertEqual(mapping["rating"], "Rating")

    def test_detects_generic_aliases(self):
        mapping = csv_import.detect_mapping(["name", "media_type", "release_year", "date", "your_rating"])
        self.assertEqual(mapping["title"], "name")
        self.assertEqual(mapping["year"], "release_year")
        self.assertEqual(mapping["watched_at"], "date")
        self.assertEqual(mapping["rating"], "your_rating")

    def test_missing_field_left_unmapped(self):
        mapping = csv_import.detect_mapping(["title", "type", "watched_at"])
        self.assertNotIn("season", mapping)


class CsvImportParseRowsTests(TestCase):
    def _reader(self, text):
        return csv_import.open_csv_reader(io.BytesIO(text.encode("utf-8")))

    def test_parses_movie_row_with_datetime(self):
        reader = self._reader(
            "title,type,year,watched_at,rating\n"
            "The Long Corridor,movie,2020,2024-01-05 20:30:00,8\n"
        )
        mapping = csv_import.detect_mapping(reader.fieldnames)
        rows, errors = csv_import.parse_rows(reader, mapping)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["title"], "The Long Corridor")
        self.assertEqual(row["media_type"], MediaType.MOVIE)
        self.assertEqual(row["year"], 2020)
        self.assertEqual(row["rating"], 8)

    def test_date_only_defaults_to_midnight(self):
        reader = self._reader("title,type,watched_at\nFathom,show,2024-01-05\n")
        mapping = csv_import.detect_mapping(reader.fieldnames)
        rows, errors = csv_import.parse_rows(reader, mapping)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["watched_at"].hour, 0)
        self.assertEqual(rows[0]["watched_at"].minute, 0)

    def test_show_alias_maps_to_tv(self):
        reader = self._reader("title,type,watched_at\nFathom,show,2024-01-05\n")
        mapping = csv_import.detect_mapping(reader.fieldnames)
        rows, _ = csv_import.parse_rows(reader, mapping)
        self.assertEqual(rows[0]["media_type"], MediaType.TV)

    def test_out_of_range_rating_is_dropped_not_an_error(self):
        reader = self._reader("title,type,watched_at,rating\nFathom,movie,2024-01-05,99\n")
        mapping = csv_import.detect_mapping(reader.fieldnames)
        rows, errors = csv_import.parse_rows(reader, mapping)
        self.assertEqual(errors, [])
        self.assertIsNone(rows[0]["rating"])

    def test_row_missing_required_field_is_skipped_not_fatal(self):
        reader = self._reader(
            "title,type,watched_at\n"
            "Good Row,movie,2024-01-05\n"
            ",movie,2024-01-05\n"
            "Bad Type,starship,2024-01-05\n"
            "Bad Date,movie,not-a-date\n"
        )
        mapping = csv_import.detect_mapping(reader.fieldnames)
        rows, errors = csv_import.parse_rows(reader, mapping)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(errors), 3)
        reasons = [reason for _, reason in errors]
        self.assertTrue(any("title" in r for r in reasons))
        self.assertTrue(any("media_type" in r for r in reasons))
        self.assertTrue(any("watched_at" in r for r in reasons))

    def test_limit_caps_parsed_rows_but_not_errors(self):
        text = "title,type,watched_at\n" + "".join(f"Show {i},movie,2024-01-0{i}\n" for i in range(1, 6))
        reader = self._reader(text)
        mapping = csv_import.detect_mapping(reader.fieldnames)
        rows, errors = csv_import.parse_rows(reader, mapping, limit=2)
        self.assertEqual(len(rows), 2)


class CsvImportCommitTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("mira", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="Mira")

    def _rows(self, text):
        reader = csv_import.open_csv_reader(io.BytesIO(text.encode("utf-8")))
        mapping = csv_import.detect_mapping(reader.fieldnames)
        rows, _ = csv_import.parse_rows(reader, mapping)
        return rows

    def test_commits_movie_row(self):
        rows = self._rows("title,type,year,watched_at\nThe Long Corridor,movie,2020,2024-01-05\n")
        imported, skipped = csv_import.commit_rows(self.profile, rows)
        self.assertEqual(imported, 1)
        self.assertEqual(skipped, [])
        self.assertTrue(Title.objects.filter(name="The Long Corridor", year=2020, media_type=MediaType.MOVIE).exists())

    def test_tv_row_without_season_episode_is_skipped(self):
        rows = self._rows("title,type,watched_at\nFathom,show,2024-01-05\n")
        imported, skipped = csv_import.commit_rows(self.profile, rows)
        self.assertEqual(imported, 0)
        self.assertEqual(len(skipped), 1)
        self.assertIn("season and episode", skipped[0][1])

    def test_tv_row_with_season_episode_creates_episode(self):
        rows = self._rows(
            "title,type,season,episode,watched_at\nFathom,show,2,10,2024-01-05\n"
        )
        imported, skipped = csv_import.commit_rows(self.profile, rows)
        self.assertEqual(imported, 1)
        self.assertEqual(skipped, [])
        event = WatchEvent.objects.get(profile=self.profile)
        self.assertEqual(event.episode.season, 2)
        self.assertEqual(event.episode.episode, 10)

    def test_matches_existing_title_by_name_year_type_instead_of_duplicating(self):
        Title.objects.create(media_type=MediaType.MOVIE, name="The Long Corridor", year=2020)
        rows = self._rows("title,type,year,watched_at\nThe Long Corridor,movie,2020,2024-01-05\n")
        csv_import.commit_rows(self.profile, rows)
        self.assertEqual(Title.objects.filter(name="The Long Corridor").count(), 1)

    def test_duplicate_watch_event_is_skipped_on_reimport(self):
        rows = self._rows("title,type,year,watched_at\nThe Long Corridor,movie,2020,2024-01-05\n")
        csv_import.commit_rows(self.profile, rows)
        imported, skipped = csv_import.commit_rows(self.profile, rows)
        self.assertEqual(imported, 0)
        self.assertEqual(skipped[0][1], "already in history")
