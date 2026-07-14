import io
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django_celery_beat.models import PeriodicTask

from . import completion, csv_import, instance_config, release_sync, rewatches, scheduling, selectors, tasks, views
from .integrations import tmdb, trakt
from .models import (
    Episode,
    ExternalAccount,
    InstanceConfig,
    MediaType,
    Profile,
    ReleaseSchedule,
    SyncLog,
    Title,
    WatchEvent,
    WatchList,
    WatchListItem,
    WatchProgress,
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
    @patch("tracker.completion.sync_show_completion")
    @patch("tracker.completion.update_movie_runtime")
    def test_calls_completion_once_per_unique_title(self, mock_movie_runtime, mock_show_completion, mock_find_match):
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
        self.assertEqual(WatchListItem.objects.filter(watchlist=watchlist).count(), 2)

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
                    {"season_number": 1, "episode_count": 12},
                    {"season_number": 2, "episode_count": 12},
                ],
            }
        )
        details = tmdb.get_tv_details(99)
        self.assertEqual(details["number_of_episodes"], 24)
        self.assertEqual(details["episode_run_time"], 24)
        self.assertEqual(details["seasons"], [
            {"season_number": 1, "episode_count": 12},
            {"season_number": 2, "episode_count": 12},
        ])

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_get_tv_details_handles_missing_episode_run_time(self, mock_get):
        mock_get.return_value = self._response({"number_of_episodes": 24, "seasons": []})
        details = tmdb.get_tv_details(99)
        self.assertIsNone(details["episode_run_time"])


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

    def test_settings_page_shows_the_version(self):
        user = User.objects.create_user("versionchecker", password="pass12345")
        Profile.objects.create(user=user, display_name="VersionChecker")
        self.client.login(username="versionchecker", password="pass12345")
        resp = self.client.get(reverse("settings"))
        self.assertContains(resp, "Spool v")

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

    def test_member_does_not_see_admin_dashboard_nav_link(self):
        self.client.login(username="member2", password="pass12345")
        resp = self.client.get(reverse("settings"))
        self.assertNotContains(resp, "Admin Dashboard")

    def test_owner_sees_admin_dashboard_nav_link(self):
        self.client.login(username="owner2", password="pass12345")
        resp = self.client.get(reverse("settings"))
        self.assertContains(resp, "Admin Dashboard")

    def test_configured_secret_value_never_rendered_in_html(self):
        InstanceConfig.objects.create(pk=1, trakt_client_secret="super-secret-value")
        self.client.login(username="owner2", password="pass12345")
        resp = self.client.get(reverse("admin_dashboard"))
        self.assertNotContains(resp, "super-secret-value")


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


class SyncLogViewTests(TestCase):
    def setUp(self):
        owner_user = User.objects.create_user("logowner", password="pass12345", is_superuser=True)
        self.owner = Profile.objects.create(user=owner_user, display_name="LogOwner")
        member_user = User.objects.create_user("logmember", password="pass12345")
        Profile.objects.create(user=member_user, display_name="LogMember")
        SyncLog.objects.create(
            profile=self.owner,
            provider=ExternalAccount.Provider.TRAKT,
            status=SyncLog.Status.SUCCESS,
            item_count=3,
        )

    def test_non_owner_gets_404(self):
        self.client.login(username="logmember", password="pass12345")
        resp = self.client.get(reverse("sync_log"))
        self.assertEqual(resp.status_code, 404)

    def test_owner_sees_log_entries(self):
        self.client.login(username="logowner", password="pass12345")
        resp = self.client.get(reverse("sync_log"))
        self.assertContains(resp, "LogOwner")
        self.assertContains(resp, "success")


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


class TopbarAvatarDedupeTests(TestCase):
    def test_single_profile_avatar_not_duplicated_in_topbar(self):
        user = User.objects.create_user("soloprofile", password="pass12345")
        Profile.objects.create(user=user, display_name="Solo", avatar_color="#e8a63c")
        self.client.login(username="soloprofile", password="pass12345")
        resp = self.client.get(reverse("dashboard"))
        # One from the household stack loop (filtered out) + one from the
        # dropdown trigger - should only ever render once now.
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


