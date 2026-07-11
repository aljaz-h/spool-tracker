import io
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django_celery_beat.models import PeriodicTask

from . import completion, csv_import, instance_config, rewatches, scheduling, selectors, tasks
from .integrations import tmdb, trakt
from .models import (
    Episode,
    ExternalAccount,
    InstanceConfig,
    MediaType,
    Profile,
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
