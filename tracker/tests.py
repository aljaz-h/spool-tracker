import io
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from . import csv_import, instance_config, tasks
from .integrations import tmdb, trakt
from .models import ExternalAccount, InstanceConfig, MediaType, Profile, SyncLog, Title, WatchEvent


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


class TmdbPosterLookupTests(TestCase):
    def _response(self, results):
        resp = Mock()
        resp.json.return_value = {"results": results}
        resp.raise_for_status = Mock()
        return resp

    @override_settings(TMDB_API_KEY="")
    def test_returns_none_without_api_key(self):
        self.assertIsNone(tmdb.find_poster_url(MediaType.MOVIE, "Fathom", 2020))

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_returns_poster_url_on_match(self, mock_get):
        mock_get.return_value = self._response([{"poster_path": "/abc123.jpg"}])
        url = tmdb.find_poster_url(MediaType.MOVIE, "The Long Corridor", 2020)
        self.assertEqual(url, "https://image.tmdb.org/t/p/w500/abc123.jpg")
        self.assertEqual(mock_get.call_args.args[0], "https://api.themoviedb.org/3/search/movie")
        self.assertEqual(mock_get.call_args.kwargs["params"]["year"], 2020)

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_returns_none_on_no_results(self, mock_get):
        mock_get.return_value = self._response([])
        self.assertIsNone(tmdb.find_poster_url(MediaType.MOVIE, "Nonexistent Movie", 2020))

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_anime_tries_tv_then_falls_back_to_movie(self, mock_get):
        mock_get.side_effect = [self._response([]), self._response([{"poster_path": "/anime-movie.jpg"}])]
        url = tmdb.find_poster_url(MediaType.ANIME, "Ashfall Requiem", 2022)
        self.assertEqual(url, "https://image.tmdb.org/t/p/w500/anime-movie.jpg")
        self.assertEqual(mock_get.call_count, 2)
        self.assertIn("search/tv", mock_get.call_args_list[0].args[0])
        self.assertIn("search/movie", mock_get.call_args_list[1].args[0])

    @override_settings(TMDB_API_KEY="test-key")
    @patch("tracker.integrations.tmdb.requests.get")
    def test_returns_none_on_request_exception(self, mock_get):
        import requests

        mock_get.side_effect = requests.RequestException("boom")
        self.assertIsNone(tmdb.find_poster_url(MediaType.MOVIE, "Fathom", 2020))


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


class SettingsPageIntegrationsVisibilityTests(TestCase):
    def setUp(self):
        owner_user = User.objects.create_user("owner2", password="pass12345", is_superuser=True)
        Profile.objects.create(user=owner_user, display_name="Owner2")
        member_user = User.objects.create_user("member2", password="pass12345")
        Profile.objects.create(user=member_user, display_name="Member2")

    def test_owner_sees_integrations_card(self):
        self.client.login(username="owner2", password="pass12345")
        resp = self.client.get(reverse("settings"))
        self.assertContains(resp, "Integrations")

    def test_member_does_not_see_integrations_card(self):
        self.client.login(username="member2", password="pass12345")
        resp = self.client.get(reverse("settings"))
        self.assertNotContains(resp, "Integrations")

    def test_configured_secret_value_never_rendered_in_html(self):
        InstanceConfig.objects.create(pk=1, trakt_client_secret="super-secret-value")
        self.client.login(username="owner2", password="pass12345")
        resp = self.client.get(reverse("settings"))
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
