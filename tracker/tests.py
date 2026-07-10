import io
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django_celery_beat.models import PeriodicTask

from . import completion, csv_import, instance_config, scheduling, tasks
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