class FormatDurationTests(TestCase):
    def test_minutes_only_under_an_hour(self):
        self.assertEqual(selectors._format_duration(45), "45m")

    def test_hours_and_minutes(self):
        self.assertEqual(selectors._format_duration(82), "1h 22m")

    def test_zero_minutes(self):
        self.assertEqual(selectors._format_duration(0), "0m")

    def test_days_hours_minutes(self):
        # 2 days, 17 hours, 58 minutes = 2*1440 + 17*60 + 58 = 2880+1020+58 = 3958
        self.assertEqual(selectors._format_duration(3958), "2d 17h 58m")

    def test_exact_day_still_shows_zero_hours(self):
        self.assertEqual(selectors._format_duration(1440), "1d 0h 0m")


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
        self.assertEqual(result["average_duration"], selectors._format_duration(700 / 7))

    def test_delta_compares_against_the_preceding_window(self):
        from django.utils import timezone

        now = timezone.now()
        WatchEvent.objects.create(profile=self.profile, title=self._movie("This week", 140), watched_at=now)
        WatchEvent.objects.create(
            profile=self.profile, title=self._movie("Last week", 70), watched_at=now - timedelta(days=10)
        )
        result = selectors.daily_average(self.profile)
        self.assertTrue(result["delta_positive"])
        self.assertEqual(result["delta_label"], f"+{selectors._format_duration(round(140 / 7) - round(70 / 7))}")

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
                    {"name": "Director Person", "job": "Director"},
                ]
            }
        )
        self.assertEqual(tmdb.get_director("movie", 42), "Director Person")

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


class DashboardWatchingWatchlistTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("dashboardwatcher", password="pass12345")
        self.profile = Profile.objects.create(user=user, display_name="DashboardWatcher")
        self.client.login(username="dashboardwatcher", password="pass12345")

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
        # these two - patched here (not per-method) so adding them didn't
        # require touching every test's mock signature.
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
        self.assertContains(resp, "bg-success")
        self.assertTrue(WatchEvent.objects.filter(profile=self.profile, title=self.title).exists())

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
        self.assertContains(resp, "Add to Watchlist")

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
    def test_mark_watched_materializes_the_title_and_logs_a_watch(self, mock_details):
        mock_details.return_value = self._details()
        resp = self.client.post(reverse("title_preview_mark_watched", args=["movie", 42]))
        self.assertEqual(resp.status_code, 200)
        title = Title.objects.get(external_ids__tmdb="42")
        self.assertEqual(title.name, "Fathom")
        self.assertTrue(WatchEvent.objects.filter(profile=self.profile, title=title).exists())
        self.assertContains(resp, f"watched-btn-{title.pk}")
        self.assertContains(resp, "bg-success")

    @patch("tracker.integrations.tmdb.get_full_details")
    def test_mark_watched_reuses_an_existing_title(self, mock_details):
        mock_details.return_value = self._details()
        existing_title = Title.objects.create(
            media_type=MediaType.MOVIE, name="Fathom", year=2020,
            external_ids={"tmdb": "42", "tmdb_kind": "movie"},
        )
        self.client.post(reverse("title_preview_mark_watched", args=["movie", 42]))
        mock_details.assert_not_called()
        self.assertEqual(Title.objects.filter(external_ids__tmdb="42").count(), 1)
        self.assertTrue(WatchEvent.objects.filter(profile=self.profile, title=existing_title).exists())

    def test_mark_watched_requires_login(self):
        self.client.logout()
        resp = self.client.post(reverse("title_preview_mark_watched", args=["movie", 42]))
        self.assertNotEqual(resp.status_code, 200)

    @patch("tracker.integrations.tmdb.get_full_details")
    def test_add_to_list_materializes_the_title_and_adds_it(self, mock_details):
        mock_details.return_value = self._details()
        watchlist = WatchList.objects.create(profile=self.profile, name="Favorites")
        resp = self.client.post(reverse("title_preview_add_to_list", args=["movie", 42, watchlist.id]))
        self.assertEqual(resp.status_code, 200)
        title = Title.objects.get(external_ids__tmdb="42")
        self.assertTrue(WatchListItem.objects.filter(watchlist=watchlist, title=title).exists())
        # the returned fragment is the standard (real-title) popover, so
        # any further clicks flow through the ordinary add_to_list/
        # remove_from_list endpoints, not this preview-only one.
        self.assertContains(resp, f"list-popover-{title.pk}")

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
