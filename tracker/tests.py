import io
import os
import shutil
import tempfile
from datetime import timedelta
from unittest.mock import Mock, patch

import requests
from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django_celery_beat.models import PeriodicTask

from . import completion, csv_import, instance_config, notifications, recommendations, release_sync, rewatches, scheduling, selectors, tasks, update_check, views
from .integrations import gemini, tmdb, trakt
from .models import (
    AVATAR_COLOR_CHOICES,
    AdminAuditLogEntry,
    Episode,
    ExternalAccount,
    Genre,
    InstanceConfig,
    MediaType,
    Notification,
    Profile,
    Recommendation,
    ReleaseSchedule,
    SyncLog,
    Title,
    WatchEvent,
    WatchList,
    WatchListItem,
    WatchProgress,
    attach_genres,
    random_avatar_color,
)


def _tiny_gif_bytes():
    """A minimal valid 1x1 GIF - Pillow decodes it without needing to
    depend on Pillow itself here to build a PNG/JPEG. Used to exercise
    real image-upload validation without a binary fixture file."""
    return (
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
        b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
    )


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


class TraktFetchHistoryPaginationTests(TestCase):
    """A first version of fetch_history() only ever requested page 1 -
    confirmed against a real Trakt account that it silently capped every
    sync at exactly 200 items. These mock requests.get directly rather
    than hitting the network, same as the rest of this integration."""

    def _response(self, items, page, page_count):
        resp = Mock()
        resp.json.return_value = items
        resp.headers = {"X-Pagination-Page-Count": str(page_count)}
        resp.raise_for_status = Mock()
        return resp

    @patch("tracker.integrations.trakt.requests.get")
    def test_follows_pagination_across_multiple_pages(self, mock_get):
        mock_get.side_effect = [
            self._response([{"id": 1}, {"id": 2}], page=1, page_count=3),
            self._response([{"id": 3}, {"id": 4}], page=2, page_count=3),
            self._response([{"id": 5}], page=3, page_count=3),
        ]
        items = trakt.fetch_history("token", "client-id", limit=2)
        self.assertEqual([i["id"] for i in items], [1, 2, 3, 4, 5])
        self.assertEqual(mock_get.call_count, 3)
        self.assertEqual(mock_get.call_args_list[2].kwargs["params"], {"limit": 2, "page": 3})

    @patch("tracker.integrations.trakt.requests.get")
    def test_stops_after_single_page_when_header_missing(self, mock_get):
        resp = Mock()
        resp.json.return_value = [{"id": 1}]
        resp.headers = {}
        resp.raise_for_status = Mock()
        mock_get.return_value = resp
        items = trakt.fetch_history("token", "client-id")
        self.assertEqual(len(items), 1)
        self.assertEqual(mock_get.call_count, 1)

    @patch("tracker.integrations.trakt.requests.get")
    def test_stops_on_empty_page_even_if_header_claims_more(self, mock_get):
        mock_get.side_effect = [
            self._response([{"id": 1}], page=1, page_count=5),
            self._response([], page=2, page_count=5),
        ]
        items = trakt.fetch_history("token", "client-id", limit=1)
        self.assertEqual(len(items), 1)
        self.assertEqual(mock_get.call_count, 2)

    @patch("tracker.integrations.trakt.requests.get")
    def test_respects_max_pages_safety_cap(self, mock_get):
        mock_get.return_value = self._response([{"id": 1}], page=1, page_count=999)
        items = trakt.fetch_history("token", "client-id", limit=1, max_pages=3)
        self.assertEqual(len(items), 3)
        self.assertEqual(mock_get.call_count, 3)

    @patch("tracker.integrations.trakt.requests.get")
    def test_start_at_omitted_when_none(self, mock_get):
        mock_get.return_value = self._response([], page=1, page_count=1)
        trakt.fetch_history("token", "client-id")
        self.assertNotIn("start_at", mock_get.call_args.kwargs["params"])

    @patch("tracker.integrations.trakt.requests.get")
    def test_start_at_included_and_formatted_when_given(self, mock_get):
        import datetime

        mock_get.return_value = self._response([], page=1, page_count=1)
        dt = datetime.datetime(2024, 1, 5, 20, 30, 11, 123000, tzinfo=datetime.timezone.utc)
        trakt.fetch_history("token", "client-id", start_at=dt)
        self.assertEqual(mock_get.call_args.kwargs["params"]["start_at"], "2024-01-05T20:30:11.123Z")


class TraktUpsertCompletionWiringTests(TestCase):
    """upsert_history_items() should run completion/runtime inference once
    per unique title it touched, not once per watch event."""

    def setUp(self):
        user = User.objects.create_user("wiringwatcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="WiringWatcher")

    @patch("tracker.integrations.tmdb.find_match", return_value=None)
    @patch("tracker.completion.sync_watchlist_removal")
    @patch("tracker.completion.sync_show_completion")
    @patch("tracker.completion.update_movie_runtime")
    def test_calls_completion_once_per_unique_title(
        self, mock_movie_runtime, mock_show_completion, mock_watchlist_removal, mock_find_match
    ):
        items = [
            {
                "type": "movie",
                "watched_at": "2024-01-01T00:00:00.000Z",
                "movie": {"title": "Fathom", "year": 2020, "ids": {"trakt": 1}},
            },
            {
                "type": "episode",
                "watched_at": "2024-01-02T00:00:00.000Z",
                "show": {"title": "Cinder Street", "year": 2022, "ids": {"trakt": 2}},
                "episode": {"season": 1, "number": 1},
            },
            {
                "type": "episode",
                "watched_at": "2024-01-03T00:00:00.000Z",
                "show": {"title": "Cinder Street", "year": 2022, "ids": {"trakt": 2}},
                "episode": {"season": 1, "number": 2},
            },
        ]
        trakt.upsert_history_items(self.profile, items)
        self.assertEqual(mock_movie_runtime.call_count, 1)
        self.assertEqual(mock_show_completion.call_count, 1)
        show_title = mock_show_completion.call_args.args[1]
        self.assertEqual(show_title.name, "Cinder Street")
        self.assertEqual(mock_watchlist_removal.call_count, 2)


class TraktFetchListsTests(TestCase):
    def _response(self, data, headers=None):
        resp = Mock()
        resp.json.return_value = data
        resp.headers = headers or {}
        resp.raise_for_status = Mock()
        return resp

    @patch("tracker.integrations.trakt.requests.get")
    def test_fetches_watchlist_and_custom_lists_with_items(self, mock_get):
        mock_get.side_effect = [
            self._response([{"type": "movie", "movie": {"title": "Fathom", "ids": {"trakt": 1}}}]),  # watchlist
            self._response([{"name": "Favorites", "ids": {"trakt": 55}}]),  # users/me/lists
            self._response([{"type": "show", "show": {"title": "Cinder Street", "ids": {"trakt": 2}}}]),  # list items
        ]
        lists = trakt.fetch_lists("token", "client-id")
        self.assertEqual(len(lists), 2)
        self.assertEqual(lists[0]["name"], "Watchlist")
        self.assertEqual(lists[1]["name"], "Favorites")
        self.assertEqual(mock_get.call_args_list[0].args[0], "https://api.trakt.tv/sync/watchlist")
        self.assertEqual(mock_get.call_args_list[1].args[0], "https://api.trakt.tv/users/me/lists")
        self.assertEqual(mock_get.call_args_list[2].args[0], "https://api.trakt.tv/users/me/lists/55/items")

    @patch("tracker.integrations.trakt.requests.get")
    def test_skips_custom_lists_without_a_trakt_id(self, mock_get):
        mock_get.side_effect = [
            self._response([]),
            self._response([{"name": "No ID List", "ids": {}}]),
        ]
        lists = trakt.fetch_lists("token", "client-id")
        # Only the (empty) Watchlist - the id-less custom list is skipped
        # entirely, no items request made for it.
        self.assertEqual(len(lists), 1)
        self.assertEqual(mock_get.call_count, 2)


class TraktUpsertListsTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("listimporter", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="ListImporter")

    def test_creates_watchlist_and_items(self):
        from tracker.models import WatchList, WatchListItem

        lists_data = [
            {
                "name": "Watchlist",
                "items": [
                    {"type": "movie", "movie": {"title": "Fathom", "year": 2020, "ids": {"trakt": 1}}},
                    {"type": "show", "show": {"title": "Cinder Street", "year": 2022, "ids": {"trakt": 2}}},
                ],
            }
        ]
        added = trakt.upsert_lists(self.profile, lists_data)
        self.assertEqual(added, 2)
        watchlist = WatchList.objects.get(profile=self.profile, name="Watchlist")
        self.assertTrue(watchlist.is_watchlist)
        self.assertEqual(WatchListItem.objects.filter(watchlist=watchlist).count(), 2)

    def test_a_custom_named_list_is_not_flagged_as_the_watchlist(self):
        from tracker.models import WatchList

        lists_data = [
            {"name": "Best of 2024", "items": [{"type": "movie", "movie": {"title": "Fathom", "ids": {"trakt": 1}}}]}
        ]
        trakt.upsert_lists(self.profile, lists_data)
        custom_list = WatchList.objects.get(profile=self.profile, name="Best of 2024")
        self.assertFalse(custom_list.is_watchlist)

    def test_reimport_does_not_duplicate_items_or_lists(self):
        from tracker.models import WatchList

        lists_data = [
            {"name": "Watchlist", "items": [{"type": "movie", "movie": {"title": "Fathom", "ids": {"trakt": 1}}}]}
        ]
        trakt.upsert_lists(self.profile, lists_data)
        added_second_time = trakt.upsert_lists(self.profile, lists_data)
        self.assertEqual(added_second_time, 0)
        self.assertEqual(WatchList.objects.filter(profile=self.profile, name="Watchlist").count(), 1)

    def test_skips_items_missing_trakt_id(self):
        lists_data = [{"name": "Watchlist", "items": [{"type": "movie", "movie": {"title": "No ID"}}]}]
        added = trakt.upsert_lists(self.profile, lists_data)
        self.assertEqual(added, 0)


class SyncTraktListsWiringTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("listswiring", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="ListsWiring")
        self.account = ExternalAccount.objects.create(
            profile=self.profile, provider=ExternalAccount.Provider.TRAKT, access_token="tok"
        )

    @patch("tracker.integrations.trakt.upsert_lists")
    @patch("tracker.integrations.trakt.fetch_lists")
    @patch("tracker.integrations.trakt.upsert_history_items", return_value=0)
    @patch("tracker.integrations.trakt.fetch_history", return_value=[])
    def test_import_lists_enabled_fetches_and_upserts_lists(
        self, mock_fetch_history, mock_upsert_history, mock_fetch_lists, mock_upsert_lists
    ):
        self.account.import_lists = True
        self.account.save()
        mock_fetch_lists.return_value = [{"name": "Watchlist", "items": []}]
        mock_upsert_lists.return_value = 3
        created = tasks.sync_trakt_history(self.profile.id)
        mock_fetch_lists.assert_called_once()
        mock_upsert_lists.assert_called_once()
        self.assertEqual(created, 3)

    @patch("tracker.integrations.trakt.upsert_lists")
    @patch("tracker.integrations.trakt.fetch_lists")
    @patch("tracker.integrations.trakt.upsert_history_items", return_value=0)
    @patch("tracker.integrations.trakt.fetch_history", return_value=[])
    def test_import_lists_disabled_skips_lists(
        self, mock_fetch_history, mock_upsert_history, mock_fetch_lists, mock_upsert_lists
    ):
        created = tasks.sync_trakt_history(self.profile.id)
        mock_fetch_lists.assert_not_called()
        mock_upsert_lists.assert_not_called()
        self.assertEqual(created, 0)


class SaveSyncScheduleImportListsTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("listscheduleuser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="ListScheduleUser")
        self.trakt_account = ExternalAccount.objects.create(
            profile=self.profile, provider=ExternalAccount.Provider.TRAKT, access_token="tok"
        )
        self.simkl_account = ExternalAccount.objects.create(
            profile=self.profile, provider=ExternalAccount.Provider.SIMKL, access_token="tok"
        )
        self.client.login(username="listscheduleuser", password="pass12345")

    def test_checking_the_box_enables_import_lists_for_trakt(self):
        self.client.post(
            reverse("save_sync_schedule", args=["trakt"]),
            {"sync_interval_days": "1", "sync_time": "04:00", "import_lists": "on"},
        )
        self.trakt_account.refresh_from_db()
        self.assertTrue(self.trakt_account.import_lists)

    def test_unchecked_box_disables_import_lists(self):
        self.trakt_account.import_lists = True
        self.trakt_account.save()
        self.client.post(
            reverse("save_sync_schedule", args=["trakt"]),
            {"sync_interval_days": "1", "sync_time": "04:00"},
        )
        self.trakt_account.refresh_from_db()
        self.assertFalse(self.trakt_account.import_lists)

    def test_simkl_schedule_save_does_not_error_without_import_lists_field(self):
        resp = self.client.post(
            reverse("save_sync_schedule", args=["simkl"]),
            {"sync_interval_days": "1", "sync_time": "04:00"},
        )
        self.assertEqual(resp.status_code, 302)


class TmdbFindMatchTests(TestCase):
    def _response(self, results):
        resp = Mock()
        resp.json.return_value = {"results": results}
        resp.raise_for_status = Mock()
        return resp

    @override_settings(TMDB_API_KEY="")
    def test_returns_none_without_api_key(self):
        self.assertIsNone(tmdb.find_match(MediaType.MOVIE, "Fathom", 2020))

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_returns_id_kind_and_poster_url_on_match(self, mock_get):
        mock_get.return_value = self._response([{"id": 42, "poster_path": "/abc123.jpg"}])
        match = tmdb.find_match(MediaType.MOVIE, "The Long Corridor", 2020)
        self.assertEqual(match["id"], 42)
        self.assertEqual(match["kind"], "movie")
        self.assertEqual(match["poster_url"], "https://image.tmdb.org/t/p/w500/abc123.jpg")
        self.assertEqual(mock_get.call_args.args[0], "https://api.themoviedb.org/3/search/movie")
        self.assertEqual(mock_get.call_args.kwargs["params"]["year"], 2020)

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_poster_url_none_when_no_poster_path(self, mock_get):
        mock_get.return_value = self._response([{"id": 42, "poster_path": None}])
        match = tmdb.find_match(MediaType.MOVIE, "The Long Corridor", 2020)
        self.assertEqual(match["id"], 42)
        self.assertIsNone(match["poster_url"])

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_returns_none_on_no_results(self, mock_get):
        mock_get.return_value = self._response([])
        self.assertIsNone(tmdb.find_match(MediaType.MOVIE, "Nonexistent Movie", 2020))

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_anime_tries_tv_then_falls_back_to_movie(self, mock_get):
        mock_get.side_effect = [
            self._response([]),
            self._response([{"id": 99, "poster_path": "/anime-movie.jpg"}]),
        ]
        match = tmdb.find_match(MediaType.ANIME, "Ashfall Requiem", 2022)
        self.assertEqual(match["id"], 99)
        self.assertEqual(match["kind"], "movie")
        self.assertEqual(mock_get.call_count, 2)
        self.assertIn("search/tv", mock_get.call_args_list[0].args[0])
        self.assertIn("search/movie", mock_get.call_args_list[1].args[0])

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_returns_none_on_request_exception(self, mock_get):
        import requests

        mock_get.side_effect = requests.RequestException("boom")
        self.assertIsNone(tmdb.find_match(MediaType.MOVIE, "Fathom", 2020))


class TmdbDetailsTests(TestCase):
    def _response(self, data):
        resp = Mock()
        resp.json.return_value = data
        resp.raise_for_status = Mock()
        return resp

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_movie_details_returns_runtime(self, mock_get):
        mock_get.return_value = self._response({"runtime": 118})
        details = tmdb.get_movie_details(42)
        self.assertEqual(details["runtime"], 118)
        self.assertIn("https://api.themoviedb.org/3/movie/42", mock_get.call_args.args[0])

    @override_settings(TMDB_API_KEY="")
    def test_get_movie_details_returns_none_without_api_key(self):
        self.assertIsNone(tmdb.get_movie_details(42))

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_movie_details_returns_none_on_request_exception(self, mock_get):
        import requests

        mock_get.side_effect = requests.RequestException("boom")
        self.assertIsNone(tmdb.get_movie_details(42))

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_tv_details_parses_episode_count_and_runtime(self, mock_get):
        mock_get.return_value = self._response(
            {
                "number_of_episodes": 24,
                "episode_run_time": [24, 25],
                "seasons": [
                    {"season_number": 1, "episode_count": 12, "vote_average": 8.1},
                    {"season_number": 2, "episode_count": 12, "vote_average": 0},
                ],
            }
        )
        details = tmdb.get_tv_details(99)
        self.assertEqual(details["number_of_episodes"], 24)
        self.assertEqual(details["episode_run_time"], 24)
        self.assertEqual(details["seasons"], [
            {"season_number": 1, "episode_count": 12, "vote_average": 8.1},
            {"season_number": 2, "episode_count": 12, "vote_average": 0},
        ])

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_tv_details_handles_missing_episode_run_time(self, mock_get):
        mock_get.return_value = self._response({"number_of_episodes": 24, "seasons": []})
        details = tmdb.get_tv_details(99)
        self.assertIsNone(details["episode_run_time"])

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_full_details_movie_certification_skips_empty_entries(self, mock_get):
        # A US movie's release_dates carries several entries (festival/
        # theatrical/digital/...), most with an empty certification
        # string except whichever release actually set it - the first
        # non-empty one wins regardless of its position in the list.
        mock_get.return_value = self._response(
            {
                "id": 550, "title": "Fathom", "release_date": "2020-05-01",
                "release_dates": {
                    "results": [
                        {"iso_3166_1": "FR", "release_dates": [{"certification": "12"}]},
                        {
                            "iso_3166_1": "US",
                            "release_dates": [
                                {"certification": "", "note": "Festival"},
                                {"certification": "R", "note": "Theatrical"},
                            ],
                        },
                    ]
                },
            }
        )
        details = tmdb.get_full_details("movie", 550)
        self.assertEqual(details["certification"], "R")
        self.assertEqual(mock_get.call_args.kwargs["params"]["append_to_response"], "release_dates")

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_full_details_tv_certification_reads_us_rating(self, mock_get):
        mock_get.return_value = self._response(
            {
                "id": 1396, "name": "Breaking Bad", "first_air_date": "2008-01-20",
                "content_ratings": {"results": [{"iso_3166_1": "US", "rating": "TV-MA"}]},
            }
        )
        details = tmdb.get_full_details("tv", 1396)
        self.assertEqual(details["certification"], "TV-MA")

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_full_details_certification_none_when_no_us_entry(self, mock_get):
        mock_get.return_value = self._response(
            {
                "id": 42, "title": "Fathom", "release_date": "2020-05-01",
                "release_dates": {"results": [{"iso_3166_1": "FR", "release_dates": [{"certification": "12"}]}]},
            }
        )
        details = tmdb.get_full_details("movie", 42)
        self.assertIsNone(details["certification"])


class CompletionMovieRuntimeTests(TestCase):
    def test_sets_runtime_from_tmdb(self):
        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"tmdb": "42"}
        )
        with patch("tracker.completion.tmdb.get_movie_details", return_value={"runtime": 104}) as mock_details:
            completion.update_movie_runtime(title)
        mock_details.assert_called_once_with("42")
        title.refresh_from_db()
        self.assertEqual(title.runtime_minutes, 104)

    def test_skips_title_without_tmdb_id(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        with patch("tracker.completion.tmdb.get_movie_details") as mock_details:
            completion.update_movie_runtime(title)
        mock_details.assert_not_called()

    def test_does_not_overwrite_existing_runtime(self):
        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"tmdb": "42"}, runtime_minutes=90
        )
        with patch("tracker.completion.tmdb.get_movie_details") as mock_details:
            completion.update_movie_runtime(title)
        mock_details.assert_not_called()
        title.refresh_from_db()
        self.assertEqual(title.runtime_minutes, 90)


class CompletionShowTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("completionwatcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="CompletionWatcher")
        self.title = Title.objects.create(
            media_type=MediaType.TV, name="Fathom", year=2020, external_ids={"tmdb": "99"}
        )

    def _log_episodes(self, count):
        for i in range(1, count + 1):
            ep = Episode.objects.create(title=self.title, season=1, episode=i)
            WatchEvent.objects.create(profile=self.profile, title=self.title, episode=ep, watched_at="2024-01-01T00:00:00Z")

    def test_marks_completed_when_watched_count_meets_total(self):
        self._log_episodes(10)
        details = {"number_of_episodes": 10, "episode_run_time": 24, "seasons": []}
        with patch("tracker.completion.tmdb.get_tv_details", return_value=details):
            completion.sync_show_completion(self.profile, self.title)
        progress = WatchProgress.objects.get(profile=self.profile, title=self.title)
        self.assertEqual(progress.status, WatchProgress.Status.COMPLETED)

    def test_does_not_mark_completed_when_watched_count_is_short(self):
        self._log_episodes(5)
        details = {"number_of_episodes": 10, "episode_run_time": 24, "seasons": []}
        with patch("tracker.completion.tmdb.get_tv_details", return_value=details):
            completion.sync_show_completion(self.profile, self.title)
        self.assertFalse(WatchProgress.objects.filter(profile=self.profile, title=self.title).exists())

    def test_backfills_episode_runtime_from_show_level_average(self):
        self._log_episodes(3)
        details = {"number_of_episodes": 10, "episode_run_time": 22, "seasons": []}
        with patch("tracker.completion.tmdb.get_tv_details", return_value=details):
            completion.sync_show_completion(self.profile, self.title)
        for ep in Episode.objects.filter(title=self.title):
            self.assertEqual(ep.runtime_minutes, 22)

    def test_does_not_overwrite_existing_episode_runtime(self):
        ep = Episode.objects.create(title=self.title, season=1, episode=1, runtime_minutes=30)
        WatchEvent.objects.create(profile=self.profile, title=self.title, episode=ep, watched_at="2024-01-01T00:00:00Z")
        details = {"number_of_episodes": 10, "episode_run_time": 22, "seasons": []}
        with patch("tracker.completion.tmdb.get_tv_details", return_value=details):
            completion.sync_show_completion(self.profile, self.title)
        ep.refresh_from_db()
        self.assertEqual(ep.runtime_minutes, 30)

    def test_skips_title_without_tmdb_id(self):
        title = Title.objects.create(media_type=MediaType.TV, name="No TMDB", year=2020)
        with patch("tracker.completion.tmdb.get_tv_details") as mock_details:
            completion.sync_show_completion(self.profile, title)
        mock_details.assert_not_called()

    def test_falls_back_to_per_episode_runtime_when_show_level_average_is_missing(self):
        """The bug behind a real user-reported discrepancy: TMDB's
        show-level episode_run_time is empty for plenty of shows even
        though the season/episode endpoint has real per-episode data -
        previously that meant those episodes silently counted as 0
        minutes toward every watch-time stat, forever, with no retry."""
        self._log_episodes(2)
        details = {"number_of_episodes": 10, "episode_run_time": None, "seasons": []}
        season_data = {
            "episodes": [
                {"episode_number": 1, "name": "Pilot", "still_url": None, "air_date": None, "runtime": 42},
                {"episode_number": 2, "name": "Two", "still_url": None, "air_date": None, "runtime": 45},
            ]
        }
        with patch("tracker.completion.tmdb.get_tv_details", return_value=details):
            with patch("tracker.completion.tmdb.get_season_details", return_value=season_data) as mock_season:
                completion.sync_show_completion(self.profile, self.title)
        mock_season.assert_called_once_with("99", 1)
        ep1 = Episode.objects.get(title=self.title, season=1, episode=1)
        ep2 = Episode.objects.get(title=self.title, season=1, episode=2)
        self.assertEqual(ep1.runtime_minutes, 42)
        self.assertEqual(ep2.runtime_minutes, 45)

    def test_per_episode_fallback_only_queries_seasons_with_missing_episodes(self):
        Episode.objects.create(title=self.title, season=1, episode=1, runtime_minutes=30)
        Episode.objects.create(title=self.title, season=2, episode=1)
        details = {"number_of_episodes": 10, "episode_run_time": None, "seasons": []}
        with patch("tracker.completion.tmdb.get_tv_details", return_value=details):
            with patch("tracker.completion.tmdb.get_season_details", return_value=None) as mock_season:
                completion.sync_show_completion(self.profile, self.title)
        mock_season.assert_called_once_with("99", 2)

    def test_per_episode_fallback_does_not_overwrite_existing_runtime(self):
        Episode.objects.create(title=self.title, season=1, episode=1, runtime_minutes=30)
        Episode.objects.create(title=self.title, season=1, episode=2)
        details = {"number_of_episodes": 10, "episode_run_time": None, "seasons": []}
        season_data = {
            "episodes": [
                {"episode_number": 1, "name": "Pilot", "still_url": None, "air_date": None, "runtime": 99},
                {"episode_number": 2, "name": "Two", "still_url": None, "air_date": None, "runtime": 45},
            ]
        }
        with patch("tracker.completion.tmdb.get_tv_details", return_value=details):
            with patch("tracker.completion.tmdb.get_season_details", return_value=season_data):
                completion.sync_show_completion(self.profile, self.title)
        ep1 = Episode.objects.get(title=self.title, season=1, episode=1)
        ep2 = Episode.objects.get(title=self.title, season=1, episode=2)
        self.assertEqual(ep1.runtime_minutes, 30)
        self.assertEqual(ep2.runtime_minutes, 45)

    def test_per_episode_fallback_handles_a_missing_season_gracefully(self):
        Episode.objects.create(title=self.title, season=1, episode=1)
        details = {"number_of_episodes": 10, "episode_run_time": None, "seasons": []}
        with patch("tracker.completion.tmdb.get_tv_details", return_value=details):
            with patch("tracker.completion.tmdb.get_season_details", return_value=None):
                completion.sync_show_completion(self.profile, self.title)  # should not raise
        ep = Episode.objects.get(title=self.title, season=1, episode=1)
        self.assertIsNone(ep.runtime_minutes)


class CompletionWatchlistRemovalTests(TestCase):
    """completion.sync_watchlist_removal - the Trakt/Simkl-style behavior
    of a finished title coming off the profile's auto-managed Watchlist
    on its own, without touching any custom list."""

    def setUp(self):
        user = User.objects.create_user("watchlistwatcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="WatchlistWatcher")
        self.watchlist = WatchList.objects.create(profile=self.profile, name="Watchlist", is_watchlist=True)
        self.custom_list = WatchList.objects.create(profile=self.profile, name="Favorites")

    def test_removes_a_watched_movie_from_the_watchlist(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        WatchListItem.objects.create(watchlist=self.watchlist, title=movie)
        WatchEvent.objects.create(profile=self.profile, title=movie, watched_at="2024-01-01T00:00:00Z")
        completion.sync_watchlist_removal(self.profile, movie)
        self.assertFalse(WatchListItem.objects.filter(watchlist=self.watchlist, title=movie).exists())

    def test_leaves_an_unwatched_movie_on_the_watchlist(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        WatchListItem.objects.create(watchlist=self.watchlist, title=movie)
        completion.sync_watchlist_removal(self.profile, movie)
        self.assertTrue(WatchListItem.objects.filter(watchlist=self.watchlist, title=movie).exists())

    def test_removes_a_completed_show_from_the_watchlist(self):
        show = Title.objects.create(media_type=MediaType.TV, name="Silo", year=2023)
        WatchListItem.objects.create(watchlist=self.watchlist, title=show)
        WatchProgress.objects.create(profile=self.profile, title=show, status=WatchProgress.Status.COMPLETED)
        completion.sync_watchlist_removal(self.profile, show)
        self.assertFalse(WatchListItem.objects.filter(watchlist=self.watchlist, title=show).exists())

    def test_leaves_a_partially_watched_show_on_the_watchlist(self):
        show = Title.objects.create(media_type=MediaType.TV, name="Silo", year=2023)
        WatchListItem.objects.create(watchlist=self.watchlist, title=show)
        WatchProgress.objects.create(profile=self.profile, title=show, status=WatchProgress.Status.WATCHING)
        completion.sync_watchlist_removal(self.profile, show)
        self.assertTrue(WatchListItem.objects.filter(watchlist=self.watchlist, title=show).exists())

    def test_never_touches_a_custom_list(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        WatchListItem.objects.create(watchlist=self.watchlist, title=movie)
        WatchListItem.objects.create(watchlist=self.custom_list, title=movie)
        WatchEvent.objects.create(profile=self.profile, title=movie, watched_at="2024-01-01T00:00:00Z")
        completion.sync_watchlist_removal(self.profile, movie)
        self.assertFalse(WatchListItem.objects.filter(watchlist=self.watchlist, title=movie).exists())
        self.assertTrue(WatchListItem.objects.filter(watchlist=self.custom_list, title=movie).exists())

    def test_a_plain_list_merely_named_watchlist_is_not_touched(self):
        """The flag, not the name, is what makes a list the watchlist -
        an unflagged list a user happened to also name "Watchlist" is
        just a regular custom list."""
        lookalike = WatchList.objects.create(profile=self.profile, name="Watchlist")
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        WatchListItem.objects.create(watchlist=lookalike, title=movie)
        WatchEvent.objects.create(profile=self.profile, title=movie, watched_at="2024-01-01T00:00:00Z")
        completion.sync_watchlist_removal(self.profile, movie)
        self.assertTrue(WatchListItem.objects.filter(watchlist=lookalike, title=movie).exists())


class ReleaseSyncTests(TestCase):
    def _tv_details(self, next_episode):
        return {
            "name": "Cinder Street", "year": "2022", "overview": "", "tagline": "", "genres": [],
            "runtime": None, "number_of_seasons": 1, "number_of_episodes": 8,
            "backdrop_url": None, "poster_url": None, "vote_average": 7.0, "vote_count": 100,
            "original_language": "en", "status": "Returning Series",
            "release_date": None, "next_episode_to_air": next_episode,
        }

    def _movie_details(self, release_date):
        return {
            "name": "Fathom", "year": "2026", "overview": "", "tagline": "", "genres": [],
            "runtime": 100, "number_of_seasons": None, "number_of_episodes": None,
            "backdrop_url": None, "poster_url": None, "vote_average": 7.0, "vote_count": 100,
            "original_language": "en", "status": "Planned",
            "release_date": release_date, "next_episode_to_air": None,
        }

    def test_tv_next_episode_creates_episode_and_release_row(self):
        title = Title.objects.create(
            media_type=MediaType.TV, name="Cinder Street", year=2022, external_ids={"tmdb": "99"}
        )
        next_ep = {"air_date": "2026-08-01", "season_number": 2, "episode_number": 3, "name": "Return"}
        with patch("tracker.release_sync.tmdb.get_full_details", return_value=self._tv_details(next_ep)):
            touched = release_sync.sync_title_releases(title)
        self.assertEqual(touched, 1)
        episode = Episode.objects.get(title=title, season=2, episode=3)
        self.assertEqual(episode.name, "Return")
        row = ReleaseSchedule.objects.get(title=title, episode=episode)
        self.assertEqual(row.release_type, ReleaseSchedule.ReleaseType.EPISODE)

    def test_tv_next_episode_number_one_is_a_season_premiere(self):
        title = Title.objects.create(
            media_type=MediaType.TV, name="Cinder Street", year=2022, external_ids={"tmdb": "99"}
        )
        next_ep = {"air_date": "2026-08-01", "season_number": 2, "episode_number": 1, "name": "Return"}
        with patch("tracker.release_sync.tmdb.get_full_details", return_value=self._tv_details(next_ep)):
            release_sync.sync_title_releases(title)
        row = ReleaseSchedule.objects.get(title=title)
        self.assertEqual(row.release_type, ReleaseSchedule.ReleaseType.SEASON_PREMIERE)

    def test_tv_with_no_next_episode_touches_nothing(self):
        title = Title.objects.create(
            media_type=MediaType.TV, name="Cinder Street", year=2022, external_ids={"tmdb": "99"}
        )
        with patch("tracker.release_sync.tmdb.get_full_details", return_value=self._tv_details(None)):
            touched = release_sync.sync_title_releases(title)
        self.assertEqual(touched, 0)
        self.assertFalse(ReleaseSchedule.objects.filter(title=title).exists())

    def test_movie_future_release_date_creates_one_row(self):
        from django.utils import timezone

        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2026, external_ids={"tmdb": "42"}
        )
        future = (timezone.now() + timedelta(days=30)).date().isoformat()
        with patch("tracker.release_sync.tmdb.get_full_details", return_value=self._movie_details(future)):
            touched = release_sync.sync_title_releases(title)
        self.assertEqual(touched, 1)
        self.assertEqual(
            ReleaseSchedule.objects.filter(title=title, release_type=ReleaseSchedule.ReleaseType.MOVIE_RELEASE).count(),
            1,
        )

    def test_rerunning_movie_sync_does_not_duplicate(self):
        from django.utils import timezone

        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2026, external_ids={"tmdb": "42"}
        )
        future = (timezone.now() + timedelta(days=30)).date().isoformat()
        with patch("tracker.release_sync.tmdb.get_full_details", return_value=self._movie_details(future)):
            release_sync.sync_title_releases(title)
            release_sync.sync_title_releases(title)
        self.assertEqual(
            ReleaseSchedule.objects.filter(title=title, release_type=ReleaseSchedule.ReleaseType.MOVIE_RELEASE).count(),
            1,
        )

    def test_movie_past_release_date_touches_nothing(self):
        from django.utils import timezone

        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"tmdb": "42"}
        )
        past = (timezone.now() - timedelta(days=30)).date().isoformat()
        with patch("tracker.release_sync.tmdb.get_full_details", return_value=self._movie_details(past)):
            touched = release_sync.sync_title_releases(title)
        self.assertEqual(touched, 0)
        self.assertFalse(ReleaseSchedule.objects.filter(title=title).exists())

    def test_title_without_tmdb_id_is_skipped(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="No TMDB", year=2020)
        with patch("tracker.release_sync.tmdb.get_full_details") as mock_details:
            touched = release_sync.sync_title_releases(title)
        mock_details.assert_not_called()
        self.assertEqual(touched, 0)

    def test_none_details_returns_zero_without_crashing(self):
        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"tmdb": "42"}
        )
        with patch("tracker.release_sync.tmdb.get_full_details", return_value=None):
            touched = release_sync.sync_title_releases(title)
        self.assertEqual(touched, 0)

    def test_rerunning_tv_sync_with_a_delayed_date_updates_in_place(self):
        title = Title.objects.create(
            media_type=MediaType.TV, name="Cinder Street", year=2022, external_ids={"tmdb": "99"}
        )
        premiere = {"air_date": "2026-08-01", "season_number": 2, "episode_number": 1, "name": "Return"}
        with patch("tracker.release_sync.tmdb.get_full_details", return_value=self._tv_details(premiere)):
            release_sync.sync_title_releases(title)

        delayed = {"air_date": "2026-08-08", "season_number": 2, "episode_number": 1, "name": "Return"}
        with patch("tracker.release_sync.tmdb.get_full_details", return_value=self._tv_details(delayed)):
            release_sync.sync_title_releases(title)

        episode = Episode.objects.get(title=title, season=2, episode=1)
        rows = ReleaseSchedule.objects.filter(title=title, episode=episode)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().release_date.date().isoformat(), "2026-08-08")


class TitlesNeedingReleaseSyncTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("syncscope", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="SyncScope")

    def test_includes_a_completed_title(self):
        title = Title.objects.create(media_type=MediaType.TV, name="Finished", year=2020)
        WatchProgress.objects.create(profile=self.profile, title=title, status=WatchProgress.Status.COMPLETED)
        self.assertIn(title, selectors.titles_needing_release_sync())

    def test_includes_a_watchlist_only_title(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Listed", year=2020)
        watchlist = WatchList.objects.create(profile=self.profile, name="Watchlist")
        WatchListItem.objects.create(watchlist=watchlist, title=title)
        self.assertIn(title, selectors.titles_needing_release_sync())

    def test_includes_a_title_with_only_watch_history(self):
        # the common real-world case: watched via Trakt/Simkl/CSV import,
        # mid-way through, no WatchProgress row of any kind (nothing in
        # this app ever sets WATCHING - see calendar_releases()'s docstring).
        title = Title.objects.create(media_type=MediaType.TV, name="Mid-Watch", year=2020)
        WatchEvent.objects.create(profile=self.profile, title=title, watched_at="2024-01-01T00:00:00Z")
        self.assertIn(title, selectors.titles_needing_release_sync())

    def test_excludes_a_title_with_no_engagement(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Untouched", year=2020)
        self.assertNotIn(title, selectors.titles_needing_release_sync())


class SyncReleaseSchedulesTaskTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("releasetasker", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="ReleaseTasker")

    def test_only_syncs_titles_in_scope(self):
        watched = Title.objects.create(
            media_type=MediaType.MOVIE, name="Watched", year=2020, external_ids={"tmdb": "1"}
        )
        WatchProgress.objects.create(profile=self.profile, title=watched, status=WatchProgress.Status.COMPLETED)
        Title.objects.create(media_type=MediaType.MOVIE, name="Untouched", year=2020, external_ids={"tmdb": "2"})

        with patch("tracker.tasks.release_sync.sync_title_releases", return_value=1) as mock_sync:
            touched = tasks.sync_release_schedules()
        mock_sync.assert_called_once_with(watched)
        self.assertEqual(touched, 1)

    def test_sums_per_title_results(self):
        for i in range(3):
            title = Title.objects.create(
                media_type=MediaType.MOVIE, name=f"Title {i}", year=2020, external_ids={"tmdb": str(i)}
            )
            WatchProgress.objects.create(profile=self.profile, title=title, status=WatchProgress.Status.COMPLETED)

        with patch("tracker.tasks.release_sync.sync_title_releases", return_value=1):
            touched = tasks.sync_release_schedules()
        self.assertEqual(touched, 3)


class GenerateReleaseNotificationsTests(TestCase):
    """tracker/notifications.py's generate_release_notifications() -
    NEW_RELEASE only for profiles actively watching, UPCOMING_RELEASE for
    the broader "tracking" set (watching or watchlisted, or anyone at all
    if it's on a shared list)."""

    def setUp(self):
        from django.utils import timezone

        user = User.objects.create_user("notifwatcher", password="pass12345")
        self.watcher = Profile.objects.create(user=user, display_name="NotifWatcher")
        other_user = User.objects.create_user("notifother", password="pass12345")
        self.other = Profile.objects.create(user=other_user, display_name="NotifOther")
        self.title = Title.objects.create(media_type=MediaType.TV, name="Silo", year=2023)
        self.episode = Episode.objects.create(title=self.title, season=2, episode=1)
        self.now = timezone.now()

    def _release(self, delta, release_type=ReleaseSchedule.ReleaseType.EPISODE):
        return ReleaseSchedule.objects.create(
            title=self.title, episode=self.episode, release_type=release_type, release_date=self.now + delta
        )

    def test_watching_profile_gets_a_new_release_notification(self):
        WatchProgress.objects.create(profile=self.watcher, title=self.title, status=WatchProgress.Status.WATCHING)
        release = self._release(timedelta(hours=-2))
        created = notifications.generate_release_notifications(now=self.now)
        self.assertEqual(created, 1)
        n = Notification.objects.get(profile=self.watcher)
        self.assertEqual(n.kind, Notification.Kind.NEW_RELEASE)
        self.assertEqual(n.title, self.title)
        self.assertEqual(n.release_schedule, release)
        self.assertIn("Silo", n.message)

    def test_watchlist_only_profile_does_not_get_a_new_release_notification(self):
        watchlist = WatchList.objects.create(profile=self.watcher, name="Watchlist")
        WatchListItem.objects.create(watchlist=watchlist, title=self.title)
        self._release(timedelta(hours=-2))
        notifications.generate_release_notifications(now=self.now)
        self.assertFalse(Notification.objects.filter(kind=Notification.Kind.NEW_RELEASE).exists())

    def test_watching_profile_with_the_source_disabled_gets_nothing(self):
        self.watcher.notify_new_releases = False
        self.watcher.save(update_fields=["notify_new_releases"])
        WatchProgress.objects.create(profile=self.watcher, title=self.title, status=WatchProgress.Status.WATCHING)
        self._release(timedelta(hours=-2))
        notifications.generate_release_notifications(now=self.now)
        self.assertFalse(Notification.objects.exists())

    def test_watchlisted_profile_gets_an_upcoming_release_notification(self):
        watchlist = WatchList.objects.create(profile=self.watcher, name="Watchlist")
        WatchListItem.objects.create(watchlist=watchlist, title=self.title)
        release = self._release(timedelta(days=2))
        created = notifications.generate_release_notifications(now=self.now)
        self.assertEqual(created, 1)
        n = Notification.objects.get(profile=self.watcher)
        self.assertEqual(n.kind, Notification.Kind.UPCOMING_RELEASE)
        self.assertEqual(n.release_schedule, release)

    def test_shared_watchlist_makes_every_profile_eligible_for_the_reminder(self):
        watchlist = WatchList.objects.create(profile=self.watcher, name="Household", is_shared=True)
        WatchListItem.objects.create(watchlist=watchlist, title=self.title)
        self._release(timedelta(days=2))
        notifications.generate_release_notifications(now=self.now)
        self.assertTrue(Notification.objects.filter(profile=self.watcher).exists())
        self.assertTrue(Notification.objects.filter(profile=self.other).exists())

    def test_release_outside_either_window_is_ignored(self):
        WatchProgress.objects.create(profile=self.watcher, title=self.title, status=WatchProgress.Status.WATCHING)
        self._release(timedelta(days=-10))
        # A distinct episode - the same one twice would trip
        # ReleaseSchedule's own (title, episode, release_type) uniqueness.
        later_episode = Episode.objects.create(title=self.title, season=2, episode=2)
        ReleaseSchedule.objects.create(
            title=self.title,
            episode=later_episode,
            release_type=ReleaseSchedule.ReleaseType.EPISODE,
            release_date=self.now + timedelta(days=10),
        )
        notifications.generate_release_notifications(now=self.now)
        self.assertFalse(Notification.objects.exists())

    def test_rerunning_does_not_duplicate(self):
        WatchProgress.objects.create(profile=self.watcher, title=self.title, status=WatchProgress.Status.WATCHING)
        self._release(timedelta(hours=-2))
        notifications.generate_release_notifications(now=self.now)
        second_run_created = notifications.generate_release_notifications(now=self.now)
        self.assertEqual(second_run_created, 0)
        self.assertEqual(Notification.objects.count(), 1)


class NotifySyncFailureTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("syncfailwatcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="SyncFailWatcher")

    def test_creates_a_sync_failed_notification(self):
        notifications.notify_sync_failure(self.profile, "trakt", "connection timed out")
        n = Notification.objects.get(profile=self.profile)
        self.assertEqual(n.kind, Notification.Kind.SYNC_FAILED)
        self.assertIn("Trakt", n.message)
        self.assertIn("connection timed out", n.message)

    def test_long_error_messages_are_truncated(self):
        notifications.notify_sync_failure(self.profile, "trakt", "x" * 1000)
        n = Notification.objects.get(profile=self.profile)
        self.assertLessEqual(len(n.message), 255)

    def test_disabled_source_creates_nothing(self):
        self.profile.notify_sync_failures = False
        self.profile.save(update_fields=["notify_sync_failures"])
        result = notifications.notify_sync_failure(self.profile, "trakt", "boom")
        self.assertIsNone(result)
        self.assertFalse(Notification.objects.exists())


class RecommendationModelTests(TestCase):
    def setUp(self):
        sender_user = User.objects.create_user("recmodelsender", password="pass12345")
        self.sender = Profile.objects.create(user=sender_user, display_name="RecModelSender")
        recipient_user = User.objects.create_user("recmodelrecipient", password="pass12345")
        self.recipient = Profile.objects.create(user=recipient_user, display_name="RecModelRecipient")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)

    def test_duplicate_pending_recommendation_is_rejected(self):
        Recommendation.objects.create(from_profile=self.sender, to_profile=self.recipient, title=self.title)
        with self.assertRaises(Exception):
            Recommendation.objects.create(from_profile=self.sender, to_profile=self.recipient, title=self.title)

    def test_a_new_pending_recommendation_is_allowed_after_the_old_one_resolved(self):
        first = Recommendation.objects.create(from_profile=self.sender, to_profile=self.recipient, title=self.title)
        first.status = Recommendation.Status.WATCHED
        first.save(update_fields=["status"])
        # Should not raise - only *pending* rows are guarded by the constraint.
        Recommendation.objects.create(from_profile=self.sender, to_profile=self.recipient, title=self.title)
        self.assertEqual(Recommendation.objects.filter(to_profile=self.recipient, title=self.title).count(), 2)

    def test_different_senders_can_each_have_a_pending_recommendation(self):
        other_sender_user = User.objects.create_user("recmodelsender2", password="pass12345")
        other_sender = Profile.objects.create(user=other_sender_user, display_name="RecModelSender2")
        Recommendation.objects.create(from_profile=self.sender, to_profile=self.recipient, title=self.title)
        Recommendation.objects.create(from_profile=other_sender, to_profile=self.recipient, title=self.title)
        self.assertEqual(Recommendation.objects.filter(to_profile=self.recipient, title=self.title).count(), 2)


class MarkTitleWatchedTests(TestCase):
    def setUp(self):
        sender_user = User.objects.create_user("marksender", password="pass12345")
        self.sender = Profile.objects.create(user=sender_user, display_name="MarkSender")
        recipient_user = User.objects.create_user("markrecipient", password="pass12345")
        self.recipient = Profile.objects.create(user=recipient_user, display_name="MarkRecipient")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)

    def test_pending_recommendation_is_marked_watched(self):
        rec = Recommendation.objects.create(from_profile=self.sender, to_profile=self.recipient, title=self.title)
        recommendations.mark_title_watched(self.recipient, self.title)
        rec.refresh_from_db()
        self.assertEqual(rec.status, Recommendation.Status.WATCHED)

    def test_sender_gets_a_notification(self):
        Recommendation.objects.create(from_profile=self.sender, to_profile=self.recipient, title=self.title)
        recommendations.mark_title_watched(self.recipient, self.title)
        n = Notification.objects.get(profile=self.sender)
        self.assertEqual(n.kind, Notification.Kind.RECOMMENDATION_WATCHED)
        self.assertEqual(n.title, self.title)
        self.assertIn("MarkRecipient", n.message)
        self.assertIn("Fathom", n.message)

    def test_no_pending_recommendation_creates_nothing(self):
        recommendations.mark_title_watched(self.recipient, self.title)
        self.assertFalse(Notification.objects.exists())

    def test_already_watched_or_dismissed_recommendations_are_untouched(self):
        watched = Recommendation.objects.create(
            from_profile=self.sender, to_profile=self.recipient, title=self.title, status=Recommendation.Status.WATCHED
        )
        other_title = Title.objects.create(media_type=MediaType.MOVIE, name="Other Movie", year=2020)
        dismissed = Recommendation.objects.create(
            from_profile=self.sender, to_profile=self.recipient, title=other_title, status=Recommendation.Status.DISMISSED
        )
        recommendations.mark_title_watched(self.recipient, self.title)
        recommendations.mark_title_watched(self.recipient, other_title)
        watched.refresh_from_db()
        dismissed.refresh_from_db()
        self.assertEqual(watched.status, Recommendation.Status.WATCHED)
        self.assertEqual(dismissed.status, Recommendation.Status.DISMISSED)
        self.assertFalse(Notification.objects.exists())

    def test_only_the_matching_profile_and_title_are_resolved(self):
        other_title = Title.objects.create(media_type=MediaType.MOVIE, name="Other Movie", year=2020)
        other_user = User.objects.create_user("markother", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="MarkOther")
        rec_wrong_title = Recommendation.objects.create(from_profile=self.sender, to_profile=self.recipient, title=other_title)
        rec_wrong_profile = Recommendation.objects.create(from_profile=self.sender, to_profile=other_profile, title=self.title)
        recommendations.mark_title_watched(self.recipient, self.title)
        rec_wrong_title.refresh_from_db()
        rec_wrong_profile.refresh_from_db()
        self.assertEqual(rec_wrong_title.status, Recommendation.Status.PENDING)
        self.assertEqual(rec_wrong_profile.status, Recommendation.Status.PENDING)

    def test_multiple_senders_recommending_the_same_title_are_all_resolved(self):
        other_sender_user = User.objects.create_user("marksender2", password="pass12345")
        other_sender = Profile.objects.create(user=other_sender_user, display_name="MarkSender2")
        Recommendation.objects.create(from_profile=self.sender, to_profile=self.recipient, title=self.title)
        Recommendation.objects.create(from_profile=other_sender, to_profile=self.recipient, title=self.title)
        recommendations.mark_title_watched(self.recipient, self.title)
        self.assertEqual(
            Recommendation.objects.filter(to_profile=self.recipient, title=self.title, status=Recommendation.Status.WATCHED).count(),
            2,
        )
        self.assertEqual(Notification.objects.count(), 2)


class RecommendationWiringTests(TestCase):
    """Every place a WatchEvent gets created also calls
    recommendations.mark_title_watched - confirmed here per call site
    rather than trusting a signal would have caught them all."""

    def setUp(self):
        sender_user = User.objects.create_user("wiresender", password="pass12345")
        self.sender = Profile.objects.create(user=sender_user, display_name="WireSender")
        recipient_user = User.objects.create_user("wirerecipient", password="pass12345")
        self.recipient = Profile.objects.create(user=recipient_user, display_name="WireRecipient")
        self.client.login(username="wirerecipient", password="pass12345")

    def _recommend(self, title):
        return Recommendation.objects.create(from_profile=self.sender, to_profile=self.recipient, title=title)

    def test_title_mark_watched_resolves_a_pending_recommendation(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        rec = self._recommend(title)
        self.client.post(reverse("title_mark_watched", args=[title.pk]))
        rec.refresh_from_db()
        self.assertEqual(rec.status, Recommendation.Status.WATCHED)

    def test_episode_mark_watched_resolves_a_pending_recommendation(self):
        show = Title.objects.create(media_type=MediaType.TV, name="Bleach", year=2004)
        rec = self._recommend(show)
        self.client.post(reverse("episode_mark_watched", args=[show.pk, 1, 1]))
        rec.refresh_from_db()
        self.assertEqual(rec.status, Recommendation.Status.WATCHED)

    def test_title_rate_resolves_a_pending_recommendation(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        rec = self._recommend(title)
        self.client.post(reverse("title_rate", args=[title.pk]), {"rating": "8"})
        rec.refresh_from_db()
        self.assertEqual(rec.status, Recommendation.Status.WATCHED)

    @patch("tracker.integrations.tmdb.get_full_details")
    def test_title_preview_mark_watched_resolves_a_pending_recommendation(self, mock_details):
        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"tmdb": "42", "tmdb_kind": "movie"}
        )
        rec = self._recommend(title)
        self.client.post(reverse("title_preview_mark_watched", args=["movie", 42]))
        rec.refresh_from_db()
        self.assertEqual(rec.status, Recommendation.Status.WATCHED)

    def test_csv_import_resolves_pending_recommendations_for_movies_and_shows(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        show = Title.objects.create(media_type=MediaType.TV, name="Bleach", year=2004)
        movie_rec = self._recommend(movie)
        show_rec = self._recommend(show)
        rows = [
            {
                "row": 1, "title": "Fathom", "year": 2020, "media_type": MediaType.MOVIE,
                "season": None, "episode": None, "watched_at": "2024-01-01T00:00:00Z", "rating": None,
            },
            {
                "row": 2, "title": "Bleach", "year": 2004, "media_type": MediaType.TV,
                "season": 1, "episode": 1, "watched_at": "2024-01-01T00:00:00Z", "rating": None,
            },
        ]
        csv_import.commit_rows(self.recipient, rows)
        movie_rec.refresh_from_db()
        show_rec.refresh_from_db()
        self.assertEqual(movie_rec.status, Recommendation.Status.WATCHED)
        self.assertEqual(show_rec.status, Recommendation.Status.WATCHED)

    @patch("tracker.integrations.tmdb.find_match", return_value=None)
    def test_trakt_sync_resolves_pending_recommendations_for_movies_and_shows(self, mock_find_match):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"trakt": "1"})
        show = Title.objects.create(media_type=MediaType.TV, name="Cinder Street", year=2022, external_ids={"trakt": "2"})
        movie_rec = self._recommend(movie)
        show_rec = self._recommend(show)
        items = [
            {"type": "movie", "watched_at": "2024-01-01T00:00:00.000Z", "movie": {"title": "Fathom", "year": 2020, "ids": {"trakt": 1}}},
            {
                "type": "episode", "watched_at": "2024-01-02T00:00:00.000Z",
                "show": {"title": "Cinder Street", "year": 2022, "ids": {"trakt": 2}},
                "episode": {"season": 1, "number": 1},
            },
        ]
        trakt.upsert_history_items(self.recipient, items)
        movie_rec.refresh_from_db()
        show_rec.refresh_from_db()
        self.assertEqual(movie_rec.status, Recommendation.Status.WATCHED)
        self.assertEqual(show_rec.status, Recommendation.Status.WATCHED)

    @patch("tracker.integrations.tmdb.find_match", return_value=None)
    def test_simkl_sync_resolves_pending_recommendations_for_movies_and_shows(self, mock_find_match):
        from tracker.integrations import simkl

        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"simkl": "1"})
        anime = Title.objects.create(media_type=MediaType.ANIME, name="Bleach", year=2004, external_ids={"simkl": "2"})
        movie_rec = self._recommend(movie)
        anime_rec = self._recommend(anime)
        items = [
            {"type": "movie", "watched_at": "2024-01-01T00:00:00.000Z", "movie": {"title": "Fathom", "year": 2020, "ids": {"simkl": 1}}},
            {
                "type": "episode", "watched_at": "2024-01-02T00:00:00.000Z",
                "show": {"title": "Bleach", "year": 2004, "ids": {"simkl": 2}},
                "episode": {"season": 1, "number": 1},
            },
        ]
        simkl.upsert_history_items(self.recipient, items)
        movie_rec.refresh_from_db()
        anime_rec.refresh_from_db()
        self.assertEqual(movie_rec.status, Recommendation.Status.WATCHED)
        self.assertEqual(anime_rec.status, Recommendation.Status.WATCHED)


class SendRecommendationViewTests(TestCase):
    def setUp(self):
        sender_user = User.objects.create_user("sendsender", password="pass12345")
        self.sender = Profile.objects.create(user=sender_user, display_name="SendSender")
        recipient_user = User.objects.create_user("sendrecipient", password="pass12345")
        self.recipient = Profile.objects.create(user=recipient_user, display_name="SendRecipient")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        self.client.login(username="sendsender", password="pass12345")

    def test_creates_a_pending_recommendation(self):
        self.client.post(reverse("send_recommendation", args=[self.title.pk]), {"to_profile_id": self.recipient.pk})
        self.assertTrue(
            Recommendation.objects.filter(
                from_profile=self.sender, to_profile=self.recipient, title=self.title, status=Recommendation.Status.PENDING
            ).exists()
        )

    def test_sending_to_self_404s(self):
        resp = self.client.post(reverse("send_recommendation", args=[self.title.pk]), {"to_profile_id": self.sender.pk})
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(Recommendation.objects.exists())

    def test_duplicate_send_does_not_create_a_second_pending_row(self):
        self.client.post(reverse("send_recommendation", args=[self.title.pk]), {"to_profile_id": self.recipient.pk})
        self.client.post(reverse("send_recommendation", args=[self.title.pk]), {"to_profile_id": self.recipient.pk})
        self.assertEqual(Recommendation.objects.filter(from_profile=self.sender, to_profile=self.recipient, title=self.title).count(), 1)

    def test_already_watched_target_does_not_get_a_recommendation(self):
        WatchEvent.objects.create(profile=self.recipient, title=self.title, watched_at="2024-01-01T00:00:00Z")
        self.client.post(reverse("send_recommendation", args=[self.title.pk]), {"to_profile_id": self.recipient.pk})
        self.assertFalse(Recommendation.objects.exists())

    def test_recipient_gets_notified(self):
        self.client.post(reverse("send_recommendation", args=[self.title.pk]), {"to_profile_id": self.recipient.pk})
        n = Notification.objects.get(profile=self.recipient)
        self.assertEqual(n.kind, Notification.Kind.RECOMMENDATION_RECEIVED)
        self.assertEqual(n.title, self.title)
        self.assertIn("SendSender", n.message)
        self.assertIn("Fathom", n.message)

    def test_already_watched_target_is_not_notified(self):
        WatchEvent.objects.create(profile=self.recipient, title=self.title, watched_at="2024-01-01T00:00:00Z")
        self.client.post(reverse("send_recommendation", args=[self.title.pk]), {"to_profile_id": self.recipient.pk})
        self.assertFalse(Notification.objects.exists())

    def test_duplicate_send_does_not_notify_twice(self):
        self.client.post(reverse("send_recommendation", args=[self.title.pk]), {"to_profile_id": self.recipient.pk})
        self.client.post(reverse("send_recommendation", args=[self.title.pk]), {"to_profile_id": self.recipient.pk})
        self.assertEqual(Notification.objects.filter(profile=self.recipient).count(), 1)

    def test_response_reflects_already_watched_state(self):
        WatchEvent.objects.create(profile=self.recipient, title=self.title, watched_at="2024-01-01T00:00:00Z")
        resp = self.client.post(reverse("send_recommendation", args=[self.title.pk]), {"to_profile_id": self.recipient.pk})
        self.assertContains(resp, "Already watched")

    def test_response_reflects_sent_state(self):
        resp = self.client.post(reverse("send_recommendation", args=[self.title.pk]), {"to_profile_id": self.recipient.pk})
        self.assertContains(resp, "Sent")

    def test_invalid_to_profile_404s(self):
        resp = self.client.post(reverse("send_recommendation", args=[self.title.pk]), {"to_profile_id": 999999})
        self.assertEqual(resp.status_code, 404)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("send_recommendation", args=[self.title.pk]), {"to_profile_id": self.recipient.pk})
        self.assertEqual(resp.status_code, 302)

    def test_get_not_allowed(self):
        resp = self.client.get(reverse("send_recommendation", args=[self.title.pk]))
        self.assertEqual(resp.status_code, 405)


class TitlePreviewSendRecommendationViewTests(TestCase):
    """Recommending a not-yet-tracked preview title (title_preview_send_recommendation) -
    previously the only way to recommend anything was to first add it to
    a watchlist, which had nothing to do with recommending. Materializes
    the Title (same as every other preview action) then behaves exactly
    like send_recommendation, including notifying the recipient."""

    def setUp(self):
        sender_user = User.objects.create_user("previewrecsender", password="pass12345")
        self.sender = Profile.objects.create(user=sender_user, display_name="PreviewRecSender")
        recipient_user = User.objects.create_user("previewrecrecipient", password="pass12345")
        self.recipient = Profile.objects.create(user=recipient_user, display_name="PreviewRecRecipient")
        self.client.login(username="previewrecsender", password="pass12345")

    def _mock_details(self):
        patcher = patch("tracker.integrations.tmdb.get_full_details")
        mock_details = patcher.start()
        self.addCleanup(patcher.stop)
        mock_details.return_value = {"name": "Fathom", "year": "2020", "poster_url": None, "genres": []}
        return mock_details

    def test_materializes_the_title_and_creates_a_pending_recommendation(self):
        self._mock_details()
        self.client.post(
            reverse("title_preview_send_recommendation", args=["movie", 42]), {"to_profile_id": self.recipient.pk}
        )
        title = Title.objects.get(external_ids__tmdb="42")
        self.assertEqual(title.name, "Fathom")
        self.assertTrue(
            Recommendation.objects.filter(
                from_profile=self.sender, to_profile=self.recipient, title=title, status=Recommendation.Status.PENDING
            ).exists()
        )

    def test_recipient_gets_notified(self):
        self._mock_details()
        self.client.post(
            reverse("title_preview_send_recommendation", args=["movie", 42]), {"to_profile_id": self.recipient.pk}
        )
        title = Title.objects.get(external_ids__tmdb="42")
        n = Notification.objects.get(profile=self.recipient)
        self.assertEqual(n.kind, Notification.Kind.RECOMMENDATION_RECEIVED)
        self.assertEqual(n.title, title)

    def test_reuses_an_existing_title_instead_of_duplicating_it(self):
        mock_details = self._mock_details()
        existing_title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"tmdb": "42", "tmdb_kind": "movie"}
        )
        self.client.post(
            reverse("title_preview_send_recommendation", args=["movie", 42]), {"to_profile_id": self.recipient.pk}
        )
        mock_details.assert_not_called()
        self.assertEqual(Title.objects.filter(external_ids__tmdb="42").count(), 1)
        self.assertTrue(Recommendation.objects.filter(to_profile=self.recipient, title=existing_title).exists())

    def test_response_points_further_clicks_at_the_real_endpoint(self):
        # A third profile, still a live "Recommend" candidate after this
        # click (unlike self.recipient, who's now "Sent" - a disabled
        # button with no URL at all) - its form should point at the real,
        # now-materialized-title endpoint, not the preview one again.
        self._mock_details()
        third_user = User.objects.create_user("previewrecthird", password="pass12345")
        Profile.objects.create(user=third_user, display_name="PreviewRecThird")
        resp = self.client.post(
            reverse("title_preview_send_recommendation", args=["movie", 42]), {"to_profile_id": self.recipient.pk}
        )
        title = Title.objects.get(external_ids__tmdb="42")
        self.assertContains(resp, reverse("send_recommendation", args=[title.pk]))
        self.assertNotContains(resp, reverse("title_preview_send_recommendation", args=["movie", 42]))

    def test_sending_to_self_404s(self):
        resp = self.client.post(
            reverse("title_preview_send_recommendation", args=["movie", 42]), {"to_profile_id": self.sender.pk}
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(Recommendation.objects.exists())

    def test_invalid_media_type_404s(self):
        resp = self.client.post(
            reverse("title_preview_send_recommendation", args=["book", 42]), {"to_profile_id": self.recipient.pk}
        )
        self.assertEqual(resp.status_code, 404)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(
            reverse("title_preview_send_recommendation", args=["movie", 42]), {"to_profile_id": self.recipient.pk}
        )
        self.assertEqual(resp.status_code, 302)

    def test_get_not_allowed(self):
        resp = self.client.get(reverse("title_preview_send_recommendation", args=["movie", 42]))
        self.assertEqual(resp.status_code, 405)


class RecommendationsSendUnitTests(TestCase):
    """recommendations.send() directly - the shared helper behind both
    send_recommendation and title_preview_send_recommendation."""

    def setUp(self):
        sender_user = User.objects.create_user("sendunitsender", password="pass12345")
        self.sender = Profile.objects.create(user=sender_user, display_name="SendUnitSender")
        recipient_user = User.objects.create_user("sendunitrecipient", password="pass12345")
        self.recipient = Profile.objects.create(user=recipient_user, display_name="SendUnitRecipient")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)

    def test_creates_a_pending_recommendation_and_notifies(self):
        recommendations.send(self.sender, self.recipient, self.title)
        self.assertTrue(
            Recommendation.objects.filter(
                from_profile=self.sender, to_profile=self.recipient, title=self.title, status=Recommendation.Status.PENDING
            ).exists()
        )
        n = Notification.objects.get(profile=self.recipient)
        self.assertEqual(n.kind, Notification.Kind.RECOMMENDATION_RECEIVED)
        self.assertIn("SendUnitSender", n.message)
        self.assertIn("Fathom", n.message)

    def test_already_watched_target_gets_neither_a_row_nor_a_notification(self):
        WatchEvent.objects.create(profile=self.recipient, title=self.title, watched_at="2024-01-01T00:00:00Z")
        recommendations.send(self.sender, self.recipient, self.title)
        self.assertFalse(Recommendation.objects.exists())
        self.assertFalse(Notification.objects.exists())

    def test_second_call_while_pending_does_not_duplicate_the_notification(self):
        recommendations.send(self.sender, self.recipient, self.title)
        recommendations.send(self.sender, self.recipient, self.title)
        self.assertEqual(Notification.objects.filter(profile=self.recipient).count(), 1)


class DismissRecommendationViewTests(TestCase):
    def setUp(self):
        sender_user = User.objects.create_user("dismisssender", password="pass12345")
        self.sender = Profile.objects.create(user=sender_user, display_name="DismissSender")
        recipient_user = User.objects.create_user("dismissrecipient", password="pass12345")
        self.recipient = Profile.objects.create(user=recipient_user, display_name="DismissRecipient")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        self.rec = Recommendation.objects.create(from_profile=self.sender, to_profile=self.recipient, title=self.title)
        self.client.login(username="dismissrecipient", password="pass12345")

    def test_marks_dismissed(self):
        self.client.post(reverse("dismiss_recommendation", args=[self.rec.pk]))
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.status, Recommendation.Status.DISMISSED)

    def test_only_the_recipient_can_dismiss(self):
        self.client.logout()
        self.client.login(username="dismisssender", password="pass12345")
        resp = self.client.post(reverse("dismiss_recommendation", args=[self.rec.pk]))
        self.assertEqual(resp.status_code, 404)
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.status, Recommendation.Status.PENDING)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("dismiss_recommendation", args=[self.rec.pk]))
        self.assertEqual(resp.status_code, 302)

    def test_get_not_allowed(self):
        resp = self.client.get(reverse("dismiss_recommendation", args=[self.rec.pk]))
        self.assertEqual(resp.status_code, 405)


class AddRecommendationToWatchlistViewTests(TestCase):
    def setUp(self):
        sender_user = User.objects.create_user("addwlsender", password="pass12345")
        self.sender = Profile.objects.create(user=sender_user, display_name="AddWlSender")
        recipient_user = User.objects.create_user("addwlrecipient", password="pass12345")
        self.recipient = Profile.objects.create(user=recipient_user, display_name="AddWlRecipient")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        self.rec = Recommendation.objects.create(from_profile=self.sender, to_profile=self.recipient, title=self.title)
        self.client.login(username="addwlrecipient", password="pass12345")

    def test_adds_the_title_to_the_watchlist(self):
        self.client.post(reverse("add_recommendation_to_watchlist", args=[self.rec.pk]))
        watchlist = WatchList.objects.get(profile=self.recipient, is_watchlist=True)
        self.assertTrue(WatchListItem.objects.filter(watchlist=watchlist, title=self.title).exists())

    def test_recommendation_stays_pending_until_actually_watched(self):
        # Queuing isn't watching - the recommendation keeps nudging until
        # a real WatchEvent resolves it via recommendations.mark_title_watched.
        self.client.post(reverse("add_recommendation_to_watchlist", args=[self.rec.pk]))
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.status, Recommendation.Status.PENDING)

    def test_calling_it_twice_does_not_duplicate_the_watchlist_item(self):
        self.client.post(reverse("add_recommendation_to_watchlist", args=[self.rec.pk]))
        self.client.post(reverse("add_recommendation_to_watchlist", args=[self.rec.pk]))
        watchlist = WatchList.objects.get(profile=self.recipient, is_watchlist=True)
        self.assertEqual(WatchListItem.objects.filter(watchlist=watchlist, title=self.title).count(), 1)

    def test_only_the_recipient_can_add(self):
        self.client.logout()
        self.client.login(username="addwlsender", password="pass12345")
        resp = self.client.post(reverse("add_recommendation_to_watchlist", args=[self.rec.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("add_recommendation_to_watchlist", args=[self.rec.pk]))
        self.assertEqual(resp.status_code, 302)

    def test_get_not_allowed(self):
        resp = self.client.get(reverse("add_recommendation_to_watchlist", args=[self.rec.pk]))
        self.assertEqual(resp.status_code, 405)


class TitleDetailRecommendCardTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("carduser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="CardUser")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        self.client.login(username="carduser", password="pass12345")

    def test_no_card_on_a_single_profile_instance(self):
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertNotContains(resp, "Recommend to")

    def test_card_lists_other_profiles(self):
        other_user = User.objects.create_user("cardother", password="pass12345")
        Profile.objects.create(user=other_user, display_name="CardOther")
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertContains(resp, "Recommend to")
        self.assertContains(resp, "CardOther")

    def test_card_also_shows_on_a_preview_page(self):
        # Recommending shouldn't require adding the title to a watchlist
        # first - the card (and its live "Recommend" buttons) shows on a
        # not-yet-tracked preview page too, same as the real detail page.
        other_user = User.objects.create_user("cardother2", password="pass12345")
        Profile.objects.create(user=other_user, display_name="CardOther2")
        with patch("tracker.integrations.tmdb.get_full_details") as mock_details:
            mock_details.return_value = {
                "name": "Preview Movie", "year": "2020", "genres": [], "status": "Released", "poster_url": None,
                "number_of_seasons": None,
            }
            resp = self.client.get(reverse("title_preview", args=["movie", 999]))
        self.assertContains(resp, "Recommend to")
        self.assertContains(resp, "CardOther2")
        self.assertContains(resp, reverse("title_preview_send_recommendation", args=["movie", 999]))


class DashboardRecommendationsTests(TestCase):
    def setUp(self):
        sender_user = User.objects.create_user("dashsender", password="pass12345")
        self.sender = Profile.objects.create(user=sender_user, display_name="DashSender")
        recipient_user = User.objects.create_user("dashrecipient", password="pass12345")
        self.recipient = Profile.objects.create(user=recipient_user, display_name="DashRecipient")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        self.client.login(username="dashrecipient", password="pass12345")

    def test_no_card_without_pending_recommendations(self):
        resp = self.client.get(reverse("dashboard"))
        self.assertNotContains(resp, "Recommended to you")

    def test_card_shows_a_pending_recommendation(self):
        Recommendation.objects.create(from_profile=self.sender, to_profile=self.recipient, title=self.title)
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, "Recommended to you")
        self.assertContains(resp, "DashSender")
        self.assertContains(resp, "Fathom")

    def test_a_single_recommendation_has_no_count_badge(self):
        Recommendation.objects.create(from_profile=self.sender, to_profile=self.recipient, title=self.title)
        resp = self.client.get(reverse("dashboard"))
        self.assertNotContains(resp, "new)")

    def test_multiple_recommendations_show_a_count_badge(self):
        other_title = Title.objects.create(media_type=MediaType.MOVIE, name="Second Title", year=2021)
        Recommendation.objects.create(from_profile=self.sender, to_profile=self.recipient, title=self.title)
        Recommendation.objects.create(from_profile=self.sender, to_profile=self.recipient, title=other_title)
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, "2 new")

    def test_add_to_watchlist_button_shown_for_an_unlisted_title(self):
        Recommendation.objects.create(from_profile=self.sender, to_profile=self.recipient, title=self.title)
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, "Add to Watchlist")
        self.assertNotContains(resp, "&#10003; Added")

    def test_shows_added_state_when_title_already_on_the_watchlist(self):
        Recommendation.objects.create(from_profile=self.sender, to_profile=self.recipient, title=self.title)
        watchlist = WatchList.objects.create(profile=self.recipient, name="Watchlist", is_watchlist=True)
        WatchListItem.objects.create(watchlist=watchlist, title=self.title)
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, "Added")
        self.assertNotContains(resp, "+ Add to Watchlist")

    def test_dismissed_recommendations_are_not_shown(self):
        Recommendation.objects.create(
            from_profile=self.sender, to_profile=self.recipient, title=self.title, status=Recommendation.Status.DISMISSED
        )
        resp = self.client.get(reverse("dashboard"))
        self.assertNotContains(resp, "Recommended to you")

    def test_other_profiles_recommendations_are_not_shown(self):
        other_user = User.objects.create_user("dashother", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="DashOther")
        Recommendation.objects.create(from_profile=self.sender, to_profile=other_profile, title=self.title)
        resp = self.client.get(reverse("dashboard"))
        self.assertNotContains(resp, "Recommended to you")


class RecommendationEndToEndTests(TestCase):
    """No mocks - a real send, a real watch, a real notification with a
    working link, driven entirely through the app's own HTTP endpoints."""

    def test_full_flow(self):
        sender_user = User.objects.create_user("e2esender", password="pass12345")
        sender = Profile.objects.create(user=sender_user, display_name="E2ESender")
        recipient_user = User.objects.create_user("e2erecipient", password="pass12345")
        recipient = Profile.objects.create(user=recipient_user, display_name="E2ERecipient")
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)

        self.client.login(username="e2esender", password="pass12345")
        self.client.post(reverse("send_recommendation", args=[title.pk]), {"to_profile_id": recipient.pk})
        rec = Recommendation.objects.get(from_profile=sender, to_profile=recipient, title=title)
        self.assertEqual(rec.status, Recommendation.Status.PENDING)

        self.client.logout()
        self.client.login(username="e2erecipient", password="pass12345")
        self.client.post(reverse("title_mark_watched", args=[title.pk]))

        rec.refresh_from_db()
        self.assertEqual(rec.status, Recommendation.Status.WATCHED)

        n = Notification.objects.get(profile=sender)
        self.assertEqual(n.kind, Notification.Kind.RECOMMENDATION_WATCHED)
        self.assertIn("E2ERecipient watched your recommendation", n.message)

        self.client.logout()
        self.client.login(username="e2esender", password="pass12345")
        resp = self.client.get(reverse("notifications_panel"))
        self.assertContains(resp, reverse("title_detail", args=[title.pk]))
        self.assertContains(resp, "watched your recommendation")


def _version_response(text):
    resp = Mock()
    resp.text = text
    resp.raise_for_status = Mock()
    return resp


class RefreshLatestVersionTests(TestCase):
    @patch("tracker.update_check.APP_VERSION", "1.0.0")
    @patch("tracker.update_check.requests.get")
    def test_newer_remote_version_is_saved_and_returned(self, mock_get):
        mock_get.return_value = _version_response("1.1.0")
        result = update_check.refresh_latest_version()
        self.assertEqual(result, "1.1.0")
        self.assertEqual(InstanceConfig.load().latest_known_version, "1.1.0")

    @patch("tracker.update_check.APP_VERSION", "1.0.0")
    @patch("tracker.update_check.requests.get")
    def test_same_version_returns_none_and_does_not_save(self, mock_get):
        mock_get.return_value = _version_response("1.0.0")
        self.assertIsNone(update_check.refresh_latest_version())
        self.assertEqual(InstanceConfig.load().latest_known_version, "")

    @patch("tracker.update_check.APP_VERSION", "1.0.0")
    @patch("tracker.update_check.requests.get")
    def test_older_remote_version_returns_none(self, mock_get):
        mock_get.return_value = _version_response("0.9.0")
        self.assertIsNone(update_check.refresh_latest_version())

    @patch("tracker.update_check.requests.get")
    def test_network_failure_returns_none(self, mock_get):
        mock_get.side_effect = requests.RequestException("boom")
        self.assertIsNone(update_check.refresh_latest_version())

    @patch("tracker.update_check.requests.get")
    def test_empty_response_returns_none(self, mock_get):
        mock_get.return_value = _version_response("")
        self.assertIsNone(update_check.refresh_latest_version())


class AvailableVersionTests(TestCase):
    @patch("tracker.update_check.APP_VERSION", "1.0.0")
    def test_no_stored_version_returns_none(self):
        self.assertIsNone(update_check.available_version())

    @patch("tracker.update_check.APP_VERSION", "1.0.0")
    def test_stored_newer_version_is_returned(self):
        InstanceConfig.objects.update_or_create(pk=1, defaults={"latest_known_version": "1.2.0"})
        self.assertEqual(update_check.available_version(), "1.2.0")

    @patch("tracker.update_check.APP_VERSION", "1.2.0")
    def test_stale_stored_version_is_self_corrected_after_an_upgrade(self):
        # Stored from before an upgrade to 1.2.0 actually landed - should
        # no longer read as "available" now that it's caught up.
        InstanceConfig.objects.update_or_create(pk=1, defaults={"latest_known_version": "1.1.0"})
        self.assertIsNone(update_check.available_version())


class CheckForNewVersionTaskTests(TestCase):
    def setUp(self):
        owner_user = User.objects.create_user("updateowner", password="pass12345", is_superuser=True)
        self.owner = Profile.objects.create(user=owner_user, display_name="UpdateOwner")
        member_user = User.objects.create_user("updatemember", password="pass12345")
        self.member = Profile.objects.create(user=member_user, display_name="UpdateMember")

    @patch("tracker.update_check.refresh_latest_version")
    def test_notifies_owner_profiles_only(self, mock_refresh):
        mock_refresh.return_value = "9.9.9"
        created = tasks.check_for_new_version()
        self.assertEqual(created, 1)
        self.assertTrue(Notification.objects.filter(profile=self.owner, kind=Notification.Kind.SYSTEM_UPDATE).exists())
        self.assertFalse(Notification.objects.filter(profile=self.member).exists())

    @patch("tracker.update_check.refresh_latest_version")
    def test_message_mentions_both_versions(self, mock_refresh):
        mock_refresh.return_value = "9.9.9"
        tasks.check_for_new_version()
        n = Notification.objects.get(profile=self.owner)
        self.assertIn("9.9.9", n.message)

    @patch("tracker.update_check.refresh_latest_version")
    def test_no_available_update_creates_nothing(self, mock_refresh):
        mock_refresh.return_value = None
        created = tasks.check_for_new_version()
        self.assertEqual(created, 0)
        self.assertFalse(Notification.objects.exists())

    @patch("tracker.update_check.refresh_latest_version")
    def test_rerunning_for_the_same_version_does_not_duplicate(self, mock_refresh):
        mock_refresh.return_value = "9.9.9"
        tasks.check_for_new_version()
        tasks.check_for_new_version()
        self.assertEqual(Notification.objects.filter(profile=self.owner).count(), 1)


class UpdateAvailableContextProcessorTests(TestCase):
    def setUp(self):
        owner_user = User.objects.create_user("ctxowner", password="pass12345", is_superuser=True)
        self.owner = Profile.objects.create(user=owner_user, display_name="CtxOwner")
        member_user = User.objects.create_user("ctxmember", password="pass12345")
        Profile.objects.create(user=member_user, display_name="CtxMember")
        InstanceConfig.objects.update_or_create(pk=1, defaults={"latest_known_version": "99.0.0"})

    def test_owner_sees_latest_available_version(self):
        self.client.login(username="ctxowner", password="pass12345")
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.context["latest_available_version"], "99.0.0")

    def test_member_does_not_see_it(self):
        self.client.login(username="ctxmember", password="pass12345")
        resp = self.client.get(reverse("dashboard"))
        self.assertIsNone(resp.context["latest_available_version"])

    def test_changelog_url_always_present(self):
        self.client.login(username="ctxmember", password="pass12345")
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.context["changelog_url"], update_check.CHANGELOG_URL)


class SettingsUpdateBannerTests(TestCase):
    def setUp(self):
        owner_user = User.objects.create_user("bannerowner", password="pass12345", is_superuser=True)
        Profile.objects.create(user=owner_user, display_name="BannerOwner")
        self.client.login(username="bannerowner", password="pass12345")

    def test_banner_shown_when_update_available(self):
        InstanceConfig.objects.update_or_create(pk=1, defaults={"latest_known_version": "99.0.0"})
        resp = self.client.get(reverse("settings"))
        self.assertContains(resp, "v99.0.0 is available")
        self.assertContains(resp, update_check.CHANGELOG_URL)

    def test_no_banner_when_already_up_to_date(self):
        resp = self.client.get(reverse("settings"))
        self.assertNotContains(resp, "is available")


class NotificationsPanelSystemUpdateTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("panelowner", password="pass12345", is_superuser=True)
        self.profile = Profile.objects.create(user=user, display_name="PanelOwner")
        self.client.login(username="panelowner", password="pass12345")

    def test_system_update_notification_links_to_changelog(self):
        Notification.objects.create(
            profile=self.profile, kind=Notification.Kind.SYSTEM_UPDATE, message="Spool v9.9.9 is available."
        )
        resp = self.client.get(reverse("notifications_panel"))
        self.assertContains(resp, update_check.CHANGELOG_URL)
        self.assertContains(resp, "Spool v9.9.9 is available")


class CalendarReleasesBroadeningTests(TestCase):
    """calendar_releases() used to only surface WATCHING-status or
    watchlisted titles under its default "all" scope, so a completed show
    that later gets renewed never showed up. The explicit source="watching"
    filter's own meaning must stay unchanged, though - it's a deliberate
    narrower filter, not the bug."""

    def setUp(self):
        from django.utils import timezone

        user = User.objects.create_user("calscope", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="CalScope")
        self.title = Title.objects.create(media_type=MediaType.TV, name="Renewed Show", year=2020)
        WatchProgress.objects.create(profile=self.profile, title=self.title, status=WatchProgress.Status.COMPLETED)
        self.release = ReleaseSchedule.objects.create(
            title=self.title,
            release_type=ReleaseSchedule.ReleaseType.SEASON_PREMIERE,
            release_date=timezone.now() + timedelta(days=10),
        )

    def test_completed_titles_release_surfaces_under_default_scope(self):
        results = list(selectors.calendar_releases(self.profile))
        self.assertIn(self.release, results)

    def test_completed_titles_release_does_not_surface_under_watching_filter(self):
        results = list(selectors.calendar_releases(self.profile, source="watching"))
        self.assertNotIn(self.release, results)

    def test_actively_watching_still_surfaces_under_watching_filter(self):
        from django.utils import timezone

        watching_title = Title.objects.create(media_type=MediaType.TV, name="Currently Watching", year=2020)
        WatchProgress.objects.create(profile=self.profile, title=watching_title, status=WatchProgress.Status.WATCHING)
        release = ReleaseSchedule.objects.create(
            title=watching_title,
            release_type=ReleaseSchedule.ReleaseType.EPISODE,
            release_date=timezone.now() + timedelta(days=1),
        )
        results = list(selectors.calendar_releases(self.profile, source="watching"))
        self.assertIn(release, results)

    def test_watch_history_only_title_surfaces_under_default_scope(self):
        # the common real-world case: mid-way through a show via Trakt/
        # Simkl/CSV import, no WatchProgress row of any kind.
        from django.utils import timezone

        mid_watch = Title.objects.create(media_type=MediaType.TV, name="Mid-Watch", year=2020)
        WatchEvent.objects.create(profile=self.profile, title=mid_watch, watched_at="2024-01-01T00:00:00Z")
        release = ReleaseSchedule.objects.create(
            title=mid_watch,
            release_type=ReleaseSchedule.ReleaseType.EPISODE,
            release_date=timezone.now() + timedelta(days=5),
        )
        results = list(selectors.calendar_releases(self.profile))
        self.assertIn(release, results)

    def test_watch_history_only_title_does_not_surface_under_watching_filter(self):
        from django.utils import timezone

        mid_watch = Title.objects.create(media_type=MediaType.TV, name="Mid-Watch", year=2020)
        WatchEvent.objects.create(profile=self.profile, title=mid_watch, watched_at="2024-01-01T00:00:00Z")
        release = ReleaseSchedule.objects.create(
            title=mid_watch,
            release_type=ReleaseSchedule.ReleaseType.EPISODE,
            release_date=timezone.now() + timedelta(days=5),
        )
        results = list(selectors.calendar_releases(self.profile, source="watching"))
        self.assertNotIn(release, results)

    def test_start_param_surfaces_a_past_release(self):
        # regression test: the calendar grid needs to show a past month's
        # own releases (release_date before now), which the default
        # "upcoming from now" scoping used by the sidebar would hide.
        from django.utils import timezone

        past_release = ReleaseSchedule.objects.create(
            title=self.title,
            episode=None,
            release_type=ReleaseSchedule.ReleaseType.MOVIE_RELEASE,
            release_date=timezone.now() - timedelta(days=200),
        )
        results = list(selectors.calendar_releases(self.profile, start=timezone.now() - timedelta(days=365)))
        self.assertIn(past_release, results)

    def test_end_param_excludes_releases_outside_the_window(self):
        from django.utils import timezone

        now = timezone.now()
        in_window = ReleaseSchedule.objects.create(
            title=self.title, release_type=ReleaseSchedule.ReleaseType.EPISODE, release_date=now + timedelta(days=1)
        )
        results = list(
            selectors.calendar_releases(self.profile, start=now, end=now + timedelta(days=5))
        )
        self.assertIn(in_window, results)
        self.assertNotIn(self.release, results)  # release_date is +10 days, outside the 5-day window


class CalendarViewPastMonthTests(TestCase):
    """The calendar view's grid used to reuse the sidebar's "upcoming from
    now" query, so any past month always rendered with zero releases even
    though ReleaseSchedule rows for past dates are never deleted."""

    def test_a_past_months_release_still_appears_in_the_grid(self):
        from django.utils import timezone

        user = User.objects.create_user("calpast", password="pass12345")
        profile = Profile.objects.create(user=user, display_name="CalPast")
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Old Release", year=2020)
        WatchEvent.objects.create(profile=profile, title=title, watched_at=timezone.now() - timedelta(days=100))
        past_date = (timezone.now() - timedelta(days=90)).date()
        ReleaseSchedule.objects.create(
            title=title,
            release_type=ReleaseSchedule.ReleaseType.MOVIE_RELEASE,
            release_date=timezone.make_aware(
                timezone.datetime.combine(past_date, timezone.datetime.min.time().replace(hour=12))
            ),
        )
        self.client.login(username="calpast", password="pass12345")
        resp = self.client.get(reverse("calendar"), {"month": past_date.strftime("%Y-%m")})
        self.assertEqual(resp.status_code, 200)
        # calendar_main.html only renders a poster thumbnail per release, no
        # title text, so assert against the rendered grid context directly.
        grid_days = [day for week in resp.context["grid"] for day in week if day["date"] == past_date]
        self.assertEqual(len(grid_days), 1)
        self.assertEqual(grid_days[0]["items"][0].title.name, "Old Release")


class CalendarAgendaLookbackTests(TestCase):
    """The sidebar's agenda used to only ever show releases from right now
    onward, so a weekly release effectively vanished from it the instant
    its time passed. It should keep showing releases from a bit in the
    past too (views.AGENDA_LOOKBACK_DAYS), not just what's still ahead."""

    def setUp(self):
        user = User.objects.create_user("agendalookback", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="AgendaLookback")
        self.client.login(username="agendalookback", password="pass12345")

    def _release(self, name, days_ago):
        from django.utils import timezone

        title = Title.objects.create(media_type=MediaType.TV, name=name, year=2023)
        WatchEvent.objects.create(profile=self.profile, title=title, watched_at=timezone.now() - timedelta(days=200))
        return ReleaseSchedule.objects.create(
            title=title,
            release_type=ReleaseSchedule.ReleaseType.EPISODE,
            release_date=timezone.now() - timedelta(days=days_ago),
        )

    def _agenda_titles(self, resp):
        return {rs.title.name for group in resp.context["agenda_groups"] for rs in group["items"]}

    def test_a_release_from_ten_days_ago_still_appears_in_the_agenda(self):
        self._release("Recent Weekly Ep", days_ago=10)
        resp = self.client.get(reverse("calendar"))
        self.assertIn("Recent Weekly Ep", self._agenda_titles(resp))

    def test_a_release_from_forty_days_ago_has_aged_out_of_the_agenda(self):
        self._release("Ancient Ep", days_ago=40)
        resp = self.client.get(reverse("calendar"))
        self.assertNotIn("Ancient Ep", self._agenda_titles(resp))


class UpNextBroadeningTests(TestCase):
    def setUp(self):
        from django.utils import timezone

        user = User.objects.create_user("upnextscope", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="UpNextScope")
        self.title = Title.objects.create(media_type=MediaType.TV, name="Renewed Show", year=2020)
        WatchProgress.objects.create(profile=self.profile, title=self.title, status=WatchProgress.Status.COMPLETED)
        ReleaseSchedule.objects.create(
            title=self.title,
            release_type=ReleaseSchedule.ReleaseType.SEASON_PREMIERE,
            release_date=timezone.now() + timedelta(days=10),
        )

    def test_completed_titles_release_surfaces_in_up_next(self):
        items = selectors.up_next(self.profile)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], self.title)

    def test_watch_history_only_title_surfaces_in_up_next(self):
        from django.utils import timezone

        mid_watch = Title.objects.create(media_type=MediaType.TV, name="Mid-Watch", year=2020)
        WatchEvent.objects.create(profile=self.profile, title=mid_watch, watched_at="2024-01-01T00:00:00Z")
        ReleaseSchedule.objects.create(
            title=mid_watch,
            release_type=ReleaseSchedule.ReleaseType.EPISODE,
            release_date=timezone.now() + timedelta(days=1),
        )
        titles = [item["title"] for item in selectors.up_next(self.profile, limit=10)]
        self.assertIn(mid_watch, titles)

    def test_multiple_watch_events_do_not_duplicate_the_release(self):
        # WatchEvent has no per-title uniqueness constraint (unlike
        # WatchProgress) - joining through it without .distinct() would
        # multiply-match the same ReleaseSchedule row once per episode
        # watched.
        from django.utils import timezone

        binged = Title.objects.create(media_type=MediaType.TV, name="Binged", year=2020)
        for i in range(5):
            WatchEvent.objects.create(profile=self.profile, title=binged, watched_at="2024-01-01T00:00:00Z")
        ReleaseSchedule.objects.create(
            title=binged,
            release_type=ReleaseSchedule.ReleaseType.EPISODE,
            release_date=timezone.now() + timedelta(days=1),
        )
        items = selectors.up_next(self.profile, limit=10)
        matching = [item for item in items if item["title"] == binged]
        self.assertEqual(len(matching), 1)


class AppVersionTests(TestCase):
    def test_version_module_reads_the_version_file(self):
        from tracker.version import APP_VERSION

        # exact value isn't the point (it changes on every bump) - just
        # confirm it read something real off disk, not the "no file found"
        # fallback.
        self.assertNotEqual(APP_VERSION, "0.0.0")
        self.assertRegex(APP_VERSION, r"^\d+\.\d+\.\d+$")

    def test_context_processor_exposes_it(self):
        from django.test import RequestFactory

        from tracker.context_processors import app_version

        context = app_version(RequestFactory().get("/"))
        self.assertIn("app_version", context)

    def test_admin_dashboard_shows_the_version(self):
        # Moved here from Settings & Import - it's server metadata, so it
        # belongs in the Server card next to Django version/DB/debug, not
        # a personal-preferences page every profile can see.
        user = User.objects.create_user("versionchecker", password="pass12345", is_superuser=True)
        Profile.objects.create(user=user, display_name="VersionChecker")
        self.client.login(username="versionchecker", password="pass12345")
        resp = self.client.get(reverse("admin_dashboard"))
        self.assertContains(resp, "v")
        from tracker.version import APP_VERSION

        self.assertContains(resp, APP_VERSION)

    def test_sidebar_shows_the_version_on_any_page(self):
        user = User.objects.create_user("versionchecker2", password="pass12345")
        Profile.objects.create(user=user, display_name="VersionChecker2")
        self.client.login(username="versionchecker2", password="pass12345")
        resp = self.client.get(reverse("dashboard"))
        from tracker.version import APP_VERSION

        self.assertContains(resp, f"v{APP_VERSION}")


class InstanceConfigTests(TestCase):
    @override_settings(TRAKT_CLIENT_ID="env-id", TRAKT_CLIENT_SECRET="env-secret")
    def test_falls_back_to_env_when_db_blank(self):
        client_id, client_secret = instance_config.get_trakt_credentials()
        self.assertEqual(client_id, "env-id")
        self.assertEqual(client_secret, "env-secret")

    @override_settings(TRAKT_CLIENT_ID="env-id", TRAKT_CLIENT_SECRET="env-secret")
    def test_db_value_overrides_env(self):
        InstanceConfig.objects.create(pk=1, trakt_client_id="db-id", trakt_client_secret="db-secret")
        client_id, client_secret = instance_config.get_trakt_credentials()
        self.assertEqual(client_id, "db-id")
        self.assertEqual(client_secret, "db-secret")

    def test_load_is_a_singleton(self):
        a = InstanceConfig.load()
        b = InstanceConfig.load()
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(InstanceConfig.objects.count(), 1)


class SaveInstanceConfigViewTests(TestCase):
    def setUp(self):
        owner_user = User.objects.create_user("owner", password="pass12345", is_superuser=True)
        self.owner = Profile.objects.create(user=owner_user, display_name="Owner")
        member_user = User.objects.create_user("member", password="pass12345")
        self.member = Profile.objects.create(user=member_user, display_name="Member")

    def test_non_owner_gets_404(self):
        self.client.login(username="member", password="pass12345")
        resp = self.client.post(reverse("save_instance_config"), {"trakt_client_id": "x"})
        self.assertEqual(resp.status_code, 404)

    def test_owner_can_save_credentials(self):
        self.client.login(username="owner", password="pass12345")
        self.client.post(
            reverse("save_instance_config"),
            {"trakt_client_id": "new-id", "trakt_client_secret": "new-secret"},
        )
        cfg = InstanceConfig.load()
        self.assertEqual(cfg.trakt_client_id, "new-id")
        self.assertEqual(cfg.trakt_client_secret, "new-secret")

    def test_blank_field_does_not_clear_existing_value(self):
        InstanceConfig.objects.create(pk=1, trakt_client_id="existing-id")
        self.client.login(username="owner", password="pass12345")
        self.client.post(
            reverse("save_instance_config"),
            {"trakt_client_id": "", "simkl_client_id": "new-simkl"},
        )
        cfg = InstanceConfig.load()
        self.assertEqual(cfg.trakt_client_id, "existing-id")
        self.assertEqual(cfg.simkl_client_id, "new-simkl")


class SaveAppearanceViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("appearanceuser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="AppearanceUser")
        self.client.login(username="appearanceuser", password="pass12345")

    def test_saves_time_format(self):
        self.client.post(reverse("save_appearance"), {"time_format": "24h"})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.time_format, "24h")

    def test_invalid_time_format_is_ignored(self):
        self.client.post(reverse("save_appearance"), {"time_format": "bogus"})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.time_format, Profile.TimeFormat.H12)

    def test_saves_default_landing_page(self):
        self.client.post(reverse("save_appearance"), {"default_landing_page": "stats"})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.default_landing_page, "stats")

    def test_invalid_landing_page_is_ignored(self):
        self.client.post(reverse("save_appearance"), {"default_landing_page": "not-a-real-page"})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.default_landing_page, Profile.LandingPage.DASHBOARD)

    def test_saves_preferred_language(self):
        self.client.post(reverse("save_appearance"), {"preferred_language": "ja"})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.preferred_language, "ja")

    def test_preferred_language_can_be_cleared_back_to_any(self):
        self.profile.preferred_language = "ja"
        self.profile.save(update_fields=["preferred_language"])
        self.client.post(reverse("save_appearance"), {"preferred_language": ""})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.preferred_language, "")

    def test_unrecognized_language_code_is_ignored(self):
        self.client.post(reverse("save_appearance"), {"preferred_language": "xx-not-real"})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.preferred_language, "")

    def test_get_is_rejected(self):
        resp = self.client.get(reverse("save_appearance"))
        self.assertEqual(resp.status_code, 405)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("save_appearance"), {"time_format": "24h"})
        self.assertNotEqual(resp.status_code, 200)

    def test_saves_gemini_api_key(self):
        self.client.post(reverse("save_appearance"), {"gemini_api_key": "AIzaSyTest123"})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.gemini_api_key, "AIzaSyTest123")

    def test_gemini_api_key_can_be_cleared(self):
        self.profile.gemini_api_key = "AIzaSyTest123"
        self.profile.save(update_fields=["gemini_api_key"])
        self.client.post(reverse("save_appearance"), {"gemini_api_key": ""})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.gemini_api_key, "")


class SavePrivacyViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("privacyuser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="PrivacyUser")
        self.client.login(username="privacyuser", password="pass12345")

    def test_checked_box_enables_sharing(self):
        self.profile.share_activity = False
        self.profile.save(update_fields=["share_activity"])
        self.client.post(reverse("save_privacy"), {"share_activity": "on"})
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.share_activity)

    def test_omitted_box_disables_sharing(self):
        # A real browser never sends an unchecked checkbox's field at all.
        self.client.post(reverse("save_privacy"), {})
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.share_activity)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("save_privacy"), {"share_activity": "on"})
        self.assertNotEqual(resp.status_code, 200)


class SaveNotificationsViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("notifsettingsuser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="NotifSettingsUser")
        self.client.login(username="notifsettingsuser", password="pass12345")

    def test_all_three_toggles_save_together(self):
        self.client.post(
            reverse("save_notifications"),
            {"notify_new_releases": "on", "notify_sync_failures": "on"},  # upcoming omitted = unchecked
        )
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.notify_new_releases)
        self.assertFalse(self.profile.notify_upcoming_releases)
        self.assertTrue(self.profile.notify_sync_failures)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("save_notifications"), {"notify_new_releases": "on"})
        self.assertNotEqual(resp.status_code, 200)


class NotificationsPanelViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("panelviewer", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="PanelViewer")
        self.client.login(username="panelviewer", password="pass12345")

    def test_renders_the_profiles_own_notifications(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        Notification.objects.create(
            profile=self.profile, kind=Notification.Kind.NEW_RELEASE, title=title, message="Now available: Fathom"
        )
        resp = self.client.get(reverse("notifications_panel"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Now available: Fathom")

    def test_does_not_show_another_profiles_notifications(self):
        other_user = User.objects.create_user("panelother", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="PanelOther")
        Notification.objects.create(profile=other_profile, kind=Notification.Kind.SYNC_FAILED, message="Not mine")
        resp = self.client.get(reverse("notifications_panel"))
        self.assertNotContains(resp, "Not mine")

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse("notifications_panel"))
        self.assertNotEqual(resp.status_code, 200)


class MarkNotificationReadViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("markreaduser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="MarkReadUser")
        self.client.login(username="markreaduser", password="pass12345")
        self.notification = Notification.objects.create(
            profile=self.profile, kind=Notification.Kind.SYNC_FAILED, message="Sync failed"
        )

    def test_marks_a_single_notification_read(self):
        self.client.post(reverse("mark_notification_read", args=[self.notification.pk]))
        self.notification.refresh_from_db()
        self.assertTrue(self.notification.read)

    def test_cannot_mark_another_profiles_notification_read(self):
        other_user = User.objects.create_user("markreadother", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="MarkReadOther")
        others_notification = Notification.objects.create(
            profile=other_profile, kind=Notification.Kind.SYNC_FAILED, message="Not yours"
        )
        resp = self.client.post(reverse("mark_notification_read", args=[others_notification.pk]))
        self.assertEqual(resp.status_code, 404)
        others_notification.refresh_from_db()
        self.assertFalse(others_notification.read)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("mark_notification_read", args=[self.notification.pk]))
        self.assertNotEqual(resp.status_code, 200)


class MarkAllNotificationsReadViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("markalluser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="MarkAllUser")
        self.client.login(username="markalluser", password="pass12345")

    def test_marks_every_unread_notification_read(self):
        Notification.objects.create(profile=self.profile, kind=Notification.Kind.SYNC_FAILED, message="One")
        Notification.objects.create(profile=self.profile, kind=Notification.Kind.SYNC_FAILED, message="Two")
        self.client.post(reverse("mark_all_notifications_read"))
        self.assertEqual(Notification.objects.filter(profile=self.profile, read=False).count(), 0)

    def test_does_not_touch_another_profiles_notifications(self):
        other_user = User.objects.create_user("markallother", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="MarkAllOther")
        others = Notification.objects.create(profile=other_profile, kind=Notification.Kind.SYNC_FAILED, message="Not yours")
        self.client.post(reverse("mark_all_notifications_read"))
        others.refresh_from_db()
        self.assertFalse(others.read)


class UnreadNotificationCountContextTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("badgeuser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="BadgeUser")
        self.client.login(username="badgeuser", password="pass12345")

    def test_unread_count_reflects_unread_notifications(self):
        Notification.objects.create(profile=self.profile, kind=Notification.Kind.SYNC_FAILED, message="One")
        Notification.objects.create(profile=self.profile, kind=Notification.Kind.SYNC_FAILED, message="Two", read=True)
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.context["unread_notification_count"], 1)

    def test_zero_when_unauthenticated(self):
        self.client.logout()
        resp = self.client.get(reverse("login"))
        self.assertEqual(resp.context["unread_notification_count"], 0)


class SpoolLoginRedirectTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("loginredirectuser", password="pass12345")
        self.profile = Profile.objects.create(user=self.user, display_name="LoginRedirectUser")

    def _login(self):
        return self.client.post(reverse("login"), {"username": "loginredirectuser", "password": "pass12345"})

    def test_defaults_to_dashboard(self):
        resp = self._login()
        self.assertRedirects(resp, reverse("dashboard"))

    def test_redirects_to_the_profiles_configured_landing_page(self):
        self.profile.default_landing_page = "stats"
        self.profile.save(update_fields=["default_landing_page"])
        resp = self._login()
        self.assertRedirects(resp, reverse("stats"))

    def test_movies_tv_landing_page_goes_to_its_trending_category(self):
        self.profile.default_landing_page = "movies_tv"
        self.profile.save(update_fields=["default_landing_page"])
        resp = self._login()
        self.assertRedirects(resp, reverse("movies_tv", args=["trending"]))

    def test_an_explicit_next_param_still_wins_over_the_landing_page(self):
        self.profile.default_landing_page = "stats"
        self.profile.save(update_fields=["default_landing_page"])
        resp = self.client.post(
            reverse("login") + "?next=" + reverse("history"),
            {"username": "loginredirectuser", "password": "pass12345"},
        )
        self.assertRedirects(resp, reverse("history"))


class ExportCsvViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("csvexporter", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="CsvExporter")
        self.client.login(username="csvexporter", password="pass12345")

    def test_exports_a_movie_and_an_episode_watch(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        WatchEvent.objects.create(
            profile=self.profile, title=movie, watched_at="2024-01-01T00:00:00Z", user_rating=8
        )
        show = Title.objects.create(media_type=MediaType.TV, name="Silo", year=2023)
        ep = Episode.objects.create(title=show, season=1, episode=2)
        WatchEvent.objects.create(profile=self.profile, title=show, episode=ep, watched_at="2024-01-02T00:00:00Z")

        resp = self.client.get(reverse("export_csv"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv")
        self.assertIn("attachment", resp["Content-Disposition"])
        body = resp.content.decode()
        self.assertIn("title,media_type,year,season,episode,watched_at,rating", body)
        self.assertIn("Fathom,movie,2020,,,", body)
        self.assertIn(",8\r\n", body)
        self.assertIn("Silo,tv,2023,1,2,", body)

    def test_only_exports_the_requesting_profiles_events(self):
        other_user = User.objects.create_user("otherexporter", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="OtherExporter")
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Not Mine", year=2020)
        WatchEvent.objects.create(profile=other_profile, title=movie, watched_at="2024-01-01T00:00:00Z")
        resp = self.client.get(reverse("export_csv"))
        self.assertNotIn("Not Mine", resp.content.decode())

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse("export_csv"))
        self.assertNotEqual(resp.status_code, 200)


class ExportTraktJsonViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("jsonexporter", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="JsonExporter")
        self.client.login(username="jsonexporter", password="pass12345")

    def test_exports_a_movie_with_ids_when_known(self):
        movie = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"trakt": "5", "tmdb": "42"}
        )
        WatchEvent.objects.create(profile=self.profile, title=movie, watched_at="2024-01-01T00:00:00Z")
        resp = self.client.get(reverse("export_trakt_json"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["type"], "movie")
        self.assertEqual(data[0]["movie"]["title"], "Fathom")
        self.assertEqual(data[0]["movie"]["ids"], {"trakt": "5", "tmdb": 42})

    def test_exports_an_episode_with_show_and_episode_shape(self):
        show = Title.objects.create(media_type=MediaType.TV, name="Silo", year=2023)
        ep = Episode.objects.create(title=show, season=1, episode=2, name="Holston's Pick")
        WatchEvent.objects.create(profile=self.profile, title=show, episode=ep, watched_at="2024-01-02T00:00:00Z")
        resp = self.client.get(reverse("export_trakt_json"))
        data = resp.json()
        self.assertEqual(data[0]["type"], "episode")
        self.assertEqual(data[0]["show"]["title"], "Silo")
        self.assertEqual(data[0]["episode"], {"season": 1, "number": 2, "title": "Holston's Pick"})

    def test_titles_without_external_ids_export_empty_ids(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="CSV Only", year=2020)
        WatchEvent.objects.create(profile=self.profile, title=movie, watched_at="2024-01-01T00:00:00Z")
        resp = self.client.get(reverse("export_trakt_json"))
        data = resp.json()
        self.assertEqual(data[0]["movie"]["ids"], {})

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse("export_trakt_json"))
        self.assertNotEqual(resp.status_code, 200)


class AdminDashboardVisibilityTests(TestCase):
    def setUp(self):
        owner_user = User.objects.create_user("owner2", password="pass12345", is_superuser=True)
        Profile.objects.create(user=owner_user, display_name="Owner2")
        member_user = User.objects.create_user("member2", password="pass12345")
        Profile.objects.create(user=member_user, display_name="Member2")

    def test_owner_sees_integrations_card(self):
        self.client.login(username="owner2", password="pass12345")
        resp = self.client.get(reverse("admin_dashboard"))
        self.assertContains(resp, "Integrations")

    def test_member_gets_404(self):
        self.client.login(username="member2", password="pass12345")
        resp = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(resp.status_code, 404)

    def test_member_does_not_see_admin_sidebar_section(self):
        self.client.login(username="member2", password="pass12345")
        resp = self.client.get(reverse("settings"))
        self.assertNotContains(resp, "Server Integrations")

    def test_owner_sees_admin_sidebar_section(self):
        self.client.login(username="owner2", password="pass12345")
        resp = self.client.get(reverse("settings"))
        self.assertContains(resp, "Server Integrations")

    def test_configured_secret_value_never_rendered_in_html(self):
        InstanceConfig.objects.create(pk=1, trakt_client_secret="super-secret-value")
        self.client.login(username="owner2", password="pass12345")
        resp = self.client.get(reverse("admin_dashboard"))
        self.assertNotContains(resp, "super-secret-value")

    def test_server_card_shows_app_version(self):
        self.client.login(username="owner2", password="pass12345")
        resp = self.client.get(reverse("admin_dashboard"))
        self.assertContains(resp, f"v{update_check.APP_VERSION}")


class SettingsConnectedAppsTests(TestCase):
    """Settings & Import's Trakt/Simkl card - renamed to "Connected Apps",
    and its Connect button now accounts for whether the server owner has
    configured credentials at all (previously it always linked to
    oauth_connect, which redirected back with an error if not)."""

    def setUp(self):
        user = User.objects.create_user("connectedappsuser", password="pass12345")
        Profile.objects.create(user=user, display_name="ConnectedAppsUser")
        self.client.login(username="connectedappsuser", password="pass12345")

    def test_section_is_renamed(self):
        resp = self.client.get(reverse("settings"))
        self.assertContains(resp, "Connected Apps")
        self.assertNotContains(resp, "Import &amp; Export")

    def test_connect_button_disabled_when_trakt_not_configured(self):
        resp = self.client.get(reverse("settings"))
        self.assertContains(resp, "Ask your server owner to set this up first")

    @override_settings(TRAKT_CLIENT_ID="configured-id", TRAKT_CLIENT_SECRET="configured-secret")
    def test_connect_button_enabled_when_trakt_configured(self):
        resp = self.client.get(reverse("settings"))
        self.assertContains(resp, reverse("trakt_connect"))

    def test_version_footer_no_longer_on_settings_page(self):
        resp = self.client.get(reverse("settings"))
        self.assertNotContains(resp, f"Spool v{update_check.APP_VERSION}")

    def test_sync_now_button_shown_for_a_connected_provider(self):
        profile = Profile.objects.get(display_name="ConnectedAppsUser")
        ExternalAccount.objects.create(profile=profile, provider=ExternalAccount.Provider.TRAKT, access_token="tok")
        resp = self.client.get(reverse("settings"))
        self.assertContains(resp, reverse("trigger_manual_sync", args=["trakt"]))
        self.assertContains(resp, "Sync now")

    def test_sync_now_button_absent_when_not_connected(self):
        resp = self.client.get(reverse("settings"))
        self.assertNotContains(resp, "Sync now")


class SettingsTimezoneTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("tzuser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="TzUser")
        self.client.login(username="tzuser", password="pass12345")

    def test_renders_timezone_dropdown_with_server_default_option(self):
        resp = self.client.get(reverse("settings"))
        self.assertContains(resp, "Timezone")
        self.assertContains(resp, "Server default")

    def test_saves_a_valid_timezone(self):
        self.client.post(reverse("save_appearance"), {"timezone": "America/New_York"})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.timezone, "America/New_York")

    def test_rejects_an_invalid_timezone(self):
        self.profile.timezone = "America/New_York"
        self.profile.save(update_fields=["timezone"])
        self.client.post(reverse("save_appearance"), {"timezone": "Not/A_Real_Zone"})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.timezone, "America/New_York")

    def test_blank_timezone_clears_back_to_server_default(self):
        self.profile.timezone = "America/New_York"
        self.profile.save(update_fields=["timezone"])
        self.client.post(reverse("save_appearance"), {"timezone": ""})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.timezone, "")


class ProfileTimezoneMiddlewareTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("tzmiddlewareuser", password="pass12345")
        self.profile = Profile.objects.create(
            user=user, display_name="TzMiddlewareUser", timezone="America/New_York"
        )
        self.client.login(username="tzmiddlewareuser", password="pass12345")

    def test_activates_the_profiles_own_timezone_during_a_request(self):
        from django.utils import timezone as django_timezone

        seen = {}

        def _capture(request):
            seen["tzname"] = django_timezone.get_current_timezone_name()
            from django.http import HttpResponse

            return HttpResponse()

        from .middleware import ProfileTimezoneMiddleware

        middleware = ProfileTimezoneMiddleware(_capture)
        factory_request = self.client.get(reverse("dashboard")).wsgi_request
        middleware(factory_request)
        self.assertEqual(seen["tzname"], "America/New_York")

    def test_blank_profile_timezone_deactivates_to_server_default(self):
        self.profile.timezone = ""
        self.profile.save(update_fields=["timezone"])
        from django.utils import timezone as django_timezone

        from .middleware import ProfileTimezoneMiddleware

        def _capture(request):
            from django.http import HttpResponse

            self.assertEqual(django_timezone.get_current_timezone_name(), django_settings.TIME_ZONE)
            return HttpResponse()

        middleware = ProfileTimezoneMiddleware(_capture)
        factory_request = self.client.get(reverse("dashboard")).wsgi_request
        middleware(factory_request)


class PromoteToOwnerTests(TestCase):
    def setUp(self):
        owner_user = User.objects.create_user("promoteowner", password="pass12345", is_superuser=True)
        self.owner = Profile.objects.create(user=owner_user, display_name="PromoteOwner")
        member_user = User.objects.create_user("promotemember", password="pass12345")
        self.member = Profile.objects.create(user=member_user, display_name="PromoteMember")

    def test_owner_can_promote_a_member(self):
        self.client.login(username="promoteowner", password="pass12345")
        self.client.post(reverse("promote_to_owner", args=[self.member.id]))
        self.member.user.refresh_from_db()
        self.assertTrue(self.member.user.is_superuser)

    def test_member_cannot_promote_anyone(self):
        self.client.login(username="promotemember", password="pass12345")
        other_user = User.objects.create_user("promoteother", password="pass12345")
        other = Profile.objects.create(user=other_user, display_name="PromoteOther")
        self.client.post(reverse("promote_to_owner", args=[other.id]))
        other.user.refresh_from_db()
        self.assertFalse(other.user.is_superuser)

    def test_promoting_logs_an_audit_entry(self):
        self.client.login(username="promoteowner", password="pass12345")
        self.client.post(reverse("promote_to_owner", args=[self.member.id]))
        entry = AdminAuditLogEntry.objects.get(action=AdminAuditLogEntry.Action.PROFILE_PROMOTED)
        self.assertEqual(entry.actor, self.owner)
        self.assertEqual(entry.target_display_name, "PromoteMember")


class DemoteFromOwnerTests(TestCase):
    def setUp(self):
        owner_user = User.objects.create_user("demoteowner", password="pass12345", is_superuser=True)
        self.owner = Profile.objects.create(user=owner_user, display_name="DemoteOwner")
        other_owner_user = User.objects.create_user("demoteotherowner", password="pass12345", is_superuser=True)
        self.other_owner = Profile.objects.create(user=other_owner_user, display_name="DemoteOtherOwner")
        member_user = User.objects.create_user("demotemember", password="pass12345")
        self.member = Profile.objects.create(user=member_user, display_name="DemoteMember")

    def test_owner_can_demote_another_owner(self):
        self.client.login(username="demoteowner", password="pass12345")
        self.client.post(reverse("demote_from_owner", args=[self.other_owner.id]))
        self.other_owner.user.refresh_from_db()
        self.assertFalse(self.other_owner.user.is_superuser)

    def test_member_cannot_demote_anyone(self):
        self.client.login(username="demotemember", password="pass12345")
        self.client.post(reverse("demote_from_owner", args=[self.other_owner.id]))
        self.other_owner.user.refresh_from_db()
        self.assertTrue(self.other_owner.user.is_superuser)

    def test_cannot_demote_self(self):
        self.client.login(username="demoteowner", password="pass12345")
        self.client.post(reverse("demote_from_owner", args=[self.owner.id]))
        self.owner.user.refresh_from_db()
        self.assertTrue(self.owner.user.is_superuser)

    def test_cannot_demote_a_plain_member(self):
        self.client.login(username="demoteowner", password="pass12345")
        resp = self.client.post(reverse("demote_from_owner", args=[self.member.id]), follow=True)
        self.assertContains(resp, "already a member")

    def test_demoting_logs_an_audit_entry(self):
        self.client.login(username="demoteowner", password="pass12345")
        self.client.post(reverse("demote_from_owner", args=[self.other_owner.id]))
        entry = AdminAuditLogEntry.objects.get(action=AdminAuditLogEntry.Action.PROFILE_DEMOTED)
        self.assertEqual(entry.actor, self.owner)
        self.assertEqual(entry.target_display_name, "DemoteOtherOwner")


class AdminDashboardProfileControlsTests(TestCase):
    """Promote/demote/remove render as icon buttons (with tooltips), not
    text links - a crown for promote, a plain ring/circle for demote,
    a trash can for remove."""

    def setUp(self):
        owner_user = User.objects.create_user("iconsowner", password="pass12345", is_superuser=True)
        Profile.objects.create(user=owner_user, display_name="IconsOwner")
        other_owner_user = User.objects.create_user("iconsotherowner", password="pass12345", is_superuser=True)
        Profile.objects.create(user=other_owner_user, display_name="IconsOtherOwner")
        member_user = User.objects.create_user("iconsmember", password="pass12345")
        Profile.objects.create(user=member_user, display_name="IconsMember")
        self.client.login(username="iconsowner", password="pass12345")

    def test_member_row_shows_crown_promote_icon(self):
        resp = self.client.get(reverse("admin_dashboard"))
        self.assertContains(resp, 'title="Promote to owner')
        # crown icon glyph
        self.assertContains(resp, 'd="M11.562 3.266a.5.5 0 0 1 .876 0')

    def test_owner_row_shows_circle_demote_icon(self):
        resp = self.client.get(reverse("admin_dashboard"))
        self.assertContains(resp, 'title="Demote to member')

    def test_every_row_shows_trash_remove_icon(self):
        resp = self.client.get(reverse("admin_dashboard"))
        self.assertContains(resp, 'title="Remove - permanently deletes')
        self.assertContains(resp, 'd="M3 6h18"')  # trash-2 glyph

    def test_no_bare_text_buttons_remain(self):
        resp = self.client.get(reverse("admin_dashboard"))
        self.assertNotContains(resp, ">Promote<")
        self.assertNotContains(resp, ">Remove<")


class DeleteOwnAccountTests(TestCase):
    def setUp(self):
        self.owner_user = User.objects.create_user("selfowner", password="pass12345", is_superuser=True)
        self.owner = Profile.objects.create(user=self.owner_user, display_name="SelfOwner")
        self.member_user = User.objects.create_user("selfmember", password="pass12345")
        self.member = Profile.objects.create(user=self.member_user, display_name="SelfMember")

    def test_member_can_delete_own_account(self):
        self.client.login(username="selfmember", password="pass12345")
        self.client.post(reverse("delete_own_account"))
        self.assertFalse(User.objects.filter(username="selfmember").exists())

    def test_deleting_own_account_logs_out(self):
        self.client.login(username="selfmember", password="pass12345")
        self.client.post(reverse("delete_own_account"))
        resp = self.client.get(reverse("dashboard"))
        self.assertRedirects(resp, f"{reverse('login')}?next={reverse('dashboard')}")

    def test_sole_owner_cannot_delete_own_account(self):
        self.client.login(username="selfowner", password="pass12345")
        self.client.post(reverse("delete_own_account"))
        self.assertTrue(User.objects.filter(username="selfowner").exists())

    def test_owner_can_delete_own_account_once_another_owner_exists(self):
        self.member_user.is_superuser = True
        self.member_user.save(update_fields=["is_superuser"])
        self.client.login(username="selfowner", password="pass12345")
        self.client.post(reverse("delete_own_account"))
        self.assertFalse(User.objects.filter(username="selfowner").exists())

    def test_self_deletion_logs_an_audit_entry_with_actor_nulled_out(self):
        self.client.login(username="selfmember", password="pass12345")
        self.client.post(reverse("delete_own_account"))
        entry = AdminAuditLogEntry.objects.get(action=AdminAuditLogEntry.Action.PROFILE_SELF_DELETED)
        self.assertIsNone(entry.actor)
        self.assertEqual(entry.target_display_name, "SelfMember")


class MyProfileDangerZoneTests(TestCase):
    def setUp(self):
        self.owner_user = User.objects.create_user("dangerowner", password="pass12345", is_superuser=True)
        self.owner = Profile.objects.create(user=self.owner_user, display_name="DangerOwner")
        member_user = User.objects.create_user("dangermember", password="pass12345")
        Profile.objects.create(user=member_user, display_name="DangerMember")

    def test_member_sees_enabled_delete_button(self):
        self.client.login(username="dangermember", password="pass12345")
        resp = self.client.get(reverse("my_profile"))
        self.assertContains(resp, reverse("delete_own_account"))

    def test_sole_owner_sees_disabled_button_and_explanation(self):
        self.client.login(username="dangerowner", password="pass12345")
        resp = self.client.get(reverse("my_profile"))
        self.assertContains(resp, "only owner")
        self.assertNotContains(resp, f'action="{reverse("delete_own_account")}"')


class AdminAuditLogDisplayTests(TestCase):
    def setUp(self):
        owner_user = User.objects.create_user("auditowner", password="pass12345", is_superuser=True)
        self.owner = Profile.objects.create(user=owner_user, display_name="AuditOwner")
        self.client.login(username="auditowner", password="pass12345")

    def test_create_profile_logs_an_entry_shown_on_the_dashboard(self):
        self.client.post(
            reverse("create_profile"),
            {"display_name": "Fresh Person", "username": "freshperson", "password": "pass12345"},
        )
        resp = self.client.get(reverse("admin_dashboard"))
        self.assertContains(resp, "Fresh Person")
        self.assertContains(resp, "profile created")

    def test_remove_profile_logs_an_entry(self):
        member_user = User.objects.create_user("auditmember", password="pass12345")
        member = Profile.objects.create(user=member_user, display_name="AuditMember")
        self.client.post(reverse("delete_profile", args=[member.id]))
        entry = AdminAuditLogEntry.objects.get(action=AdminAuditLogEntry.Action.PROFILE_REMOVED)
        self.assertEqual(entry.target_display_name, "AuditMember")
        resp = self.client.get(reverse("admin_dashboard"))
        self.assertContains(resp, "AuditMember")

    def test_no_activity_yet_message_when_log_is_empty(self):
        resp = self.client.get(reverse("admin_dashboard"))
        self.assertContains(resp, "No activity yet.")


class ForceCredentialChangeTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("admin", password="temp-pass-123", is_superuser=True)
        self.profile = Profile.objects.create(user=user, display_name="Admin", must_change_credentials=True)

    def test_flagged_user_is_redirected_away_from_normal_pages(self):
        self.client.login(username="admin", password="temp-pass-123")
        resp = self.client.get(reverse("dashboard"))
        self.assertRedirects(resp, reverse("change_credentials"))

    def test_flagged_user_can_reach_the_change_form_itself(self):
        self.client.login(username="admin", password="temp-pass-123")
        resp = self.client.get(reverse("change_credentials"))
        self.assertEqual(resp.status_code, 200)

    def test_unflagged_user_is_not_redirected(self):
        self.profile.must_change_credentials = False
        self.profile.save()
        self.client.login(username="admin", password="temp-pass-123")
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_successful_change_updates_username_password_and_clears_flag(self):
        self.client.login(username="admin", password="temp-pass-123")
        resp = self.client.post(
            reverse("change_credentials"),
            {"username": "realuser", "password": "a-real-password", "confirm_password": "a-real-password"},
        )
        self.assertRedirects(resp, reverse("dashboard"))
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.must_change_credentials)
        self.profile.user.refresh_from_db()
        self.assertEqual(self.profile.user.username, "realuser")
        self.assertTrue(self.profile.user.check_password("a-real-password"))

    def test_session_stays_valid_after_password_change(self):
        self.client.login(username="admin", password="temp-pass-123")
        self.client.post(
            reverse("change_credentials"),
            {"username": "realuser", "password": "a-real-password", "confirm_password": "a-real-password"},
        )
        # A stale session-auth-hash would immediately bounce this back to login.
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_mismatched_passwords_rejected(self):
        self.client.login(username="admin", password="temp-pass-123")
        self.client.post(
            reverse("change_credentials"),
            {"username": "realuser", "password": "a-real-password", "confirm_password": "different"},
        )
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.must_change_credentials)

    def test_duplicate_username_rejected(self):
        User.objects.create_user("taken", password="whatever123")
        self.client.login(username="admin", password="temp-pass-123")
        self.client.post(
            reverse("change_credentials"),
            {"username": "taken", "password": "a-real-password", "confirm_password": "a-real-password"},
        )
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.must_change_credentials)


class BootstrapAdminFlagTests(TestCase):
    def test_created_account_is_flagged(self):
        from django.core.management import call_command

        with patch.dict(
            "os.environ", {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "pass12345", "ADMIN_DISPLAY_NAME": "Admin"}
        ):
            call_command("bootstrap_admin")
        profile = Profile.objects.get(user__username="admin")
        self.assertTrue(profile.must_change_credentials)

    def test_attaching_profile_to_preexisting_user_is_not_flagged(self):
        from django.core.management import call_command

        User.objects.create_user("admin", password="whatever-they-set")
        with patch.dict(
            "os.environ", {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "pass12345", "ADMIN_DISPLAY_NAME": "Admin"}
        ):
            call_command("bootstrap_admin")
        profile = Profile.objects.get(user__username="admin")
        self.assertFalse(profile.must_change_credentials)


class SyncLogTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("watcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="Watcher")
        ExternalAccount.objects.create(
            profile=self.profile, provider=ExternalAccount.Provider.TRAKT, access_token="tok"
        )

    @patch("tracker.integrations.trakt.upsert_history_items")
    @patch("tracker.integrations.trakt.fetch_history")
    def test_successful_sync_creates_success_log(self, mock_fetch, mock_upsert):
        mock_fetch.return_value = [{"id": 1}]
        mock_upsert.return_value = 5
        tasks.sync_trakt_history(self.profile.id)
        log = SyncLog.objects.get(profile=self.profile)
        self.assertEqual(log.status, SyncLog.Status.SUCCESS)
        self.assertEqual(log.item_count, 5)
        self.assertEqual(log.provider, ExternalAccount.Provider.TRAKT)
        self.assertIsNotNone(log.finished_at)
        self.assertEqual(log.error_message, "")

    @patch("tracker.integrations.trakt.fetch_history")
    def test_failed_sync_creates_failed_log_and_reraises(self, mock_fetch):
        import requests

        mock_fetch.side_effect = requests.RequestException("network broke")
        with self.assertRaises(requests.RequestException):
            tasks.sync_trakt_history(self.profile.id)
        log = SyncLog.objects.get(profile=self.profile)
        self.assertEqual(log.status, SyncLog.Status.FAILED)
        self.assertIn("network broke", log.error_message)
        self.assertIsNone(log.item_count)

    def test_no_connected_account_does_not_create_log(self):
        ExternalAccount.objects.filter(profile=self.profile).delete()
        tasks.sync_trakt_history(self.profile.id)
        self.assertEqual(SyncLog.objects.count(), 0)

    @patch("tracker.integrations.trakt.fetch_history")
    def test_failed_sync_creates_a_notification(self, mock_fetch):
        import requests

        mock_fetch.side_effect = requests.RequestException("network broke")
        with self.assertRaises(requests.RequestException):
            tasks.sync_trakt_history(self.profile.id)
        n = Notification.objects.get(profile=self.profile)
        self.assertEqual(n.kind, Notification.Kind.SYNC_FAILED)
        self.assertIn("network broke", n.message)

    @patch("tracker.integrations.trakt.fetch_history")
    def test_failed_sync_creates_no_notification_when_disabled(self, mock_fetch):
        import requests

        self.profile.notify_sync_failures = False
        self.profile.save(update_fields=["notify_sync_failures"])
        mock_fetch.side_effect = requests.RequestException("network broke")
        with self.assertRaises(requests.RequestException):
            tasks.sync_trakt_history(self.profile.id)
        self.assertFalse(Notification.objects.exists())


def _http_401():
    import requests

    resp = requests.Response()
    resp.status_code = 401
    return requests.HTTPError(response=resp)


class TraktRefreshAccessTokenTests(TestCase):
    @patch("tracker.integrations.trakt.requests.post")
    def test_posts_refresh_grant_with_stored_redirect_uri(self, mock_post):
        mock_post.return_value = Mock(
            status_code=200,
            json=lambda: {"access_token": "new-tok", "refresh_token": "new-refresh", "expires_in": 7776000},
        )
        mock_post.return_value.raise_for_status = lambda: None
        result = trakt.refresh_access_token("old-refresh", "cid", "csecret", "https://spool.example.com/import/trakt/callback/")
        sent = mock_post.call_args.kwargs["json"]
        self.assertEqual(sent["grant_type"], "refresh_token")
        self.assertEqual(sent["refresh_token"], "old-refresh")
        self.assertEqual(sent["redirect_uri"], "https://spool.example.com/import/trakt/callback/")
        self.assertEqual(result["access_token"], "new-tok")


class SyncTokenRefreshTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("refresher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="Refresher")
        self.account = ExternalAccount.objects.create(
            profile=self.profile,
            provider=ExternalAccount.Provider.TRAKT,
            access_token="stale-tok",
            refresh_token="my-refresh",
            redirect_uri="https://spool.example.com/import/trakt/callback/",
        )

    @patch("tracker.integrations.trakt.refresh_access_token")
    @patch("tracker.integrations.trakt.upsert_history_items")
    @patch("tracker.integrations.trakt.fetch_history")
    def test_401_triggers_refresh_and_retry(self, mock_fetch, mock_upsert, mock_refresh):
        mock_fetch.side_effect = [_http_401(), [{"id": 1}]]
        mock_upsert.return_value = 3
        mock_refresh.return_value = {"access_token": "fresh-tok", "refresh_token": "fresh-refresh", "expires_in": 7776000}

        created = tasks.sync_trakt_history(self.profile.id)

        self.assertEqual(created, 3)
        mock_refresh.assert_called_once_with("my-refresh", *instance_config.get_trakt_credentials(), "https://spool.example.com/import/trakt/callback/")
        self.assertEqual(mock_fetch.call_count, 2)
        self.account.refresh_from_db()
        self.assertEqual(self.account.access_token, "fresh-tok")
        self.assertEqual(self.account.refresh_token, "fresh-refresh")
        log = SyncLog.objects.get(profile=self.profile)
        self.assertEqual(log.status, SyncLog.Status.SUCCESS)

    @patch("tracker.integrations.trakt.fetch_history")
    def test_401_without_refresh_token_propagates_and_fails_log(self, mock_fetch):
        self.account.refresh_token = ""
        self.account.save(update_fields=["refresh_token"])
        mock_fetch.side_effect = _http_401()

        with self.assertRaises(requests.HTTPError):
            tasks.sync_trakt_history(self.profile.id)

        log = SyncLog.objects.get(profile=self.profile)
        self.assertEqual(log.status, SyncLog.Status.FAILED)

    @patch("tracker.integrations.trakt.fetch_history")
    def test_401_without_stored_redirect_uri_propagates(self, mock_fetch):
        self.account.redirect_uri = ""
        self.account.save(update_fields=["redirect_uri"])
        mock_fetch.side_effect = _http_401()

        with self.assertRaises(requests.HTTPError):
            tasks.sync_trakt_history(self.profile.id)

    @patch("tracker.integrations.trakt.refresh_access_token")
    @patch("tracker.integrations.trakt.fetch_history")
    def test_second_401_after_refresh_is_not_retried_again(self, mock_fetch, mock_refresh):
        mock_fetch.side_effect = _http_401()
        mock_refresh.return_value = {"access_token": "fresh-tok", "expires_in": 7776000}

        with self.assertRaises(requests.HTTPError):
            tasks.sync_trakt_history(self.profile.id)

        mock_refresh.assert_called_once()
        self.assertEqual(mock_fetch.call_count, 2)

    @patch("tracker.integrations.trakt.fetch_history")
    def test_non_401_http_error_is_not_treated_as_refreshable(self, mock_fetch):
        import requests as requests_module

        resp = requests_module.Response()
        resp.status_code = 500
        mock_fetch.side_effect = requests_module.HTTPError(response=resp)

        with self.assertRaises(requests.HTTPError):
            tasks.sync_trakt_history(self.profile.id)
        self.assertEqual(mock_fetch.call_count, 1)


class OAuthCallbackRedirectUriTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("connector", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="Connector")
        InstanceConfig.objects.update_or_create(
            pk=1, defaults={"trakt_client_id": "cid", "trakt_client_secret": "csecret"}
        )
        self.client.login(username="connector", password="pass12345")

    @patch("tracker.integrations.trakt.exchange_code")
    def test_stores_the_redirect_uri_used_for_the_exchange(self, mock_exchange):
        mock_exchange.return_value = {"access_token": "tok", "refresh_token": "rtok", "expires_in": 7776000}
        session = self.client.session
        session["trakt_oauth_state"] = "abc123"
        session.save()

        self.client.get(reverse("trakt_callback"), {"code": "authcode", "state": "abc123"})

        account = ExternalAccount.objects.get(profile=self.profile, provider="trakt")
        expected_uri = mock_exchange.call_args[0][1]
        self.assertTrue(expected_uri.endswith(reverse("trakt_callback")))
        self.assertEqual(account.redirect_uri, expected_uri)


class IncrementalSyncTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("incremental", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="Incremental")
        self.account = ExternalAccount.objects.create(
            profile=self.profile, provider=ExternalAccount.Provider.TRAKT, access_token="tok"
        )

    @patch("tracker.integrations.trakt.upsert_history_items")
    @patch("tracker.integrations.trakt.fetch_history")
    def test_first_sync_passes_no_start_at(self, mock_fetch, mock_upsert):
        mock_fetch.return_value = []
        mock_upsert.return_value = 0
        tasks.sync_trakt_history(self.profile.id)
        self.assertIsNone(mock_fetch.call_args.kwargs["start_at"])

    @patch("tracker.integrations.trakt.upsert_history_items")
    @patch("tracker.integrations.trakt.fetch_history")
    def test_second_sync_passes_previous_last_synced_at(self, mock_fetch, mock_upsert):
        import datetime

        mock_fetch.return_value = []
        mock_upsert.return_value = 0
        marker = datetime.datetime(2024, 6, 1, tzinfo=datetime.timezone.utc)
        self.account.last_synced_at = marker
        self.account.save(update_fields=["last_synced_at"])

        tasks.sync_trakt_history(self.profile.id)
        self.assertEqual(mock_fetch.call_args.kwargs["start_at"], marker)

    @patch("tracker.integrations.trakt.upsert_history_items")
    @patch("tracker.integrations.trakt.fetch_history")
    def test_last_synced_at_advances_on_success(self, mock_fetch, mock_upsert):
        mock_fetch.return_value = []
        mock_upsert.return_value = 0
        self.assertIsNone(self.account.last_synced_at)
        tasks.sync_trakt_history(self.profile.id)
        self.account.refresh_from_db()
        self.assertIsNotNone(self.account.last_synced_at)

    @patch("tracker.integrations.trakt.fetch_history")
    def test_last_synced_at_does_not_advance_on_failure(self, mock_fetch):
        import requests

        mock_fetch.side_effect = requests.RequestException("boom")
        with self.assertRaises(requests.RequestException):
            tasks.sync_trakt_history(self.profile.id)
        self.account.refresh_from_db()
        self.assertIsNone(self.account.last_synced_at)


class SyncFailureStreaksTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("streakowner", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="StreakOwner")

    def _log(self, status, provider=ExternalAccount.Provider.TRAKT, error="", profile=None, age_days=0):
        from django.utils import timezone

        log = SyncLog.objects.create(
            profile=profile or self.profile, provider=provider, status=status, error_message=error
        )
        started = timezone.now() - timedelta(days=age_days)
        SyncLog.objects.filter(pk=log.pk).update(started_at=started, finished_at=started)
        return log

    def test_no_logs_means_no_streaks(self):
        self.assertEqual(selectors.sync_failure_streaks(), [])

    def test_all_successes_means_no_streak(self):
        self._log(SyncLog.Status.SUCCESS, age_days=2)
        self._log(SyncLog.Status.SUCCESS, age_days=1)
        self.assertEqual(selectors.sync_failure_streaks(), [])

    def test_single_failure_at_head_is_not_a_streak(self):
        self._log(SyncLog.Status.SUCCESS, age_days=1)
        self._log(SyncLog.Status.FAILED, age_days=0)
        self.assertEqual(selectors.sync_failure_streaks(), [])

    def test_two_consecutive_failures_at_head_counted(self):
        self._log(SyncLog.Status.SUCCESS, age_days=2)
        self._log(SyncLog.Status.FAILED, age_days=1)
        self._log(SyncLog.Status.FAILED, age_days=0)
        streaks = selectors.sync_failure_streaks()
        self.assertEqual(len(streaks), 1)
        self.assertEqual(streaks[0]["count"], 2)
        self.assertEqual(streaks[0]["profile"], self.profile)
        self.assertEqual(streaks[0]["provider"], ExternalAccount.Provider.TRAKT)

    def test_success_at_head_means_no_streak_even_with_older_failures(self):
        self._log(SyncLog.Status.FAILED, age_days=2)
        self._log(SyncLog.Status.FAILED, age_days=1)
        self._log(SyncLog.Status.SUCCESS, age_days=0)
        self.assertEqual(selectors.sync_failure_streaks(), [])

    def test_running_excluded_and_does_not_break_a_streak(self):
        self._log(SyncLog.Status.FAILED, age_days=2)
        self._log(SyncLog.Status.FAILED, age_days=1)
        self._log(SyncLog.Status.RUNNING, age_days=0)
        streaks = selectors.sync_failure_streaks()
        self.assertEqual(len(streaks), 1)
        self.assertEqual(streaks[0]["count"], 2)

    def test_401_error_flagged_as_auth_failure(self):
        self._log(SyncLog.Status.FAILED, error="401 Client Error: Unauthorized for url: ...", age_days=1)
        self._log(SyncLog.Status.FAILED, error="401 Client Error: Unauthorized for url: ...", age_days=0)
        streaks = selectors.sync_failure_streaks()
        self.assertTrue(streaks[0]["looks_like_auth_failure"])

    def test_non_auth_error_not_flagged(self):
        self._log(SyncLog.Status.FAILED, error="Connection timed out", age_days=1)
        self._log(SyncLog.Status.FAILED, error="Connection timed out", age_days=0)
        streaks = selectors.sync_failure_streaks()
        self.assertFalse(streaks[0]["looks_like_auth_failure"])

    def test_pairs_handled_independently(self):
        self._log(SyncLog.Status.FAILED, provider=ExternalAccount.Provider.TRAKT, age_days=1)
        self._log(SyncLog.Status.FAILED, provider=ExternalAccount.Provider.TRAKT, age_days=0)
        self._log(SyncLog.Status.SUCCESS, provider=ExternalAccount.Provider.SIMKL, age_days=0)
        streaks = selectors.sync_failure_streaks()
        self.assertEqual(len(streaks), 1)
        self.assertEqual(streaks[0]["provider"], ExternalAccount.Provider.TRAKT)

    def test_sorted_oldest_streak_first(self):
        other_user = User.objects.create_user("otherstreak", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="OtherStreak")
        self._log(SyncLog.Status.FAILED, age_days=1)
        self._log(SyncLog.Status.FAILED, age_days=0)
        self._log(SyncLog.Status.FAILED, profile=other_profile, age_days=6)
        self._log(SyncLog.Status.FAILED, profile=other_profile, age_days=5)
        streaks = selectors.sync_failure_streaks()
        self.assertEqual(len(streaks), 2)
        self.assertEqual(streaks[0]["profile"], other_profile)
        self.assertEqual(streaks[1]["profile"], self.profile)


class SyncLogViewTests(TestCase):
    def setUp(self):
        from django.utils import timezone

        owner_user = User.objects.create_user("logowner", password="pass12345", is_superuser=True)
        self.owner = Profile.objects.create(user=owner_user, display_name="LogOwner")
        member_user = User.objects.create_user("logmember", password="pass12345")
        self.member = Profile.objects.create(user=member_user, display_name="LogMember")
        log = SyncLog.objects.create(
            profile=self.owner,
            provider=ExternalAccount.Provider.TRAKT,
            status=SyncLog.Status.SUCCESS,
            item_count=3,
        )
        # Pinned safely in the past (rather than "now", set by auto_now_add)
        # so tests that add their own failures at age_days=0/1 land after
        # it - this log staying the oldest, not landing in between two
        # synthetic failures and silently breaking the streak they're
        # testing for.
        old = timezone.now() - timedelta(days=30)
        SyncLog.objects.filter(pk=log.pk).update(started_at=old, finished_at=old)

    def test_non_owner_gets_404(self):
        self.client.login(username="logmember", password="pass12345")
        resp = self.client.get(reverse("sync_log"))
        self.assertEqual(resp.status_code, 404)

    def test_owner_sees_log_entries(self):
        self.client.login(username="logowner", password="pass12345")
        resp = self.client.get(reverse("sync_log"))
        self.assertContains(resp, "LogOwner")
        self.assertContains(resp, "success")

    def test_no_banner_for_a_lone_success(self):
        self.client.login(username="logowner", password="pass12345")
        resp = self.client.get(reverse("sync_log"))
        self.assertNotContains(resp, "has failed")

    def _fail_twice(self, profile, error="401 Client Error: Unauthorized for url: https://api.trakt.tv/x"):
        from django.utils import timezone

        for age in (1, 0):
            log = SyncLog.objects.create(
                profile=profile, provider=ExternalAccount.Provider.TRAKT, status=SyncLog.Status.FAILED,
                error_message=error,
            )
            started = timezone.now() - timedelta(days=age)
            SyncLog.objects.filter(pk=log.pk).update(started_at=started, finished_at=started + timedelta(seconds=0.2))

    def test_banner_and_reconnect_link_shown_for_own_consecutive_failures(self):
        self._fail_twice(self.owner)
        self.client.login(username="logowner", password="pass12345")
        resp = self.client.get(reverse("sync_log"))
        self.assertContains(resp, "has failed 2 times in a row")
        self.assertContains(resp, "Reconnect Trakt")
        self.assertContains(resp, reverse("trakt_connect"))

    def test_banner_shown_without_reconnect_link_for_other_profiles_failures(self):
        self._fail_twice(self.member)
        self.client.login(username="logowner", password="pass12345")
        resp = self.client.get(reverse("sync_log"))
        self.assertContains(resp, "has failed 2 times in a row")
        self.assertContains(resp, "LogMember")
        self.assertNotContains(resp, "Reconnect Trakt")

    def test_full_error_text_present_for_copy_expand(self):
        from django.utils.html import escape

        long_error = "401 Client Error: Unauthorized for url: https://api.trakt.tv/sync/history?limit=200&start_at=2026-07-16T13%3A30%3A00.028Z&page=1"
        self._fail_twice(self.owner, error=long_error)
        self.client.login(username="logowner", password="pass12345")
        resp = self.client.get(reverse("sync_log"))
        self.assertContains(resp, escape(long_error))

    def test_status_icons_rendered_for_success_and_failure(self):
        self._fail_twice(self.owner)
        self.client.login(username="logowner", password="pass12345")
        resp = self.client.get(reverse("sync_log"))
        self.assertGreaterEqual(resp.content.decode().count("<svg"), 3)

    def test_fast_fail_badge_shown_for_subsecond_failure(self):
        self._fail_twice(self.owner)
        self.client.login(username="logowner", password="pass12345")
        resp = self.client.get(reverse("sync_log"))
        self.assertContains(resp, "fast fail")

    def test_fast_fail_badge_not_shown_for_slow_failure(self):
        from django.utils import timezone

        log = SyncLog.objects.create(
            profile=self.owner, provider=ExternalAccount.Provider.TRAKT, status=SyncLog.Status.FAILED,
            error_message="timed out",
        )
        started = timezone.now()
        SyncLog.objects.filter(pk=log.pk).update(started_at=started, finished_at=started + timedelta(seconds=12))
        self.client.login(username="logowner", password="pass12345")
        resp = self.client.get(reverse("sync_log"))
        self.assertNotContains(resp, "fast fail")


class SchedulingTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("scheduled", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="Scheduled")
        self.account = ExternalAccount.objects.create(
            profile=self.profile, provider=ExternalAccount.Provider.TRAKT, access_token="tok"
        )

    def test_creates_periodic_task_matching_defaults(self):
        scheduling.ensure_periodic_task(self.account)
        pt = PeriodicTask.objects.get(name=scheduling.sync_periodic_task_name(self.account))
        self.assertEqual(pt.task, "tracker.tasks.sync_trakt_history")
        self.assertEqual(pt.args, f"[{self.profile.id}]")
        self.assertEqual(pt.crontab.hour, "4")
        self.assertEqual(pt.crontab.minute, "0")
        self.assertEqual(pt.crontab.day_of_month, "*")
        self.assertTrue(pt.enabled)

    def test_every_n_days_uses_day_of_month_step(self):
        self.account.sync_interval_days = 3
        self.account.sync_hour = 9
        self.account.sync_minute = 30
        self.account.save()
        scheduling.ensure_periodic_task(self.account)
        pt = PeriodicTask.objects.get(name=scheduling.sync_periodic_task_name(self.account))
        self.assertEqual(pt.crontab.day_of_month, "*/3")
        self.assertEqual(pt.crontab.hour, "9")
        self.assertEqual(pt.crontab.minute, "30")

    def test_re_running_updates_rather_than_duplicates(self):
        scheduling.ensure_periodic_task(self.account)
        self.account.sync_hour = 15
        self.account.save()
        scheduling.ensure_periodic_task(self.account)
        self.assertEqual(
            PeriodicTask.objects.filter(name=scheduling.sync_periodic_task_name(self.account)).count(), 1
        )
        pt = PeriodicTask.objects.get(name=scheduling.sync_periodic_task_name(self.account))
        self.assertEqual(pt.crontab.hour, "15")

    def test_remove_periodic_task(self):
        scheduling.ensure_periodic_task(self.account)
        scheduling.remove_periodic_task(self.account)
        self.assertFalse(
            PeriodicTask.objects.filter(name=scheduling.sync_periodic_task_name(self.account)).exists()
        )


class EnsureReleaseSyncTaskTests(TestCase):
    def test_creates_the_single_task_with_defaults(self):
        scheduling.ensure_release_sync_task()
        pt = PeriodicTask.objects.get(name=scheduling.RELEASE_SYNC_TASK_NAME)
        self.assertEqual(pt.task, "tracker.tasks.sync_release_schedules")
        self.assertEqual(pt.crontab.hour, "3")
        self.assertEqual(pt.crontab.minute, "0")
        self.assertTrue(pt.enabled)

    def test_re_running_updates_rather_than_duplicates(self):
        scheduling.ensure_release_sync_task()
        scheduling.ensure_release_sync_task(hour=5)
        self.assertEqual(PeriodicTask.objects.filter(name=scheduling.RELEASE_SYNC_TASK_NAME).count(), 1)
        pt = PeriodicTask.objects.get(name=scheduling.RELEASE_SYNC_TASK_NAME)
        self.assertEqual(pt.crontab.hour, "5")


class EnsureReleaseNotificationsTaskTests(TestCase):
    def test_creates_the_single_task_with_defaults(self):
        scheduling.ensure_release_notifications_task()
        pt = PeriodicTask.objects.get(name=scheduling.RELEASE_NOTIFICATIONS_TASK_NAME)
        self.assertEqual(pt.task, "tracker.tasks.generate_release_notifications")
        self.assertEqual(pt.crontab.hour, "3")
        self.assertEqual(pt.crontab.minute, "30")
        self.assertTrue(pt.enabled)

    def test_re_running_updates_rather_than_duplicates(self):
        scheduling.ensure_release_notifications_task()
        scheduling.ensure_release_notifications_task(hour=5)
        self.assertEqual(PeriodicTask.objects.filter(name=scheduling.RELEASE_NOTIFICATIONS_TASK_NAME).count(), 1)
        pt = PeriodicTask.objects.get(name=scheduling.RELEASE_NOTIFICATIONS_TASK_NAME)
        self.assertEqual(pt.crontab.hour, "5")


class EnsureUpdateCheckTaskTests(TestCase):
    def test_creates_the_single_task_with_defaults(self):
        scheduling.ensure_update_check_task()
        pt = PeriodicTask.objects.get(name=scheduling.UPDATE_CHECK_TASK_NAME)
        self.assertEqual(pt.task, "tracker.tasks.check_for_new_version")
        self.assertEqual(pt.crontab.hour, "3")
        self.assertEqual(pt.crontab.minute, "45")
        self.assertTrue(pt.enabled)

    def test_re_running_updates_rather_than_duplicates(self):
        scheduling.ensure_update_check_task()
        scheduling.ensure_update_check_task(hour=5)
        self.assertEqual(PeriodicTask.objects.filter(name=scheduling.UPDATE_CHECK_TASK_NAME).count(), 1)
        pt = PeriodicTask.objects.get(name=scheduling.UPDATE_CHECK_TASK_NAME)
        self.assertEqual(pt.crontab.hour, "5")


class BootstrapPeriodicTasksTests(TestCase):
    def test_creates_task_per_connected_account(self):
        from django.core.management import call_command

        user = User.objects.create_user("bootscheduled", password="pass12345")
        profile = Profile.objects.create(user=user, display_name="BootScheduled")
        account = ExternalAccount.objects.create(
            profile=profile, provider=ExternalAccount.Provider.TRAKT, access_token="tok"
        )
        call_command("bootstrap_periodic_tasks")
        self.assertTrue(
            PeriodicTask.objects.filter(name=scheduling.sync_periodic_task_name(account)).exists()
        )

    def test_also_registers_the_release_sync_task(self):
        from django.core.management import call_command

        call_command("bootstrap_periodic_tasks")
        self.assertTrue(PeriodicTask.objects.filter(name=scheduling.RELEASE_SYNC_TASK_NAME).exists())

    def test_also_registers_the_release_notifications_task(self):
        from django.core.management import call_command

        call_command("bootstrap_periodic_tasks")
        self.assertTrue(PeriodicTask.objects.filter(name=scheduling.RELEASE_NOTIFICATIONS_TASK_NAME).exists())

    def test_also_registers_the_update_check_task(self):
        from django.core.management import call_command

        call_command("bootstrap_periodic_tasks")
        self.assertTrue(PeriodicTask.objects.filter(name=scheduling.UPDATE_CHECK_TASK_NAME).exists())

    def test_removes_old_blanket_job(self):
        from django.core.management import call_command
        from django_celery_beat.models import CrontabSchedule

        schedule = CrontabSchedule.objects.create(minute="0", hour="4")
        PeriodicTask.objects.create(
            name="daily-external-sync", task="tracker.tasks.sync_all_connected_accounts", crontab=schedule
        )
        call_command("bootstrap_periodic_tasks")
        self.assertFalse(PeriodicTask.objects.filter(name="daily-external-sync").exists())

    def test_idempotent_on_rerun(self):
        from django.core.management import call_command

        user = User.objects.create_user("bootscheduled2", password="pass12345")
        profile = Profile.objects.create(user=user, display_name="BootScheduled2")
        ExternalAccount.objects.create(profile=profile, provider=ExternalAccount.Provider.SIMKL, access_token="tok")
        call_command("bootstrap_periodic_tasks")
        call_command("bootstrap_periodic_tasks")
        self.assertEqual(PeriodicTask.objects.filter(task="tracker.tasks.sync_simkl_history").count(), 1)


class CreateProfileOwnerGateTests(TestCase):
    """create_profile previously had no owner check at all - any logged-in
    profile could create an unrelated new account, even though the UI only
    ever showed the delete button to owners. Tightened alongside the admin
    dashboard split, since "admin adds users" was the explicit request."""

    def setUp(self):
        owner_user = User.objects.create_user("createowner", password="pass12345", is_superuser=True)
        Profile.objects.create(user=owner_user, display_name="CreateOwner")
        member_user = User.objects.create_user("creatememberuser", password="pass12345")
        Profile.objects.create(user=member_user, display_name="CreateMember")

    def test_member_cannot_create_profile(self):
        self.client.login(username="creatememberuser", password="pass12345")
        resp = self.client.post(
            reverse("create_profile"),
            {"display_name": "Sneaky", "username": "sneaky", "password": "pass12345"},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(User.objects.filter(username="sneaky").exists())

    def test_owner_can_create_profile(self):
        self.client.login(username="createowner", password="pass12345")
        resp = self.client.post(
            reverse("create_profile"),
            {"display_name": "New Person", "username": "newperson", "password": "pass12345"},
        )
        self.assertRedirects(resp, reverse("admin_dashboard"))
        self.assertTrue(User.objects.filter(username="newperson").exists())

    def test_omitting_avatar_color_gets_a_palette_color_not_a_fixed_default(self):
        self.client.login(username="createowner", password="pass12345")
        self.client.post(
            reverse("create_profile"),
            {"display_name": "No Color Chosen", "username": "nocolorchosen", "password": "pass12345"},
        )
        profile = Profile.objects.get(user__username="nocolorchosen")
        self.assertIn(profile.avatar_color, AVATAR_COLOR_CHOICES)

    def test_explicit_avatar_color_choice_is_respected(self):
        self.client.login(username="createowner", password="pass12345")
        self.client.post(
            reverse("create_profile"),
            {
                "display_name": "Chose A Color",
                "username": "choseacolor",
                "password": "pass12345",
                "avatar_color": "#d67ab1",
            },
        )
        profile = Profile.objects.get(user__username="choseacolor")
        self.assertEqual(profile.avatar_color, "#d67ab1")


class RandomAvatarColorTests(TestCase):
    """Every new profile previously got the same hardcoded avatar_color -
    random_avatar_color() is the model-level default fixing that."""

    def test_prefers_a_color_no_existing_profile_is_using(self):
        used_user = User.objects.create_user("colorused", password="pass12345")
        Profile.objects.create(user=used_user, display_name="ColorUsed", avatar_color=AVATAR_COLOR_CHOICES[0])
        for _ in range(20):
            self.assertNotEqual(random_avatar_color(), AVATAR_COLOR_CHOICES[0])

    def test_falls_back_to_full_palette_once_every_color_is_taken(self):
        for i, color in enumerate(AVATAR_COLOR_CHOICES):
            user = User.objects.create_user(f"colortaken{i}", password="pass12345")
            Profile.objects.create(user=user, display_name=f"ColorTaken{i}", avatar_color=color)
        self.assertIn(random_avatar_color(), AVATAR_COLOR_CHOICES)

    def test_profile_created_without_avatar_color_gets_a_palette_color(self):
        user = User.objects.create_user("nodefaultcolor", password="pass12345")
        profile = Profile.objects.create(user=user, display_name="NoDefaultColor")
        self.assertIn(profile.avatar_color, AVATAR_COLOR_CHOICES)


class SaveSyncScheduleViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("scheduleview", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="ScheduleView")
        self.account = ExternalAccount.objects.create(
            profile=self.profile, provider=ExternalAccount.Provider.TRAKT, access_token="tok"
        )
        self.client.login(username="scheduleview", password="pass12345")

    def test_updates_account_and_periodic_task(self):
        self.client.post(
            reverse("save_sync_schedule", args=["trakt"]),
            {"sync_interval_days": "2", "sync_time": "13:45"},
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.sync_interval_days, 2)
        self.assertEqual(self.account.sync_hour, 13)
        self.assertEqual(self.account.sync_minute, 45)
        pt = PeriodicTask.objects.get(name=scheduling.sync_periodic_task_name(self.account))
        self.assertEqual(pt.crontab.hour, "13")
        self.assertEqual(pt.crontab.minute, "45")

    def test_invalid_time_falls_back_to_default(self):
        self.client.post(
            reverse("save_sync_schedule", args=["trakt"]),
            {"sync_interval_days": "1", "sync_time": "garbage"},
        )
        self.account.refresh_from_db()
        self.assertEqual(self.account.sync_hour, 4)
        self.assertEqual(self.account.sync_minute, 0)

    def test_other_profiles_account_is_not_editable(self):
        other_user = User.objects.create_user("otherschedule", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="OtherSchedule")
        ExternalAccount.objects.create(profile=other_profile, provider=ExternalAccount.Provider.SIMKL, access_token="x")
        self.client.logout()
        self.client.login(username="otherschedule", password="pass12345")
        resp = self.client.post(
            reverse("save_sync_schedule", args=["trakt"]),
            {"sync_interval_days": "2", "sync_time": "13:45"},
        )
        self.assertEqual(resp.status_code, 404)


class TriggerManualSyncViewTests(TestCase):
    """Settings & Import's "Sync now" button - dispatches the same task a
    scheduled sync would, immediately, without touching the schedule
    itself. _dispatch_sync_task_safely is mocked out (rather than left to
    run for real, like OAuthCallbackRedirectUriTests accepts elsewhere)
    since this class only needs to confirm the view calls it correctly,
    not re-prove the broker-hiccup-safety behavior that already has its
    own dedicated coverage."""

    def setUp(self):
        user = User.objects.create_user("manualsyncuser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="ManualSyncUser")
        self.account = ExternalAccount.objects.create(
            profile=self.profile, provider=ExternalAccount.Provider.TRAKT, access_token="tok"
        )
        self.client.login(username="manualsyncuser", password="pass12345")

    @patch("tracker.views._dispatch_sync_task_safely")
    def test_dispatches_the_right_task_for_the_provider(self, mock_dispatch):
        self.client.post(reverse("trigger_manual_sync", args=["trakt"]))
        mock_dispatch.assert_called_once_with(views.tasks.sync_trakt_history, self.profile.id)

    @patch("tracker.views._dispatch_sync_task_safely")
    def test_does_not_change_the_sync_schedule(self, mock_dispatch):
        self.account.sync_interval_days = 3
        self.account.save(update_fields=["sync_interval_days"])
        self.client.post(reverse("trigger_manual_sync", args=["trakt"]))
        self.account.refresh_from_db()
        self.assertEqual(self.account.sync_interval_days, 3)

    def test_404s_for_a_provider_with_no_connected_account(self):
        resp = self.client.post(reverse("trigger_manual_sync", args=["simkl"]))
        self.assertEqual(resp.status_code, 404)

    def test_other_profiles_account_cannot_be_triggered(self):
        other_user = User.objects.create_user("othermanualsync", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="OtherManualSync")
        ExternalAccount.objects.create(profile=other_profile, provider=ExternalAccount.Provider.SIMKL, access_token="x")
        self.client.logout()
        self.client.login(username="othermanualsync", password="pass12345")
        resp = self.client.post(reverse("trigger_manual_sync", args=["trakt"]))
        self.assertEqual(resp.status_code, 404)


class MyProfileViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("myprofileuser", password="original-pass-123")
        self.profile = Profile.objects.create(
            user=user, display_name="Original Name", avatar_color="#e8a63c"
        )
        self.client.login(username="myprofileuser", password="original-pass-123")

    def test_updates_display_name_and_avatar_color(self):
        self.client.post(
            reverse("my_profile"),
            {"action": "update_profile", "display_name": "New Name", "avatar_color": "#3fa9a0"},
        )
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.display_name, "New Name")
        self.assertEqual(self.profile.avatar_color, "#3fa9a0")

    def test_rejects_avatar_color_outside_the_fixed_palette(self):
        self.client.post(
            reverse("my_profile"),
            {"action": "update_profile", "display_name": "New Name", "avatar_color": "#ff0000"},
        )
        self.profile.refresh_from_db()
        # Display name still updates - only the out-of-palette color is rejected.
        self.assertEqual(self.profile.display_name, "New Name")
        self.assertEqual(self.profile.avatar_color, "#e8a63c")

    def test_blank_display_name_rejected(self):
        self.client.post(reverse("my_profile"), {"action": "update_profile", "display_name": ""})
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.display_name, "Original Name")

    def test_change_password_requires_correct_current_password(self):
        self.client.post(
            reverse("my_profile"),
            {
                "action": "change_password",
                "current_password": "wrong-password",
                "new_password": "a-new-password-1",
                "confirm_password": "a-new-password-1",
            },
        )
        self.profile.user.refresh_from_db()
        self.assertTrue(self.profile.user.check_password("original-pass-123"))

    def test_change_password_succeeds_and_keeps_session_valid(self):
        resp = self.client.post(
            reverse("my_profile"),
            {
                "action": "change_password",
                "current_password": "original-pass-123",
                "new_password": "a-new-password-1",
                "confirm_password": "a-new-password-1",
            },
        )
        self.profile.user.refresh_from_db()
        self.assertTrue(self.profile.user.check_password("a-new-password-1"))
        # A stale session-auth-hash would immediately bounce this back to login.
        dashboard_resp = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard_resp.status_code, 200)

    def test_mismatched_new_passwords_rejected(self):
        self.client.post(
            reverse("my_profile"),
            {
                "action": "change_password",
                "current_password": "original-pass-123",
                "new_password": "a-new-password-1",
                "confirm_password": "something-else",
            },
        )
        self.profile.user.refresh_from_db()
        self.assertTrue(self.profile.user.check_password("original-pass-123"))

    def test_updates_bio(self):
        self.client.post(
            reverse("my_profile"),
            {"action": "update_profile", "display_name": "Original Name", "bio": "Watching anything with dragons."},
        )
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.bio, "Watching anything with dragons.")

    def test_bio_is_optional_and_clearable(self):
        self.profile.bio = "Old bio"
        self.profile.save(update_fields=["bio"])
        self.client.post(
            reverse("my_profile"),
            {"action": "update_profile", "display_name": "Original Name", "bio": ""},
        )
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.bio, "")


class MyProfilePageContextTests(TestCase):
    """Member-since date and Trakt/Simkl connected-status badges - the
    account-context strip added under the page heading."""

    def setUp(self):
        user = User.objects.create_user("contextuser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="Context User", avatar_color="#e8a63c")
        self.client.login(username="contextuser", password="pass12345")

    def test_shows_member_since_date(self):
        resp = self.client.get(reverse("my_profile"))
        self.assertContains(resp, f"Member since {self.profile.created_at:%B %Y}")

    def test_shows_not_connected_when_no_external_accounts(self):
        resp = self.client.get(reverse("my_profile"))
        self.assertContains(resp, "Trakt not connected")
        self.assertContains(resp, "Simkl not connected")

    def test_shows_connected_badge_for_a_linked_provider(self):
        ExternalAccount.objects.create(profile=self.profile, provider="trakt")
        resp = self.client.get(reverse("my_profile"))
        self.assertContains(resp, "Trakt connected")
        self.assertContains(resp, "Simkl not connected")


class NotificationsAndPrivacyOnTheMergedSettingsPageTests(TestCase):
    """Settings & Import, My Profile, and Admin Dashboard all render the
    same merged template now (client-side tabs, not separate pages), so
    Notifications/Privacy show up in the raw HTML regardless of which of
    the three URLs was hit - the underlying save_notifications/
    save_privacy endpoints are untouched, only where the forms live."""

    def setUp(self):
        user = User.objects.create_user("movedcarduser", password="pass12345")
        Profile.objects.create(user=user, display_name="MovedCardUser")
        self.client.login(username="movedcarduser", password="pass12345")

    def test_notifications_card_on_my_profile(self):
        resp = self.client.get(reverse("my_profile"))
        self.assertContains(resp, ">Notifications<")
        self.assertContains(resp, reverse("save_notifications"))

    def test_notifications_card_also_on_settings(self):
        # The topbar's bell button (present on every page) carries
        # aria-label="Notifications" - checking for the card heading's
        # exact tag boundaries avoids a false positive on that.
        resp = self.client.get(reverse("settings"))
        self.assertContains(resp, ">Notifications<")
        self.assertContains(resp, reverse("save_notifications"))

    def test_privacy_toggle_on_my_profile_with_multiple_profiles(self):
        other_user = User.objects.create_user("movedcardother", password="pass12345")
        Profile.objects.create(user=other_user, display_name="MovedCardOther")
        resp = self.client.get(reverse("my_profile"))
        self.assertContains(resp, "Show my activity to other profiles")
        self.assertContains(resp, reverse("save_privacy"))

    def test_privacy_toggle_also_on_settings_with_multiple_profiles(self):
        other_user = User.objects.create_user("movedcardother2", password="pass12345")
        Profile.objects.create(user=other_user, display_name="MovedCardOther2")
        resp = self.client.get(reverse("settings"))
        self.assertContains(resp, "Show my activity to other profiles")
        self.assertContains(resp, reverse("save_privacy"))

    def test_privacy_toggle_hidden_on_single_profile_instance(self):
        resp = self.client.get(reverse("my_profile"))
        self.assertNotContains(resp, "Show my activity to other profiles")
        self.assertNotContains(resp, reverse("save_privacy"))


class MergedSettingsPageAccountFormsPostToMyProfileTests(TestCase):
    """The Account tab's "Profile" and "Change password" forms used to
    rely on an implicit <form method="post"> (posting back to whatever
    URL rendered the page) since my_profile.html was the only page that
    ever rendered them. Now that settings_view/my_profile/admin_dashboard
    all render the same template, submitting either form from a page
    loaded at /settings/ or /admin-dashboard/ would silently 404/no-op
    without an explicit action pointing at my_profile - this guards that
    fix stays in place regardless of which of the three URLs rendered
    the page."""

    def setUp(self):
        user = User.objects.create_user("formactionuser", password="pass12345", is_superuser=True)
        Profile.objects.create(user=user, display_name="FormActionUser")
        self.client.login(username="formactionuser", password="pass12345")

    def test_both_account_forms_target_my_profile_from_settings(self):
        resp = self.client.get(reverse("settings"))
        # One for the "Profile" form (update_profile), one for "Change
        # password" (change_password) - both explicit now, neither
        # implicit self-posting.
        self.assertEqual(resp.content.decode().count(f'action="{reverse("my_profile")}"'), 2)

    def test_both_account_forms_target_my_profile_from_admin_dashboard(self):
        resp = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(resp.content.decode().count(f'action="{reverse("my_profile")}"'), 2)


class MyProfileAvatarImageTests(TestCase):
    """Uploading/removing a photo on My Profile - stored under a temp
    MEDIA_ROOT for the duration of each test so nothing lands in (or gets
    deleted from) the real dev media directory."""

    def setUp(self):
        self._media_root = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._media_root)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(shutil.rmtree, self._media_root, True)

        user = User.objects.create_user("avatarimageuser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="Avatar Image User", avatar_color="#e8a63c")
        self.client.login(username="avatarimageuser", password="pass12345")

    def test_valid_image_upload_is_saved(self):
        upload = SimpleUploadedFile("avatar.gif", _tiny_gif_bytes(), content_type="image/gif")
        self.client.post(
            reverse("my_profile"),
            {"action": "update_profile", "display_name": "Avatar Image User", "avatar_image": upload},
        )
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.avatar_image)

    def test_non_image_upload_is_rejected(self):
        upload = SimpleUploadedFile("avatar.jpg", b"this is not actually an image", content_type="image/jpeg")
        resp = self.client.post(
            reverse("my_profile"),
            {"action": "update_profile", "display_name": "Avatar Image User", "avatar_image": upload},
            follow=True,
        )
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.avatar_image)
        self.assertContains(resp, "doesn&#x27;t look like a valid image")

    def test_oversized_upload_is_rejected(self):
        oversized = b"\x00" * (views.MAX_AVATAR_IMAGE_SIZE + 1)
        upload = SimpleUploadedFile("avatar.gif", oversized, content_type="image/gif")
        resp = self.client.post(
            reverse("my_profile"),
            {"action": "update_profile", "display_name": "Avatar Image User", "avatar_image": upload},
            follow=True,
        )
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.avatar_image)
        self.assertContains(resp, "too large")

    def test_remove_photo_clears_the_field_and_deletes_the_file(self):
        upload = SimpleUploadedFile("avatar.gif", _tiny_gif_bytes(), content_type="image/gif")
        self.client.post(
            reverse("my_profile"),
            {"action": "update_profile", "display_name": "Avatar Image User", "avatar_image": upload},
        )
        self.profile.refresh_from_db()
        stored_path = self.profile.avatar_image.path
        self.assertTrue(os.path.exists(stored_path))

        self.client.post(
            reverse("my_profile"),
            {"action": "update_profile", "display_name": "Avatar Image User", "remove_photo": "1"},
        )
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.avatar_image)
        self.assertFalse(os.path.exists(stored_path))

    def test_remove_photo_button_is_an_icon_not_text(self):
        upload = SimpleUploadedFile("avatar.gif", _tiny_gif_bytes(), content_type="image/gif")
        self.client.post(
            reverse("my_profile"),
            {"action": "update_profile", "display_name": "Avatar Image User", "avatar_image": upload},
        )
        resp = self.client.get(reverse("my_profile"))
        self.assertContains(resp, 'name="remove_photo"')
        self.assertContains(resp, 'd="M3 6h18"')  # trash-2 icon glyph
        self.assertNotContains(resp, ">Remove<")

    def test_display_name_and_color_only_update_still_works_unaffected(self):
        self.client.post(
            reverse("my_profile"),
            {"action": "update_profile", "display_name": "Renamed", "avatar_color": "#3fa9a0"},
        )
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.display_name, "Renamed")
        self.assertEqual(self.profile.avatar_color, "#3fa9a0")
        self.assertFalse(self.profile.avatar_image)


class AvatarImageRenderingTests(TestCase):
    """When a profile has an uploaded avatar_image, templates should show
    it (background-image + no initial letter) instead of the color/initial
    fallback - spot-checked on My Profile and the topbar, the two most
    directly touched by this feature."""

    def setUp(self):
        self._media_root = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._media_root)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(shutil.rmtree, self._media_root, True)

        user = User.objects.create_user("renderavataruser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="Render Avatar User", avatar_color="#e8a63c")
        self.profile.avatar_image.save("avatar.gif", io.BytesIO(_tiny_gif_bytes()), save=True)
        self.client.login(username="renderavataruser", password="pass12345")

    def test_my_profile_renders_image_not_initial_fallback(self):
        resp = self.client.get(reverse("my_profile"))
        self.assertContains(resp, "background-image:url(")
        self.assertNotContains(resp, ':style="`background:${color}')

    def test_topbar_active_profile_renders_image_not_initial(self):
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, "background-image:url(")
        # The initial-letter fallback text node for the active-profile button
        # is absent when an image is set.
        initial = self.profile.display_name[0].upper()
        self.assertNotContains(resp, f">{initial}</button>")


class MediaServingTests(TestCase):
    """Confirms the actual /media/<path> route (urls.py's re_path, wired to
    django.views.static.serve) works end-to-end. Deliberately does NOT
    override_settings(MEDIA_ROOT=...) here - urls.py bakes settings.MEDIA_ROOT
    into the urlpattern's document_root kwarg once at URL-conf import time,
    so a later override wouldn't be picked up by routing. Writes into the
    real configured MEDIA_ROOT instead and cleans up the file afterward."""

    def setUp(self):
        user = User.objects.create_user("mediaserveuser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="Media Serve User")
        self.profile.avatar_image.save("avatar.gif", io.BytesIO(_tiny_gif_bytes()), save=True)
        self.addCleanup(self.profile.avatar_image.delete, False)

    def test_uploaded_avatar_is_servable_over_http(self):
        resp = self.client.get(self.profile.avatar_image.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content), _tiny_gif_bytes())


class TopbarAvatarDedupeTests(TestCase):
    def test_single_profile_avatar_not_duplicated_in_topbar(self):
        user = User.objects.create_user("soloprofile", password="pass12345")
        Profile.objects.create(user=user, display_name="Solo", avatar_color="#e8a63c")
        self.client.login(username="soloprofile", password="pass12345")
        resp = self.client.get(reverse("dashboard"))
        # A single-profile household has no "other_profiles" - the Friends
        # dropdown doesn't render at all, so "Solo" only ever appears once,
        # via the active-profile dropdown trigger's own title.
        self.assertEqual(resp.content.decode().count('title="Solo"'), 1)

    def test_multi_profile_shows_others_plus_self(self):
        user = User.objects.create_user("selfprofile", password="pass12345")
        Profile.objects.create(user=user, display_name="Self", avatar_color="#e8a63c")
        other_user = User.objects.create_user("otherprofile", password="pass12345")
        Profile.objects.create(user=other_user, display_name="Other", avatar_color="#3fa9a0")
        self.client.login(username="selfprofile", password="pass12345")
        resp = self.client.get(reverse("dashboard"))
        content = resp.content.decode()
        self.assertEqual(content.count('title="Self"'), 1)
        self.assertEqual(content.count('title="Other"'), 1)


class TopbarMobileLayoutTests(TestCase):
    """Regression coverage for the mobile topbar's icon cluster drifting
    off the right edge - see the col-start-N comment in topbar.html.
    CSS grid auto-placement (not just track sizing) is what actually
    breaks once the middle <nav> is display:none on mobile, so this
    pins down the explicit column assignment that fixes it, plus the
    new mobile search toggle that replaces the desktop-only search bar
    below the xl: breakpoint."""

    def setUp(self):
        user = User.objects.create_user("mobiletopbaruser", password="pass12345")
        Profile.objects.create(user=user, display_name="MobileTopbarUser")
        self.client.login(username="mobiletopbaruser", password="pass12345")

    def test_header_blocks_carry_explicit_grid_column_placement(self):
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, "col-start-1")
        self.assertContains(resp, "col-start-2")
        self.assertContains(resp, "col-start-3")

    def test_mobile_search_toggle_and_panel_present(self):
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, "mobileSearchOpen = true")
        self.assertContains(resp, f'action="{reverse("search")}"')
        # Two forms should post to search now - the desktop inline bar
        # (xl: and up) and the mobile toggle panel (below xl:).
        self.assertEqual(resp.content.decode().count(f'action="{reverse("search")}"'), 2)


class TopbarFriendsDropdownTests(TestCase):
    """The topbar's household-member circles were replaced by a single
    Friends icon that opens a dropdown listing everyone else, each row
    showing when they were last online (Profile.last_seen_at, stamped by
    middleware.LastSeenMiddleware on every authenticated request - see
    LastSeenMiddlewareTests for that machinery itself)."""

    def setUp(self):
        from django.utils import timezone

        self.timezone = timezone
        user = User.objects.create_user("friendsviewer", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="FriendsViewer")
        self.client.login(username="friendsviewer", password="pass12345")

    def test_no_dropdown_on_a_single_profile_instance(self):
        resp = self.client.get(reverse("dashboard"))
        self.assertNotContains(resp, 'aria-label="Friends"')

    def test_dropdown_lists_other_profiles_not_the_viewer(self):
        other_user = User.objects.create_user("frienddropother", password="pass12345")
        Profile.objects.create(user=other_user, display_name="FriendDropOther")
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, 'aria-label="Friends"')
        self.assertContains(resp, "FriendDropOther")
        self.assertEqual(resp.context["other_profiles"].count(), 1)
        self.assertNotIn(self.profile, list(resp.context["other_profiles"]))

    def test_shows_time_since_last_seen_for_an_active_friend(self):
        other_user = User.objects.create_user("frienddropactive", password="pass12345")
        other_profile = Profile.objects.create(
            user=other_user, display_name="FriendDropActive", last_seen_at=self.timezone.now() - timedelta(hours=2)
        )
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, "Active")
        self.assertContains(resp, "ago")
        self.assertIsNotNone(resp.context["other_profiles"].get(pk=other_profile.pk).last_seen_at)

    def test_shows_no_activity_yet_for_a_friend_who_has_never_visited(self):
        other_user = User.objects.create_user("frienddropquiet", password="pass12345")
        Profile.objects.create(user=other_user, display_name="FriendDropQuiet")
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, "No activity yet")

    def test_watching_something_alone_does_not_count_as_being_seen(self):
        # last_seen_at is presence, not watch activity - a friend whose
        # history was only ever synced/imported (never actually visited
        # the app themselves) should still show "No activity yet."
        other_user = User.objects.create_user("frienddropwatcher", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="FriendDropWatcher")
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        WatchEvent.objects.create(profile=other_profile, title=title, watched_at=self.timezone.now())
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, "No activity yet")


class LastSeenMiddlewareTests(TestCase):
    """middleware.LastSeenMiddleware - stamps Profile.last_seen_at on
    every authenticated request, throttled to once a minute so normal
    browsing doesn't turn into a DB write on every single page load."""

    def setUp(self):
        self.user = User.objects.create_user("lastseenuser", password="pass12345")
        self.profile = Profile.objects.create(user=self.user, display_name="LastSeenUser")
        self.client.login(username="lastseenuser", password="pass12345")

    def test_first_request_sets_last_seen_at(self):
        self.assertIsNone(self.profile.last_seen_at)
        self.client.get(reverse("dashboard"))
        self.profile.refresh_from_db()
        self.assertIsNotNone(self.profile.last_seen_at)

    def test_a_stale_timestamp_gets_refreshed(self):
        from django.utils import timezone

        stale = timezone.now() - timedelta(hours=1)
        self.profile.last_seen_at = stale
        self.profile.save(update_fields=["last_seen_at"])
        self.client.get(reverse("dashboard"))
        self.profile.refresh_from_db()
        self.assertGreater(self.profile.last_seen_at, stale)

    def test_a_recent_timestamp_is_not_rewritten_on_every_request(self):
        from django.utils import timezone

        recent = timezone.now()
        self.profile.last_seen_at = recent
        self.profile.save(update_fields=["last_seen_at"])
        self.client.get(reverse("dashboard"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_seen_at, recent)

    def test_anonymous_requests_do_not_error(self):
        self.client.logout()
        resp = self.client.get(reverse("login"))
        self.assertEqual(resp.status_code, 200)


class DisconnectProviderTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("disconnecter", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="Disconnecter")
        self.account = ExternalAccount.objects.create(
            profile=self.profile, provider=ExternalAccount.Provider.TRAKT, access_token="tok"
        )
        scheduling.ensure_periodic_task(self.account)
        self.client.login(username="disconnecter", password="pass12345")

    def test_removes_account_and_periodic_task(self):
        task_name = scheduling.sync_periodic_task_name(self.account)
        self.client.post(reverse("disconnect_provider", args=["trakt"]))
        self.assertFalse(ExternalAccount.objects.filter(pk=self.account.pk).exists())
        self.assertFalse(PeriodicTask.objects.filter(name=task_name).exists())

    def test_does_not_touch_imported_watch_history(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Kept Movie", year=2020)
        WatchEvent.objects.create(profile=self.profile, title=title, watched_at="2024-01-01T00:00:00Z")
        self.client.post(reverse("disconnect_provider", args=["trakt"]))
        self.assertTrue(WatchEvent.objects.filter(profile=self.profile, title=title).exists())

    def test_disconnecting_unconnected_provider_404s(self):
        resp = self.client.post(reverse("disconnect_provider", args=["simkl"]))
        self.assertEqual(resp.status_code, 404)


class ClearWatchHistoryViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("clearer", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="Clearer")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Cleared Movie", year=2020)
        self.client.login(username="clearer", password="pass12345")

    def test_deletes_watch_events_and_progress(self):
        WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at="2024-01-01T00:00:00Z")
        WatchProgress.objects.create(profile=self.profile, title=self.title, status=WatchProgress.Status.WATCHING)
        self.client.post(reverse("clear_watch_history"))
        self.assertFalse(WatchEvent.objects.filter(profile=self.profile).exists())
        self.assertFalse(WatchProgress.objects.filter(profile=self.profile).exists())

    def test_does_not_touch_lists_or_watchlist(self):
        watchlist = WatchList.objects.create(profile=self.profile, name="Watchlist", is_watchlist=True)
        WatchListItem.objects.create(watchlist=watchlist, title=self.title)
        custom_list = WatchList.objects.create(profile=self.profile, name="Favorites")
        WatchListItem.objects.create(watchlist=custom_list, title=self.title)
        self.client.post(reverse("clear_watch_history"))
        self.assertTrue(WatchListItem.objects.filter(watchlist=watchlist, title=self.title).exists())
        self.assertTrue(WatchListItem.objects.filter(watchlist=custom_list, title=self.title).exists())

    def test_does_not_touch_other_profiles_history(self):
        other_user = User.objects.create_user("otherclearer", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="OtherClearer")
        WatchEvent.objects.create(profile=other_profile, title=self.title, watched_at="2024-01-01T00:00:00Z")
        self.client.post(reverse("clear_watch_history"))
        self.assertTrue(WatchEvent.objects.filter(profile=other_profile).exists())

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("clear_watch_history"))
        self.assertEqual(resp.status_code, 302)

    def test_get_not_allowed(self):
        resp = self.client.get(reverse("clear_watch_history"))
        self.assertEqual(resp.status_code, 405)


class DisconnectAndWipeProviderViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("wiper", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="Wiper")
        self.account = ExternalAccount.objects.create(
            profile=self.profile, provider=ExternalAccount.Provider.TRAKT, access_token="tok"
        )
        scheduling.ensure_periodic_task(self.account)
        self.matched_title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Trakt Matched", year=2020, external_ids={"trakt": "123"}
        )
        self.unmatched_title = Title.objects.create(
            media_type=MediaType.MOVIE, name="No Trakt Id", year=2020, external_ids={"simkl": "456"}
        )
        self.client.login(username="wiper", password="pass12345")

    def test_removes_account_and_periodic_task(self):
        task_name = scheduling.sync_periodic_task_name(self.account)
        self.client.post(reverse("disconnect_and_wipe_provider", args=["trakt"]))
        self.assertFalse(ExternalAccount.objects.filter(pk=self.account.pk).exists())
        self.assertFalse(PeriodicTask.objects.filter(name=task_name).exists())

    def test_deletes_only_watch_events_for_titles_matched_via_provider(self):
        WatchEvent.objects.create(profile=self.profile, title=self.matched_title, watched_at="2024-01-01T00:00:00Z")
        WatchEvent.objects.create(profile=self.profile, title=self.unmatched_title, watched_at="2024-01-01T00:00:00Z")
        self.client.post(reverse("disconnect_and_wipe_provider", args=["trakt"]))
        self.assertFalse(WatchEvent.objects.filter(profile=self.profile, title=self.matched_title).exists())
        self.assertTrue(WatchEvent.objects.filter(profile=self.profile, title=self.unmatched_title).exists())

    def test_does_not_touch_other_profiles_history(self):
        other_user = User.objects.create_user("otherwiper", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="OtherWiper")
        WatchEvent.objects.create(profile=other_profile, title=self.matched_title, watched_at="2024-01-01T00:00:00Z")
        self.client.post(reverse("disconnect_and_wipe_provider", args=["trakt"]))
        self.assertTrue(WatchEvent.objects.filter(profile=other_profile, title=self.matched_title).exists())

    def test_disconnecting_unconnected_provider_404s(self):
        resp = self.client.post(reverse("disconnect_and_wipe_provider", args=["simkl"]))
        self.assertEqual(resp.status_code, 404)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("disconnect_and_wipe_provider", args=["trakt"]))
        self.assertEqual(resp.status_code, 302)

    def test_get_not_allowed(self):
        resp = self.client.get(reverse("disconnect_and_wipe_provider", args=["trakt"]))
        self.assertEqual(resp.status_code, 405)


class ProfilePopupViewTests(TestCase):
    def setUp(self):
        viewer_user = User.objects.create_user("popupviewer", password="pass12345")
        Profile.objects.create(user=viewer_user, display_name="PopupViewer")
        target_user = User.objects.create_user("popuptarget", password="pass12345")
        self.target = Profile.objects.create(user=target_user, display_name="PopupTarget", avatar_color="#3fa9a0")
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Watched By Target", year=2020)
        WatchEvent.objects.create(profile=self.target, title=title, watched_at="2024-01-01T00:00:00Z")
        self.client.login(username="popupviewer", password="pass12345")

    def test_shows_target_profiles_name_and_recent_watch(self):
        resp = self.client.get(reverse("profile_popup", args=[self.target.id]))
        self.assertContains(resp, "PopupTarget")
        self.assertContains(resp, "Watched By Target")

    def test_unauthenticated_user_redirected_to_login(self):
        self.client.logout()
        resp = self.client.get(reverse("profile_popup", args=[self.target.id]))
        self.assertEqual(resp.status_code, 302)

    def test_nonexistent_profile_404s(self):
        resp = self.client.get(reverse("profile_popup", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_shows_stats_overview_labels_matching_the_main_stats_page(self):
        resp = self.client.get(reverse("profile_popup", args=[self.target.id]))
        self.assertContains(resp, "Longest streak (days)")
        self.assertContains(resp, "Movies watched / Shows completed")
        self.assertContains(resp, "Total watch time")

    def test_shows_top_genre_chips(self):
        title = Title.objects.get(name="Watched By Target")
        attach_genres(title, ["Action"])
        resp = self.client.get(reverse("profile_popup", args=[self.target.id]))
        self.assertContains(resp, "Action")

    def test_links_to_the_targets_full_stats_page(self):
        resp = self.client.get(reverse("profile_popup", args=[self.target.id]))
        self.assertContains(resp, reverse("member_stats", args=[self.target.id]))

    def test_shows_last_30_days_and_all_time_instead_of_a_pie_chart(self):
        resp = self.client.get(reverse("profile_popup", args=[self.target.id]))
        self.assertContains(resp, "Last 30 days")
        self.assertContains(resp, "All time")
        self.assertContains(resp, "Combined")

    def test_last_30_days_also_gets_its_own_combined_row(self):
        from django.utils import timezone

        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Fresh Movie", year=2024, runtime_minutes=120)
        WatchEvent.objects.create(profile=self.target, title=movie, watched_at=timezone.now())
        resp = self.client.get(reverse("profile_popup", args=[self.target.id]))
        # One "Combined" row under Last 30 days, one under All time.
        self.assertEqual(resp.content.decode().count("Combined"), 2)
        self.assertEqual(resp.context["watch_time_breakdown"]["last_30_days"]["combined"]["hours"], 2)
        self.assertNotContains(resp, "Split by type")


class MemberScopedViewsTests(TestCase):
    """Deep-linking into another household profile's Stats/History from
    the profile popup's "View full stats" link - read-only, same no-
    extra-restriction convention as the popup itself (any logged-in
    profile can view any other profile's stats/history)."""

    def setUp(self):
        viewer_user = User.objects.create_user("statsviewer", password="pass12345")
        self.viewer = Profile.objects.create(user=viewer_user, display_name="StatsViewer")
        target_user = User.objects.create_user("statstarget", password="pass12345")
        self.target = Profile.objects.create(user=target_user, display_name="StatsTarget")
        self.client.login(username="statsviewer", password="pass12345")

    def test_member_stats_is_not_own_and_scoped_to_target(self):
        resp = self.client.get(reverse("member_stats", args=[self.target.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["is_own_stats"])
        self.assertEqual(resp.context["profile"], self.target)
        self.assertContains(resp, "StatsTarget")

    def test_member_stats_links_history_to_member_history(self):
        resp = self.client.get(reverse("member_stats", args=[self.target.id]))
        self.assertEqual(resp.context["history_url"], reverse("member_history", args=[self.target.id]))

    def test_own_stats_is_own_and_links_to_plain_history(self):
        resp = self.client.get(reverse("stats"))
        self.assertTrue(resp.context["is_own_stats"])
        self.assertEqual(resp.context["history_url"], reverse("history"))

    def test_member_stats_404s_for_a_nonexistent_profile(self):
        resp = self.client.get(reverse("member_stats", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_member_stats_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse("member_stats", args=[self.target.id]))
        self.assertNotEqual(resp.status_code, 200)

    def test_member_stats_heatmap_self_links_stay_scoped_to_the_member(self):
        resp = self.client.get(reverse("member_stats_heatmap", args=[self.target.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["heatmap_base_url"], reverse("member_stats_heatmap", args=[self.target.id]))

    def test_own_stats_heatmap_self_links_stay_on_the_plain_url(self):
        resp = self.client.get(reverse("stats_heatmap"))
        self.assertEqual(resp.context["heatmap_base_url"], reverse("stats_heatmap"))

    def test_member_history_is_flagged_read_only_and_hides_bulk_delete(self):
        resp = self.client.get(reverse("member_history", args=[self.target.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context["is_own_history"])
        self.assertNotContains(resp, "selecting = !selecting")
        self.assertNotContains(resp, reverse("history_bulk_delete"))

    def test_own_history_has_the_select_control(self):
        resp = self.client.get(reverse("history"))
        self.assertTrue(resp.context["is_own_history"])
        self.assertContains(resp, "selecting = !selecting")

    def test_member_history_shows_the_targets_events_not_the_viewers(self):
        target_title = Title.objects.create(media_type=MediaType.MOVIE, name="Target's Movie", year=2021)
        WatchEvent.objects.create(profile=self.target, title=target_title, watched_at="2024-01-01T00:00:00Z")
        viewer_title = Title.objects.create(media_type=MediaType.MOVIE, name="Viewer's Movie", year=2021)
        WatchEvent.objects.create(profile=self.viewer, title=viewer_title, watched_at="2024-01-01T00:00:00Z")
        resp = self.client.get(reverse("member_history", args=[self.target.id]))
        self.assertContains(resp, "Target&#x27;s Movie")
        self.assertNotContains(resp, "Viewer&#x27;s Movie")

    def test_member_history_404s_for_a_nonexistent_profile(self):
        resp = self.client.get(reverse("member_history", args=[999999]))
        self.assertEqual(resp.status_code, 404)


class FormatDurationTests(TestCase):
    def test_minutes_only_under_an_hour(self):
        self.assertEqual(selectors.format_duration(45), "45m")

    def test_hours_and_minutes(self):
        self.assertEqual(selectors.format_duration(82), "1h 22m")

    def test_zero_minutes(self):
        self.assertEqual(selectors.format_duration(0), "0m")

    def test_days_hours_minutes(self):
        # 2 days, 17 hours, 58 minutes = 2*1440 + 17*60 + 58 = 2880+1020+58 = 3958
        self.assertEqual(selectors.format_duration(3958), "2d 17h 58m")

    def test_exact_day_still_shows_zero_hours(self):
        self.assertEqual(selectors.format_duration(1440), "1d 0h 0m")


class StatsOverviewMoviesWatchedTests(TestCase):
    """movies_watched previously counted every WatchEvent (plays,
    rewatches included) but was labeled/used as if it were a count of
    distinct movies - confirmed against a real account where the two
    numbers differed by 700+. Now movies_watched is the unique-title
    count and movies_plays is the total, matching how Trakt/Simkl
    present both ("2,034 movies (2,773 plays)")."""

    def setUp(self):
        user = User.objects.create_user("moviecountwatcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="MovieCountWatcher")

    def test_distinguishes_unique_titles_from_total_plays(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Rewatched A Lot", year=2020)
        other = Title.objects.create(media_type=MediaType.MOVIE, name="Watched Once", year=2021)
        for watched_at in ["2024-01-01T00:00:00Z", "2024-02-01T00:00:00Z", "2024-03-01T00:00:00Z"]:
            WatchEvent.objects.create(profile=self.profile, title=movie, watched_at=watched_at)
        WatchEvent.objects.create(profile=self.profile, title=other, watched_at="2024-01-01T00:00:00Z")

        overview = selectors.stats_overview(self.profile)
        self.assertEqual(overview["movies_watched"], 2)  # 2 distinct movies
        self.assertEqual(overview["movies_plays"], 4)  # 4 total watches

    def test_no_movies_watched_is_zero_for_both(self):
        overview = selectors.stats_overview(self.profile)
        self.assertEqual(overview["movies_watched"], 0)
        self.assertEqual(overview["movies_plays"], 0)


class GenreBreakdownTests(TestCase):
    """Stats' "Your top genres" panel (styled after Simkl's own genre
    chart) - selectors.genre_breakdown(profile, media_type, metric),
    sortable by item count or total watch time."""

    def setUp(self):
        user = User.objects.create_user("genrewatcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="GenreWatcher")
        self.action = Genre.objects.create(name="Action")
        self.comedy = Genre.objects.create(name="Comedy")

    def _movie(self, name, genre, runtime=100):
        title = Title.objects.create(media_type=MediaType.MOVIE, name=name, year=2020, runtime_minutes=runtime)
        title.genres.add(genre)
        return title

    def _watch(self, title):
        from django.utils import timezone

        return WatchEvent.objects.create(profile=self.profile, title=title, watched_at=timezone.now())

    def test_items_metric_counts_and_sorts_by_watch_count(self):
        self._watch(self._movie("Action A", self.action))
        self._watch(self._movie("Action B", self.action))
        self._watch(self._movie("Comedy A", self.comedy))

        rows = selectors.genre_breakdown(self.profile, MediaType.MOVIE, metric="items")
        self.assertEqual(rows[0]["name"], "Action")
        self.assertEqual(rows[0]["value"], 2)
        self.assertEqual(rows[0]["pct"], 67)
        self.assertEqual(rows[0]["display"], "2 movies")
        self.assertEqual(rows[1]["name"], "Comedy")
        self.assertEqual(rows[1]["value"], 1)

    def test_duration_metric_sums_runtime_and_formats_compactly(self):
        self._watch(self._movie("Action A", self.action, runtime=90))
        self._watch(self._movie("Action B", self.action, runtime=1500))  # >1 day
        self._watch(self._movie("Comedy A", self.comedy, runtime=30))

        rows = selectors.genre_breakdown(self.profile, MediaType.MOVIE, metric="duration")
        self.assertEqual(rows[0]["name"], "Action")
        self.assertEqual(rows[0]["value"], 1590)
        self.assertEqual(rows[0]["display"], "1d")  # 1590min = 1 day 2h30m, compact format drops to days
        self.assertEqual(rows[1]["display"], "30m")

    def test_tv_and_anime_use_episodes_as_the_items_unit(self):
        show = Title.objects.create(media_type=MediaType.TV, name="A Show", year=2020)
        show.genres.add(self.action)
        self._watch(show)
        rows = selectors.genre_breakdown(self.profile, MediaType.TV, metric="items")
        self.assertEqual(rows[0]["display"], "1 episodes")

    def test_titles_without_a_genre_are_excluded(self):
        no_genre = Title.objects.create(media_type=MediaType.MOVIE, name="No Genre", year=2020)
        self._watch(no_genre)
        rows = selectors.genre_breakdown(self.profile, MediaType.MOVIE, metric="items")
        self.assertEqual(rows, [])

    def test_no_watch_history_returns_empty_list(self):
        rows = selectors.genre_breakdown(self.profile, MediaType.MOVIE, metric="items")
        self.assertEqual(rows, [])

    def test_stats_view_exposes_most_and_least_genre(self):
        self._watch(self._movie("Action A", self.action))
        self._watch(self._movie("Action B", self.action))
        self._watch(self._movie("Comedy A", self.comedy))
        self.client.login(username="genrewatcher", password="pass12345")
        resp = self.client.get(reverse("stats"))
        self.assertEqual(resp.context["most_genre"]["name"], "Action")
        self.assertEqual(resp.context["least_genre"]["name"], "Comedy")

    def test_stats_view_most_least_are_none_without_genre_data(self):
        self.client.login(username="genrewatcher", password="pass12345")
        resp = self.client.get(reverse("stats"))
        self.assertIsNone(resp.context["most_genre"])
        self.assertIsNone(resp.context["least_genre"])


class TopGenresSelectorTests(TestCase):
    """selectors.top_genres - the profile popup's compact, all-media-types
    genre ranking, distinct from genre_breakdown's single-media-type,
    full-Stats-page breakdown."""

    def setUp(self):
        user = User.objects.create_user("topgenrewatcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="TopGenreWatcher")
        self.action = Genre.objects.create(name="Action")
        self.comedy = Genre.objects.create(name="Comedy")
        self.drama = Genre.objects.create(name="Drama")

    def _watch(self, title):
        WatchEvent.objects.create(profile=self.profile, title=title, watched_at="2024-01-01T00:00:00Z")

    def test_combines_every_media_type_into_one_ranking(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="A Movie", year=2020)
        movie.genres.add(self.action)
        show = Title.objects.create(media_type=MediaType.TV, name="A Show", year=2021)
        show.genres.add(self.action)
        anime = Title.objects.create(media_type=MediaType.ANIME, name="An Anime", year=2022)
        anime.genres.add(self.comedy)
        self._watch(movie)
        self._watch(show)
        self._watch(anime)
        rows = selectors.top_genres(self.profile)
        self.assertEqual(rows[0]["name"], "Action")
        self.assertEqual(rows[0]["value"], 2)
        self.assertEqual(rows[1]["name"], "Comedy")

    def test_respects_the_limit(self):
        for genre, name in [(self.action, "M1"), (self.comedy, "M2"), (self.drama, "M3")]:
            movie = Title.objects.create(media_type=MediaType.MOVIE, name=name, year=2020)
            movie.genres.add(genre)
            self._watch(movie)
        rows = selectors.top_genres(self.profile, limit=2)
        self.assertEqual(len(rows), 2)

    def test_no_watch_history_returns_empty_list(self):
        self.assertEqual(selectors.top_genres(self.profile), [])


class EpisodeBrowserSelectorTests(TestCase):
    """selectors.default_season_for_title / watched_episode_numbers, in
    isolation from the title_detail view that consumes them."""

    def setUp(self):
        user = User.objects.create_user("epselectorwatcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="EpSelectorWatcher")
        self.show = Title.objects.create(media_type=MediaType.TV, name="Silo", year=2023)

    def _watch(self, season, episode_num):
        from django.utils import timezone

        ep = Episode.objects.create(title=self.show, season=season, episode=episode_num)
        return WatchEvent.objects.create(profile=self.profile, title=self.show, episode=ep, watched_at=timezone.now())

    def test_no_watch_history_returns_none(self):
        self.assertIsNone(selectors.default_season_for_title(self.profile, self.show))

    def test_picks_the_highest_season_with_any_watched_episode(self):
        self._watch(season=1, episode_num=5)
        self._watch(season=3, episode_num=1)
        self._watch(season=2, episode_num=8)
        self.assertEqual(selectors.default_season_for_title(self.profile, self.show), 3)

    def test_plain_episode_less_watch_events_are_ignored(self):
        WatchEvent.objects.create(profile=self.profile, title=self.show, watched_at="2024-01-01T00:00:00Z")
        self.assertIsNone(selectors.default_season_for_title(self.profile, self.show))

    def test_watched_episode_numbers_scoped_to_the_given_season(self):
        self._watch(season=1, episode_num=1)
        self._watch(season=1, episode_num=3)
        self._watch(season=2, episode_num=1)
        self.assertEqual(selectors.watched_episode_numbers(self.profile, self.show, season=1), {1, 3})
        self.assertEqual(selectors.watched_episode_numbers(self.profile, self.show, season=2), {1})
        self.assertEqual(selectors.watched_episode_numbers(self.profile, self.show, season=99), set())


class DailyBreakdownTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("dailybreakdownwatcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="DailyBreakdownWatcher")

    def _movie(self, name, runtime):
        return Title.objects.create(media_type=MediaType.MOVIE, name=name, year=2020, runtime_minutes=runtime)

    def test_covers_the_last_seven_days_including_today(self):
        from django.utils import timezone

        result = selectors.daily_breakdown(self.profile)
        self.assertEqual(len(result["days"]), 7)
        self.assertEqual(result["days"][-1]["label"], "Today")
        self.assertEqual(result["days"][-1]["date"], timezone.localdate())

    def test_sums_minutes_per_day_and_finds_the_peak(self):
        from django.utils import timezone

        today = timezone.localdate()
        WatchEvent.objects.create(profile=self.profile, title=self._movie("A", 60), watched_at=timezone.now())
        two_days_ago = timezone.make_aware(timezone.datetime.combine(today - timedelta(days=2), timezone.datetime.min.time().replace(hour=20)))
        WatchEvent.objects.create(profile=self.profile, title=self._movie("B", 120), watched_at=two_days_ago)
        WatchEvent.objects.create(profile=self.profile, title=self._movie("C", 90), watched_at=two_days_ago)

        result = selectors.daily_breakdown(self.profile)
        by_date = {d["date"]: d for d in result["days"]}
        self.assertEqual(by_date[today]["minutes"], 60)
        self.assertEqual(by_date[today - timedelta(days=2)]["minutes"], 210)
        self.assertEqual(result["peak_minutes"], 210)
        self.assertEqual(by_date[today - timedelta(days=2)]["height_pct"], 100)
        self.assertEqual(by_date[today]["height_pct"], round(60 / 210 * 100))

    def test_days_with_nothing_watched_are_zero_not_missing(self):
        result = selectors.daily_breakdown(self.profile)
        self.assertTrue(all(d["minutes"] == 0 for d in result["days"]))
        self.assertEqual(result["peak_minutes"], 0)

    def test_events_outside_the_window_are_excluded(self):
        from django.utils import timezone

        old = self._movie("Old", 500)
        WatchEvent.objects.create(profile=self.profile, title=old, watched_at=timezone.now() - timedelta(days=30))
        result = selectors.daily_breakdown(self.profile)
        self.assertEqual(result["peak_minutes"], 0)


class DailyAverageTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("dailyaveragewatcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="DailyAverageWatcher")

    def _movie(self, name, runtime):
        return Title.objects.create(media_type=MediaType.MOVIE, name=name, year=2020, runtime_minutes=runtime)

    def test_average_is_total_over_seven_days(self):
        from django.utils import timezone

        WatchEvent.objects.create(profile=self.profile, title=self._movie("A", 700), watched_at=timezone.now())
        result = selectors.daily_average(self.profile)
        self.assertEqual(result["average_duration"], selectors.format_duration(700 / 7))

    def test_delta_compares_against_the_preceding_window(self):
        from django.utils import timezone

        now = timezone.now()
        WatchEvent.objects.create(profile=self.profile, title=self._movie("This week", 140), watched_at=now)
        WatchEvent.objects.create(
            profile=self.profile, title=self._movie("Last week", 70), watched_at=now - timedelta(days=10)
        )
        result = selectors.daily_average(self.profile)
        self.assertTrue(result["delta_positive"])
        self.assertEqual(result["delta_label"], f"+{selectors.format_duration(round(140 / 7) - round(70 / 7))}")

    def test_no_delta_label_when_nothing_changed(self):
        result = selectors.daily_average(self.profile)
        self.assertIsNone(result["delta_label"])


class PeakHoursTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("peakhourswatcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="PeakHoursWatcher")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)

    def _watch_at_local_hour(self, hour):
        from django.utils import timezone

        tz = timezone.get_current_timezone()
        naive = timezone.datetime.combine(timezone.localdate(), timezone.datetime.min.time().replace(hour=hour))
        WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=timezone.make_aware(naive, tz))

    def test_buckets_by_local_time_of_day(self):
        self._watch_at_local_hour(9)  # morning
        self._watch_at_local_hour(14)  # afternoon
        self._watch_at_local_hour(18)  # evening
        self._watch_at_local_hour(23)  # night

        buckets = {b["label"]: b["count"] for b in selectors.peak_hours(self.profile)}
        self.assertEqual(buckets["Morning"], 1)
        self.assertEqual(buckets["Afternoon"], 1)
        self.assertEqual(buckets["Evening"], 1)
        self.assertEqual(buckets["Night"], 1)

    def test_night_bucket_wraps_past_midnight(self):
        self._watch_at_local_hour(2)  # 2am - still "Night"
        buckets = {b["label"]: b["count"] for b in selectors.peak_hours(self.profile)}
        self.assertEqual(buckets["Night"], 1)

    def test_pct_is_relative_to_the_largest_bucket(self):
        for _ in range(4):
            self._watch_at_local_hour(23)  # night x4
        self._watch_at_local_hour(9)  # morning x1

        buckets = {b["label"]: b for b in selectors.peak_hours(self.profile)}
        self.assertEqual(buckets["Night"]["pct"], 100)
        self.assertEqual(buckets["Morning"]["pct"], round(1 / 4 * 100))

    def test_no_events_returns_all_zero_buckets(self):
        buckets = selectors.peak_hours(self.profile)
        self.assertEqual(len(buckets), 4)
        self.assertTrue(all(b["count"] == 0 and b["pct"] == 0 for b in buckets))

    def test_stats_page_explains_what_the_count_means(self):
        """Peak Hours previously showed a bare, unlabeled integer per
        bucket - "6992" with no unit is meaningless out of context. A
        caption under the heading plus a per-row tooltip should make it
        read as a play count, not a random number."""
        self._watch_at_local_hour(9)
        self.client.login(username="peakhourswatcher", password="pass12345")
        resp = self.client.get(reverse("stats"))
        self.assertContains(resp, "Plays logged in each part of the day")
        self.assertContains(resp, 'title="1 play"')


class MilestoneMessageTests(TestCase):
    def test_streak_milestone_returns_a_message(self):
        self.assertIsNotNone(selectors.milestone_message(streak=7, movies_this_year=3))
        self.assertIsNotNone(selectors.milestone_message(streak=30, movies_this_year=0))
        self.assertIsNotNone(selectors.milestone_message(streak=100, movies_this_year=0))
        self.assertIsNotNone(selectors.milestone_message(streak=365, movies_this_year=0))

    def test_movie_count_milestone_returns_a_message(self):
        self.assertIsNotNone(selectors.milestone_message(streak=0, movies_this_year=25))
        self.assertIsNotNone(selectors.milestone_message(streak=0, movies_this_year=50))
        self.assertIsNotNone(selectors.milestone_message(streak=0, movies_this_year=100))
        self.assertIsNotNone(selectors.milestone_message(streak=0, movies_this_year=200))

    def test_non_milestone_values_return_none(self):
        self.assertIsNone(selectors.milestone_message(streak=1, movies_this_year=1))
        self.assertIsNone(selectors.milestone_message(streak=8, movies_this_year=26))
        self.assertIsNone(selectors.milestone_message(streak=0, movies_this_year=0))

    def test_streak_milestone_takes_priority_over_movie_count(self):
        # both hit on the same day - streak wins
        message = selectors.milestone_message(streak=7, movies_this_year=25)
        self.assertEqual(message, selectors.STREAK_MILESTONES[7])

    def test_milestone_only_fires_on_the_exact_day_reached(self):
        # equality check, not >=, so it doesn't persist on every later visit
        self.assertIsNone(selectors.milestone_message(streak=8, movies_this_year=0))
        self.assertIsNone(selectors.milestone_message(streak=31, movies_this_year=0))


class WatchTimeBreakdownTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("breakdownwatcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="BreakdownWatcher")

    def test_splits_by_type_and_time_window(self):
        from django.utils import timezone

        now = timezone.now()
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Recent Movie", year=2020, runtime_minutes=120)
        old_movie = Title.objects.create(media_type=MediaType.MOVIE, name="Old Movie", year=2010, runtime_minutes=90)
        WatchEvent.objects.create(profile=self.profile, title=movie, watched_at=now - timedelta(days=1))
        WatchEvent.objects.create(profile=self.profile, title=old_movie, watched_at=now - timedelta(days=60))

        breakdown = selectors.watch_time_breakdown(self.profile)
        self.assertEqual(breakdown["last_30_days"][MediaType.MOVIE]["count"], 1)
        self.assertEqual(breakdown["last_30_days"][MediaType.MOVIE]["duration"], "2h 0m")
        self.assertEqual(breakdown["all_time"][MediaType.MOVIE]["count"], 2)
        self.assertEqual(breakdown["all_time"][MediaType.MOVIE]["duration"], "3h 30m")

    def test_tv_uses_episode_runtime(self):
        from django.utils import timezone

        show = Title.objects.create(media_type=MediaType.TV, name="A Show", year=2020)
        ep = Episode.objects.create(title=show, season=1, episode=1, runtime_minutes=42)
        WatchEvent.objects.create(profile=self.profile, title=show, episode=ep, watched_at=timezone.now())
        breakdown = selectors.watch_time_breakdown(self.profile)
        self.assertEqual(breakdown["all_time"][MediaType.TV]["duration"], "42m")
        self.assertEqual(breakdown["all_time"][MediaType.TV]["count"], 1)

    def test_empty_profile_returns_zeros_not_error(self):
        breakdown = selectors.watch_time_breakdown(self.profile)
        for window in ("last_30_days", "all_time"):
            for media_type in (MediaType.MOVIE, MediaType.TV, MediaType.ANIME):
                self.assertEqual(breakdown[window][media_type]["duration"], "0m")
                self.assertEqual(breakdown[window][media_type]["count"], 0)

    def test_combined_sums_across_types_within_each_window(self):
        from django.utils import timezone

        now = timezone.now()
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Recent Movie", year=2020, runtime_minutes=120)
        show = Title.objects.create(media_type=MediaType.TV, name="A Show", year=2020)
        ep = Episode.objects.create(title=show, season=1, episode=1, runtime_minutes=60)
        old_movie = Title.objects.create(media_type=MediaType.MOVIE, name="Old Movie", year=2010, runtime_minutes=90)
        WatchEvent.objects.create(profile=self.profile, title=movie, watched_at=now - timedelta(days=1))
        WatchEvent.objects.create(profile=self.profile, title=show, episode=ep, watched_at=now - timedelta(days=1))
        WatchEvent.objects.create(profile=self.profile, title=old_movie, watched_at=now - timedelta(days=60))

        breakdown = selectors.watch_time_breakdown(self.profile)
        # last_30_days: 120 + 60 = 180min = 3h; all_time: 120+60+90 = 270min = 4.5h -> rounds to 5h
        self.assertEqual(breakdown["last_30_days"]["combined"]["hours"], 3)
        self.assertEqual(breakdown["last_30_days"]["combined"]["days"], round(3 / 24, 1))
        self.assertEqual(breakdown["all_time"]["combined"]["hours"], round(270 / 60))

    def test_empty_profile_combined_is_zero(self):
        breakdown = selectors.watch_time_breakdown(self.profile)
        self.assertEqual(breakdown["last_30_days"]["combined"], {"hours": 0, "days": 0.0})
        self.assertEqual(breakdown["all_time"]["combined"], {"hours": 0, "days": 0.0})


class StatsPageLast30DaysCombinedTests(TestCase):
    """The Stats page's own "Last 30 days" column gets a Combined row too,
    matching the one "All time" already had."""

    def setUp(self):
        from django.utils import timezone

        # Deliberately not named anything containing "Combined" - the
        # topbar renders the profile's own display name (avatar title
        # attribute, dropdown), and a name like "StatsCombinedUser"
        # would produce false-positive matches against the row label
        # this test is actually counting.
        user = User.objects.create_user("statswatcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="StatsWatcher")
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Fresh Movie", year=2024, runtime_minutes=120)
        WatchEvent.objects.create(profile=self.profile, title=movie, watched_at=timezone.now())
        self.client.login(username="statswatcher", password="pass12345")

    def test_both_columns_show_a_combined_row(self):
        resp = self.client.get(reverse("stats"))
        self.assertEqual(resp.content.decode().count("Combined"), 2)

    def test_last_30_days_combined_reflects_the_right_total(self):
        resp = self.client.get(reverse("stats"))
        self.assertEqual(resp.context["watch_time_breakdown"]["last_30_days"]["combined"]["hours"], 2)


class BackfillPostersCommandTests(TestCase):
    """A poster_url=="" -only filter would silently skip titles that
    already have a poster from an earlier run of this command that
    predates it also capturing the TMDB id - confirmed against a real
    library where that left almost everything without an id despite
    already having posters, which then made backfill_completion find
    nothing to do."""

    def _match(self, tmdb_id, poster_url="https://image.tmdb.org/t/p/w500/x.jpg"):
        return {"id": tmdb_id, "kind": "movie", "poster_url": poster_url}

    @patch("tracker.integrations.tmdb.find_match")
    def test_backfills_id_for_title_that_already_has_a_poster(self, mock_find_match):
        from django.core.management import call_command

        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Already Has Poster", year=2020,
            poster_url="https://image.tmdb.org/t/p/w500/existing.jpg",
        )
        mock_find_match.return_value = self._match(42, poster_url="https://image.tmdb.org/t/p/w500/new.jpg")
        call_command("backfill_posters")
        title.refresh_from_db()
        self.assertEqual(title.external_ids.get("tmdb"), "42")
        # Existing poster is left alone - only the missing id gets filled in.
        self.assertEqual(title.poster_url, "https://image.tmdb.org/t/p/w500/existing.jpg")

    @patch("tracker.integrations.tmdb.find_match")
    def test_skips_title_that_already_has_both(self, mock_find_match):
        from django.core.management import call_command

        Title.objects.create(
            media_type=MediaType.MOVIE, name="Fully Done", year=2020,
            poster_url="https://image.tmdb.org/t/p/w500/x.jpg", external_ids={"tmdb": "1"},
        )
        call_command("backfill_posters")
        mock_find_match.assert_not_called()

    @patch("tracker.integrations.tmdb.find_match")
    def test_fills_both_for_title_missing_everything(self, mock_find_match):
        from django.core.management import call_command

        title = Title.objects.create(media_type=MediaType.MOVIE, name="Missing Everything", year=2020)
        mock_find_match.return_value = self._match(7)
        call_command("backfill_posters")
        title.refresh_from_db()
        self.assertEqual(title.external_ids.get("tmdb"), "7")
        self.assertTrue(title.poster_url)


class AttachGenresTests(TestCase):
    """attach_genres() - shared by every import path (Trakt/Simkl/CSV,
    the discover/preview materialize flow, and the backfill_genres
    command) that discovers genre names via a TMDB match."""

    def test_creates_missing_genres_and_sets_them_on_the_title(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="A Movie", year=2020)
        attach_genres(title, ["Action", "Comedy"])
        self.assertEqual(sorted(g.name for g in title.genres.all()), ["Action", "Comedy"])
        self.assertEqual(Genre.objects.count(), 2)

    def test_reuses_an_existing_genre_by_name_instead_of_duplicating(self):
        Genre.objects.create(name="Action")
        title = Title.objects.create(media_type=MediaType.MOVIE, name="A Movie", year=2020)
        attach_genres(title, ["Action"])
        self.assertEqual(Genre.objects.filter(name="Action").count(), 1)

    def test_empty_list_does_nothing(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="A Movie", year=2020)
        attach_genres(title, [])
        self.assertEqual(title.genres.count(), 0)


class ImportPathGenreAttachmentTests(TestCase):
    """Genre-fetching was never wired into any import path before this -
    every synced title had zero genres, forever, regardless of source.
    Each of Trakt/Simkl/CSV's own _get_or_create_title should now attach
    genres for a newly-matched title, fetched via the same TMDB id its
    poster lookup already found."""

    def _match(self, tmdb_id=42, kind="movie"):
        return {"id": tmdb_id, "kind": kind, "poster_url": "https://image.tmdb.org/t/p/w500/x.jpg"}

    def _details(self, genre_names):
        return {"genres": genre_names}

    @patch("tracker.integrations.tmdb.get_full_details")
    @patch("tracker.integrations.tmdb.find_match")
    def test_csv_import_attaches_genres_from_the_tmdb_match(self, mock_find_match, mock_get_full_details):
        mock_find_match.return_value = self._match()
        mock_get_full_details.return_value = self._details(["Action", "Thriller"])
        title = csv_import._get_or_create_title(MediaType.MOVIE, "New Movie", 2020)
        self.assertEqual(sorted(g.name for g in title.genres.all()), ["Action", "Thriller"])

    @patch("tracker.integrations.tmdb.get_full_details")
    @patch("tracker.integrations.tmdb.find_match")
    def test_trakt_import_attaches_genres_from_the_tmdb_match(self, mock_find_match, mock_get_full_details):
        mock_find_match.return_value = self._match()
        mock_get_full_details.return_value = self._details(["Drama"])
        title = trakt._get_or_create_title(MediaType.MOVIE, "New Movie", 2020, trakt_id=99)
        self.assertEqual([g.name for g in title.genres.all()], ["Drama"])

    @patch("tracker.integrations.tmdb.get_full_details")
    @patch("tracker.integrations.tmdb.find_match")
    def test_simkl_import_attaches_genres_from_the_tmdb_match(self, mock_find_match, mock_get_full_details):
        from tracker.integrations import simkl

        mock_find_match.return_value = self._match()
        mock_get_full_details.return_value = self._details(["Comedy"])
        title = simkl._get_or_create_title(MediaType.MOVIE, "New Movie", 2020, simkl_id=99)
        self.assertEqual([g.name for g in title.genres.all()], ["Comedy"])

    @patch("tracker.integrations.tmdb.find_match", return_value=None)
    def test_no_tmdb_match_means_no_genres_and_no_extra_api_call(self, mock_find_match):
        title = csv_import._get_or_create_title(MediaType.MOVIE, "Unmatched Movie", 2020)
        self.assertEqual(title.genres.count(), 0)

    @patch("tracker.integrations.tmdb.get_full_details")
    def test_discover_preview_materialize_attaches_genres(self, mock_get_full_details):
        mock_get_full_details.return_value = {
            "name": "Some Movie",
            "year": "2020",
            "poster_url": "https://image.tmdb.org/t/p/w500/x.jpg",
            "genres": ["Horror", "Mystery"],
        }
        title = views._get_or_create_preview_title("movie", 555)
        self.assertEqual(sorted(g.name for g in title.genres.all()), ["Horror", "Mystery"])


class BackfillGenresCommandTests(TestCase):
    def _details(self, genre_names):
        return {"genres": genre_names}

    @patch("tracker.integrations.tmdb.get_full_details")
    def test_backfills_genres_for_a_title_with_a_tmdb_id(self, mock_get_full_details):
        from django.core.management import call_command

        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Needs Genres", year=2020, external_ids={"tmdb": "10"}
        )
        mock_get_full_details.return_value = self._details(["Action"])
        call_command("backfill_genres")
        self.assertEqual([g.name for g in title.genres.all()], ["Action"])

    @patch("tracker.integrations.tmdb.get_full_details")
    def test_skips_title_without_a_tmdb_id(self, mock_get_full_details):
        from django.core.management import call_command

        Title.objects.create(media_type=MediaType.MOVIE, name="No TMDB Id", year=2020)
        call_command("backfill_genres")
        mock_get_full_details.assert_not_called()

    @patch("tracker.integrations.tmdb.get_full_details")
    def test_skips_title_that_already_has_genres(self, mock_get_full_details):
        from django.core.management import call_command

        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Already Has Genres", year=2020, external_ids={"tmdb": "11"}
        )
        title.genres.add(Genre.objects.create(name="Comedy"))
        call_command("backfill_genres")
        mock_get_full_details.assert_not_called()


class RecomputeIsRewatchTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("rewatcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="Rewatcher")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Rewatched Movie", year=2020)

    def test_earliest_watch_is_not_a_rewatch_others_are(self):
        # Created out of chronological order, mirroring Trakt's own
        # newest-first history order - the fix has to be order-independent.
        newest = WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at="2024-03-01T00:00:00Z")
        oldest = WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at="2024-01-01T00:00:00Z")
        middle = WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at="2024-02-01T00:00:00Z")

        rewatches.recompute_is_rewatch(self.profile, self.title, None)

        oldest.refresh_from_db()
        middle.refresh_from_db()
        newest.refresh_from_db()
        self.assertFalse(oldest.is_rewatch)
        self.assertTrue(middle.is_rewatch)
        self.assertTrue(newest.is_rewatch)

    def test_single_watch_is_never_a_rewatch(self):
        event = WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at="2024-01-01T00:00:00Z")
        rewatches.recompute_is_rewatch(self.profile, self.title, None)
        event.refresh_from_db()
        self.assertFalse(event.is_rewatch)

    def test_corrects_a_wrongly_set_flag(self):
        # Simulates bad data (e.g. from before this existed) - the
        # earliest watch incorrectly flagged as a rewatch gets fixed.
        oldest = WatchEvent.objects.create(
            profile=self.profile, title=self.title, watched_at="2024-01-01T00:00:00Z", is_rewatch=True
        )
        rewatches.recompute_is_rewatch(self.profile, self.title, None)
        oldest.refresh_from_db()
        self.assertFalse(oldest.is_rewatch)


class RewatchImportWiringTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("rewatchimporter", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="RewatchImporter")

    @patch("tracker.integrations.tmdb.find_match", return_value=None)
    def test_trakt_import_marks_rewatch_correctly(self, mock_find_match):
        items = [
            {
                "type": "movie",
                "watched_at": "2024-03-01T00:00:00.000Z",
                "movie": {"title": "Fathom", "year": 2020, "ids": {"trakt": 1}},
            },
            {
                "type": "movie",
                "watched_at": "2024-01-01T00:00:00.000Z",
                "movie": {"title": "Fathom", "year": 2020, "ids": {"trakt": 1}},
            },
        ]
        trakt.upsert_history_items(self.profile, items)
        events = WatchEvent.objects.filter(profile=self.profile).order_by("watched_at")
        self.assertEqual(events.count(), 2)
        self.assertFalse(events[0].is_rewatch)
        self.assertTrue(events[1].is_rewatch)

    @patch("tracker.integrations.tmdb.find_match", return_value=None)
    def test_trakt_reimport_same_watched_at_does_not_duplicate_or_break_rewatch_flags(self, mock_find_match):
        items = [
            {
                "type": "movie",
                "watched_at": "2024-01-01T00:00:00.000Z",
                "movie": {"title": "Fathom", "year": 2020, "ids": {"trakt": 1}},
            },
        ]
        trakt.upsert_history_items(self.profile, items)
        trakt.upsert_history_items(self.profile, items)  # re-sync, same item
        events = WatchEvent.objects.filter(profile=self.profile)
        self.assertEqual(events.count(), 1)
        self.assertFalse(events.first().is_rewatch)


class BackfillRewatchesCommandTests(TestCase):
    def test_fixes_existing_history_across_multiple_profiles_and_titles(self):
        from django.core.management import call_command

        user1 = User.objects.create_user("backfillrewatch1", password="pass12345")
        profile1 = Profile.objects.create(user=user1, display_name="BackfillRewatch1")
        user2 = User.objects.create_user("backfillrewatch2", password="pass12345")
        profile2 = Profile.objects.create(user=user2, display_name="BackfillRewatch2")

        title = Title.objects.create(media_type=MediaType.MOVIE, name="Shared Movie", year=2020)
        e1 = WatchEvent.objects.create(profile=profile1, title=title, watched_at="2024-01-01T00:00:00Z")
        e2 = WatchEvent.objects.create(profile=profile1, title=title, watched_at="2024-02-01T00:00:00Z")
        e3 = WatchEvent.objects.create(profile=profile2, title=title, watched_at="2024-01-15T00:00:00Z")

        call_command("backfill_rewatches")

        e1.refresh_from_db()
        e2.refresh_from_db()
        e3.refresh_from_db()
        self.assertFalse(e1.is_rewatch)
        self.assertTrue(e2.is_rewatch)
        self.assertFalse(e3.is_rewatch)  # profile2's only watch - not a rewatch even though same title


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class TmdbDiscoverTests(TestCase):
    """Class-level LocMemCache override (see TmdbDiscoverCachingTests for
    why it has to be class-level, not per-method) - these tests don't care
    about caching itself, but without a real cache backend every call
    pays _list_request's ~2s unreachable-Redis timeout for nothing."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def _response(self, results, total_pages=1, page=1):
        resp = Mock()
        resp.json.return_value = {"results": results, "page": page, "total_pages": total_pages}
        resp.raise_for_status = Mock()
        return resp

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_normalizes_movie_result_fields(self, mock_get):
        mock_get.return_value = self._response(
            [{"id": 42, "title": "Fathom", "release_date": "2020-05-01", "poster_path": "/x.jpg", "vote_average": 7.5}]
        )
        page = tmdb.discover("movie", category="popular")
        r = page["results"][0]
        self.assertEqual(r["tmdb_id"], 42)
        self.assertEqual(r["name"], "Fathom")
        self.assertEqual(r["year"], "2020")
        self.assertEqual(r["poster_url"], "https://image.tmdb.org/t/p/w500/x.jpg")
        self.assertEqual(r["vote_average"], 7.5)

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_normalizes_tv_result_fields_and_handles_no_poster(self, mock_get):
        mock_get.return_value = self._response(
            [{"id": 99, "name": "Cinder Street", "first_air_date": "2022-01-01", "poster_path": None}]
        )
        page = tmdb.discover("tv", category="popular")
        r = page["results"][0]
        self.assertEqual(r["name"], "Cinder Street")
        self.assertEqual(r["year"], "2022")
        self.assertIsNone(r["poster_url"])

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_top_rated_sets_sort_and_vote_count_floor(self, mock_get):
        mock_get.return_value = self._response([])
        tmdb.discover("movie", category="top_rated")
        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["sort_by"], "vote_average.desc")
        self.assertEqual(params["vote_count.gte"], 200)

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_upcoming_filters_to_future_dates_sorted_ascending(self, mock_get):
        mock_get.return_value = self._response([])
        tmdb.discover("movie", category="upcoming")
        params = mock_get.call_args.kwargs["params"]
        self.assertIn("primary_release_date.gte", params)
        self.assertEqual(params["sort_by"], "primary_release_date.asc")

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_filters_pass_through_to_discover_params(self, mock_get):
        mock_get.return_value = self._response([])
        tmdb.discover(
            "movie",
            category="popular",
            genre_ids=[28, 16],
            year_from=2020,
            year_to=2022,
            runtime_from=90,
            runtime_to=150,
            rating_from=6,
            rating_to=9,
            original_language="en",
            origin_country="US",
            with_companies="420",
        )
        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["with_genres"], "28,16")
        self.assertEqual(params["primary_release_date.gte"], "2020-01-01")
        self.assertEqual(params["primary_release_date.lte"], "2022-12-31")
        self.assertEqual(params["with_runtime.gte"], 90)
        self.assertEqual(params["with_runtime.lte"], 150)
        self.assertEqual(params["vote_average.gte"], 6)
        self.assertEqual(params["vote_average.lte"], 9)
        self.assertEqual(params["with_original_language"], "en")
        self.assertEqual(params["with_origin_country"], "US")
        self.assertEqual(params["with_companies"], "420")

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_always_excludes_adult_and_unsafe_keywords(self, mock_get):
        # Not opt-in - TMDB's own adult=false default isn't reliable
        # enough on its own for anime/hentai (see _UNSAFE_KEYWORD_IDS'
        # own comment for the live-verified reasoning), so every
        # discover() call carries both regardless of what the caller
        # asked for.
        mock_get.return_value = self._response([])
        tmdb.discover("tv", category="popular")
        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["include_adult"], "false")
        self.assertEqual(params["without_keywords"], tmdb._UNSAFE_KEYWORD_IDS)

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_certification_applied_for_movies(self, mock_get):
        mock_get.return_value = self._response([])
        tmdb.discover("movie", category="popular", certification="R")
        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["certification"], "R")
        self.assertEqual(params["certification_country"], "US")

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_certification_is_a_no_op_for_tv(self, mock_get):
        # /discover/tv has no certification filter param at all - passing
        # one through anyway would just be silently ignored by TMDB (or
        # worse, error), so discover() drops it for anything but movies.
        mock_get.return_value = self._response([])
        tmdb.discover("tv", category="popular", certification="R")
        params = mock_get.call_args.kwargs["params"]
        self.assertNotIn("certification", params)
        self.assertNotIn("certification_country", params)

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_status_applied_for_tv(self, mock_get):
        mock_get.return_value = self._response([])
        tmdb.discover("tv", category="popular", status="Ended")
        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["with_status"], tmdb.TV_STATUSES["Ended"])

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_status_is_a_no_op_for_movies(self, mock_get):
        # /discover/movie has no status-filter param at all (verified live -
        # applying with_status there is silently ignored) - mirror image of
        # certification being a movie-only no-op for tv.
        mock_get.return_value = self._response([])
        tmdb.discover("movie", category="popular", status="Ended")
        params = mock_get.call_args.kwargs["params"]
        self.assertNotIn("with_status", params)

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_availability_applied_for_movies_and_tv(self, mock_get):
        mock_get.return_value = self._response([])
        tmdb.discover("movie", category="popular", availability="streaming")
        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["watch_region"], "US")
        self.assertEqual(params["with_watch_monetization_types"], "flatrate|free|ads")

        mock_get.reset_mock()
        tmdb.discover("tv", category="popular", availability="digital")
        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["watch_region"], "US")
        self.assertEqual(params["with_watch_monetization_types"], "flatrate|free|ads|rent|buy")

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_no_availability_param_when_unset(self, mock_get):
        mock_get.return_value = self._response([])
        tmdb.discover("movie", category="popular")
        params = mock_get.call_args.kwargs["params"]
        self.assertNotIn("watch_region", params)
        self.assertNotIn("with_watch_monetization_types", params)

    @override_settings(TMDB_API_KEY="")
    def test_returns_empty_without_api_key(self):
        page = tmdb.discover("movie")
        self.assertEqual(page["results"], [])
        self.assertEqual(page["total_pages"], 0)

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_returns_empty_on_request_exception(self, mock_get):
        import requests

        mock_get.side_effect = requests.RequestException("boom")
        page = tmdb.discover("movie")
        self.assertEqual(page["results"], [])

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_genres_returns_id_name_list(self, mock_get):
        resp = Mock()
        resp.json.return_value = {"genres": [{"id": 16, "name": "Animation"}]}
        resp.raise_for_status = Mock()
        mock_get.return_value = resp
        self.assertEqual(tmdb.genres("movie"), [{"id": 16, "name": "Animation"}])

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_merges_multiple_tmdb_pages_to_fill_a_bigger_grid(self, mock_get):
        # A single TMDB page (20 results) barely fills 2 grid rows -
        # discover() merges RESULTS_PAGE_SIZE (3) consecutive TMDB pages
        # into one logical page instead, so the grid gets ~6 rows worth.
        mock_get.side_effect = [
            self._response([{"id": n, "title": f"Movie {n}", "release_date": "2020-01-01"}], total_pages=5, page=n)
            for n in (1, 2, 3)
        ]
        page = tmdb.discover("movie", category="popular")
        self.assertEqual(mock_get.call_count, 3)
        self.assertEqual([r["tmdb_id"] for r in page["results"]], [1, 2, 3])
        self.assertEqual(page["total_pages"], 2)  # ceil(5 TMDB pages / 3 per merged page)

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_stops_early_when_tmdb_has_fewer_pages_than_the_merge_width(self, mock_get):
        mock_get.side_effect = [
            self._response([{"id": n, "title": f"Movie {n}", "release_date": "2020-01-01"}], total_pages=2, page=n)
            for n in (1, 2)
        ]
        page = tmdb.discover("movie", category="popular")
        self.assertEqual(mock_get.call_count, 2)  # never asks TMDB for a page 3 that can't exist
        self.assertEqual(page["total_pages"], 1)  # ceil(2/3)

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_page_2_starts_at_the_right_tmdb_page_offset(self, mock_get):
        mock_get.return_value = self._response(
            [{"id": 1, "title": "Filler", "release_date": "2020-01-01"}], total_pages=10
        )
        tmdb.discover("movie", category="popular", page=2)
        called_pages = [c.kwargs["params"]["page"] for c in mock_get.call_args_list]
        self.assertEqual(called_pages, [4, 5, 6])

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_upcoming_date_floor_survives_a_slider_at_its_default_lower_bound(self, mock_get):
        # The year range slider always submits a value, even untouched -
        # a wide-open year_from (e.g. 1950) must not push "upcoming"'s
        # gte=today preset backwards in time.
        mock_get.return_value = self._response([])
        tmdb.discover("movie", category="upcoming", year_from=1950)
        params = mock_get.call_args.kwargs["params"]
        import datetime

        self.assertEqual(params["primary_release_date.gte"], datetime.date.today().isoformat())

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_upcoming_date_floor_is_overridden_by_a_stricter_year_from(self, mock_get):
        mock_get.return_value = self._response([])
        tmdb.discover("movie", category="upcoming", year_from=2030)
        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["primary_release_date.gte"], "2030-01-01")


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class TmdbSearchTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def _response(self, results, total_pages=1):
        resp = Mock()
        resp.json.return_value = {"results": results, "total_pages": total_pages}
        resp.raise_for_status = Mock()
        return resp

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_calls_search_multi_with_the_query(self, mock_get):
        mock_get.return_value = self._response([])
        tmdb.search("dune")
        self.assertIn("search/multi", mock_get.call_args.args[0])
        self.assertEqual(mock_get.call_args.kwargs["params"]["query"], "dune")

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_excludes_adult_results(self, mock_get):
        mock_get.return_value = self._response([])
        tmdb.search("dune")
        self.assertEqual(mock_get.call_args.kwargs["params"]["include_adult"], "false")

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_normalizes_movie_and_tv_results_using_their_own_media_type(self, mock_get):
        mock_get.return_value = self._response(
            [
                {"id": 1, "media_type": "movie", "title": "Fathom", "release_date": "2020-05-01", "poster_path": "/x.jpg"},
                {"id": 2, "media_type": "tv", "name": "Cinder Street", "first_air_date": "2022-01-01", "poster_path": None},
            ]
        )
        results = tmdb.search("x")["results"]
        self.assertEqual(results[0]["media_type"], "movie")
        self.assertEqual(results[0]["name"], "Fathom")
        self.assertEqual(results[1]["media_type"], "tv")
        self.assertEqual(results[1]["name"], "Cinder Street")

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_person_results_are_dropped(self, mock_get):
        mock_get.return_value = self._response(
            [{"id": 3, "media_type": "person", "name": "Some Actor"}]
        )
        self.assertEqual(tmdb.search("x")["results"], [])

    @override_settings(TMDB_API_KEY="")
    def test_no_api_key_returns_empty_results(self):
        self.assertEqual(tmdb.search("x")["results"], [])

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_is_anime_flag_set_for_animation_genre_and_japanese_language(self, mock_get):
        mock_get.return_value = self._response(
            [
                {
                    "id": 1, "media_type": "tv", "name": "Naruto", "first_air_date": "2002-10-03",
                    "poster_path": None, "genre_ids": [16, 10759], "original_language": "ja",
                },
                {
                    "id": 2, "media_type": "movie", "title": "Fathom", "release_date": "2020-01-01",
                    "poster_path": None, "genre_ids": [16], "original_language": "en",
                },
            ]
        )
        results = tmdb.search("x")["results"]
        self.assertTrue(results[0]["is_anime"])
        self.assertFalse(results[1]["is_anime"])


class TmdbSplitQueryYearTests(TestCase):
    """_split_query_year - pure string parsing, no network."""

    def test_pulls_a_trailing_year(self):
        self.assertEqual(tmdb._split_query_year("avengers 2012"), ("avengers", 2012))

    def test_multi_word_title_before_the_year(self):
        self.assertEqual(tmdb._split_query_year("the matrix 1999"), ("the matrix", 1999))

    def test_no_year_present(self):
        self.assertEqual(tmdb._split_query_year("avengers"), ("avengers", None))

    def test_a_title_thats_just_a_number_is_not_treated_as_year_qualified(self):
        # "1917" has no text portion before the year token, so it isn't
        # split at all - it's a real movie title, not "<title> <year>".
        self.assertEqual(tmdb._split_query_year("1917"), ("1917", None))

    def test_year_out_of_19xx_20xx_range_is_left_alone(self):
        self.assertEqual(tmdb._split_query_year("blade runner 2150"), ("blade runner 2150", None))


class TmdbAutocorrectedQueryTests(TestCase):
    """_autocorrected_query against the real bundled dictionary - no
    network involved, just confirms the dependency behaves as relied on
    elsewhere (unit tests for search() itself mock this out for
    determinism/speed - see TmdbSearchYearAndAutocorrectTests)."""

    def test_fixes_a_recognized_typo(self):
        self.assertEqual(tmdb._autocorrected_query("avangers"), "avengers")

    def test_leaves_an_unrecognized_proper_noun_untouched(self):
        self.assertIsNone(tmdb._autocorrected_query("sakamoto"))

    def test_returns_none_when_already_correctly_spelled(self):
        self.assertIsNone(tmdb._autocorrected_query("dune"))


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class TmdbSearchYearAndAutocorrectTests(TestCase):
    """search()'s two extra retries (year-qualified, spelling-corrected) -
    _get_spellchecker is mocked throughout for deterministic, network-free
    correction behavior (its real bundled-dictionary behavior is covered
    separately by TmdbAutocorrectedQueryTests). LocMemCache override same
    as TmdbSearchTests - without a real cache backend every call pays
    _list_request's ~2s unreachable-Redis timeout for nothing, multiplied
    by however many /search/* calls a single search() invocation makes
    here."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def _response(self, results, total_pages=1):
        resp = Mock()
        resp.json.return_value = {"results": results, "total_pages": total_pages}
        resp.raise_for_status = Mock()
        return resp

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb._get_spellchecker")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_year_qualified_query_merges_in_the_year_specific_match(self, mock_get, mock_spell):
        mock_spell.return_value = Mock(correction=lambda w: w)

        def side_effect(url, params, timeout):
            if "search/movie" in url:
                return self._response(
                    [{"id": 1, "title": "The Avengers", "release_date": "2012-04-25", "poster_path": None}]
                )
            return self._response([])  # baseline multi + search/tv: nothing

        mock_get.side_effect = side_effect
        results = tmdb.search("avengers 2012")["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "The Avengers")
        self.assertEqual(results[0]["year"], "2012")
        movie_call = next(c for c in mock_get.call_args_list if "search/movie" in c.args[0])
        self.assertEqual(movie_call.kwargs["params"]["query"], "avengers")
        self.assertEqual(movie_call.kwargs["params"]["year"], 2012)

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb._get_spellchecker")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_exact_year_match_is_sorted_before_other_years(self, mock_get, mock_spell):
        mock_spell.return_value = Mock(correction=lambda w: w)

        def side_effect(url, params, timeout):
            if "search/multi" in url:
                return self._response(
                    [{"id": 1, "media_type": "movie", "title": "The Avengers", "release_date": "1998-08-14", "poster_path": None}]
                )
            if "search/movie" in url:
                return self._response(
                    [{"id": 2, "title": "The Avengers", "release_date": "2012-04-25", "poster_path": None}]
                )
            return self._response([])

        mock_get.side_effect = side_effect
        results = tmdb.search("avengers 2012")["results"]
        self.assertEqual(results[0]["tmdb_id"], 2)
        self.assertEqual(results[0]["year"], "2012")
        self.assertEqual(results[1]["tmdb_id"], 1)

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb._get_spellchecker")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_no_year_never_calls_search_movie_or_tv(self, mock_get, mock_spell):
        mock_spell.return_value = Mock(correction=lambda w: w)
        mock_get.return_value = self._response([])
        tmdb.search("dune")
        called_paths = [c.args[0] for c in mock_get.call_args_list]
        self.assertTrue(called_paths)
        self.assertTrue(all("search/multi" in p for p in called_paths))

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb._get_spellchecker")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_typo_retries_with_the_corrected_spelling(self, mock_get, mock_spell):
        mock_spell.return_value = Mock(correction=lambda w: "avengers" if w == "avangers" else w)

        def side_effect(url, params, timeout):
            if params.get("query") == "avengers":
                return self._response(
                    [{"id": 5, "media_type": "movie", "title": "The Avengers", "release_date": "2012-04-25", "poster_path": None}]
                )
            return self._response([])  # baseline "avangers" search: nothing

        mock_get.side_effect = side_effect
        results = tmdb.search("avangers")["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "The Avengers")

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb._get_spellchecker")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_correctly_spelled_query_skips_the_second_call(self, mock_get, mock_spell):
        mock_spell.return_value = Mock(correction=lambda w: w)  # never changes anything
        mock_get.return_value = self._response([])
        tmdb.search("dune")
        self.assertEqual(mock_get.call_count, 1)


class GeminiGenerateTests(TestCase):
    def test_no_key_returns_none_without_a_request(self):
        with patch("tracker.integrations.gemini.requests.post") as mock_post:
            self.assertIsNone(gemini.generate("", "hello"))
        mock_post.assert_not_called()

    @patch("tracker.integrations.gemini.requests.post")
    def test_returns_the_reply_text(self, mock_post):
        resp = Mock()
        resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": "Try Groundhog Day."}]}}]}
        resp.raise_for_status = Mock()
        mock_post.return_value = resp
        self.assertEqual(gemini.generate("test-key", "what should I watch"), "Try Groundhog Day.")
        self.assertEqual(mock_post.call_args.kwargs["params"], {"key": "test-key"})

    @patch("tracker.integrations.gemini.requests.post")
    def test_network_failure_returns_none(self, mock_post):
        mock_post.side_effect = requests.RequestException("boom")
        self.assertIsNone(gemini.generate("test-key", "hi"))

    @patch("tracker.integrations.gemini.requests.post")
    def test_unexpected_response_shape_returns_none(self, mock_post):
        resp = Mock()
        resp.json.return_value = {"unexpected": "shape"}
        resp.raise_for_status = Mock()
        mock_post.return_value = resp
        self.assertIsNone(gemini.generate("test-key", "hi"))


class GeminiPromptTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("promptuser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="PromptUser")

    def test_includes_the_mood(self):
        prompt = gemini.build_recommendation_prompt(self.profile, "something light and funny")
        self.assertIn("something light and funny", prompt)

    def test_includes_recent_watch_history(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Paddington", year=2014)
        WatchEvent.objects.create(profile=self.profile, title=title, watched_at="2024-01-01T00:00:00Z")
        prompt = gemini.build_recommendation_prompt(self.profile, "something cozy")
        self.assertIn("Paddington", prompt)

    def test_no_history_still_produces_a_prompt(self):
        prompt = gemini.build_recommendation_prompt(self.profile, "a thriller")
        self.assertIn("a thriller", prompt)


class RecommendViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("recommenduser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="RecommendUser", gemini_api_key="test-key")
        self.client.login(username="recommenduser", password="pass12345")

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("recommend"), {"mood": "something fun"})
        self.assertEqual(resp.status_code, 302)

    def test_get_not_allowed(self):
        resp = self.client.get(reverse("recommend"))
        self.assertEqual(resp.status_code, 405)

    def test_empty_mood_shows_a_prompt_error(self):
        resp = self.client.post(reverse("recommend"), {"mood": ""})
        self.assertContains(resp, "Type what you")

    def test_no_api_key_shows_a_setup_error(self):
        self.profile.gemini_api_key = ""
        self.profile.save(update_fields=["gemini_api_key"])
        resp = self.client.post(reverse("recommend"), {"mood": "something fun"})
        self.assertContains(resp, "Add a free Gemini API key")

    @patch("tracker.integrations.gemini.generate")
    def test_success_renders_the_reply(self, mock_generate):
        mock_generate.return_value = "Try Paddington."
        resp = self.client.post(reverse("recommend"), {"mood": "something cozy"})
        self.assertContains(resp, "Try Paddington.")
        self.assertEqual(mock_generate.call_args.args[0], "test-key")

    @patch("tracker.integrations.gemini.generate")
    def test_gemini_failure_shows_a_friendly_error(self, mock_generate):
        mock_generate.return_value = None
        resp = self.client.post(reverse("recommend"), {"mood": "something cozy"})
        self.assertContains(resp, "reach Gemini")


@override_settings(
    TMDB_API_KEY="test-key",
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class TmdbCollectionsTests(TestCase):
    """tmdb.collections()/get_collection_details() - the Movies & TV page's
    Collections tab. collections() has no dedicated TMDB endpoint to call
    (see its own docstring), so it scans popular movies' individual detail
    responses for belongs_to_collection."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def _response(self, json_data):
        resp = Mock()
        resp.json.return_value = json_data
        resp.raise_for_status = Mock()
        return resp

    @patch("tracker.integrations.tmdb.requests.get")
    def test_dedupes_and_normalizes_collections_found_on_popular_movies(self, mock_get):
        mock_get.side_effect = [
            self._response({"results": [{"id": 1}, {"id": 2}, {"id": 3}]}),
            self._response({"id": 1, "belongs_to_collection": {"id": 100, "name": "John Wick Collection", "poster_path": "/jw.jpg"}}),
            self._response({"id": 2, "belongs_to_collection": {"id": 100, "name": "John Wick Collection", "poster_path": "/jw.jpg"}}),
            self._response({"id": 3, "belongs_to_collection": None}),
        ]
        rows = tmdb.collections(limit=20, movies_to_scan=3)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], {"id": 100, "name": "John Wick Collection", "poster_url": "https://image.tmdb.org/t/p/w500/jw.jpg"})

    @patch("tracker.integrations.tmdb.requests.get")
    def test_stops_once_the_limit_is_reached(self, mock_get):
        mock_get.side_effect = [
            self._response({"results": [{"id": 1}, {"id": 2}, {"id": 3}]}),
            self._response({"id": 1, "belongs_to_collection": {"id": 100, "name": "Collection A"}}),
            self._response({"id": 2, "belongs_to_collection": {"id": 200, "name": "Collection B"}}),
        ]
        rows = tmdb.collections(limit=2, movies_to_scan=3)
        self.assertEqual(len(rows), 2)
        # the 3rd movie's detail was never fetched once the limit was hit
        self.assertEqual(mock_get.call_count, 3)

    @patch("tracker.integrations.tmdb.requests.get")
    def test_movies_with_no_collection_are_skipped(self, mock_get):
        mock_get.side_effect = [
            self._response({"results": [{"id": 1}]}),
            self._response({"id": 1, "belongs_to_collection": None}),
        ]
        self.assertEqual(tmdb.collections(movies_to_scan=1), [])

    @override_settings(TMDB_API_KEY="")
    def test_returns_empty_without_an_api_key(self):
        self.assertEqual(tmdb.collections(), [])

    @patch("tracker.integrations.tmdb.requests.get")
    def test_returns_collection_details_with_parts_sorted_by_year(self, mock_get):
        mock_get.return_value = self._response(
            {
                "id": 100,
                "name": "John Wick Collection",
                "overview": "An assassin.",
                "poster_path": "/p.jpg",
                "backdrop_path": "/b.jpg",
                "parts": [
                    {"id": 2, "title": "John Wick: Chapter 2", "release_date": "2017-02-10", "poster_path": "/2.jpg"},
                    {"id": 1, "title": "John Wick", "release_date": "2014-10-24", "poster_path": "/1.jpg"},
                ],
            }
        )
        collection = tmdb.get_collection_details(100)
        self.assertEqual(collection["name"], "John Wick Collection")
        self.assertEqual(collection["poster_url"], "https://image.tmdb.org/t/p/w500/p.jpg")
        self.assertEqual(collection["backdrop_url"], "https://image.tmdb.org/t/p/w1280/b.jpg")
        self.assertEqual([p["name"] for p in collection["parts"]], ["John Wick", "John Wick: Chapter 2"])

    @patch("tracker.integrations.tmdb.requests.get")
    def test_returns_none_for_an_unknown_collection(self, mock_get):
        mock_get.return_value = self._response({"success": False, "status_code": 34})
        self.assertIsNone(tmdb.get_collection_details(999999))

    @override_settings(TMDB_API_KEY="")
    def test_get_collection_details_returns_none_without_an_api_key(self):
        self.assertIsNone(tmdb.get_collection_details(100))


@override_settings(
    TMDB_API_KEY="test-key",
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class TmdbDetailPageTests(TestCase):
    """Class-level LocMemCache override for the same reason as
    TmdbDiscoverTests - these don't test caching itself, just avoid
    paying _list_request's unreachable-Redis timeout on every call."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def _response(self, json_data):
        resp = Mock()
        resp.json.return_value = json_data
        resp.raise_for_status = Mock()
        return resp

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_full_details_normalizes_movie_fields(self, mock_get):
        mock_get.return_value = self._response(
            {
                "id": 42,
                "title": "Fathom",
                "release_date": "2020-05-01",
                "overview": "A movie.",
                "tagline": "Deep.",
                "genres": [{"id": 1, "name": "Drama"}, {"id": 2, "name": "Thriller"}],
                "runtime": 118,
                "backdrop_path": "/bd.jpg",
                "poster_path": "/p.jpg",
                "vote_average": 7.2,
                "vote_count": 300,
                "original_language": "en",
            }
        )
        details = tmdb.get_full_details("movie", 42)
        self.assertEqual(details["name"], "Fathom")
        self.assertEqual(details["year"], "2020")
        self.assertEqual(details["genres"], ["Drama", "Thriller"])
        self.assertEqual(details["runtime"], 118)
        self.assertIsNone(details["number_of_seasons"])
        self.assertEqual(details["backdrop_url"], "https://image.tmdb.org/t/p/w1280/bd.jpg")
        self.assertEqual(details["poster_url"], "https://image.tmdb.org/t/p/w500/p.jpg")

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_full_details_normalizes_tv_fields(self, mock_get):
        mock_get.return_value = self._response(
            {
                "id": 99,
                "name": "Cinder Street",
                "first_air_date": "2022-01-01",
                "genres": [],
                "number_of_seasons": 3,
                "number_of_episodes": 24,
            }
        )
        details = tmdb.get_full_details("tv", 99)
        self.assertEqual(details["name"], "Cinder Street")
        self.assertIsNone(details["runtime"])
        self.assertEqual(details["number_of_seasons"], 3)
        self.assertEqual(details["number_of_episodes"], 24)

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_full_details_includes_raw_status(self, mock_get):
        mock_get.return_value = self._response(
            {
                "id": 99, "name": "Cinder Street", "first_air_date": "2022-01-01",
                "genres": [], "status": "Returning Series",
            }
        )
        details = tmdb.get_full_details("tv", 99)
        self.assertEqual(details["status"], "Returning Series")

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_full_details_extracts_next_episode_to_air(self, mock_get):
        mock_get.return_value = self._response(
            {
                "id": 99, "name": "Cinder Street", "first_air_date": "2022-01-01", "genres": [],
                "next_episode_to_air": {
                    "air_date": "2026-08-01", "season_number": 2, "episode_number": 1, "name": "Return",
                },
            }
        )
        details = tmdb.get_full_details("tv", 99)
        self.assertEqual(
            details["next_episode_to_air"],
            {"air_date": "2026-08-01", "season_number": 2, "episode_number": 1, "name": "Return"},
        )

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_full_details_next_episode_null_becomes_none(self, mock_get):
        mock_get.return_value = self._response(
            {
                "id": 99, "name": "Cinder Street", "first_air_date": "2022-01-01", "genres": [],
                "next_episode_to_air": None,
            }
        )
        details = tmdb.get_full_details("tv", 99)
        self.assertIsNone(details["next_episode_to_air"])

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_full_details_next_episode_without_air_date_becomes_none(self, mock_get):
        mock_get.return_value = self._response(
            {
                "id": 99, "name": "Cinder Street", "first_air_date": "2022-01-01", "genres": [],
                "next_episode_to_air": {"season_number": 2, "episode_number": 1},
            }
        )
        details = tmdb.get_full_details("tv", 99)
        self.assertIsNone(details["next_episode_to_air"])

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_full_details_movie_release_date_distinct_from_year(self, mock_get):
        mock_get.return_value = self._response(
            {"id": 42, "title": "Fathom", "release_date": "2026-12-25", "genres": []}
        )
        details = tmdb.get_full_details("movie", 42)
        self.assertEqual(details["release_date"], "2026-12-25")
        self.assertEqual(details["year"], "2026")

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_full_details_tv_has_no_release_date(self, mock_get):
        mock_get.return_value = self._response(
            {"id": 99, "name": "Cinder Street", "first_air_date": "2022-01-01", "genres": []}
        )
        details = tmdb.get_full_details("tv", 99)
        self.assertIsNone(details["release_date"])

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_full_details_returns_none_on_missing_id(self, mock_get):
        mock_get.return_value = self._response({})
        self.assertIsNone(tmdb.get_full_details("movie", 1))

    @override_settings(TMDB_API_KEY="")
    def test_get_full_details_returns_none_without_api_key(self):
        self.assertIsNone(tmdb.get_full_details("movie", 1))

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_full_details_returns_none_on_request_exception(self, mock_get):
        import requests

        mock_get.side_effect = requests.RequestException("boom")
        self.assertIsNone(tmdb.get_full_details("movie", 1))

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_credits_returns_billing_ordered_cast(self, mock_get):
        mock_get.return_value = self._response(
            {
                "cast": [
                    {"name": "Actor One", "character": "Hero", "profile_path": "/a.jpg"},
                    {"name": "Actor Two", "character": "Villain", "profile_path": None},
                ]
            }
        )
        cast = tmdb.get_credits("movie", 42)
        self.assertEqual(len(cast), 2)
        self.assertEqual(cast[0], {"name": "Actor One", "character": "Hero", "profile_url": "https://image.tmdb.org/t/p/w185/a.jpg"})
        self.assertIsNone(cast[1]["profile_url"])

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_credits_respects_limit(self, mock_get):
        mock_get.return_value = self._response({"cast": [{"name": f"Actor {i}"} for i in range(20)]})
        self.assertEqual(len(tmdb.get_credits("movie", 42, limit=5)), 5)

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_credits_returns_empty_list_on_failure(self, mock_get):
        import requests

        mock_get.side_effect = requests.RequestException("boom")
        self.assertEqual(tmdb.get_credits("movie", 1), [])

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_similar_normalizes_like_discover_does(self, mock_get):
        mock_get.return_value = self._response(
            {"results": [{"id": 7, "title": "Similar Movie", "release_date": "2019-01-01", "vote_average": 6.5}]}
        )
        similar = tmdb.get_similar("movie", 42)
        self.assertEqual(similar[0]["tmdb_id"], 7)
        self.assertEqual(similar[0]["name"], "Similar Movie")
        self.assertEqual(similar[0]["year"], "2019")

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_similar_returns_empty_list_on_failure(self, mock_get):
        import requests

        mock_get.side_effect = requests.RequestException("boom")
        self.assertEqual(tmdb.get_similar("movie", 1), [])

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_director_finds_the_director_job_in_crew(self, mock_get):
        mock_get.return_value = self._response(
            {
                "crew": [
                    {"name": "Editor Person", "job": "Editor"},
                    {"name": "Director Person", "job": "Director", "profile_path": "/d.jpg"},
                ]
            }
        )
        director = tmdb.get_director("movie", 42)
        self.assertEqual(director["name"], "Director Person")
        self.assertEqual(director["profile_url"], "https://image.tmdb.org/t/p/w185/d.jpg")

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_director_profile_url_is_none_without_a_photo(self, mock_get):
        mock_get.return_value = self._response({"crew": [{"name": "Director Person", "job": "Director"}]})
        self.assertIsNone(tmdb.get_director("movie", 42)["profile_url"])

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_director_returns_none_when_no_director_credited(self, mock_get):
        mock_get.return_value = self._response({"crew": [{"name": "Editor Person", "job": "Editor"}]})
        self.assertIsNone(tmdb.get_director("movie", 42))

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_director_returns_none_on_failure(self, mock_get):
        import requests

        mock_get.side_effect = requests.RequestException("boom")
        self.assertIsNone(tmdb.get_director("movie", 1))

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_season_details_normalizes_episodes(self, mock_get):
        mock_get.return_value = self._response(
            {
                "id": 99,
                "episodes": [
                    {"episode_number": 1, "name": "Pilot", "still_path": "/e1.jpg", "air_date": "2020-01-01"},
                    {"episode_number": 2, "name": "Second", "still_path": None, "air_date": "2020-01-08"},
                ],
            }
        )
        season = tmdb.get_season_details(555, 1)
        self.assertEqual(len(season["episodes"]), 2)
        self.assertEqual(season["episodes"][0]["still_url"], "https://image.tmdb.org/t/p/w500/e1.jpg")
        self.assertIsNone(season["episodes"][1]["still_url"])
        self.assertEqual(season["episodes"][1]["name"], "Second")

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_season_details_carries_each_episodes_own_runtime(self, mock_get):
        mock_get.return_value = self._response(
            {
                "id": 99,
                "episodes": [
                    {"episode_number": 1, "name": "Pilot", "still_path": None, "air_date": None, "runtime": 42},
                    {"episode_number": 2, "name": "Second", "still_path": None, "air_date": None, "runtime": None},
                ],
            }
        )
        season = tmdb.get_season_details(555, 1)
        self.assertEqual(season["episodes"][0]["runtime"], 42)
        self.assertIsNone(season["episodes"][1]["runtime"])

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_season_details_carries_each_episodes_vote_average(self, mock_get):
        mock_get.return_value = self._response(
            {
                "id": 99,
                "episodes": [
                    {"episode_number": 1, "name": "Pilot", "still_path": None, "air_date": None, "vote_average": 8.4},
                    {"episode_number": 2, "name": "Second", "still_path": None, "air_date": None, "vote_average": 0},
                ],
            }
        )
        season = tmdb.get_season_details(555, 1)
        self.assertEqual(season["episodes"][0]["vote_average"], 8.4)
        self.assertEqual(season["episodes"][1]["vote_average"], 0)

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_season_details_returns_none_on_missing_id(self, mock_get):
        mock_get.return_value = self._response({})
        self.assertIsNone(tmdb.get_season_details(555, 1))

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_season_details_returns_none_on_request_exception(self, mock_get):
        import requests

        mock_get.side_effect = requests.RequestException("boom")
        self.assertIsNone(tmdb.get_season_details(555, 1))

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_watch_providers_flattens_flatrate_free_and_ads(self, mock_get):
        mock_get.return_value = self._response(
            {
                "results": {
                    "US": {
                        "flatrate": [{"provider_name": "Netflix", "logo_path": "/n.jpg"}],
                        "free": [{"provider_name": "Tubi", "logo_path": None}],
                        "ads": [{"provider_name": "Netflix", "logo_path": "/n.jpg"}],
                    }
                }
            }
        )
        providers = tmdb.get_watch_providers("movie", 42)
        self.assertEqual([p["name"] for p in providers], ["Netflix", "Tubi"])  # de-duplicated
        self.assertEqual(providers[0]["logo_url"], "https://image.tmdb.org/t/p/w185/n.jpg")
        self.assertIsNone(providers[1]["logo_url"])

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_watch_providers_returns_empty_for_missing_region(self, mock_get):
        mock_get.return_value = self._response({"results": {"DE": {"flatrate": [{"provider_name": "Amazon"}]}}})
        self.assertEqual(tmdb.get_watch_providers("movie", 42, region="US"), [])

    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_watch_providers_returns_empty_list_on_failure(self, mock_get):
        import requests

        mock_get.side_effect = requests.RequestException("boom")
        self.assertEqual(tmdb.get_watch_providers("movie", 1), [])


class TmdbMediaTypeForTests(TestCase):
    def test_tmdb_kind_is_authoritative_when_present(self):
        anime = Title.objects.create(
            media_type=MediaType.ANIME, name="Anime Show", year=2020,
            external_ids={"tmdb": "1", "tmdb_kind": "tv"},
        )
        self.assertEqual(tmdb.media_type_for(anime), "tv")

    def test_falls_back_to_media_type_when_no_tmdb_kind(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="A Movie", year=2020)
        tv = Title.objects.create(media_type=MediaType.TV, name="A Show", year=2020)
        self.assertEqual(tmdb.media_type_for(movie), "movie")
        self.assertEqual(tmdb.media_type_for(tv), "tv")


class TmdbStatusBadgeTests(TestCase):
    def test_ongoing_show_maps_to_a_success_badge(self):
        self.assertEqual(tmdb.status_badge("Returning Series"), {"label": "Ongoing", "color": "success"})

    def test_cancelled_show_maps_to_an_error_badge(self):
        self.assertEqual(tmdb.status_badge("Canceled"), {"label": "Cancelled", "color": "error"})

    def test_ended_show_maps_to_a_neutral_badge(self):
        self.assertEqual(tmdb.status_badge("Ended"), {"label": "Ended", "color": "ink-dim"})

    def test_a_released_movie_gets_no_badge(self):
        self.assertIsNone(tmdb.status_badge("Released"))

    def test_unknown_or_missing_status_gets_no_badge(self):
        self.assertIsNone(tmdb.status_badge("Some Future TMDB Status"))
        self.assertIsNone(tmdb.status_badge(None))


@override_settings(
    TMDB_API_KEY="test-key",
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
class TmdbDiscoverCachingTests(TestCase):
    """Class-level override so the LocMemCache swap is active during
    setUp() too, not just inside each decorated test method - needed so
    cache.clear() below actually clears the right backend instead of
    trying (and, pre-fix, failing) to reach the real default/Redis cache."""

    def setUp(self):
        # LocMemCache keeps a process-wide dict keyed by LOCATION, shared
        # across every test using it (Django doesn't clear it between
        # tests automatically) - without this, an earlier test's cached
        # "movie popular" response makes this test see a false cache hit
        # on its very first call.
        from django.core.cache import cache

        cache.clear()

    @patch("tracker.integrations.tmdb.requests.get")
    def test_repeated_call_with_same_params_hits_cache_not_network(self, mock_get):
        resp = Mock()
        resp.json.return_value = {"results": [], "page": 1, "total_pages": 1}
        resp.raise_for_status = Mock()
        mock_get.return_value = resp

        tmdb.discover("movie", category="popular")
        tmdb.discover("movie", category="popular")
        self.assertEqual(mock_get.call_count, 1)

    @patch("tracker.integrations.tmdb.requests.get")
    def test_different_params_are_not_cached_together(self, mock_get):
        resp = Mock()
        resp.json.return_value = {"results": [], "page": 1, "total_pages": 1}
        resp.raise_for_status = Mock()
        mock_get.return_value = resp

        tmdb.discover("movie", category="popular")
        tmdb.discover("tv", category="popular")
        self.assertEqual(mock_get.call_count, 2)


class TmdbDiscoverCacheResilienceTests(TestCase):
    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_unreachable_cache_degrades_instead_of_crashing(self, mock_get):
        # Default (non-overridden) settings' CACHES points at a Redis
        # instance that isn't running in this environment - confirms
        # _list_request's try/except degrades to "no cache" rather than
        # raising, instead of just trusting it without checking.
        resp = Mock()
        resp.json.return_value = {"results": [], "page": 1, "total_pages": 1}
        resp.raise_for_status = Mock()
        mock_get.return_value = resp
        page = tmdb.discover("movie", category="popular")
        self.assertEqual(page["results"], [])


class DiscoverViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("discoverviewer", password="pass12345")
        Profile.objects.create(user=user, display_name="DiscoverViewer")
        self.client.login(username="discoverviewer", password="pass12345")

    def test_invalid_category_404s(self):
        resp = self.client.get(reverse("movies_tv", args=["bogus"]))
        self.assertEqual(resp.status_code, 404)

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_movies_tv_defaults_to_movie_type(self, mock_discover, mock_genres):
        mock_discover.return_value = {"results": [], "page": 1, "total_pages": 1}
        self.client.get(reverse("movies_tv", args=["trending"]))
        self.assertEqual(mock_discover.call_args.args[0], "movie")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_movies_tv_respects_type_query_param(self, mock_discover, mock_genres):
        mock_discover.return_value = {"results": [], "page": 1, "total_pages": 1}
        self.client.get(reverse("movies_tv", args=["trending"]), {"type": "tv"})
        self.assertEqual(mock_discover.call_args.args[0], "tv")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_anime_always_uses_tv_and_japan_and_animation_genre(self, mock_discover, mock_genres):
        mock_discover.return_value = {"results": [], "page": 1, "total_pages": 1}
        # Even a stray ?type=movie shouldn't switch anime off tv.
        self.client.get(reverse("anime", args=["trending"]), {"type": "movie"})
        self.assertEqual(mock_discover.call_args.args[0], "tv")
        kwargs = mock_discover.call_args.kwargs
        self.assertEqual(kwargs["origin_country"], "JP")
        self.assertIn(tmdb.ANIMATION_GENRE_ID, kwargs["genre_ids"])

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_page_number_clamped_to_500(self, mock_discover, mock_genres):
        mock_discover.return_value = {"results": [], "page": 500, "total_pages": 500}
        self.client.get(reverse("movies_tv", args=["popular"]), {"page": "99999"})
        self.assertEqual(mock_discover.call_args.kwargs["page"], 500)

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_genre_filter_parsed_from_query_params(self, mock_discover, mock_genres):
        mock_discover.return_value = {"results": [], "page": 1, "total_pages": 1}
        self.client.get(reverse("movies_tv", args=["popular"]), {"genre": ["28", "16"]})
        self.assertEqual(set(mock_discover.call_args.kwargs["genre_ids"]), {28, 16})

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_renders_200_with_results(self, mock_discover, mock_genres):
        mock_discover.return_value = {
            "results": [{"tmdb_id": 1, "media_type": "movie", "name": "Fathom", "year": "2020",
                         "poster_url": None, "vote_average": 7.1}],
            "page": 1,
            "total_pages": 3,
        }
        resp = self.client.get(reverse("movies_tv", args=["popular"]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Fathom")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_preferred_language_prefills_the_filter_when_unset_in_the_url(self, mock_discover, mock_genres):
        profile = Profile.objects.get(display_name="DiscoverViewer")
        profile.preferred_language = "ja"
        profile.save(update_fields=["preferred_language"])
        mock_discover.return_value = {"results": [], "page": 1, "total_pages": 1}
        resp = self.client.get(reverse("movies_tv", args=["trending"]))
        self.assertEqual(mock_discover.call_args.kwargs["original_language"], "ja")
        self.assertEqual(resp.context["selected_language"], "ja")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_an_explicit_language_param_overrides_the_preference(self, mock_discover, mock_genres):
        profile = Profile.objects.get(display_name="DiscoverViewer")
        profile.preferred_language = "ja"
        profile.save(update_fields=["preferred_language"])
        mock_discover.return_value = {"results": [], "page": 1, "total_pages": 1}
        self.client.get(reverse("movies_tv", args=["trending"]), {"language": "fr"})
        self.assertEqual(mock_discover.call_args.kwargs["original_language"], "fr")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_explicit_empty_language_means_any_even_with_a_preference_set(self, mock_discover, mock_genres):
        profile = Profile.objects.get(display_name="DiscoverViewer")
        profile.preferred_language = "ja"
        profile.save(update_fields=["preferred_language"])
        mock_discover.return_value = {"results": [], "page": 1, "total_pages": 1}
        self.client.get(reverse("movies_tv", args=["trending"]), {"language": ""})
        self.assertIsNone(mock_discover.call_args.kwargs["original_language"])

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_a_previously_watched_title_reappearing_shows_the_green_checkmark(self, mock_discover, mock_genres):
        profile = Profile.objects.get(display_name="DiscoverViewer")
        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"tmdb": "42", "tmdb_kind": "movie"}
        )
        WatchEvent.objects.create(profile=profile, title=title, watched_at="2024-01-01T00:00:00Z")
        mock_discover.return_value = {
            "results": [{"tmdb_id": 42, "media_type": "movie", "name": "Fathom", "year": "2020",
                         "poster_url": None, "vote_average": 7.1}],
            "page": 1,
            "total_pages": 1,
        }
        resp = self.client.get(reverse("movies_tv", args=["popular"]))
        self.assertContains(resp, "text-success")
        self.assertContains(resp, f"watched-btn-{title.pk}")
        # Already watched -> a further click is guarded by a confirm.
        self.assertContains(resp, 'hx-confirm="')

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_certification_filter_passed_through_for_movies(self, mock_discover, mock_genres):
        mock_discover.return_value = {"results": [], "page": 1, "total_pages": 1}
        resp = self.client.get(reverse("movies_tv", args=["popular"]), {"type": "movie", "certification": "R"})
        self.assertEqual(mock_discover.call_args.kwargs["certification"], "R")
        self.assertEqual(resp.context["selected_certification"], "R")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_certification_dropped_for_tv(self, mock_discover, mock_genres):
        # No /discover/tv equivalent exists (see tmdb.MOVIE_CERTIFICATIONS)
        # - a ?certification= carried over from a prior Movies search
        # shouldn't silently pass through to a call that can't use it.
        mock_discover.return_value = {"results": [], "page": 1, "total_pages": 1}
        resp = self.client.get(reverse("movies_tv", args=["popular"]), {"type": "tv", "certification": "R"})
        self.assertIsNone(mock_discover.call_args.kwargs["certification"])
        self.assertEqual(resp.context["selected_certification"], "")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_invalid_certification_falls_back_to_none(self, mock_discover, mock_genres):
        mock_discover.return_value = {"results": [], "page": 1, "total_pages": 1}
        resp = self.client.get(reverse("movies_tv", args=["popular"]), {"type": "movie", "certification": "bogus"})
        self.assertIsNone(mock_discover.call_args.kwargs["certification"])
        self.assertEqual(resp.context["selected_certification"], "")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_status_filter_passed_through_for_tv(self, mock_discover, mock_genres):
        mock_discover.return_value = {"results": [], "page": 1, "total_pages": 1}
        resp = self.client.get(reverse("movies_tv", args=["popular"]), {"type": "tv", "status": "Ended"})
        self.assertEqual(mock_discover.call_args.kwargs["status"], "Ended")
        self.assertEqual(resp.context["selected_status"], "Ended")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_status_dropped_for_movies(self, mock_discover, mock_genres):
        # No /discover/movie equivalent exists (see tmdb.TV_STATUSES) - a
        # ?status= carried over from a prior TV search shouldn't silently
        # pass through to a call that can't use it.
        mock_discover.return_value = {"results": [], "page": 1, "total_pages": 1}
        resp = self.client.get(reverse("movies_tv", args=["popular"]), {"type": "movie", "status": "Ended"})
        self.assertIsNone(mock_discover.call_args.kwargs["status"])
        self.assertEqual(resp.context["selected_status"], "")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_invalid_status_falls_back_to_none(self, mock_discover, mock_genres):
        mock_discover.return_value = {"results": [], "page": 1, "total_pages": 1}
        resp = self.client.get(reverse("movies_tv", args=["popular"]), {"type": "tv", "status": "bogus"})
        self.assertIsNone(mock_discover.call_args.kwargs["status"])
        self.assertEqual(resp.context["selected_status"], "")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_availability_filter_passed_through(self, mock_discover, mock_genres):
        mock_discover.return_value = {"results": [], "page": 1, "total_pages": 1}
        resp = self.client.get(reverse("movies_tv", args=["popular"]), {"availability": "streaming"})
        self.assertEqual(mock_discover.call_args.kwargs["availability"], "streaming")
        self.assertEqual(resp.context["selected_availability"], "streaming")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_invalid_availability_falls_back_to_none(self, mock_discover, mock_genres):
        mock_discover.return_value = {"results": [], "page": 1, "total_pages": 1}
        resp = self.client.get(reverse("movies_tv", args=["popular"]), {"availability": "bogus"})
        self.assertIsNone(mock_discover.call_args.kwargs["availability"])
        self.assertEqual(resp.context["selected_availability"], "")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_watched_display_defaults_to_showing_watched_titles_normally(self, mock_discover, mock_genres):
        profile = Profile.objects.get(display_name="DiscoverViewer")
        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"tmdb": "42", "tmdb_kind": "movie"}
        )
        WatchEvent.objects.create(profile=profile, title=title, watched_at="2024-01-01T00:00:00Z")
        mock_discover.return_value = {
            "results": [{"tmdb_id": 42, "media_type": "movie", "name": "Fathom", "year": "2020",
                         "poster_url": None, "vote_average": 7.1}],
            "page": 1,
            "total_pages": 1,
        }
        resp = self.client.get(reverse("movies_tv", args=["popular"]))
        self.assertContains(resp, "Fathom")
        self.assertNotContains(resp, "opacity-25")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_watched_display_hide_removes_the_tile(self, mock_discover, mock_genres):
        profile = Profile.objects.get(display_name="DiscoverViewer")
        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"tmdb": "42", "tmdb_kind": "movie"}
        )
        WatchEvent.objects.create(profile=profile, title=title, watched_at="2024-01-01T00:00:00Z")
        mock_discover.return_value = {
            "results": [{"tmdb_id": 42, "media_type": "movie", "name": "Fathom", "year": "2020",
                         "poster_url": None, "vote_average": 7.1}],
            "page": 1,
            "total_pages": 1,
        }
        resp = self.client.get(reverse("movies_tv", args=["popular"]), {"watched_display": "hide"})
        self.assertNotContains(resp, "Fathom")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_watched_display_dim_keeps_the_tile_with_a_lowered_opacity_class(self, mock_discover, mock_genres):
        profile = Profile.objects.get(display_name="DiscoverViewer")
        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"tmdb": "42", "tmdb_kind": "movie"}
        )
        WatchEvent.objects.create(profile=profile, title=title, watched_at="2024-01-01T00:00:00Z")
        mock_discover.return_value = {
            "results": [{"tmdb_id": 42, "media_type": "movie", "name": "Fathom", "year": "2020",
                         "poster_url": None, "vote_average": 7.1}],
            "page": 1,
            "total_pages": 1,
        }
        resp = self.client.get(reverse("movies_tv", args=["popular"]), {"watched_display": "dim"})
        self.assertContains(resp, "Fathom")
        self.assertContains(resp, "opacity-25")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_watchlisted_display_hide_removes_the_tile(self, mock_discover, mock_genres):
        profile = Profile.objects.get(display_name="DiscoverViewer")
        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"tmdb": "42", "tmdb_kind": "movie"}
        )
        watchlist = WatchList.objects.create(profile=profile, name="Watchlist", is_watchlist=True)
        WatchListItem.objects.create(watchlist=watchlist, title=title)
        mock_discover.return_value = {
            "results": [{"tmdb_id": 42, "media_type": "movie", "name": "Fathom", "year": "2020",
                         "poster_url": None, "vote_average": 7.1}],
            "page": 1,
            "total_pages": 1,
        }
        resp = self.client.get(reverse("movies_tv", args=["popular"]), {"watchlisted_display": "hide"})
        self.assertNotContains(resp, "Fathom")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_watchlisted_display_ignores_membership_in_a_custom_non_watchlist_list(self, mock_discover, mock_genres):
        # Only the auto-managed Watchlist (is_watchlist=True) counts as
        # "watchlisted" for this filter - a custom list shouldn't trigger it.
        profile = Profile.objects.get(display_name="DiscoverViewer")
        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"tmdb": "42", "tmdb_kind": "movie"}
        )
        custom_list = WatchList.objects.create(profile=profile, name="Favorites", is_watchlist=False)
        WatchListItem.objects.create(watchlist=custom_list, title=title)
        mock_discover.return_value = {
            "results": [{"tmdb_id": 42, "media_type": "movie", "name": "Fathom", "year": "2020",
                         "poster_url": None, "vote_average": 7.1}],
            "page": 1,
            "total_pages": 1,
        }
        resp = self.client.get(reverse("movies_tv", args=["popular"]), {"watchlisted_display": "hide"})
        self.assertContains(resp, "Fathom")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_invalid_display_value_falls_back_to_show(self, mock_discover, mock_genres):
        mock_discover.return_value = {"results": [], "page": 1, "total_pages": 1}
        resp = self.client.get(reverse("movies_tv", args=["popular"]), {"watched_display": "bogus"})
        self.assertEqual(resp.context["watched_display"], "show")

    @patch("tracker.integrations.tmdb.genres", return_value=[])
    @patch("tracker.integrations.tmdb.discover")
    def test_an_untracked_title_still_shows_the_unwatched_state(self, mock_discover, mock_genres):
        mock_discover.return_value = {
            "results": [{"tmdb_id": 42, "media_type": "movie", "name": "Fathom", "year": "2020",
                         "poster_url": None, "vote_average": 7.1}],
            "page": 1,
            "total_pages": 1,
        }
        resp = self.client.get(reverse("movies_tv", args=["popular"]))
        self.assertNotContains(resp, "text-success")
        self.assertNotContains(resp, 'hx-confirm="')
        self.assertContains(resp, reverse("title_preview_mark_watched", args=["movie", 42]))


class SearchViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("searcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="Searcher")
        self.client.login(username="searcher", password="pass12345")

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse("search"), {"q": "dune"})
        self.assertEqual(resp.status_code, 302)

    def test_no_query_shows_prompt_and_does_not_call_tmdb(self):
        with patch("tracker.integrations.tmdb.search") as mock_search:
            resp = self.client.get(reverse("search"))
        mock_search.assert_not_called()
        self.assertContains(resp, "Type something above")

    @patch("tracker.integrations.tmdb.search")
    def test_matches_a_title_already_in_the_library(self, mock_search):
        mock_search.return_value = {"results": []}
        Title.objects.create(media_type=MediaType.MOVIE, name="Dune Part Two", year=2024)
        resp = self.client.get(reverse("search"), {"q": "dune"})
        self.assertContains(resp, "Dune Part Two")
        self.assertContains(resp, "In your library")

    @patch("tracker.integrations.tmdb.search")
    def test_library_search_is_case_insensitive_and_partial(self, mock_search):
        mock_search.return_value = {"results": []}
        Title.objects.create(media_type=MediaType.MOVIE, name="The Matrix", year=1999)
        resp = self.client.get(reverse("search"), {"q": "MATR"})
        self.assertContains(resp, "The Matrix")

    @patch("tracker.integrations.tmdb.search")
    def test_tmdb_results_shown_for_titles_not_yet_tracked(self, mock_search):
        mock_search.return_value = {
            "results": [{"tmdb_id": 42, "media_type": "movie", "name": "Fathom", "year": "2020", "poster_url": None, "vote_average": 7.5, "overview": ""}]
        }
        resp = self.client.get(reverse("search"), {"q": "fathom"})
        self.assertContains(resp, "Fathom")

    @patch("tracker.integrations.tmdb.search")
    def test_tmdb_results_already_tracked_are_excluded(self, mock_search):
        Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"tmdb": "42"}
        )
        mock_search.return_value = {
            "results": [{"tmdb_id": 42, "media_type": "movie", "name": "Fathom", "year": "2020", "poster_url": None, "vote_average": 7.5, "overview": ""}]
        }
        resp = self.client.get(reverse("search"), {"q": "fathom"})
        self.assertContains(resp, "Fathom")
        # The library card is present (asserted above), but no *TMDB preview*
        # card for the same tmdb_id - that's the discover_tile link this
        # title would render if it weren't excluded as already-tracked.
        self.assertNotContains(resp, reverse("title_preview", args=["movie", 42]))

    @patch("tracker.integrations.tmdb.search")
    def test_no_matches_shows_empty_state_messages(self, mock_search):
        mock_search.return_value = {"results": []}
        resp = self.client.get(reverse("search"), {"q": "zzzznomatch"})
        self.assertContains(resp, "Nothing in your library matches")
        self.assertContains(resp, "Nothing new found on TMDB")

    @patch("tracker.integrations.tmdb.search")
    def test_type_filter_narrows_both_library_and_tmdb_sections(self, mock_search):
        Title.objects.create(media_type=MediaType.MOVIE, name="Dune Part Two", year=2024)
        Title.objects.create(media_type=MediaType.TV, name="Dune: Prophecy", year=2024)
        mock_search.return_value = {
            "results": [
                {"tmdb_id": 1, "media_type": "movie", "is_anime": False, "name": "Dune Movie Preview", "year": "2021", "poster_url": None, "vote_average": 7.5, "overview": ""},
                {"tmdb_id": 2, "media_type": "tv", "is_anime": False, "name": "Dune TV Preview", "year": "2021", "poster_url": None, "vote_average": 7.5, "overview": ""},
            ]
        }
        resp = self.client.get(reverse("search"), {"q": "dune", "type": "movie"})
        self.assertContains(resp, "Dune Part Two")
        self.assertNotContains(resp, "Dune: Prophecy")
        self.assertContains(resp, "Dune Movie Preview")
        self.assertNotContains(resp, "Dune TV Preview")
        self.assertEqual(resp.context["selected_type"], "movie")

    @patch("tracker.integrations.tmdb.search")
    def test_anime_tab_matches_is_anime_flag_not_tv_media_type(self, mock_search):
        # is_anime is layered on top of a plain tv/movie media_type (see
        # tmdb._normalize_result) - the anime tab has to key off that
        # flag, not media_type=="tv", or every ordinary (non-anime) show
        # would wrongly show up under Anime too.
        Title.objects.create(media_type=MediaType.TV, name="Regular Show", year=2020)
        Title.objects.create(media_type=MediaType.ANIME, name="Anime Show", year=2020)
        mock_search.return_value = {
            "results": [
                {"tmdb_id": 1, "media_type": "tv", "is_anime": False, "name": "Regular Preview", "year": "2021", "poster_url": None, "vote_average": 7.5, "overview": ""},
                {"tmdb_id": 2, "media_type": "tv", "is_anime": True, "name": "Anime Preview", "year": "2021", "poster_url": None, "vote_average": 7.5, "overview": ""},
            ]
        }
        resp = self.client.get(reverse("search"), {"q": "show", "type": "anime"})
        self.assertNotContains(resp, "Regular Show")
        self.assertContains(resp, "Anime Show")
        self.assertNotContains(resp, "Regular Preview")
        self.assertContains(resp, "Anime Preview")

    @patch("tracker.integrations.tmdb.search")
    def test_invalid_type_falls_back_to_all(self, mock_search):
        mock_search.return_value = {"results": []}
        Title.objects.create(media_type=MediaType.MOVIE, name="Dune Part Two", year=2024)
        resp = self.client.get(reverse("search"), {"q": "dune", "type": "not-a-real-type"})
        self.assertEqual(resp.context["selected_type"], "all")
        self.assertContains(resp, "Dune Part Two")


@patch("tracker.views.COLLECTIONS_ENABLED", True)
class DiscoverCollectionsViewTests(TestCase):
    """Movies & TV's "Collections" tab - a distinct code path from the
    other categories (no filter panel/pagination, a different tile
    partial), movie-only. Force-enabled at the class level since the
    feature itself defaults off (see CollectionsDisabledTests) - these
    tests are about the underlying view logic staying correct and ready
    to go, not today's default-off UI state."""

    def setUp(self):
        user = User.objects.create_user("collectionsviewer", password="pass12345")
        Profile.objects.create(user=user, display_name="CollectionsViewer")
        self.client.login(username="collectionsviewer", password="pass12345")

    @patch("tracker.integrations.tmdb.collections")
    def test_renders_collection_tiles(self, mock_collections):
        mock_collections.return_value = [{"id": 100, "name": "John Wick Collection", "poster_url": None}]
        resp = self.client.get(reverse("movies_tv", args=["collections"]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["is_collections"])
        self.assertContains(resp, "John Wick Collection")
        self.assertContains(resp, reverse("collection_detail", args=[100]))

    def test_anime_collections_404s(self):
        resp = self.client.get(reverse("anime", args=["collections"]))
        self.assertEqual(resp.status_code, 404)

    @patch("tracker.integrations.tmdb.collections", return_value=[])
    def test_no_filters_button_or_pagination_shown(self, mock_collections):
        resp = self.client.get(reverse("movies_tv", args=["collections"]))
        self.assertNotContains(resp, "Filters")

    @patch("tracker.integrations.tmdb.collections", return_value=[])
    def test_requires_login(self, mock_collections):
        self.client.logout()
        resp = self.client.get(reverse("movies_tv", args=["collections"]))
        self.assertNotEqual(resp.status_code, 200)

    @patch("tracker.integrations.tmdb.collections", return_value=[])
    def test_movies_tv_toggle_stays_visible_but_links_back_to_trending(self, mock_collections):
        resp = self.client.get(reverse("movies_tv", args=["collections"]))
        trending_movie = reverse("movies_tv", args=["trending"]) + "?type=movie"
        trending_tv = reverse("movies_tv", args=["trending"]) + "?type=tv"
        self.assertContains(resp, f'href="{trending_movie}"')
        self.assertContains(resp, f'href="{trending_tv}"')
        # neither Movies nor TV reads as "active" while viewing collections
        self.assertNotContains(resp, f'href="?type=movie"')
        self.assertNotContains(resp, f'href="?type=tv"')


@patch("tracker.views.COLLECTIONS_ENABLED", True)
class CollectionDetailViewTests(TestCase):
    """Force-enabled at the class level - see CollectionsDisabledTests for
    today's default-off behavior."""

    def setUp(self):
        user = User.objects.create_user("collectiondetailviewer", password="pass12345")
        Profile.objects.create(user=user, display_name="CollectionDetailViewer")
        self.client.login(username="collectiondetailviewer", password="pass12345")

    @patch("tracker.integrations.tmdb.get_collection_details")
    def test_renders_the_collections_movies(self, mock_details):
        mock_details.return_value = {
            "id": 100,
            "name": "John Wick Collection",
            "overview": "An assassin.",
            "poster_url": None,
            "backdrop_url": None,
            "parts": [
                {"tmdb_id": 1, "media_type": "movie", "name": "John Wick", "year": "2014",
                 "poster_url": None, "vote_average": 7.4},
            ],
        }
        resp = self.client.get(reverse("collection_detail", args=[100]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "John Wick Collection")
        self.assertContains(resp, "John Wick")
        mock_details.assert_called_once_with(100)

    @patch("tracker.integrations.tmdb.get_collection_details", return_value=None)
    def test_unknown_collection_404s(self, mock_details):
        resp = self.client.get(reverse("collection_detail", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    @patch("tracker.integrations.tmdb.get_collection_details")
    def test_requires_login(self, mock_details):
        self.client.logout()
        resp = self.client.get(reverse("collection_detail", args=[100]))
        self.assertNotEqual(resp.status_code, 200)


class CollectionsDisabledTests(TestCase):
    """The Collections feature is fully built (see DiscoverCollectionsViewTests/
    CollectionDetailViewTests/TmdbCollectionsTests) but turned off by
    default via views.COLLECTIONS_ENABLED - not enough distinct
    collections surfaced yet to feel worth a permanent nav tab. These
    confirm that default-off state without touching the underlying
    logic: flipping the flag back on is the only thing re-enabling it."""

    def setUp(self):
        user = User.objects.create_user("collectionsdisableduser", password="pass12345")
        Profile.objects.create(user=user, display_name="CollectionsDisabledUser")
        self.client.login(username="collectionsdisableduser", password="pass12345")

    def test_collections_category_404s_by_default(self):
        resp = self.client.get(reverse("movies_tv", args=["collections"]))
        self.assertEqual(resp.status_code, 404)

    def test_collection_detail_404s_by_default(self):
        resp = self.client.get(reverse("collection_detail", args=[100]))
        self.assertEqual(resp.status_code, 404)

    @patch("tracker.integrations.tmdb.discover", return_value={"results": [], "page": 1, "total_pages": 1})
    @patch("tracker.integrations.tmdb.genres", return_value=[])
    def test_collections_tab_not_shown_on_the_trending_page(self, mock_genres, mock_discover):
        resp = self.client.get(reverse("movies_tv", args=["trending"]))
        self.assertNotContains(resp, "Collections</a>")
        self.assertNotContains(resp, reverse("movies_tv", args=["collections"]))


class RecentlyAddedToListsSelectorTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("recentlyaddeduser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="RecentlyAddedUser")

    def test_excludes_watchlist_items(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Watchlisted Movie", year=2020)
        watchlist = WatchList.objects.create(profile=self.profile, name="Watchlist", is_watchlist=True)
        WatchListItem.objects.create(watchlist=watchlist, title=title)
        self.assertEqual(list(selectors.recently_added_to_lists(self.profile)), [])

    def test_includes_custom_list_items(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Favorited Movie", year=2020)
        custom_list = WatchList.objects.create(profile=self.profile, name="Favorites", is_watchlist=False)
        item = WatchListItem.objects.create(watchlist=custom_list, title=title)
        self.assertEqual(list(selectors.recently_added_to_lists(self.profile)), [item])


class BecauseYouWatchedTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("byw", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="BYW")

    def test_no_watch_history_returns_none(self):
        self.assertIsNone(selectors.because_you_watched(self.profile))

    def test_watch_history_without_tmdb_ids_returns_none(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="No TMDB Id", year=2020)
        WatchEvent.objects.create(profile=self.profile, title=title, watched_at="2024-01-01T00:00:00Z")
        self.assertIsNone(selectors.because_you_watched(self.profile))

    @patch("tracker.integrations.tmdb.get_similar")
    def test_uses_most_recently_watched_title_with_a_tmdb_id(self, mock_get_similar):
        older = Title.objects.create(
            media_type=MediaType.MOVIE, name="Older Movie", year=2019, external_ids={"tmdb": "1"}
        )
        newer = Title.objects.create(
            media_type=MediaType.MOVIE, name="Newer Movie", year=2020, external_ids={"tmdb": "2"}
        )
        WatchEvent.objects.create(profile=self.profile, title=older, watched_at="2024-01-01T00:00:00Z")
        WatchEvent.objects.create(profile=self.profile, title=newer, watched_at="2024-02-01T00:00:00Z")
        mock_get_similar.return_value = [{"tmdb_id": 99, "media_type": "movie", "name": "Similar"}]

        result = selectors.because_you_watched(self.profile)

        self.assertEqual(result["anchor_title"], newer)
        mock_get_similar.assert_called_once_with("movie", "2", limit=12)

    @patch("tracker.integrations.tmdb.get_similar")
    def test_falls_through_to_an_older_candidate_when_the_newest_has_no_recommendations(self, mock_get_similar):
        older = Title.objects.create(
            media_type=MediaType.MOVIE, name="Older Movie", year=2019, external_ids={"tmdb": "1"}
        )
        newer = Title.objects.create(
            media_type=MediaType.MOVIE, name="Newer Movie", year=2020, external_ids={"tmdb": "2"}
        )
        WatchEvent.objects.create(profile=self.profile, title=older, watched_at="2024-01-01T00:00:00Z")
        WatchEvent.objects.create(profile=self.profile, title=newer, watched_at="2024-02-01T00:00:00Z")
        mock_get_similar.side_effect = [[], [{"tmdb_id": 99, "media_type": "movie", "name": "Similar"}]]

        result = selectors.because_you_watched(self.profile)

        self.assertEqual(result["anchor_title"], older)
        self.assertEqual(mock_get_similar.call_count, 2)

    @patch("tracker.integrations.tmdb.get_similar")
    def test_gives_up_after_the_candidate_pool_is_exhausted(self, mock_get_similar):
        for i in range(5):
            title = Title.objects.create(
                media_type=MediaType.MOVIE, name=f"Movie {i}", year=2020, external_ids={"tmdb": str(i)}
            )
            WatchEvent.objects.create(profile=self.profile, title=title, watched_at="2024-01-01T00:00:00Z")
        mock_get_similar.return_value = []

        result = selectors.because_you_watched(self.profile, candidate_pool=3)

        self.assertIsNone(result)
        self.assertEqual(mock_get_similar.call_count, 3)

    @patch("tracker.integrations.tmdb.get_similar")
    def test_a_title_watched_multiple_times_only_counts_as_one_candidate(self, mock_get_similar):
        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Rewatched", year=2020, external_ids={"tmdb": "1"}
        )
        WatchEvent.objects.create(profile=self.profile, title=title, watched_at="2024-01-01T00:00:00Z")
        WatchEvent.objects.create(profile=self.profile, title=title, watched_at="2024-02-01T00:00:00Z")
        mock_get_similar.return_value = [{"tmdb_id": 99, "media_type": "movie", "name": "Similar"}]

        selectors.because_you_watched(self.profile)

        mock_get_similar.assert_called_once()


class QuickStatsFormatTests(TestCase):
    def test_total_watch_time_uses_the_stats_pages_duration_format(self):
        user = User.objects.create_user("statsformat", password="pass12345")
        profile = Profile.objects.create(user=user, display_name="StatsFormat")
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Long Movie", year=2020, runtime_minutes=130)
        WatchEvent.objects.create(profile=profile, title=title, watched_at="2024-01-01T00:00:00Z")
        stats = selectors.quick_stats(profile)
        self.assertEqual(stats["total_watch_time"], "2h 10m")


class DashboardWatchingWatchlistTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("dashboardwatcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="DashboardWatcher")
        self.client.login(username="dashboardwatcher", password="pass12345")

    def test_total_watch_time_rendered_in_breakdown_format(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Timed Movie", year=2020, runtime_minutes=130)
        WatchEvent.objects.create(profile=self.profile, title=title, watched_at="2024-01-01T00:00:00Z")
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, "2h 10m")

    @patch("tracker.integrations.tmdb.get_similar")
    def test_because_you_watched_row_rendered_when_present(self, mock_get_similar):
        title = Title.objects.create(
            media_type=MediaType.TV, name="Bleach", year=2004, external_ids={"tmdb": "1", "tmdb_kind": "tv"}
        )
        WatchEvent.objects.create(profile=self.profile, title=title, watched_at="2024-01-01T00:00:00Z")
        mock_get_similar.return_value = [
            {"tmdb_id": 42, "media_type": "tv", "name": "Naruto", "year": "2002", "poster_url": None, "vote_average": 8.0}
        ]
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, "Because you watched Bleach")
        self.assertContains(resp, "Naruto")

    def test_no_because_you_watched_row_without_qualifying_history(self):
        resp = self.client.get(reverse("dashboard"))
        self.assertNotContains(resp, "Because you watched")

    def test_shows_all_watching_items_not_just_a_teaser(self):
        for i in range(10):
            title = Title.objects.create(media_type=MediaType.TV, name=f"Show {i}", year=2020)
            WatchProgress.objects.create(profile=self.profile, title=title, status=WatchProgress.Status.WATCHING)
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(len(resp.context["continue_watching"]), 10)

    def test_shows_watchlist_items(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Listed Movie", year=2020)
        watchlist = WatchList.objects.create(profile=self.profile, name="My List")
        WatchListItem.objects.create(watchlist=watchlist, title=title)
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(len(resp.context["watchlist_items"]), 1)
        self.assertEqual(resp.context["watchlist_items"][0].title, title)

    def test_poster_cards_get_a_fixed_width_not_an_empty_class(self):
        # regression test: poster_card.html's width_class fallback must
        # use Django's `default` filter, not `default_if_none` - an
        # include that never passes width_class at all (every Dashboard
        # carousel) resolves the variable to an empty string, not None,
        # so `default_if_none` silently produces class="" and the card
        # falls back to sizing itself from its own (unconstrained,
        # variable-length) title text instead of a fixed width.
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Listed Movie", year=2020)
        watchlist = WatchList.objects.create(profile=self.profile, name="My List")
        WatchListItem.objects.create(watchlist=watchlist, title=title)
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, "w-[168px]")

    def test_milestone_banner_shows_on_a_streak_milestone_day(self):
        from django.utils import timezone

        today = timezone.localdate()
        for i in range(7):
            title = Title.objects.create(media_type=MediaType.MOVIE, name=f"Movie {i}", year=2020)
            WatchEvent.objects.create(
                profile=self.profile, title=title,
                watched_at=timezone.make_aware(timezone.datetime.combine(today - timedelta(days=i), timezone.datetime.min.time())),
            )
        resp = self.client.get(reverse("dashboard"))
        self.assertEqual(resp.context["stats"]["streak"], 7)
        self.assertEqual(resp.context["milestone"], selectors.STREAK_MILESTONES[7])
        self.assertContains(resp, "Seven days straight")

    def test_no_milestone_banner_on_an_ordinary_day(self):
        resp = self.client.get(reverse("dashboard"))
        self.assertIsNone(resp.context["milestone"])


class ActivityFeedGroupingTests(TestCase):
    """A binge (many consecutive same-profile/same-title episode watches)
    should collapse into one "watched N episodes" entry instead of burying
    every other profile's activity under a wall of near-identical rows."""

    def setUp(self):
        from django.utils import timezone

        user = User.objects.create_user("aljaz", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="aljaz")
        self.show = Title.objects.create(media_type=MediaType.TV, name="Bleach", year=2004)
        self.now = timezone.now()

    def _watch(self, episode_num, minutes_ago, season=1, profile=None, rating=None):
        ep = Episode.objects.create(title=self.show, season=season, episode=episode_num)
        return WatchEvent.objects.create(
            profile=profile or self.profile,
            title=self.show,
            episode=ep,
            watched_at=self.now - timedelta(minutes=minutes_ago),
            user_rating=rating,
        )

    def test_consecutive_episode_watches_collapse_into_one_group(self):
        for i, minutes_ago in enumerate([10, 20, 30, 40, 50]):
            self._watch(episode_num=207 + i, minutes_ago=minutes_ago)
        feed = selectors.activity_feed()
        self.assertEqual(len(feed), 1)
        self.assertEqual(feed[0]["kind"], "watched_group")
        self.assertEqual(feed[0]["count"], 5)
        self.assertEqual(feed[0]["range_label"], "S1E207–S1E211")

    def test_group_timestamp_is_the_most_recent_episode(self):
        self._watch(episode_num=1, minutes_ago=50)
        self._watch(episode_num=2, minutes_ago=10)
        feed = selectors.activity_feed()
        self.assertEqual(feed[0]["timestamp"], self.now - timedelta(minutes=10))

    def test_single_episode_does_not_get_grouped(self):
        self._watch(episode_num=1, minutes_ago=10)
        feed = selectors.activity_feed()
        self.assertEqual(feed[0]["kind"], "watched")

    def test_different_profiles_do_not_merge(self):
        other_user = User.objects.create_user("other", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="Other")
        self._watch(episode_num=1, minutes_ago=20)
        self._watch(episode_num=2, minutes_ago=15, profile=other_profile)
        self._watch(episode_num=3, minutes_ago=10)
        feed = selectors.activity_feed()
        # the other profile's single watch breaks the run into two separate
        # (non-grouped, since each side only has one episode) entries
        self.assertEqual(len(feed), 3)
        self.assertTrue(all(item["kind"] == "watched" for item in feed))

    def test_different_titles_do_not_merge(self):
        other_show = Title.objects.create(media_type=MediaType.TV, name="Naruto", year=2002)
        ep = Episode.objects.create(title=other_show, season=1, episode=1)
        WatchEvent.objects.create(profile=self.profile, title=other_show, episode=ep, watched_at=self.now - timedelta(minutes=25))
        self._watch(episode_num=1, minutes_ago=20)
        self._watch(episode_num=2, minutes_ago=10)
        feed = selectors.activity_feed()
        self.assertEqual(len(feed), 2)
        self.assertEqual(feed[0]["kind"], "watched_group")
        self.assertEqual(feed[0]["title"], self.show)
        self.assertEqual(feed[1]["title"], other_show)

    def test_rated_event_is_not_grouped_and_breaks_the_run(self):
        self._watch(episode_num=1, minutes_ago=30)
        self._watch(episode_num=2, minutes_ago=20, rating=8)
        self._watch(episode_num=3, minutes_ago=10)
        feed = selectors.activity_feed()
        # the rated watch is a real, individually-surfaced entry that
        # splits what would otherwise be one three-episode run into two
        # single (ungrouped) watches around it
        self.assertEqual(len(feed), 3)
        self.assertEqual(feed[1]["kind"], "rated")

    def test_multi_season_group_range_spans_seasons(self):
        self._watch(episode_num=24, minutes_ago=20, season=1)
        self._watch(episode_num=1, minutes_ago=10, season=2)
        feed = selectors.activity_feed()
        self.assertEqual(feed[0]["range_label"], "S1E24–S2E1")

    def test_expanding_a_group_exposes_each_individual_episode(self):
        for i, minutes_ago in enumerate([30, 20, 10]):
            self._watch(episode_num=1 + i, minutes_ago=minutes_ago)
        feed = selectors.activity_feed()
        episode_numbers = [item["episode"].episode for item in feed[0]["episodes"]]
        self.assertEqual(episode_numbers, [3, 2, 1])  # newest-first, matching the feed's own order

    def test_consecutive_movie_watches_group_regardless_of_title(self):
        # a movie marathon is almost always different films back to back,
        # not the same one repeatedly - movies group per profile alone.
        movie_a = Title.objects.create(media_type=MediaType.MOVIE, name="Movie A", year=2020)
        movie_b = Title.objects.create(media_type=MediaType.MOVIE, name="Movie B", year=2021)
        WatchEvent.objects.create(profile=self.profile, title=movie_a, watched_at=self.now - timedelta(minutes=20))
        WatchEvent.objects.create(profile=self.profile, title=movie_b, watched_at=self.now - timedelta(minutes=10))
        feed = selectors.activity_feed()
        self.assertEqual(len(feed), 1)
        self.assertEqual(feed[0]["kind"], "watched_movies_group")
        self.assertEqual(feed[0]["count"], 2)
        self.assertEqual([m["title"] for m in feed[0]["movies"]], [movie_b, movie_a])  # newest-first

    def test_single_movie_watch_does_not_get_grouped(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Movie A", year=2020)
        WatchEvent.objects.create(profile=self.profile, title=movie, watched_at=self.now - timedelta(minutes=10))
        feed = selectors.activity_feed()
        self.assertEqual(feed[0]["kind"], "watched")

    def test_movie_and_episode_watches_do_not_merge(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Movie A", year=2020)
        WatchEvent.objects.create(profile=self.profile, title=movie, watched_at=self.now - timedelta(minutes=20))
        self._watch(episode_num=1, minutes_ago=10)
        feed = selectors.activity_feed()
        self.assertEqual(len(feed), 2)
        self.assertTrue(all(item["kind"] == "watched" for item in feed))

    def test_consecutive_list_adds_to_the_same_list_group_regardless_of_title(self):
        profile2_user = User.objects.create_user("p2", password="pass12345")
        watchlist = WatchList.objects.create(profile=self.profile, name="Anime")
        titles = [Title.objects.create(media_type=MediaType.TV, name=f"Show {i}", year=2020) for i in range(3)]
        for i, title in enumerate(titles):
            wli = WatchListItem.objects.create(watchlist=watchlist, title=title)
            # added_at is auto_now_add - .update() bypasses that to backdate it for the test
            WatchListItem.objects.filter(pk=wli.pk).update(added_at=self.now - timedelta(minutes=30 - i * 10))
        feed = selectors.activity_feed()
        self.assertEqual(len(feed), 1)
        self.assertEqual(feed[0]["kind"], "added_to_list_group")
        self.assertEqual(feed[0]["count"], 3)
        self.assertEqual(feed[0]["watchlist"], watchlist)
        self.assertEqual([i["title"] for i in feed[0]["items"]], list(reversed(titles)))  # newest-first

    def test_list_adds_to_different_lists_do_not_merge(self):
        watchlist_a = WatchList.objects.create(profile=self.profile, name="Anime")
        watchlist_b = WatchList.objects.create(profile=self.profile, name="Watchlist")
        title_a = Title.objects.create(media_type=MediaType.TV, name="Show A", year=2020)
        title_b = Title.objects.create(media_type=MediaType.MOVIE, name="Movie B", year=2020)
        WatchListItem.objects.create(watchlist=watchlist_a, title=title_a)
        WatchListItem.objects.create(watchlist=watchlist_b, title=title_b)
        feed = selectors.activity_feed()
        self.assertEqual(len(feed), 2)
        self.assertTrue(all(item["kind"] == "added_to_list" for item in feed))

    def test_single_list_add_does_not_get_grouped(self):
        watchlist = WatchList.objects.create(profile=self.profile, name="Anime")
        title = Title.objects.create(media_type=MediaType.TV, name="Show A", year=2020)
        WatchListItem.objects.create(watchlist=watchlist, title=title)
        feed = selectors.activity_feed()
        self.assertEqual(feed[0]["kind"], "added_to_list")

    def test_non_group_items_have_no_is_group_flag(self):
        self._watch(episode_num=1, minutes_ago=10)
        feed = selectors.activity_feed()
        self.assertNotIn("is_group", feed[0])

    def test_two_sessions_far_apart_do_not_merge_into_one_group(self):
        # Same profile, same show, but ~19 hours apart - two real
        # sittings, not one binge. Without the max-gap check these would
        # falsely collapse into a single group whose one timestamp is
        # only honest for half the episodes it claims to cover.
        for i, minutes_ago in enumerate([1550, 1540, 1530, 1520, 1510]):  # ~1 day 2h ago, 5 episodes
            self._watch(episode_num=1 + i, minutes_ago=minutes_ago)
        for i, minutes_ago in enumerate([30, 20, 10]):  # recent, 3 episodes
            self._watch(episode_num=100 + i, minutes_ago=minutes_ago)
        feed = selectors.activity_feed()
        self.assertEqual(len(feed), 2)
        self.assertEqual(feed[0]["count"], 3)
        self.assertEqual(feed[1]["count"], 5)

    def test_a_gap_just_under_the_cutoff_still_merges(self):
        self._watch(episode_num=1, minutes_ago=359)  # just under 6h
        self._watch(episode_num=2, minutes_ago=0)
        feed = selectors.activity_feed()
        self.assertEqual(len(feed), 1)
        self.assertEqual(feed[0]["count"], 2)

    def test_a_gap_just_over_the_cutoff_splits(self):
        self._watch(episode_num=1, minutes_ago=361)  # just over 6h
        self._watch(episode_num=2, minutes_ago=0)
        feed = selectors.activity_feed()
        self.assertEqual(len(feed), 2)

    def test_gap_is_checked_against_the_chain_not_the_first_item(self):
        # A long-but-continuous binge (each episode ~just under the cutoff
        # apart from the previous one) should stay one group even though
        # the total span from first to last exceeds the cutoff.
        for i, minutes_ago in enumerate([700, 340, 0]):
            self._watch(episode_num=1 + i, minutes_ago=minutes_ago)
        feed = selectors.activity_feed()
        self.assertEqual(len(feed), 1)
        self.assertEqual(feed[0]["count"], 3)


class MultiProfileActivityInterleavingTests(TestCase):
    """Activity is a merged household feed, not grouped/filtered by user -
    whoever did something most recently should lead, regardless of who
    they are."""

    def setUp(self):
        from django.utils import timezone

        alice_user = User.objects.create_user("alice", password="pass12345")
        self.alice = Profile.objects.create(user=alice_user, display_name="Alice")
        bob_user = User.objects.create_user("bob", password="pass12345")
        self.bob = Profile.objects.create(user=bob_user, display_name="Bob")
        self.now = timezone.now()

    def test_feed_interleaves_by_timestamp_regardless_of_profile(self):
        movie_a = Title.objects.create(media_type=MediaType.MOVIE, name="Movie A", year=2020)
        movie_b = Title.objects.create(media_type=MediaType.MOVIE, name="Movie B", year=2020)
        movie_c = Title.objects.create(media_type=MediaType.MOVIE, name="Movie C", year=2020)
        WatchEvent.objects.create(profile=self.alice, title=movie_a, watched_at=self.now - timedelta(minutes=30))
        WatchEvent.objects.create(profile=self.bob, title=movie_b, watched_at=self.now - timedelta(minutes=20))
        WatchEvent.objects.create(profile=self.alice, title=movie_c, watched_at=self.now - timedelta(minutes=10))
        feed = selectors.activity_feed()
        self.assertEqual([item["profile"] for item in feed], [self.alice, self.bob, self.alice])

    def test_view_shows_activity_from_every_profile(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Shared Movie", year=2020)
        WatchEvent.objects.create(profile=self.alice, title=title, watched_at=self.now - timedelta(minutes=20))
        other_title = Title.objects.create(media_type=MediaType.MOVIE, name="Another Movie", year=2020)
        WatchEvent.objects.create(profile=self.bob, title=other_title, watched_at=self.now - timedelta(minutes=10))
        self.client.login(username="alice", password="pass12345")
        resp = self.client.get(reverse("activity"))
        self.assertContains(resp, "Alice")
        self.assertContains(resp, "Bob")


class ActivityFeedPrivacyTests(TestCase):
    """Settings → Privacy's share_activity toggle - a profile with it off
    is entirely absent from the merged feed, not just unlabeled."""

    def setUp(self):
        user = User.objects.create_user("privatewatcher", password="pass12345")
        self.private_profile = Profile.objects.create(
            user=user, display_name="PrivateWatcher", share_activity=False
        )
        public_user = User.objects.create_user("publicwatcher", password="pass12345")
        self.public_profile = Profile.objects.create(user=public_user, display_name="PublicWatcher")

    def test_watch_events_from_a_private_profile_are_excluded(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        WatchEvent.objects.create(profile=self.private_profile, title=title, watched_at="2024-01-01T00:00:00Z")
        WatchEvent.objects.create(profile=self.public_profile, title=title, watched_at="2024-01-02T00:00:00Z")
        feed = selectors.activity_feed()
        self.assertEqual(len(feed), 1)
        self.assertEqual(feed[0]["profile"], self.public_profile)

    def test_list_adds_from_a_private_profile_are_excluded(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        private_list = WatchList.objects.create(profile=self.private_profile, name="Watchlist")
        public_list = WatchList.objects.create(profile=self.public_profile, name="Watchlist")
        WatchListItem.objects.create(watchlist=private_list, title=title)
        WatchListItem.objects.create(watchlist=public_list, title=title)
        feed = selectors.activity_feed()
        self.assertEqual(len(feed), 1)
        self.assertEqual(feed[0]["profile"], self.public_profile)


class ActivityViewTemplateTests(TestCase):
    """The collapsed-summary-only redesign - no per-episode expand/chevron
    interaction (that's History's job), just a type-coded summary line."""

    def setUp(self):
        from django.utils import timezone

        user = User.objects.create_user("activityviewer", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="ActivityViewer")
        other_user = User.objects.create_user("activityother", password="pass12345")
        Profile.objects.create(user=other_user, display_name="ActivityOther")
        self.show = Title.objects.create(media_type=MediaType.TV, name="Bleach", year=2004)
        self.now = timezone.now()
        self.client.login(username="activityviewer", password="pass12345")

    def test_no_expand_chevron_or_alpine_state_on_a_group(self):
        for i, minutes_ago in enumerate([30, 20, 10]):
            ep = Episode.objects.create(title=self.show, season=1, episode=1 + i)
            WatchEvent.objects.create(profile=self.profile, title=self.show, episode=ep, watched_at=self.now - timedelta(minutes=minutes_ago))
        resp = self.client.get(reverse("activity"))
        body = resp.content.decode()
        self.assertContains(resp, "<b>3</b> episodes")
        self.assertContains(resp, "S1E1")
        self.assertNotIn("chevron-down", body)
        # Scoped to the feed container itself - the page's own sidebar/
        # topbar chrome legitimately uses x-data/x-show elsewhere
        # (sidebar toggle, notification bell), unrelated to this feature.
        feed_html = body.split('rounded-2xl p-2">', 1)[1].split("</main>", 1)[0]
        self.assertNotIn("x-data", feed_html)
        self.assertNotIn("x-show", feed_html)

    def test_no_leaked_template_comment_text(self):
        # Regression test: a multi-line {# ... #} comment isn't valid
        # Django template syntax (that tag is single-line only) and
        # silently renders as literal page text per feed row instead of
        # being stripped - shipped exactly that way once already.
        for i, minutes_ago in enumerate([30, 20, 10]):
            ep = Episode.objects.create(title=self.show, season=1, episode=1 + i)
            WatchEvent.objects.create(profile=self.profile, title=self.show, episode=ep, watched_at=self.now - timedelta(minutes=minutes_ago))
        resp = self.client.get(reverse("activity"))
        self.assertNotContains(resp, "{#")
        self.assertNotContains(resp, "{%")

    def test_watched_group_gets_the_primary_accent(self):
        for i, minutes_ago in enumerate([30, 20]):
            ep = Episode.objects.create(title=self.show, season=1, episode=1 + i)
            WatchEvent.objects.create(profile=self.profile, title=self.show, episode=ep, watched_at=self.now - timedelta(minutes=minutes_ago))
        resp = self.client.get(reverse("activity"))
        self.assertContains(resp, "border-l-primary")

    def test_rated_watch_gets_the_warning_accent(self):
        ep = Episode.objects.create(title=self.show, season=1, episode=1)
        WatchEvent.objects.create(profile=self.profile, title=self.show, episode=ep, watched_at=self.now, user_rating=9)
        resp = self.client.get(reverse("activity"))
        self.assertContains(resp, "border-l-warning")

    def test_added_to_list_gets_the_secondary_accent(self):
        watchlist = WatchList.objects.create(profile=self.profile, name="Anime")
        WatchListItem.objects.create(watchlist=watchlist, title=self.show)
        resp = self.client.get(reverse("activity"))
        self.assertContains(resp, "border-l-secondary")


class TitleDetailViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("detailviewer", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="DetailViewer")
        self.client.login(username="detailviewer", password="pass12345")
        self.title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020,
            external_ids={"tmdb": "42", "tmdb_kind": "movie"},
        )
        # every test in this class hits title_detail, which now also calls
        # these - patched here (not per-method) so adding them didn't
        # require touching every test's mock signature. get_season_details
        # is only actually invoked when a test's _details() sets a real
        # number_of_seasons (default None), but it's patched unconditionally
        # here too so nothing in this class can attempt a real HTTP call.
        for name, default in (
            ("get_director", None), ("get_watch_providers", []), ("get_season_details", None),
        ):
            patcher = patch(f"tracker.integrations.tmdb.{name}", return_value=default)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _details(self, **overrides):
        base = {
            "tmdb_id": 42, "media_type": "movie", "name": "Fathom", "year": "2020",
            "overview": "A movie.", "tagline": "", "genres": ["Drama"], "runtime": 100,
            "number_of_seasons": None, "number_of_episodes": None,
            "backdrop_url": None, "poster_url": None, "vote_average": 7.0,
            "vote_count": 100, "original_language": "en", "status": None,
        }
        base.update(overrides)
        return base

    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_renders_200_with_tmdb_details(self, mock_details, mock_credits, mock_similar):
        mock_details.return_value = self._details()
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Fathom")
        self.assertContains(resp, "A movie.")
        mock_details.assert_called_once_with("movie", "42")

    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_age_rating_badge_renders_next_to_the_language_badge(self, mock_details, mock_credits, mock_similar):
        mock_details.return_value = self._details(certification="R")
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertContains(resp, ">R<")

    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_no_age_rating_badge_when_tmdb_has_no_certification(self, mock_details, mock_credits, mock_similar):
        mock_details.return_value = self._details(certification=None)
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertEqual(resp.status_code, 200)

    def test_404s_for_nonexistent_title(self):
        resp = self.client.get(reverse("title_detail", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details", return_value=None)
    def test_renders_with_local_data_only_when_tmdb_unavailable(self, mock_details, mock_credits, mock_similar):
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Fathom")

    def test_no_tmdb_call_when_title_has_no_tmdb_id(self):
        untracked_tmdb = Title.objects.create(media_type=MediaType.MOVIE, name="No TMDB", year=2021)
        with patch("tracker.integrations.tmdb.get_full_details") as mock_details:
            resp = self.client.get(reverse("title_detail", args=[untracked_tmdb.pk]))
        self.assertEqual(resp.status_code, 200)
        mock_details.assert_not_called()

    def test_header_shows_mark_as_watched_before_any_plain_watch(self):
        never_watched = Title.objects.create(media_type=MediaType.MOVIE, name="Never Watched", year=2021)
        resp = self.client.get(reverse("title_detail", args=[never_watched.pk]))
        self.assertFalse(resp.context["is_watched"])
        self.assertContains(resp, "+ Mark as Watched")
        self.assertContains(resp, reverse("title_mark_watched", args=[never_watched.pk]))
        self.assertNotContains(resp, "Remove your watched mark for")

    def test_header_shows_watched_toggle_once_a_plain_watch_exists(self):
        from django.utils import timezone

        already_watched = Title.objects.create(media_type=MediaType.MOVIE, name="Already Watched", year=2021)
        WatchEvent.objects.create(profile=self.profile, title=already_watched, watched_at=timezone.now())
        resp = self.client.get(reverse("title_detail", args=[already_watched.pk]))
        self.assertTrue(resp.context["is_watched"])
        self.assertEqual(resp.context["watch_count"], 1)
        self.assertContains(resp, "&#10003; Watched")
        self.assertContains(resp, reverse("title_unmark_watched", args=[already_watched.pk]))
        # Same popover menu as the poster card's watched button - not the
        # old bare toggle-with-JS-confirm.
        self.assertContains(resp, "View history plays")
        self.assertContains(resp, "Mark as watched again")
        self.assertContains(resp, "Remove last watched")
        self.assertContains(resp, "Remove all watched history")
        self.assertNotContains(resp, "Remove your watched mark for")

    def test_header_shows_a_count_badge_once_watched_more_than_once(self):
        from django.utils import timezone

        rewatched = Title.objects.create(media_type=MediaType.MOVIE, name="Rewatched", year=2021)
        WatchEvent.objects.create(profile=self.profile, title=rewatched, watched_at=timezone.now() - timedelta(days=1))
        WatchEvent.objects.create(profile=self.profile, title=rewatched, watched_at=timezone.now())
        resp = self.client.get(reverse("title_detail", args=[rewatched.pk]))
        self.assertEqual(resp.context["watch_count"], 2)
        self.assertContains(resp, "&times;2")

    def test_header_stays_unwatched_when_only_episode_level_history_exists(self):
        # A show watched only via the episode browser (no whole-title
        # header click yet) shouldn't falsely claim to be fully done.
        from django.utils import timezone

        show = Title.objects.create(
            media_type=MediaType.TV, name="Partly Watched Show", year=2021,
            external_ids={"tmdb": "77", "tmdb_kind": "tv"},
        )
        episode = Episode.objects.create(title=show, season=1, episode=1)
        WatchEvent.objects.create(profile=self.profile, title=show, episode=episode, watched_at=timezone.now())
        with patch("tracker.integrations.tmdb.get_full_details", return_value=None):
            resp = self.client.get(reverse("title_detail", args=[show.pk]))
        self.assertFalse(resp.context["is_watched"])
        # Shows don't get the movie-style single header "Watched" control
        # at all (see test_header_button_is_movie_only) - nothing to
        # assert about its state here beyond the context flag above.
        self.assertNotContains(resp, "+ Mark as Watched")
        self.assertNotContains(resp, "&#10003; Watched")

    def test_header_button_is_movie_only(self):
        show = Title.objects.create(
            media_type=MediaType.TV, name="A Show", year=2021, external_ids={"tmdb": "88", "tmdb_kind": "tv"},
        )
        with patch("tracker.integrations.tmdb.get_full_details", return_value=None):
            resp = self.client.get(reverse("title_detail", args=[show.pk]))
        self.assertNotContains(resp, "+ Mark as Watched")
        self.assertNotContains(resp, reverse("title_mark_watched", args=[show.pk]))

    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_shows_watch_history_and_rating(self, mock_details, mock_credits, mock_similar):
        from django.utils import timezone

        mock_details.return_value = self._details()
        WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=timezone.now(), user_rating=8)
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertEqual(resp.context["latest_rating"], 8)
        self.assertEqual(len(resp.context["recent_events"]), 1)

    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_shows_which_lists_it_is_already_in(self, mock_details, mock_credits, mock_similar):
        mock_details.return_value = self._details()
        watchlist = WatchList.objects.create(profile=self.profile, name="Favorites")
        WatchListItem.objects.create(watchlist=watchlist, title=self.title)
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertIn(watchlist.id, resp.context["in_list_ids"])

    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_shows_a_status_badge_for_an_ended_show(self, mock_details, mock_credits, mock_similar):
        mock_details.return_value = self._details(status="Ended")
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertEqual(resp.context["status_badge"], {"label": "Ended", "color": "ink-dim"})
        self.assertContains(resp, "Ended")

    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_no_status_badge_for_a_released_movie(self, mock_details, mock_credits, mock_similar):
        mock_details.return_value = self._details(status="Released")
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertIsNone(resp.context["status_badge"])


class TitleEpisodeBrowserTests(TestCase):
    """The episode browser - season <select>, per-episode watched badges,
    and the default-season "resume where you left off" logic (the
    highest season the profile has any watched episode in)."""

    def setUp(self):
        user = User.objects.create_user("episodewatcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="EpisodeWatcher")
        self.client.login(username="episodewatcher", password="pass12345")
        self.title = Title.objects.create(
            media_type=MediaType.TV, name="Silo", year=2023,
            external_ids={"tmdb": "99", "tmdb_kind": "tv"},
        )
        for name, default in (("get_director", None), ("get_watch_providers", [])):
            patcher = patch(f"tracker.integrations.tmdb.{name}", return_value=default)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _details(self, number_of_seasons=3):
        return {
            "tmdb_id": 99, "media_type": "tv", "name": "Silo", "year": "2023",
            "overview": "", "tagline": "", "genres": [], "runtime": None,
            "number_of_seasons": number_of_seasons, "number_of_episodes": 30,
            "backdrop_url": None, "poster_url": None, "vote_average": 7.0,
            "vote_count": 100, "original_language": "en", "status": None,
        }

    def _season(self, episode_names, vote_averages=None):
        return {
            "episodes": [
                {
                    "episode_number": i + 1, "name": name, "still_url": None, "air_date": None,
                    "vote_average": vote_averages[i] if vote_averages else None,
                }
                for i, name in enumerate(episode_names)
            ]
        }

    def _tv_details(self, season_ratings=None, number_of_seasons=3):
        season_ratings = season_ratings or {}
        return {
            "number_of_episodes": 30,
            "episode_run_time": None,
            "seasons": [
                {"season_number": s, "episode_count": 10, "vote_average": season_ratings.get(s)}
                for s in range(1, number_of_seasons + 1)
            ],
        }

    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_defaults_to_season_1_with_no_watch_history(self, mock_details, mock_credits, mock_similar, mock_season):
        mock_details.return_value = self._details()
        mock_season.return_value = self._season(["Freedom Day", "Holston's Pick"])
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertEqual(resp.context["season"], 1)
        self.assertEqual(resp.context["seasons"], [1, 2, 3])
        mock_season.assert_called_once_with("99", 1)

    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_defaults_to_the_highest_watched_season(self, mock_details, mock_credits, mock_similar, mock_season):
        from django.utils import timezone

        mock_details.return_value = self._details()
        mock_season.return_value = self._season(["Ep"])
        ep = Episode.objects.create(title=self.title, season=2, episode=1)
        WatchEvent.objects.create(profile=self.profile, title=self.title, episode=ep, watched_at=timezone.now())
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertEqual(resp.context["season"], 2)

    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_marks_watched_episodes_and_flags_the_finale(self, mock_details, mock_credits, mock_similar, mock_season):
        from django.utils import timezone

        mock_details.return_value = self._details()
        mock_season.return_value = self._season(["Ep1", "Ep2", "Ep3"])
        ep2 = Episode.objects.create(title=self.title, season=1, episode=2)
        WatchEvent.objects.create(profile=self.profile, title=self.title, episode=ep2, watched_at=timezone.now())
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        episodes = resp.context["episodes"]
        self.assertEqual([e["watched"] for e in episodes], [False, True, False])
        self.assertTrue(episodes[-1]["is_finale"])
        self.assertNotIn("is_finale", episodes[0])

    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_season_total_runtime_sums_every_episode_with_a_known_runtime(
        self, mock_details, mock_credits, mock_similar, mock_season
    ):
        mock_details.return_value = self._details()
        mock_season.return_value = {
            "episodes": [
                {"episode_number": 1, "name": "Ep1", "still_url": None, "air_date": None, "vote_average": None, "runtime": 45},
                {"episode_number": 2, "name": "Ep2", "still_url": None, "air_date": None, "vote_average": None, "runtime": 50},
                # No runtime for this one - excluded from the total, not
                # treated as 0, same convention season_avg_rating already
                # uses for missing vote_average.
                {"episode_number": 3, "name": "Ep3", "still_url": None, "air_date": None, "vote_average": None, "runtime": None},
            ]
        }
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertEqual(resp.context["episodes"][0]["runtime"], 45)
        self.assertEqual(resp.context["season_total_runtime"], "1h 35m")
        self.assertContains(resp, "1h 35m total")
        self.assertContains(resp, "45m")

    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_season_total_runtime_is_none_when_no_episode_has_a_runtime(
        self, mock_details, mock_credits, mock_similar, mock_season
    ):
        mock_details.return_value = self._details()
        mock_season.return_value = self._season(["Ep1", "Ep2"])
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertIsNone(resp.context["season_total_runtime"])
        self.assertNotContains(resp, "total</span>")

    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details", return_value=None)
    def test_movies_never_show_an_episode_browser(self, mock_details, mock_credits, mock_similar, mock_season):
        movie = Title.objects.create(
            media_type=MediaType.MOVIE, name="A Movie", year=2020, external_ids={"tmdb": "1"}
        )
        resp = self.client.get(reverse("title_detail", args=[movie.pk]))
        self.assertEqual(resp.context["seasons"], [])
        mock_season.assert_not_called()

    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_season_select_switches_via_the_episodes_endpoint(self, mock_details, mock_season):
        mock_details.return_value = self._details()
        mock_season.return_value = self._season(["S2 Ep1"])
        resp = self.client.get(reverse("title_episodes", args=[self.title.pk]), {"season": "2"})
        self.assertEqual(resp.context["season"], 2)
        mock_season.assert_called_once_with("99", 2)

    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_out_of_range_season_falls_back_to_default(self, mock_details, mock_season):
        mock_details.return_value = self._details(number_of_seasons=2)
        mock_season.return_value = self._season(["Ep"])
        resp = self.client.get(reverse("title_episodes", args=[self.title.pk]), {"season": "99"})
        self.assertEqual(resp.context["season"], 1)

    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_season_avg_rating_is_the_mean_of_rated_episodes(self, mock_details, mock_credits, mock_similar, mock_season):
        mock_details.return_value = self._details()
        mock_season.return_value = self._season(["Ep1", "Ep2", "Ep3"], vote_averages=[8.0, 9.0, None])
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertEqual(resp.context["season_avg_rating"], 8.5)

    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_season_avg_rating_is_none_when_nothing_is_rated_yet(self, mock_details, mock_credits, mock_similar, mock_season):
        mock_details.return_value = self._details()
        # 0 is TMDB's "not enough votes" convention, same as a real None -
        # neither should count toward the average.
        mock_season.return_value = self._season(["Ep1", "Ep2"], vote_averages=[0, None])
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertIsNone(resp.context["season_avg_rating"])

    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_episode_and_season_rating_render_on_the_page(self, mock_details, mock_credits, mock_similar, mock_season):
        mock_details.return_value = self._details()
        mock_season.return_value = self._season(["Ep1"], vote_averages=[8.4])
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertContains(resp, "8.4")
        self.assertContains(resp, "season avg")

    @patch("tracker.integrations.tmdb.get_tv_details")
    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_season_ratings_available_for_every_season_not_just_the_selected_one(
        self, mock_details, mock_credits, mock_similar, mock_season, mock_tv_details
    ):
        mock_details.return_value = self._details()
        mock_season.return_value = self._season(["Ep"])
        mock_tv_details.return_value = self._tv_details(season_ratings={1: 7.5, 2: 8.9, 3: 0})
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertEqual(resp.context["season_ratings"], {1: 7.5, 2: 8.9, 3: None})
        self.assertContains(resp, "7.5")
        self.assertContains(resp, "8.9")

    @patch("tracker.integrations.tmdb.get_tv_details", return_value=None)
    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_season_ratings_empty_when_tv_details_unavailable(
        self, mock_details, mock_credits, mock_similar, mock_season, mock_tv_details
    ):
        mock_details.return_value = self._details()
        mock_season.return_value = self._season(["Ep"])
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        self.assertEqual(resp.context["season_ratings"], {})

    @patch("tracker.integrations.tmdb.get_tv_details", return_value=None)
    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_mobile_and_desktop_episode_layouts_both_render_with_distinct_button_ids(
        self, mock_details, mock_credits, mock_similar, mock_season, mock_tv_details
    ):
        # The compact mobile row list and the sm:+ card grid render the
        # same episodes twice (one hidden via CSS at any given
        # breakpoint) - episode_watched_button.html's id_suffix keeps
        # their button ids from colliding (see EpisodeMarkWatchedTests
        # for the round-trip through a click).
        mock_details.return_value = self._details()
        mock_season.return_value = self._season(["Freedom Day"])
        resp = self.client.get(reverse("title_detail", args=[self.title.pk]))
        content = resp.content.decode()
        self.assertIn(f'id="ep-watched-btn-{self.title.pk}-1-1"', content)
        self.assertIn(f'id="ep-watched-btn-{self.title.pk}-1-1-m"', content)
        self.assertEqual(content.count("Freedom Day"), 2)


class TitleMarkSeasonWatchedTests(TestCase):
    """The episode browser's "Mark season watched"/"Mark all watched"
    bulk actions - unlike a single episode tile's own watched button,
    these only add a play for an episode that doesn't already have one
    (catching watched status up to reality, not logging a rewatch of
    everything already seen)."""

    def setUp(self):
        from django.utils import timezone

        self.timezone = timezone
        user = User.objects.create_user("bulkwatcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="BulkWatcher")
        self.client.login(username="bulkwatcher", password="pass12345")
        self.title = Title.objects.create(
            media_type=MediaType.TV, name="Silo", year=2023, external_ids={"tmdb": "99", "tmdb_kind": "tv"},
        )

    def _season(self, episode_names):
        return {
            "episodes": [
                {"episode_number": i + 1, "name": name, "still_url": None, "air_date": None, "vote_average": None}
                for i, name in enumerate(episode_names)
            ]
        }

    @patch("tracker.integrations.tmdb.get_tv_details", return_value=None)
    @patch("tracker.integrations.tmdb.get_full_details", return_value=None)
    @patch("tracker.integrations.tmdb.get_season_details")
    def test_marks_every_unwatched_episode_in_the_season(self, mock_season, mock_details, mock_tv_details):
        mock_season.return_value = self._season(["Ep1", "Ep2", "Ep3"])
        resp = self.client.post(reverse("title_mark_season_watched", args=[self.title.pk, 1]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            WatchEvent.objects.filter(profile=self.profile, title=self.title, episode__season=1).count(), 3
        )
        self.assertEqual(Episode.objects.get(title=self.title, season=1, episode=1).name, "Ep1")

    @patch("tracker.integrations.tmdb.get_tv_details", return_value=None)
    @patch("tracker.integrations.tmdb.get_full_details", return_value=None)
    @patch("tracker.integrations.tmdb.get_season_details")
    def test_does_not_duplicate_a_play_for_an_already_watched_episode(self, mock_season, mock_details, mock_tv_details):
        mock_season.return_value = self._season(["Ep1", "Ep2"])
        existing = Episode.objects.create(title=self.title, season=1, episode=1)
        WatchEvent.objects.create(profile=self.profile, title=self.title, episode=existing, watched_at=self.timezone.now())
        self.client.post(reverse("title_mark_season_watched", args=[self.title.pk, 1]))
        self.assertEqual(WatchEvent.objects.filter(profile=self.profile, title=self.title, episode=existing).count(), 1)
        self.assertEqual(
            WatchEvent.objects.filter(profile=self.profile, title=self.title, episode__episode=2).count(), 1
        )

    @patch("tracker.integrations.tmdb.get_tv_details", return_value=None)
    @patch("tracker.integrations.tmdb.get_full_details", return_value=None)
    @patch("tracker.integrations.tmdb.get_season_details")
    def test_leaves_other_seasons_untouched(self, mock_season, mock_details, mock_tv_details):
        mock_season.return_value = self._season(["Ep1"])
        self.client.post(reverse("title_mark_season_watched", args=[self.title.pk, 2]))
        self.assertFalse(WatchEvent.objects.filter(profile=self.profile, title=self.title, episode__season=1).exists())
        self.assertTrue(WatchEvent.objects.filter(profile=self.profile, title=self.title, episode__season=2).exists())

    @patch("tracker.integrations.tmdb.get_tv_details", return_value=None)
    @patch("tracker.integrations.tmdb.get_full_details")
    @patch("tracker.integrations.tmdb.get_season_details")
    def test_response_is_pinned_to_the_marked_season(self, mock_season, mock_details, mock_tv_details):
        mock_details.return_value = {
            "tmdb_id": 99, "media_type": "tv", "name": "Silo", "year": "2023",
            "overview": "", "tagline": "", "genres": [], "runtime": None,
            "number_of_seasons": 3, "number_of_episodes": 30,
            "backdrop_url": None, "poster_url": None, "vote_average": 7.0,
            "vote_count": 100, "original_language": "en", "status": None,
        }
        mock_season.return_value = self._season(["Ep1"])
        resp = self.client.post(reverse("title_mark_season_watched", args=[self.title.pk, 2]))
        self.assertEqual(resp.context["season"], 2)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("title_mark_season_watched", args=[self.title.pk, 1]))
        self.assertNotEqual(resp.status_code, 200)

    def test_requires_post(self):
        resp = self.client.get(reverse("title_mark_season_watched", args=[self.title.pk, 1]))
        self.assertEqual(resp.status_code, 405)


class TitleMarkAllSeasonsWatchedTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("bulkwatcherall", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="BulkWatcherAll")
        self.client.login(username="bulkwatcherall", password="pass12345")
        self.title = Title.objects.create(
            media_type=MediaType.TV, name="Silo", year=2023, external_ids={"tmdb": "99", "tmdb_kind": "tv"},
        )

    def _details(self, number_of_seasons=2):
        return {
            "tmdb_id": 99, "media_type": "tv", "name": "Silo", "year": "2023",
            "overview": "", "tagline": "", "genres": [], "runtime": None,
            "number_of_seasons": number_of_seasons, "number_of_episodes": 4,
            "backdrop_url": None, "poster_url": None, "vote_average": 7.0,
            "vote_count": 100, "original_language": "en", "status": None,
        }

    def _season(self, episode_names):
        return {
            "episodes": [
                {"episode_number": i + 1, "name": name, "still_url": None, "air_date": None, "vote_average": None}
                for i, name in enumerate(episode_names)
            ]
        }

    @patch("tracker.integrations.tmdb.get_tv_details", return_value=None)
    @patch("tracker.integrations.tmdb.get_full_details")
    @patch("tracker.integrations.tmdb.get_season_details")
    def test_marks_every_episode_across_every_season(self, mock_season, mock_details, mock_tv_details):
        mock_details.return_value = self._details(number_of_seasons=2)
        mock_season.return_value = self._season(["Ep1", "Ep2"])
        resp = self.client.post(reverse("title_mark_all_seasons_watched", args=[self.title.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(WatchEvent.objects.filter(profile=self.profile, title=self.title).count(), 4)
        # One get_season_details call per season while marking, plus one
        # more afterward to build the display context for whichever
        # season ends up "current" in the response.
        mock_season.assert_any_call("99", 1)
        mock_season.assert_any_call("99", 2)

    @patch("tracker.integrations.tmdb.get_tv_details", return_value=None)
    @patch("tracker.integrations.tmdb.get_full_details")
    @patch("tracker.integrations.tmdb.get_season_details")
    def test_completes_the_show_when_every_episode_is_now_watched(self, mock_season, mock_details, mock_tv_details):
        mock_details.return_value = self._details(number_of_seasons=1)
        mock_season.return_value = self._season(["Ep1", "Ep2"])
        with patch("tracker.completion.tmdb.get_tv_details", return_value={"number_of_episodes": 2, "episode_run_time": None, "seasons": []}):
            self.client.post(reverse("title_mark_all_seasons_watched", args=[self.title.pk]))
        self.assertTrue(
            WatchProgress.objects.filter(
                profile=self.profile, title=self.title, status=WatchProgress.Status.COMPLETED
            ).exists()
        )

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("title_mark_all_seasons_watched", args=[self.title.pk]))
        self.assertNotEqual(resp.status_code, 200)

    def test_requires_post(self):
        resp = self.client.get(reverse("title_mark_all_seasons_watched", args=[self.title.pk]))
        self.assertEqual(resp.status_code, 405)


class PreviewEpisodeBrowserTests(TestCase):
    """A TV/anime title's episode browser should show up on its preview
    page too (not yet added to any list/watchlist/marked watched), not
    just once it's been materialized into a real Title row - previously
    the whole browser was hidden behind {% if not is_preview %}."""

    def setUp(self):
        user = User.objects.create_user("previewepisodeuser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="PreviewEpisodeUser")
        self.client.login(username="previewepisodeuser", password="pass12345")
        for name, default in (
            ("get_director", None), ("get_watch_providers", []), ("get_credits", []), ("get_similar", []),
        ):
            patcher = patch(f"tracker.integrations.tmdb.{name}", return_value=default)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _details(self, number_of_seasons=2):
        return {
            "tmdb_id": 500, "media_type": "tv", "name": "Preview Show", "year": "2023",
            "overview": "", "tagline": "", "genres": [], "runtime": None,
            "number_of_seasons": number_of_seasons, "number_of_episodes": 20,
            "backdrop_url": None, "poster_url": None, "vote_average": 7.0,
            "vote_count": 100, "original_language": "en", "status": None,
        }

    def _season(self):
        return {"episodes": [{"episode_number": 1, "name": "Pilot", "still_url": None, "air_date": None, "vote_average": None}]}

    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_preview_page_shows_seasons_and_episodes(self, mock_details, mock_season):
        mock_details.return_value = self._details()
        mock_season.return_value = self._season()
        resp = self.client.get(reverse("title_preview", args=["tv", 500]))
        self.assertEqual(resp.context["seasons"], [1, 2])
        self.assertContains(resp, "Pilot")

    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_preview_episodes_are_never_shown_as_watched(self, mock_details, mock_season):
        mock_details.return_value = self._details()
        mock_season.return_value = self._season()
        resp = self.client.get(reverse("title_preview", args=["tv", 500]))
        self.assertFalse(resp.context["episodes"][0]["watched"])

    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_preview_page_has_no_watched_button_for_episodes(self, mock_details, mock_season):
        mock_details.return_value = self._details()
        mock_season.return_value = self._season()
        resp = self.client.get(reverse("title_preview", args=["tv", 500]))
        self.assertNotContains(resp, "ep-watched-btn-")

    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_movie_preview_still_shows_no_episode_browser(self, mock_details, mock_season):
        mock_details.return_value = self._details(number_of_seasons=None)
        resp = self.client.get(reverse("title_preview", args=["movie", 500]))
        self.assertEqual(resp.context["seasons"], [])
        mock_season.assert_not_called()

    @patch("tracker.integrations.tmdb.get_season_details")
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_season_select_switches_via_the_preview_episodes_endpoint(self, mock_details, mock_season):
        mock_details.return_value = self._details()
        mock_season.return_value = {"episodes": [{"episode_number": 1, "name": "S2 Ep1", "still_url": None, "air_date": None, "vote_average": None}]}
        resp = self.client.get(reverse("title_preview_episodes", args=["tv", 500]), {"season": "2"})
        self.assertEqual(resp.context["season"], 2)
        mock_season.assert_called_once_with(500, 2)


class TitleMarkWatchedAndRateTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("tracker_user", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="TrackerUser")
        self.client.login(username="tracker_user", password="pass12345")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)

    def test_mark_watched_creates_a_plain_watch_event(self):
        resp = self.client.post(reverse("title_mark_watched", args=[self.title.pk]))
        self.assertRedirects(resp, reverse("title_detail", args=[self.title.pk]), fetch_redirect_response=False)
        event = WatchEvent.objects.get(profile=self.profile, title=self.title)
        self.assertIsNone(event.episode)
        self.assertIsNone(event.user_rating)

    def test_mark_watched_a_second_time_flags_it_as_a_rewatch(self):
        self.client.post(reverse("title_mark_watched", args=[self.title.pk]))
        self.client.post(reverse("title_mark_watched", args=[self.title.pk]))
        events = list(WatchEvent.objects.filter(profile=self.profile, title=self.title).order_by("watched_at"))
        self.assertEqual(len(events), 2)
        self.assertFalse(events[0].is_rewatch)
        self.assertTrue(events[1].is_rewatch)

    def test_mark_watched_requires_get_is_rejected(self):
        resp = self.client.get(reverse("title_mark_watched", args=[self.title.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_mark_watched_via_htmx_returns_the_watched_button_fragment_not_a_redirect(self):
        resp = self.client.post(reverse("title_mark_watched", args=[self.title.pk]), HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"watched-btn-{self.title.pk}")
        self.assertContains(resp, "text-success")
        self.assertTrue(WatchEvent.objects.filter(profile=self.profile, title=self.title).exists())

    def test_returned_fragment_guards_further_clicks_with_a_confirm(self):
        # The fragment handed back after marking watched is itself already
        # in the "watched" state - a second, likely-accidental click on it
        # shouldn't silently log another rewatch, so it carries hx-confirm.
        resp = self.client.post(reverse("title_mark_watched", args=[self.title.pk]), HTTP_HX_REQUEST="true")
        self.assertContains(resp, "hx-confirm")

    def test_rate_with_no_prior_watch_creates_a_watch_event(self):
        resp = self.client.post(reverse("title_rate", args=[self.title.pk]), {"rating": "7"})
        self.assertRedirects(resp, reverse("title_detail", args=[self.title.pk]), fetch_redirect_response=False)
        event = WatchEvent.objects.get(profile=self.profile, title=self.title)
        self.assertEqual(event.user_rating, 7)

    def test_rate_with_a_prior_watch_updates_the_most_recent_one(self):
        from django.utils import timezone

        older = WatchEvent.objects.create(
            profile=self.profile, title=self.title, watched_at=timezone.now() - timedelta(days=5)
        )
        newer = WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=timezone.now())
        self.client.post(reverse("title_rate", args=[self.title.pk]), {"rating": "9"})
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertIsNone(older.user_rating)
        self.assertEqual(newer.user_rating, 9)
        self.assertEqual(WatchEvent.objects.filter(profile=self.profile, title=self.title).count(), 2)

    def test_rate_rejects_out_of_range_values(self):
        resp = self.client.post(reverse("title_rate", args=[self.title.pk]), {"rating": "11"})
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(WatchEvent.objects.filter(profile=self.profile, title=self.title).exists())

    def test_rate_rejects_non_numeric_values(self):
        resp = self.client.post(reverse("title_rate", args=[self.title.pk]), {"rating": "great"})
        self.assertEqual(resp.status_code, 404)

    def test_mark_watched_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("title_mark_watched", args=[self.title.pk]))
        self.assertNotEqual(resp.status_code, 200)


class TitleUnmarkWatchedTests(TestCase):
    """The detail page's header 'Watched' toggle, once already watched -
    unlike title_mark_watched's other callers (poster card, episode
    browser), this is a genuine unmark, not another rewatch log."""

    def setUp(self):
        from django.utils import timezone

        self.timezone = timezone
        user = User.objects.create_user("unmarkuser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="UnmarkUser")
        self.client.login(username="unmarkuser", password="pass12345")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)

    def test_removes_the_plain_watch_mark(self):
        WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=self.timezone.now())
        resp = self.client.post(reverse("title_unmark_watched", args=[self.title.pk]))
        self.assertRedirects(resp, reverse("title_detail", args=[self.title.pk]), fetch_redirect_response=False)
        self.assertFalse(WatchEvent.objects.filter(profile=self.profile, title=self.title).exists())

    def test_removes_every_plain_rewatch_too(self):
        WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=self.timezone.now() - timedelta(days=1))
        WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=self.timezone.now())
        self.client.post(reverse("title_unmark_watched", args=[self.title.pk]))
        self.assertEqual(WatchEvent.objects.filter(profile=self.profile, title=self.title).count(), 0)

    def test_leaves_episode_level_history_untouched(self):
        show = Title.objects.create(
            media_type=MediaType.TV, name="Silo", year=2023, external_ids={"tmdb": "99", "tmdb_kind": "tv"}
        )
        episode = Episode.objects.create(title=show, season=1, episode=1)
        episode_event = WatchEvent.objects.create(profile=self.profile, title=show, episode=episode, watched_at=self.timezone.now())
        WatchEvent.objects.create(profile=self.profile, title=show, watched_at=self.timezone.now())
        self.client.post(reverse("title_unmark_watched", args=[show.pk]))
        self.assertTrue(WatchEvent.objects.filter(pk=episode_event.pk).exists())
        self.assertFalse(WatchEvent.objects.filter(profile=self.profile, title=show, episode__isnull=True).exists())

    def test_leaves_another_profiles_watch_marks_untouched(self):
        other_user = User.objects.create_user("unmarkother", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="UnmarkOther")
        other_event = WatchEvent.objects.create(profile=other_profile, title=self.title, watched_at=self.timezone.now())
        self.client.post(reverse("title_unmark_watched", args=[self.title.pk]))
        self.assertTrue(WatchEvent.objects.filter(pk=other_event.pk).exists())

    def test_no_op_when_nothing_to_unmark(self):
        resp = self.client.post(reverse("title_unmark_watched", args=[self.title.pk]))
        self.assertEqual(resp.status_code, 302)

    def test_requires_get_is_rejected(self):
        resp = self.client.get(reverse("title_unmark_watched", args=[self.title.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("title_unmark_watched", args=[self.title.pk]))
        self.assertNotEqual(resp.status_code, 200)

    def test_via_htmx_returns_the_unwatched_button_fragment_not_a_redirect(self):
        WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=self.timezone.now())
        resp = self.client.post(reverse("title_unmark_watched", args=[self.title.pk]), HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"watched-btn-{self.title.pk}")
        self.assertContains(resp, "Mark as watched")
        self.assertNotContains(resp, "text-success")

    def test_via_htmx_still_shows_watched_when_episode_history_remains(self):
        # Regression: clearing the plain watch marks for a show that also
        # has episode-level history shouldn't flip its poster card back to
        # the never-watched state - the episode history is still there.
        show = Title.objects.create(
            media_type=MediaType.TV, name="Silo", year=2023, external_ids={"tmdb": "99", "tmdb_kind": "tv"}
        )
        episode = Episode.objects.create(title=show, season=1, episode=1)
        WatchEvent.objects.create(profile=self.profile, title=show, episode=episode, watched_at=self.timezone.now())
        WatchEvent.objects.create(profile=self.profile, title=show, watched_at=self.timezone.now())
        resp = self.client.post(reverse("title_unmark_watched", args=[show.pk]), HTTP_HX_REQUEST="true")
        self.assertContains(resp, "text-success")
        self.assertNotContains(resp, 'title="Mark as watched"')


class TitleUnmarkLastWatchedTests(TestCase):
    """The poster card watched-button popover's "Remove last watched" -
    undoes a single play, unlike title_unmark_watched (which clears every
    plain watch mark for the title)."""

    def setUp(self):
        from django.utils import timezone

        self.timezone = timezone
        user = User.objects.create_user("unmarklastuser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="UnmarkLastUser")
        self.client.login(username="unmarklastuser", password="pass12345")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)

    def test_removes_only_the_most_recent_play(self):
        older = WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=self.timezone.now() - timedelta(days=1))
        newer = WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=self.timezone.now())
        self.client.post(reverse("title_unmark_last_watched", args=[self.title.pk]))
        self.assertTrue(WatchEvent.objects.filter(pk=older.pk).exists())
        self.assertFalse(WatchEvent.objects.filter(pk=newer.pk).exists())

    def test_removing_the_only_play_leaves_the_title_unwatched(self):
        WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=self.timezone.now())
        resp = self.client.post(reverse("title_unmark_last_watched", args=[self.title.pk]), HTTP_HX_REQUEST="true")
        self.assertFalse(WatchEvent.objects.filter(profile=self.profile, title=self.title).exists())
        self.assertContains(resp, "Mark as watched")
        self.assertNotContains(resp, "text-success")

    def test_via_htmx_returns_the_fragment_still_showing_watched_when_plays_remain(self):
        WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=self.timezone.now() - timedelta(days=1))
        WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=self.timezone.now())
        resp = self.client.post(reverse("title_unmark_last_watched", args=[self.title.pk]), HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "text-success")

    def test_no_op_when_nothing_to_remove(self):
        resp = self.client.post(reverse("title_unmark_last_watched", args=[self.title.pk]))
        self.assertEqual(resp.status_code, 302)

    def test_requires_get_is_rejected(self):
        resp = self.client.get(reverse("title_unmark_last_watched", args=[self.title.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("title_unmark_last_watched", args=[self.title.pk]))
        self.assertNotEqual(resp.status_code, 200)


class PosterCardWatchedButtonPopoverTests(TestCase):
    """The watched-button popover (poster_card_watched_button.html) that
    replaces the bare checkmark once a title has been watched at least
    once - rendered via list_detail (real Title poster cards)."""

    def setUp(self):
        from django.utils import timezone

        self.timezone = timezone
        user = User.objects.create_user("watchedpopoveruser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="WatchedPopoverUser")
        self.client.login(username="watchedpopoveruser", password="pass12345")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        self.watchlist = WatchList.objects.create(profile=self.profile, name="Watchlist", is_watchlist=True)
        WatchListItem.objects.create(watchlist=self.watchlist, title=self.title)

    def test_unwatched_title_has_no_popover(self):
        resp = self.client.get(reverse("list_detail", args=[self.watchlist.pk]))
        self.assertNotContains(resp, "Remove all watched history")
        self.assertContains(resp, f'hx-post="{reverse("title_mark_watched", args=[self.title.pk])}"')

    def test_watched_once_shows_popover_with_no_count_badge(self):
        WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=self.timezone.now())
        resp = self.client.get(reverse("list_detail", args=[self.watchlist.pk]))
        self.assertContains(resp, "View history plays")
        self.assertContains(resp, "Mark as watched again")
        self.assertContains(resp, "Remove last watched")
        self.assertContains(resp, "Remove all watched history")
        self.assertNotContains(resp, "&times;1</span>")

    def test_watched_multiple_times_shows_count_badge(self):
        WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=self.timezone.now() - timedelta(days=1))
        WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=self.timezone.now())
        resp = self.client.get(reverse("list_detail", args=[self.watchlist.pk]))
        self.assertContains(resp, "&times;2</span>")

    def test_history_link_targets_this_title(self):
        WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=self.timezone.now())
        resp = self.client.get(reverse("list_detail", args=[self.watchlist.pk]))
        self.assertContains(resp, f'href="{reverse("history")}?title={self.title.pk}"')

    def test_show_watched_entirely_via_episode_browser_shows_popover_not_plain_button(self):
        # Regression: a show watched to completion through the episode
        # browser (title_mark_season_watched/title_mark_all_seasons_watched,
        # or one-by-one episode_mark_watched) never logs a plain,
        # episode-less WatchEvent, so it must not fall back to the
        # never-watched "Mark as watched" button on grids (Dashboard,
        # Watchlist, Search) just because plain_watch_count is 0.
        show = Title.objects.create(media_type=MediaType.TV, name="Silo", year=2023)
        episode = Episode.objects.create(title=show, season=1, episode=1)
        WatchEvent.objects.create(profile=self.profile, title=show, episode=episode, watched_at=self.timezone.now())
        WatchListItem.objects.create(watchlist=self.watchlist, title=show)
        resp = self.client.get(reverse("list_detail", args=[self.watchlist.pk]))
        self.assertContains(resp, "View history plays")
        self.assertContains(resp, f'id="watched-popover-{show.pk}"')


class WatchedButtonTemplateSelectionTests(TestCase):
    """title_detail's own header "Watched" control shares
    title_mark_watched/title_unmark_watched/title_unmark_last_watched
    with the poster card's watched button, but needs its own fragment
    back (title_detail_watched_button.html, not poster_card_watched_button.html)
    so the header keeps its pill styling and drop-down positioning
    instead of turning into the grid's compact icon square. Routed off
    HTMX's resolved HX-Target header - see views._watched_button_template."""

    def setUp(self):
        from django.utils import timezone

        self.timezone = timezone
        user = User.objects.create_user("templateselectuser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="TemplateSelectUser")
        self.client.login(username="templateselectuser", password="pass12345")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)

    def test_first_click_from_the_detail_page_returns_the_detail_fragment(self):
        # The click itself creates a WatchEvent (watch_count 0 -> 1), so
        # the response is the detail page's watched *popover*, not its
        # bare "+ Mark as Watched" state - the marker to check for is the
        # detail-prefixed popover wrapper, not the pre-click button id.
        resp = self.client.post(
            reverse("title_mark_watched", args=[self.title.pk]),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET=f"watched-btn-detail-{self.title.pk}",
        )
        self.assertContains(resp, f'id="watched-popover-detail-{self.title.pk}"')

    def test_first_click_from_a_poster_card_returns_the_grid_fragment(self):
        resp = self.client.post(
            reverse("title_mark_watched", args=[self.title.pk]),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET=f"watched-btn-{self.title.pk}",
        )
        self.assertNotContains(resp, f'id="watched-btn-detail-{self.title.pk}"')
        self.assertContains(resp, f'id="watched-popover-{self.title.pk}"')

    def test_menu_action_from_the_detail_page_returns_the_detail_fragment(self):
        WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=self.timezone.now())
        resp = self.client.post(
            reverse("title_unmark_last_watched", args=[self.title.pk]),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET=f"watched-popover-detail-{self.title.pk}",
        )
        self.assertContains(resp, "+ Mark as Watched")
        self.assertNotContains(resp, f'id="watched-popover-{self.title.pk}"')

    def test_menu_action_from_a_poster_card_returns_the_grid_fragment(self):
        WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=self.timezone.now() - timedelta(days=1))
        WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=self.timezone.now())
        resp = self.client.post(
            reverse("title_unmark_last_watched", args=[self.title.pk]),
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET=f"watched-popover-{self.title.pk}",
        )
        self.assertContains(resp, f'id="watched-popover-{self.title.pk}"')
        self.assertNotContains(resp, f'id="watched-popover-detail-{self.title.pk}"')


class EpisodeMarkWatchedTests(TestCase):
    """The episode browser's per-episode watched button."""

    def setUp(self):
        user = User.objects.create_user("episodeclicker", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="EpisodeClicker")
        self.client.login(username="episodeclicker", password="pass12345")
        self.title = Title.objects.create(
            media_type=MediaType.TV, name="Silo", year=2023, external_ids={"tmdb": "99", "tmdb_kind": "tv"}
        )

    @patch("tracker.integrations.tmdb.get_season_details")
    def test_creates_episode_and_watch_event_with_tmdb_name(self, mock_season):
        mock_season.return_value = {"episodes": [{"episode_number": 1, "name": "Freedom Day"}]}
        resp = self.client.post(reverse("episode_mark_watched", args=[self.title.pk, 1, 1]), HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        episode = Episode.objects.get(title=self.title, season=1, episode=1)
        self.assertEqual(episode.name, "Freedom Day")
        event = WatchEvent.objects.get(profile=self.profile, title=self.title, episode=episode)
        self.assertFalse(event.is_rewatch)
        self.assertContains(resp, f"ep-watched-btn-{self.title.pk}-1-1")
        self.assertContains(resp, "bg-success")
        self.assertContains(resp, "hx-confirm")

    @patch("tracker.integrations.tmdb.get_season_details")
    def test_reuses_an_episode_row_created_by_a_prior_sync(self, mock_season):
        mock_season.return_value = {"episodes": [{"episode_number": 2, "name": "Holston's Pick"}]}
        existing = Episode.objects.create(title=self.title, season=1, episode=2, name="Holston's Pick")
        self.client.post(reverse("episode_mark_watched", args=[self.title.pk, 1, 2]))
        self.assertEqual(Episode.objects.filter(title=self.title, season=1, episode=2).count(), 1)
        event = WatchEvent.objects.get(profile=self.profile, title=self.title)
        self.assertEqual(event.episode_id, existing.pk)

    @patch("tracker.integrations.tmdb.get_season_details", return_value=None)
    def test_a_second_click_logs_a_rewatch(self, mock_season):
        self.client.post(reverse("episode_mark_watched", args=[self.title.pk, 1, 1]))
        self.client.post(reverse("episode_mark_watched", args=[self.title.pk, 1, 1]))
        events = list(WatchEvent.objects.filter(profile=self.profile, title=self.title).order_by("watched_at"))
        self.assertEqual(len(events), 2)
        self.assertFalse(events[0].is_rewatch)
        self.assertTrue(events[1].is_rewatch)

    def test_requires_get_is_rejected(self):
        resp = self.client.get(reverse("episode_mark_watched", args=[self.title.pk, 1, 1]))
        self.assertEqual(resp.status_code, 405)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("episode_mark_watched", args=[self.title.pk, 1, 1]))
        self.assertNotEqual(resp.status_code, 200)

    @patch("tracker.integrations.tmdb.get_season_details", return_value=None)
    def test_id_suffix_round_trips_so_the_mobile_button_keeps_a_distinct_id(self, mock_season):
        # The mobile episode row's button posts its own id_suffix (see
        # episode_watched_button.html's hx-vals) so the swapped-in
        # response keeps the "-m" id instead of colliding with the
        # desktop card's un-suffixed one for the same episode.
        resp = self.client.post(
            reverse("episode_mark_watched", args=[self.title.pk, 1, 1]),
            {"id_suffix": "-m"},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(resp, f"ep-watched-btn-{self.title.pk}-1-1-m")


class WatchlistAutoRemovalIntegrationTests(TestCase):
    """End-to-end: the views that log a watch (title_mark_watched,
    episode_mark_watched, title_rate) actually trigger
    completion.sync_watchlist_removal, and a custom list survives it."""

    def setUp(self):
        user = User.objects.create_user("watchlistclicker", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="WatchlistClicker")
        self.client.login(username="watchlistclicker", password="pass12345")
        self.watchlist = WatchList.objects.create(profile=self.profile, name="Watchlist", is_watchlist=True)
        self.custom_list = WatchList.objects.create(profile=self.profile, name="Favorites")

    def test_marking_a_movie_watched_removes_it_from_the_watchlist_not_custom_lists(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        WatchListItem.objects.create(watchlist=self.watchlist, title=movie)
        WatchListItem.objects.create(watchlist=self.custom_list, title=movie)
        self.client.post(reverse("title_mark_watched", args=[movie.pk]))
        self.assertFalse(WatchListItem.objects.filter(watchlist=self.watchlist, title=movie).exists())
        self.assertTrue(WatchListItem.objects.filter(watchlist=self.custom_list, title=movie).exists())

    def test_rating_a_movie_for_the_first_time_removes_it_from_the_watchlist(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        WatchListItem.objects.create(watchlist=self.watchlist, title=movie)
        self.client.post(reverse("title_rate", args=[movie.pk]), {"rating": "8"})
        self.assertFalse(WatchListItem.objects.filter(watchlist=self.watchlist, title=movie).exists())

    @patch("tracker.integrations.tmdb.get_tv_details")
    @patch("tracker.integrations.tmdb.get_season_details")
    def test_watching_the_final_episode_removes_the_show_from_the_watchlist(self, mock_season, mock_tv_details):
        show = Title.objects.create(
            media_type=MediaType.TV, name="Silo", year=2023, external_ids={"tmdb": "99", "tmdb_kind": "tv"}
        )
        WatchListItem.objects.create(watchlist=self.watchlist, title=show)
        WatchListItem.objects.create(watchlist=self.custom_list, title=show)
        first_ep = Episode.objects.create(title=show, season=1, episode=1)
        WatchEvent.objects.create(profile=self.profile, title=show, episode=first_ep, watched_at="2024-01-01T00:00:00Z")
        mock_season.return_value = {"episodes": [{"episode_number": 2, "name": "Ep2"}]}
        mock_tv_details.return_value = {"number_of_episodes": 2, "episode_run_time": 24, "seasons": []}

        self.client.post(reverse("episode_mark_watched", args=[show.pk, 1, 2]))

        self.assertTrue(
            WatchProgress.objects.filter(
                profile=self.profile, title=show, status=WatchProgress.Status.COMPLETED
            ).exists()
        )
        self.assertFalse(WatchListItem.objects.filter(watchlist=self.watchlist, title=show).exists())
        self.assertTrue(WatchListItem.objects.filter(watchlist=self.custom_list, title=show).exists())

    @patch("tracker.integrations.tmdb.get_tv_details")
    @patch("tracker.integrations.tmdb.get_season_details")
    def test_watching_one_of_several_episodes_leaves_the_show_on_the_watchlist(self, mock_season, mock_tv_details):
        show = Title.objects.create(
            media_type=MediaType.TV, name="Silo", year=2023, external_ids={"tmdb": "99", "tmdb_kind": "tv"}
        )
        WatchListItem.objects.create(watchlist=self.watchlist, title=show)
        mock_season.return_value = {"episodes": [{"episode_number": 1, "name": "Ep1"}]}
        mock_tv_details.return_value = {"number_of_episodes": 5, "episode_run_time": 24, "seasons": []}

        self.client.post(reverse("episode_mark_watched", args=[show.pk, 1, 1]))

        self.assertTrue(WatchListItem.objects.filter(watchlist=self.watchlist, title=show).exists())


class TitlePreviewViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("previewviewer", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="PreviewViewer")
        self.client.login(username="previewviewer", password="pass12345")
        for name, default in (("get_director", None), ("get_watch_providers", [])):
            patcher = patch(f"tracker.integrations.tmdb.{name}", return_value=default)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _details(self, **overrides):
        base = {
            "tmdb_id": 42, "media_type": "movie", "name": "Fathom", "year": "2020",
            "overview": "A movie.", "tagline": "", "genres": ["Drama"], "runtime": 100,
            "number_of_seasons": None, "number_of_episodes": None,
            "backdrop_url": None, "poster_url": None, "vote_average": 7.0,
            "vote_count": 100, "original_language": "en", "status": None,
        }
        base.update(overrides)
        return base

    def test_invalid_media_type_404s(self):
        resp = self.client.get(reverse("title_preview", args=["book", 1]))
        self.assertEqual(resp.status_code, 404)

    @patch("tracker.integrations.tmdb.get_full_details", return_value=None)
    def test_404s_when_tmdb_has_nothing(self, mock_details):
        resp = self.client.get(reverse("title_preview", args=["movie", 999]))
        self.assertEqual(resp.status_code, 404)

    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_renders_200_for_untracked_title(self, mock_details, mock_credits, mock_similar):
        mock_details.return_value = self._details()
        resp = self.client.get(reverse("title_preview", args=["movie", 42]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["is_preview"])
        self.assertContains(resp, "Fathom")
        self.assertContains(resp, "Lists")

    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_mark_watched_and_add_to_a_list_are_both_independently_available(self, mock_details, mock_credits, mock_similar):
        # Neither action should require or imply the other - watched and
        # listed are two separate facts about a title. The dedicated
        # "+ Add to Watchlist" button was replaced by the same Lists chip
        # picker a tracked title's own page uses (see title_detail.html) -
        # every chip, including one named "Watchlist", posts to
        # title_preview_add_to_list here since there's no local Title yet.
        watchlist = WatchList.objects.create(profile=self.profile, name="Watchlist", is_watchlist=True)
        mock_details.return_value = self._details()
        resp = self.client.get(reverse("title_preview", args=["movie", 42]))
        self.assertContains(resp, "+ Mark as Watched")
        self.assertContains(resp, reverse("title_preview_mark_watched", args=["movie", 42]))
        self.assertContains(resp, "Watchlist")
        self.assertContains(resp, reverse("title_preview_add_to_list", args=["movie", 42, watchlist.pk]))

    @patch("tracker.integrations.tmdb.get_similar", return_value=[])
    @patch("tracker.integrations.tmdb.get_credits", return_value=[])
    @patch("tracker.integrations.tmdb.get_full_details")
    def test_shows_a_status_badge_for_an_upcoming_movie(self, mock_details, mock_credits, mock_similar):
        mock_details.return_value = self._details(status="Planned")
        resp = self.client.get(reverse("title_preview", args=["movie", 42]))
        self.assertEqual(resp.context["status_badge"], {"label": "Upcoming", "color": "info"})
        self.assertContains(resp, "Upcoming")

    def test_redirects_to_real_detail_page_if_already_tracked(self):
        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Already Tracked", year=2020,
            external_ids={"tmdb": "42", "tmdb_kind": "movie"},
        )
        resp = self.client.get(reverse("title_preview", args=["movie", 42]))
        # fetch_redirect_response=False: the redirect target's own render is
        # TitleDetailViewTests' job - following it here would hit the real
        # (unmocked in this test) tmdb.get_credits/get_similar.
        self.assertRedirects(resp, reverse("title_detail", args=[title.pk]), fetch_redirect_response=False)

    @patch("tracker.integrations.tmdb.get_full_details")
    def test_add_to_watchlist_creates_title_and_adds_it(self, mock_details):
        mock_details.return_value = self._details()
        resp = self.client.post(reverse("title_preview_add_to_watchlist", args=["movie", 42]))
        title = Title.objects.get(external_ids__tmdb="42")
        self.assertEqual(title.name, "Fathom")
        self.assertRedirects(resp, reverse("title_detail", args=[title.pk]), fetch_redirect_response=False)
        watchlist = WatchList.objects.get(profile=self.profile, name="Watchlist")
        self.assertTrue(watchlist.is_watchlist)
        self.assertTrue(WatchListItem.objects.filter(watchlist=watchlist, title=title).exists())

    @patch("tracker.integrations.tmdb.get_full_details")
    def test_add_to_watchlist_reuses_existing_title_and_watchlist(self, mock_details):
        mock_details.return_value = self._details()
        existing_title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020,
            external_ids={"tmdb": "42", "tmdb_kind": "movie"},
        )
        existing_watchlist = WatchList.objects.create(profile=self.profile, name="Watchlist")
        self.client.post(reverse("title_preview_add_to_watchlist", args=["movie", 42]))
        mock_details.assert_not_called()  # title already existed - no need to refetch
        self.assertEqual(Title.objects.filter(external_ids__tmdb="42").count(), 1)
        self.assertEqual(WatchList.objects.filter(profile=self.profile, name="Watchlist").count(), 1)
        self.assertTrue(WatchListItem.objects.filter(watchlist=existing_watchlist, title=existing_title).exists())

    def test_add_to_watchlist_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("title_preview_add_to_watchlist", args=["movie", 42]))
        self.assertNotEqual(resp.status_code, 200)

    @patch("tracker.integrations.tmdb.get_full_details")
    def test_mark_watched_via_htmx_materializes_the_title_and_returns_the_fragment(self, mock_details):
        mock_details.return_value = self._details()
        resp = self.client.post(reverse("title_preview_mark_watched", args=["movie", 42]), HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 200)
        title = Title.objects.get(external_ids__tmdb="42")
        self.assertEqual(title.name, "Fathom")
        self.assertTrue(WatchEvent.objects.filter(profile=self.profile, title=title).exists())
        self.assertContains(resp, f"watched-btn-{title.pk}")
        self.assertContains(resp, "text-success")

    @patch("tracker.integrations.tmdb.get_full_details")
    def test_mark_watched_as_a_plain_post_materializes_and_redirects_to_the_real_page(self, mock_details):
        # The preview page's own header "Mark as Watched" button - a
        # plain form post (no HX-Request), unlike the Discover grid's
        # quick-action version of this same endpoint.
        mock_details.return_value = self._details()
        resp = self.client.post(reverse("title_preview_mark_watched", args=["movie", 42]))
        title = Title.objects.get(external_ids__tmdb="42")
        self.assertRedirects(resp, reverse("title_detail", args=[title.pk]), fetch_redirect_response=False)
        self.assertTrue(WatchEvent.objects.filter(profile=self.profile, title=title).exists())

    @patch("tracker.integrations.tmdb.get_full_details")
    def test_mark_watched_reuses_an_existing_title(self, mock_details):
        mock_details.return_value = self._details()
        existing_title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020,
            external_ids={"tmdb": "42", "tmdb_kind": "movie"},
        )
        self.client.post(reverse("title_preview_mark_watched", args=["movie", 42]), HTTP_HX_REQUEST="true")
        mock_details.assert_not_called()
        self.assertEqual(Title.objects.filter(external_ids__tmdb="42").count(), 1)
        self.assertTrue(WatchEvent.objects.filter(profile=self.profile, title=existing_title).exists())

    def test_mark_watched_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("title_preview_mark_watched", args=["movie", 42]))
        self.assertNotEqual(resp.status_code, 200)

    @patch("tracker.integrations.tmdb.get_full_details")
    def test_add_to_list_materializes_the_title_and_adds_it(self, mock_details):
        # The Discover grid's HTMX popover flow - see the plain-form
        # (non-HTMX) test below for the preview page's own Lists card.
        mock_details.return_value = self._details()
        watchlist = WatchList.objects.create(profile=self.profile, name="Favorites")
        resp = self.client.post(
            reverse("title_preview_add_to_list", args=["movie", 42, watchlist.id]), HTTP_HX_REQUEST="true"
        )
        self.assertEqual(resp.status_code, 200)
        title = Title.objects.get(external_ids__tmdb="42")
        self.assertTrue(WatchListItem.objects.filter(watchlist=watchlist, title=title).exists())
        # the returned fragment is the standard (real-title) popover, so
        # any further clicks flow through the ordinary add_to_list/
        # remove_from_list endpoints, not this preview-only one.
        self.assertContains(resp, f"list-popover-{title.pk}")

    @patch("tracker.integrations.tmdb.get_full_details")
    def test_add_to_list_redirects_to_the_real_detail_page_for_a_plain_form_post(self, mock_details):
        # The preview page's own Lists card is a plain form (not HTMX),
        # same as its "+ Mark as Watched" button - so this materializes
        # the title, adds it to the list, and redirects, same shape as
        # title_preview_add_to_watchlist.
        mock_details.return_value = self._details()
        watchlist = WatchList.objects.create(profile=self.profile, name="Favorites")
        resp = self.client.post(reverse("title_preview_add_to_list", args=["movie", 42, watchlist.id]))
        title = Title.objects.get(external_ids__tmdb="42")
        self.assertRedirects(resp, reverse("title_detail", args=[title.pk]), fetch_redirect_response=False)
        self.assertTrue(WatchListItem.objects.filter(watchlist=watchlist, title=title).exists())

    @patch("tracker.integrations.tmdb.get_full_details")
    def test_add_to_list_rejects_a_list_this_profile_cannot_edit(self, mock_details):
        mock_details.return_value = self._details()
        other_user = User.objects.create_user("otherlistowner", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="OtherListOwner")
        others_list = WatchList.objects.create(profile=other_profile, name="Not Yours")
        resp = self.client.post(reverse("title_preview_add_to_list", args=["movie", 42, others_list.id]))
        self.assertEqual(resp.status_code, 404)

    def test_add_to_list_requires_login(self):
        self.client.logout()
        watchlist_owner = User.objects.create_user("listowner2", password="pass12345")
        owner_profile = Profile.objects.create(user=watchlist_owner, display_name="ListOwner2")
        watchlist = WatchList.objects.create(profile=owner_profile, name="Favorites")
        resp = self.client.post(reverse("title_preview_add_to_list", args=["movie", 42, watchlist.id]))
        self.assertNotEqual(resp.status_code, 200)


class CreateListNeverFlagsAsWatchlistTests(TestCase):
    """create_list is the only entry point for a profile's own custom
    lists - it must never set is_watchlist, even if a user names their
    custom list "Watchlist" themselves (see completion.sync_watchlist_removal,
    which keys off the flag, not the name)."""

    def setUp(self):
        user = User.objects.create_user("listcreator", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="ListCreator")
        self.client.login(username="listcreator", password="pass12345")

    def test_a_new_custom_list_is_not_flagged_as_the_watchlist(self):
        self.client.post(reverse("create_list"), {"name": "Favorites"})
        watchlist = WatchList.objects.get(profile=self.profile, name="Favorites")
        self.assertFalse(watchlist.is_watchlist)

    def test_a_custom_list_named_watchlist_is_still_not_flagged(self):
        self.client.post(reverse("create_list"), {"name": "Watchlist"})
        watchlist = WatchList.objects.get(profile=self.profile, name="Watchlist")
        self.assertFalse(watchlist.is_watchlist)


class ListActionNextRedirectTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("listactor", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="ListActor")
        self.client.login(username="listactor", password="pass12345")
        self.watchlist = WatchList.objects.create(profile=self.profile, name="Favorites")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)

    def test_add_to_list_redirects_to_list_detail_by_default(self):
        resp = self.client.post(reverse("add_to_list", args=[self.watchlist.id]), {"title_id": self.title.pk})
        self.assertRedirects(resp, reverse("list_detail", args=[self.watchlist.id]))

    def test_add_to_list_redirects_to_next_when_given(self):
        target = reverse("title_detail", args=[self.title.pk])
        resp = self.client.post(
            reverse("add_to_list", args=[self.watchlist.id]), {"title_id": self.title.pk, "next": target}
        )
        self.assertRedirects(resp, target, fetch_redirect_response=False)

    def test_add_to_list_ignores_an_external_next(self):
        resp = self.client.post(
            reverse("add_to_list", args=[self.watchlist.id]),
            {"title_id": self.title.pk, "next": "https://evil.example/"},
        )
        self.assertRedirects(resp, reverse("list_detail", args=[self.watchlist.id]))


class PosterActionContextSelectorTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("posteractionuser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="PosterActionUser")

    def test_watched_title_is_true(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Watched", year=2020)
        WatchEvent.objects.create(profile=self.profile, title=title, watched_at="2024-01-01T00:00:00Z")
        context = selectors.poster_action_context(self.profile, [title])
        self.assertTrue(context["watched_by_title"][title.pk])

    def test_unwatched_title_is_false_not_missing(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Unwatched", year=2020)
        context = selectors.poster_action_context(self.profile, [title])
        self.assertIn(title.pk, context["watched_by_title"])
        self.assertFalse(context["watched_by_title"][title.pk])

    def test_list_membership_reflects_actual_lists(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Listed", year=2020)
        watchlist = WatchList.objects.create(profile=self.profile, name="Favorites")
        WatchListItem.objects.create(watchlist=watchlist, title=title)
        context = selectors.poster_action_context(self.profile, [title])
        self.assertEqual(context["list_membership"][title.pk], {watchlist.id})

    def test_title_with_neither_still_has_both_keys_present(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Neither", year=2020)
        context = selectors.poster_action_context(self.profile, [title])
        self.assertIn(title.pk, context["watched_by_title"])
        self.assertIn(title.pk, context["list_membership"])
        self.assertEqual(context["list_membership"][title.pk], set())

    def test_watch_count_reflects_number_of_plain_plays(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Rewatched", year=2020)
        WatchEvent.objects.create(profile=self.profile, title=title, watched_at="2024-01-01T00:00:00Z")
        WatchEvent.objects.create(profile=self.profile, title=title, watched_at="2024-02-01T00:00:00Z")
        context = selectors.poster_action_context(self.profile, [title])
        self.assertEqual(context["watch_count_by_title"][title.pk], 2)

    def test_watch_count_is_zero_not_missing_for_an_unwatched_title(self):
        title = Title.objects.create(media_type=MediaType.MOVIE, name="Unwatched", year=2020)
        context = selectors.poster_action_context(self.profile, [title])
        self.assertEqual(context["watch_count_by_title"][title.pk], 0)

    def test_watch_count_for_a_show_is_the_minimum_per_episode_play_count(self):
        show = Title.objects.create(media_type=MediaType.TV, name="Silo", year=2023)
        ep1 = Episode.objects.create(title=show, season=1, episode=1)
        ep2 = Episode.objects.create(title=show, season=1, episode=2)
        # ep1 watched twice, ep2 only once - the *least*-rewatched episode
        # you've engaged with caps the show's own "times watched" figure,
        # same as not having finished a full rewatch yet.
        WatchEvent.objects.create(profile=self.profile, title=show, episode=ep1, watched_at="2024-01-01T00:00:00Z")
        WatchEvent.objects.create(profile=self.profile, title=show, episode=ep1, watched_at="2024-02-01T00:00:00Z")
        WatchEvent.objects.create(profile=self.profile, title=show, episode=ep2, watched_at="2024-01-02T00:00:00Z")
        context = selectors.poster_action_context(self.profile, [show])
        self.assertTrue(context["watched_by_title"][show.pk])
        self.assertEqual(context["watch_count_by_title"][show.pk], 1)

    def test_watch_count_for_a_show_rises_once_every_known_episode_is_rewatched(self):
        show = Title.objects.create(media_type=MediaType.TV, name="Silo", year=2023)
        ep1 = Episode.objects.create(title=show, season=1, episode=1)
        ep2 = Episode.objects.create(title=show, season=1, episode=2)
        for ep in (ep1, ep2):
            WatchEvent.objects.create(profile=self.profile, title=show, episode=ep, watched_at="2024-01-01T00:00:00Z")
            WatchEvent.objects.create(profile=self.profile, title=show, episode=ep, watched_at="2024-02-01T00:00:00Z")
        context = selectors.poster_action_context(self.profile, [show])
        self.assertEqual(context["watch_count_by_title"][show.pk], 2)

    def test_watch_count_for_a_show_falls_back_to_plain_count_with_no_episode_events(self):
        # A show quick-marked watched via the plain checkmark (never
        # opened the episode browser) has no episode-level events at all -
        # falls back to plain_watch_count, same as a movie would.
        show = Title.objects.create(media_type=MediaType.TV, name="Silo", year=2023)
        WatchEvent.objects.create(profile=self.profile, title=show, watched_at="2024-01-01T00:00:00Z")
        context = selectors.poster_action_context(self.profile, [show])
        self.assertEqual(context["watch_count_by_title"][show.pk], 1)


class DiscoverActionContextSelectorTests(TestCase):
    """discover_action_context - poster_action_context's counterpart for
    TMDB preview tiles (Movies & TV/Anime's grid, "Because you watched",
    a title's "similar" grid, ...). Without this, a title already
    watched/listed (tracked previously, now reappearing on a Trending
    page or as a suggestion) always rendered as untracked."""

    def setUp(self):
        user = User.objects.create_user("discoveractionuser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="DiscoverActionUser")

    def _item(self, tmdb_id=42, media_type="movie"):
        return {"tmdb_id": tmdb_id, "media_type": media_type, "name": "Fathom", "year": "2020", "poster_url": None, "vote_average": 7.0}

    def test_no_local_title_yet_is_unwatched_and_unlisted(self):
        context = selectors.discover_action_context(self.profile, [self._item()])
        key = "movie:42"
        self.assertIsNone(context["discover_title_by_key"][key])
        self.assertFalse(context["discover_watched"][key])
        self.assertEqual(context["discover_list_membership"][key], set())

    def test_a_previously_watched_title_reappearing_shows_as_watched(self):
        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"tmdb": "42", "tmdb_kind": "movie"}
        )
        WatchEvent.objects.create(profile=self.profile, title=title, watched_at="2024-01-01T00:00:00Z")
        context = selectors.discover_action_context(self.profile, [self._item()])
        key = "movie:42"
        self.assertEqual(context["discover_title_by_key"][key], title)
        self.assertTrue(context["discover_watched"][key])

    def test_a_previously_listed_title_reappearing_shows_its_list_membership(self):
        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"tmdb": "42", "tmdb_kind": "movie"}
        )
        watchlist = WatchList.objects.create(profile=self.profile, name="Favorites")
        WatchListItem.objects.create(watchlist=watchlist, title=title)
        context = selectors.discover_action_context(self.profile, [self._item()])
        key = "movie:42"
        self.assertEqual(context["discover_list_membership"][key], {watchlist.id})
        self.assertFalse(context["discover_watched"][key])

    def test_watch_count_reflects_number_of_plain_plays(self):
        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"tmdb": "42", "tmdb_kind": "movie"}
        )
        WatchEvent.objects.create(profile=self.profile, title=title, watched_at="2024-01-01T00:00:00Z")
        WatchEvent.objects.create(profile=self.profile, title=title, watched_at="2024-02-01T00:00:00Z")
        context = selectors.discover_action_context(self.profile, [self._item()])
        self.assertEqual(context["discover_watch_count"]["movie:42"], 2)

    def test_watch_count_is_zero_with_no_local_title(self):
        context = selectors.discover_action_context(self.profile, [self._item()])
        self.assertEqual(context["discover_watch_count"]["movie:42"], 0)

    def test_another_profiles_watch_history_does_not_leak_in(self):
        title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020, external_ids={"tmdb": "42", "tmdb_kind": "movie"}
        )
        other_user = User.objects.create_user("discoveractionother", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="DiscoverActionOther")
        WatchEvent.objects.create(profile=other_profile, title=title, watched_at="2024-01-01T00:00:00Z")
        context = selectors.discover_action_context(self.profile, [self._item()])
        self.assertFalse(context["discover_watched"]["movie:42"])

    def test_empty_items_list_returns_empty_dicts(self):
        context = selectors.discover_action_context(self.profile, [])
        self.assertEqual(context["discover_title_by_key"], {})
        self.assertEqual(context["discover_watched"], {})
        self.assertEqual(context["discover_list_membership"], {})


class PosterCardListPopoverHtmxBranchTests(TestCase):
    """add_to_list/remove_from_list are reused by both the Lists detail
    page (swaps the whole #list-items grid) and the new poster-card list
    popover (should get back only its own small fragment). This class
    guards the branch that tells those two apart - the regression that
    matters most is the *existing* Lists-page behavior staying intact."""

    def setUp(self):
        user = User.objects.create_user("popoverposter", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="PopoverPoster")
        self.client.login(username="popoverposter", password="pass12345")
        self.watchlist = WatchList.objects.create(profile=self.profile, name="Favorites")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)

    def test_add_to_list_from_the_popover_returns_just_the_popover_fragment(self):
        resp = self.client.post(
            reverse("add_to_list", args=[self.watchlist.id]),
            {"title_id": self.title.pk},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET=f"list-popover-{self.title.pk}",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"list-popover-{self.title.pk}")
        self.assertNotContains(resp, 'id="list-items"')

    def test_add_to_list_from_the_lists_page_still_returns_the_full_grid(self):
        resp = self.client.post(
            reverse("add_to_list", args=[self.watchlist.id]),
            {"title_id": self.title.pk},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="list-items"')

    def test_remove_from_list_from_the_popover_returns_just_the_popover_fragment(self):
        WatchListItem.objects.create(watchlist=self.watchlist, title=self.title)
        resp = self.client.post(
            reverse("remove_from_list", args=[self.watchlist.id]),
            {"title_id": self.title.pk},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET=f"list-popover-{self.title.pk}",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"list-popover-{self.title.pk}")
        self.assertNotContains(resp, 'id="list-items"')
        self.assertFalse(WatchListItem.objects.filter(watchlist=self.watchlist, title=self.title).exists())

    def test_remove_from_list_from_the_lists_page_still_returns_the_full_grid(self):
        WatchListItem.objects.create(watchlist=self.watchlist, title=self.title)
        resp = self.client.post(
            reverse("remove_from_list", args=[self.watchlist.id]),
            {"title_id": self.title.pk},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="list-items"')

    def test_viewing_a_shared_list_shows_the_viewers_own_state_not_the_owners(self):
        # add_to_list/remove_from_list are can_edit-gated to a list's own
        # creator even when shared, so a non-owner can never reach
        # _render_poster_actions for someone else's list - but a shared
        # list *is* GET-viewable by other profiles (list_detail), and
        # their poster cards must reflect the VIEWER's own watched/list
        # state, not the list owner's.
        other_user = User.objects.create_user("otherviewer", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="OtherViewer")
        WatchList.objects.create(profile=other_profile, name="Mine")
        WatchEvent.objects.create(profile=other_profile, title=self.title, watched_at="2024-01-01T00:00:00Z")
        self.watchlist.is_shared = True
        self.watchlist.save()
        WatchListItem.objects.create(watchlist=self.watchlist, title=self.title)

        self.client.logout()
        self.client.login(username="otherviewer", password="pass12345")
        resp = self.client.get(reverse("list_detail", args=[self.watchlist.id]))
        self.assertIn(self.title.pk, resp.context["watched_by_title"])
        self.assertTrue(resp.context["watched_by_title"][self.title.pk])
        self.assertIn("Mine", [wl.name for wl in resp.context["my_lists"]])


class HistoryTitleFilterTests(TestCase):
    """The poster card watched-button popover's "View history plays" link
    (?title=<pk>) narrows the History page down to just that one title."""

    def setUp(self):
        from django.utils import timezone

        self.timezone = timezone
        user = User.objects.create_user("historyfilteruser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="HistoryFilterUser")
        self.client.login(username="historyfilteruser", password="pass12345")
        self.title = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        self.other_title = Title.objects.create(media_type=MediaType.MOVIE, name="Evil Dead Burn", year=2019)
        WatchEvent.objects.create(profile=self.profile, title=self.title, watched_at=self.timezone.now())
        WatchEvent.objects.create(profile=self.profile, title=self.other_title, watched_at=self.timezone.now())

    def test_filters_to_just_the_given_title(self):
        resp = self.client.get(reverse("history"), {"title": self.title.pk})
        self.assertContains(resp, "Fathom")
        self.assertNotContains(resp, "Evil Dead Burn")

    def test_shows_a_filtered_banner_with_a_clear_link(self):
        resp = self.client.get(reverse("history"), {"title": self.title.pk})
        self.assertContains(resp, "Filtered to")
        self.assertContains(resp, "Clear")

    def test_no_banner_without_a_title_filter(self):
        resp = self.client.get(reverse("history"))
        self.assertNotContains(resp, "Filtered to")

    def test_invalid_title_param_is_ignored_not_a_500(self):
        resp = self.client.get(reverse("history"), {"title": "not-a-number"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Fathom")
        self.assertContains(resp, "Evil Dead Burn")

    def test_filter_form_carries_the_title_forward_for_further_requests(self):
        # The type/period/sort filter form re-submits on every change - a
        # hidden "title" field is what keeps the title filter applied
        # when switching those, instead of silently dropping it.
        resp = self.client.get(reverse("history"), {"title": self.title.pk})
        self.assertContains(resp, f'<input type="hidden" name="title" value="{self.title.pk}">')


class HistoryConsecutiveEpisodeGroupingTests(TestCase):
    """A binge session's episode cards should collapse into one group tile,
    the same idea as the Activity feed's grouping but shaped for the
    History page's WatchEvent-object cards instead of that feed's dicts."""

    def setUp(self):
        from django.utils import timezone

        user = User.objects.create_user("histgrouper", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="HistGrouper")
        self.show = Title.objects.create(media_type=MediaType.TV, name="Bleach", year=2004)
        self.now = timezone.now()

    def _watch(self, episode_num, minutes_ago, season=1, title=None):
        title = title or self.show
        ep = Episode.objects.create(title=title, season=season, episode=episode_num)
        return WatchEvent.objects.create(
            profile=self.profile, title=title, episode=ep, watched_at=self.now - timedelta(minutes=minutes_ago)
        )

    def test_consecutive_episodes_collapse_into_one_group(self):
        events = [self._watch(episode_num=i, minutes_ago=(20 - i)) for i in range(1, 6)]
        grouped = views._group_consecutive_episodes(events)
        self.assertEqual(len(grouped), 1)
        self.assertTrue(grouped[0]["is_group"])
        self.assertEqual(grouped[0]["count"], 5)
        self.assertEqual(grouped[0]["range_label"], "S1E1–S1E5")

    def test_single_episode_does_not_get_grouped(self):
        events = [self._watch(episode_num=1, minutes_ago=10)]
        grouped = views._group_consecutive_episodes(events)
        self.assertFalse(isinstance(grouped[0], dict))

    def test_total_duration_sums_episode_runtimes(self):
        show = Title.objects.create(media_type=MediaType.TV, name="Naruto", year=2002)
        events = []
        for i in range(1, 4):
            ep = Episode.objects.create(title=show, season=1, episode=i, runtime_minutes=24)
            events.append(
                WatchEvent.objects.create(
                    profile=self.profile, title=show, episode=ep, watched_at=self.now - timedelta(minutes=(10 - i))
                )
            )
        grouped = views._group_consecutive_episodes(events)
        self.assertEqual(grouped[0]["total_duration"], "1h 12m")

    def test_total_duration_is_none_without_runtime_data(self):
        events = [self._watch(episode_num=i, minutes_ago=(20 - i)) for i in range(1, 4)]
        grouped = views._group_consecutive_episodes(events)
        self.assertIsNone(grouped[0]["total_duration"])

    def test_timeline_events_are_chronological_regardless_of_input_order(self):
        # History's default "newest first" sort feeds events into the grouper
        # newest-to-oldest (ep1 watched most recently, ep5 the longest ago) -
        # the segmented timeline bar should still read left-to-right in the
        # order the episodes were actually watched, i.e. oldest first.
        events = [self._watch(episode_num=i, minutes_ago=i) for i in range(1, 6)]
        grouped = views._group_consecutive_episodes(events)
        timeline = grouped[0]["timeline_events"]
        self.assertEqual([e.episode.episode for e in timeline], [5, 4, 3, 2, 1])

    def test_different_titles_break_the_run(self):
        other_show = Title.objects.create(media_type=MediaType.TV, name="Naruto", year=2002)
        events = [
            self._watch(episode_num=1, minutes_ago=30),
            self._watch(episode_num=2, minutes_ago=20),
            self._watch(episode_num=1, minutes_ago=10, title=other_show),
        ]
        grouped = views._group_consecutive_episodes(events)
        self.assertEqual(len(grouped), 2)
        self.assertTrue(grouped[0]["is_group"])
        self.assertEqual(grouped[0]["count"], 2)
        self.assertFalse(isinstance(grouped[1], dict))  # the lone Naruto watch, ungrouped

    def test_movies_are_never_grouped(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Movie A", year=2020)
        e1 = WatchEvent.objects.create(profile=self.profile, title=movie, watched_at=self.now - timedelta(minutes=10))
        e2 = WatchEvent.objects.create(profile=self.profile, title=movie, watched_at=self.now - timedelta(minutes=5))
        grouped = views._group_consecutive_episodes([e2, e1])
        self.assertEqual(len(grouped), 2)
        self.assertTrue(all(not isinstance(g, dict) for g in grouped))

    def test_range_label_uses_min_max_episode_not_watch_order(self):
        # watched out of broadcast order - range should still span the full set
        events = [
            self._watch(episode_num=19, minutes_ago=30),
            self._watch(episode_num=3, minutes_ago=20),
            self._watch(episode_num=11, minutes_ago=10),
        ]
        grouped = views._group_consecutive_episodes(events)
        self.assertEqual(grouped[0]["range_label"], "S1E3–S1E19")

    def test_history_page_renders_group_tile_for_a_binge(self):
        from django.utils import timezone

        user = User.objects.create_user("histviewer", password="pass12345")
        profile = Profile.objects.create(user=user, display_name="HistViewer")
        show = Title.objects.create(media_type=MediaType.TV, name="Bleach", year=2004)
        for i in range(1, 4):
            ep = Episode.objects.create(title=show, season=1, episode=i)
            WatchEvent.objects.create(
                profile=profile, title=show, episode=ep, watched_at=timezone.now() - timedelta(minutes=i)
            )
        self.client.login(username="histviewer", password="pass12345")
        resp = self.client.get(reverse("history"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "3 episodes")
        self.assertContains(resp, "S1E1–S1E3")


class HistoryDayGroupTotalDurationTests(TestCase):
    """Each day-group header's own total watch time - next to the
    "1 movie · 4 episodes" count, shown in whichever unit
    (minutes/hours/days) selectors.format_duration picks for the
    total, matching Trakt/Simkl's own watch-time formatting."""

    def setUp(self):
        from django.utils import timezone

        user = User.objects.create_user("daydurationuser", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="DayDurationUser")
        self.now = timezone.now()

    def test_sums_movie_and_episode_runtimes_for_the_day(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020, runtime_minutes=100)
        show = Title.objects.create(media_type=MediaType.TV, name="Silo", year=2023)
        ep = Episode.objects.create(title=show, season=1, episode=1, runtime_minutes=50)
        events = [
            WatchEvent.objects.create(profile=self.profile, title=movie, watched_at=self.now),
            WatchEvent.objects.create(profile=self.profile, title=show, episode=ep, watched_at=self.now - timedelta(minutes=5)),
        ]
        groups = views._group_history_by_day(events)
        self.assertEqual(groups[0]["total_duration"], "2h 30m")

    def test_shows_minutes_only_for_a_short_total(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Short Film", year=2020, runtime_minutes=20)
        events = [WatchEvent.objects.create(profile=self.profile, title=movie, watched_at=self.now)]
        groups = views._group_history_by_day(events)
        self.assertEqual(groups[0]["total_duration"], "20m")

    def test_falls_back_to_episode_runtime_over_title_runtime(self):
        show = Title.objects.create(media_type=MediaType.TV, name="Silo", year=2023, runtime_minutes=999)
        ep = Episode.objects.create(title=show, season=1, episode=1, runtime_minutes=45)
        events = [WatchEvent.objects.create(profile=self.profile, title=show, episode=ep, watched_at=self.now)]
        groups = views._group_history_by_day(events)
        self.assertEqual(groups[0]["total_duration"], "45m")

    def test_none_without_any_runtime_data(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="No Runtime", year=2020)
        events = [WatchEvent.objects.create(profile=self.profile, title=movie, watched_at=self.now)]
        groups = views._group_history_by_day(events)
        self.assertIsNone(groups[0]["total_duration"])

    def test_each_day_gets_its_own_total_not_a_running_sum(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020, runtime_minutes=100)
        events = [
            WatchEvent.objects.create(profile=self.profile, title=movie, watched_at=self.now),
            WatchEvent.objects.create(profile=self.profile, title=movie, watched_at=self.now - timedelta(days=1)),
        ]
        groups = views._group_history_by_day(events)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["total_duration"], "1h 40m")
        self.assertEqual(groups[1]["total_duration"], "1h 40m")

    def test_history_page_renders_the_total_next_to_the_counts(self):
        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020, runtime_minutes=100)
        WatchEvent.objects.create(profile=self.profile, title=movie, watched_at=self.now)
        self.client.login(username="daydurationuser", password="pass12345")
        resp = self.client.get(reverse("history"))
        self.assertContains(resp, "1h 40m")


class HistoryBulkDeleteTests(TestCase):
    """History's multi-select bar posts a mix of plain event ids (single
    tiles) and comma-joined event ids (a collapsed binge-group tile's one
    checkbox standing in for all of its underlying WatchEvents)."""

    def setUp(self):
        from django.utils import timezone

        self.user = User.objects.create_user("bulkdeleter", password="pass12345")
        self.profile = Profile.objects.create(user=self.user, display_name="BulkDeleter")
        self.movie = Title.objects.create(media_type=MediaType.MOVIE, name="Movie A", year=2020)
        self.now = timezone.now()
        self.client.login(username="bulkdeleter", password="pass12345")

    def _watch(self, minutes_ago, title=None):
        return WatchEvent.objects.create(
            profile=self.profile, title=title or self.movie, watched_at=self.now - timedelta(minutes=minutes_ago)
        )

    def test_deletes_the_selected_events(self):
        e1, e2, e3 = self._watch(30), self._watch(20), self._watch(10)
        resp = self.client.post(
            reverse("history_bulk_delete"), {"event_ids": [str(e1.pk), str(e2.pk)]}, HTTP_HX_REQUEST="true"
        )
        self.assertEqual(resp.status_code, 200)
        remaining = WatchEvent.objects.filter(profile=self.profile).values_list("pk", flat=True)
        self.assertEqual(list(remaining), [e3.pk])

    def test_splits_comma_joined_group_checkbox_values(self):
        e1, e2, e3 = self._watch(30), self._watch(20), self._watch(10)
        resp = self.client.post(
            reverse("history_bulk_delete"), {"event_ids": [f"{e1.pk},{e2.pk}"]}, HTTP_HX_REQUEST="true"
        )
        self.assertEqual(resp.status_code, 200)
        remaining = WatchEvent.objects.filter(profile=self.profile).values_list("pk", flat=True)
        self.assertEqual(list(remaining), [e3.pk])

    def test_only_deletes_events_belonging_to_the_requesting_profile(self):
        other_user = User.objects.create_user("otherbulk", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="Other")
        other_event = self._watch(5, title=self.movie)
        other_event.profile = other_profile
        other_event.save()
        resp = self.client.post(
            reverse("history_bulk_delete"), {"event_ids": [str(other_event.pk)]}, HTTP_HX_REQUEST="true"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(WatchEvent.objects.filter(pk=other_event.pk).exists())

    def test_get_is_not_allowed(self):
        resp = self.client.get(reverse("history_bulk_delete"))
        self.assertEqual(resp.status_code, 405)


class CustomConfirmModalTests(TestCase):
    """base.html's styled <dialog> replacement for the browser's own
    native confirm() popup - a global htmx:confirm listener intercepts
    every hx-confirm in the app (no per-template changes needed at each
    call site) and shows this instead. Covers History's three delete
    confirms (single tile, per-episode group dropdown, bulk-select bar)
    among others."""

    def setUp(self):
        user = User.objects.create_user("confirmmodaluser", password="pass12345")
        Profile.objects.create(user=user, display_name="ConfirmModalUser")
        self.client.login(username="confirmmodaluser", password="pass12345")

    def test_dialog_and_handler_present_on_every_page(self):
        resp = self.client.get(reverse("dashboard"))
        self.assertContains(resp, 'id="confirm_modal"')
        self.assertContains(resp, 'id="confirm-modal-message"')
        self.assertContains(resp, 'id="confirm-modal-confirm-btn"')
        self.assertContains(resp, "htmx:confirm")

    def test_history_delete_buttons_still_use_hx_confirm_not_a_native_js_confirm(self):
        from django.utils import timezone

        movie = Title.objects.create(media_type=MediaType.MOVIE, name="Fathom", year=2020)
        WatchEvent.objects.create(profile=Profile.objects.get(display_name="ConfirmModalUser"), title=movie, watched_at=timezone.now())
        resp = self.client.get(reverse("history"))
        self.assertContains(resp, "hx-confirm=")
        self.assertNotContains(resp, "onclick=\"return confirm(")

    def test_bulk_delete_bar_still_uses_hx_confirm(self):
        resp = self.client.get(reverse("history"))
        self.assertContains(resp, "Remove the selected items from your watch history")


class HistoryDeleteEpisodeTests(TestCase):
    """The binge-group tile's per-episode delete dropdown - removes one
    episode and re-renders just that group, shrunk (or degraded to a
    single tile, or removed outright once nothing's left)."""

    def setUp(self):
        from django.utils import timezone

        self.user = User.objects.create_user("episodedeleter", password="pass12345")
        self.profile = Profile.objects.create(user=self.user, display_name="EpisodeDeleter")
        self.show = Title.objects.create(media_type=MediaType.TV, name="Bleach", year=2004)
        self.now = timezone.now()
        self.client.login(username="episodedeleter", password="pass12345")

    def _watch(self, episode_num, minutes_ago):
        ep = Episode.objects.create(title=self.show, season=1, episode=episode_num)
        return WatchEvent.objects.create(
            profile=self.profile, title=self.show, episode=ep, watched_at=self.now - timedelta(minutes=minutes_ago)
        )

    def test_deletes_the_event(self):
        e1, e2, e3 = self._watch(1, 30), self._watch(2, 20), self._watch(3, 10)
        self.client.post(
            reverse("history_delete_episode", args=[e2.pk]), {"remaining_ids": f"{e1.pk},{e3.pk}"}
        )
        self.assertFalse(WatchEvent.objects.filter(pk=e2.pk).exists())

    def test_shrunk_group_of_two_or_more_re_renders_as_a_group(self):
        e1, e2, e3 = self._watch(1, 30), self._watch(2, 20), self._watch(3, 10)
        resp = self.client.post(
            reverse("history_delete_episode", args=[e2.pk]), {"remaining_ids": f"{e1.pk},{e3.pk}"}
        )
        self.assertContains(resp, "2 episodes")
        self.assertContains(resp, "S1E1")
        self.assertContains(resp, "S1E3")

    def test_group_shrunk_to_one_degrades_to_a_single_tile(self):
        e1, e2 = self._watch(1, 20), self._watch(2, 10)
        resp = self.client.post(
            reverse("history_delete_episode", args=[e2.pk]), {"remaining_ids": str(e1.pk)}
        )
        self.assertContains(resp, "S1:E1")
        self.assertNotContains(resp, "episodes")

    def test_last_episode_removed_returns_empty(self):
        e1 = self._watch(1, 10)
        resp = self.client.post(reverse("history_delete_episode", args=[e1.pk]), {"remaining_ids": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"")

    def test_requires_login(self):
        self.client.logout()
        e1 = self._watch(1, 10)
        resp = self.client.post(reverse("history_delete_episode", args=[e1.pk]), {"remaining_ids": ""})
        self.assertEqual(resp.status_code, 302)

    def test_get_is_not_allowed(self):
        e1 = self._watch(1, 10)
        resp = self.client.get(reverse("history_delete_episode", args=[e1.pk]))
        self.assertEqual(resp.status_code, 405)

    def test_404s_for_another_profiles_event(self):
        other_user = User.objects.create_user("otherepisode", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="Other")
        other_event = WatchEvent.objects.create(profile=other_profile, title=self.show, watched_at=self.now)
        resp = self.client.post(reverse("history_delete_episode", args=[other_event.pk]), {"remaining_ids": ""})
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(WatchEvent.objects.filter(pk=other_event.pk).exists())

    def test_remaining_ids_belonging_to_another_profile_are_not_pulled_in(self):
        # Defensive: even if remaining_ids somehow named another
        # profile's event, it must never end up rendered into this
        # profile's own group tile.
        other_user = User.objects.create_user("otherepisode2", password="pass12345")
        other_profile = Profile.objects.create(user=other_user, display_name="Other2")
        other_event = WatchEvent.objects.create(profile=other_profile, title=self.show, watched_at=self.now)
        e1 = self._watch(1, 10)
        resp = self.client.post(
            reverse("history_delete_episode", args=[e1.pk]), {"remaining_ids": str(other_event.pk)}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"")


class HistoryGroupTileDropdownTests(TestCase):
    """The binge-group tile itself (as rendered on the real History page) -
    a per-episode dropdown wired to history_delete_episode, not the old
    plain count badge."""

    def setUp(self):
        from django.utils import timezone

        user = User.objects.create_user("tiledropdown", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="TileDropdown")
        self.show = Title.objects.create(media_type=MediaType.TV, name="Bleach", year=2004)
        now = timezone.now()
        self.events = []
        for i, epnum in enumerate([1, 2, 3]):
            ep = Episode.objects.create(title=self.show, season=1, episode=epnum)
            self.events.append(
                WatchEvent.objects.create(profile=self.profile, title=self.show, episode=ep, watched_at=now - timedelta(minutes=30 - i * 10))
            )
        self.client.login(username="tiledropdown", password="pass12345")

    def test_lists_every_episode_with_its_own_delete_action(self):
        resp = self.client.get(reverse("history"))
        for event in self.events:
            self.assertContains(resp, reverse("history_delete_episode", args=[event.pk]))

    def test_remaining_ids_exclude_the_events_own_id(self):
        import re

        resp = self.client.get(reverse("history"))
        body = resp.content.decode()
        e1, e2, e3 = self.events
        # e2's own delete button should list e1 and e3 as remaining, not itself.
        marker = f'history/delete-episode/{e2.pk}/'
        chunk_start = body.index(marker)
        chunk = body[chunk_start:chunk_start + 400]
        match = re.search(r'"remaining_ids":\s*"([^"]*)"', chunk)
        remaining = {int(part) for part in match.group(1).split(",") if part.strip().isdigit()}
        self.assertEqual(remaining, {e1.pk, e3.pk})

    def test_grid_uses_the_bumped_tile_size(self):
        resp = self.client.get(reverse("history"))
        self.assertContains(resp, "minmax(150px,1fr)")
