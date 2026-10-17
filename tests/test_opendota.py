import ssl
import tempfile
import unittest
import urllib.error
from http.client import IncompleteRead, RemoteDisconnected
from pathlib import Path

from fakes import FakeClock, FakeFetcher, http_error

from dota_winrates import http
from dota_winrates.opendota import OpenDotaClient, OpenDotaError

SECONDS_PER_HOUR = 60 * 60
RANKED_WIN_LOSS_URL = "https://api.opendota.com/api/players/123/wl?lobby_type=7"
MATCH_URL = "https://api.opendota.com/api/matches/555"
RANKED_MATCHES_URL = "https://api.opendota.com/api/players/123/matches?lobby_type=7&limit=100"


class OpenDotaClientTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.cache_directory = Path(self.temporary_directory.name)
        self.clock = FakeClock()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def make_client(self, fetcher, **options):
        return OpenDotaClient(
            self.cache_directory,
            fetch_json=fetcher,
            sleep=self.clock.sleep,
            clock=self.clock.time,
            log=lambda message: None,
            **options,
        )

    def test_ranked_win_loss_asks_for_ranked_lobby_only(self):
        fetcher = FakeFetcher({RANKED_WIN_LOSS_URL: [{"win": 2536, "lose": 2518}]})
        self.assertEqual(self.make_client(fetcher).get_ranked_win_loss(123), (2536, 2518))
        self.assertEqual(fetcher.requested_urls, [RANKED_WIN_LOSS_URL])

    def test_recent_matches_ask_for_ranked_lobby_only(self):
        fetcher = FakeFetcher({RANKED_MATCHES_URL: [[{"match_id": 1}]]})
        self.assertEqual(self.make_client(fetcher).get_recent_ranked_matches(123, 100), [{"match_id": 1}])
        self.assertEqual(fetcher.requested_urls, [RANKED_MATCHES_URL])

    def test_match_is_served_from_disk_cache_on_later_runs(self):
        fetcher = FakeFetcher({MATCH_URL: [{"match_id": 555, "players": []}]})
        self.make_client(fetcher).get_match(555)
        match = self.make_client(fetcher).get_match(555)
        self.assertEqual(match, {"match_id": 555, "players": []})
        self.assertEqual(len(fetcher.requested_urls), 1)

    def test_win_loss_is_reused_within_a_day(self):
        fetcher = FakeFetcher({RANKED_WIN_LOSS_URL: [{"win": 10, "lose": 5}, {"win": 11, "lose": 5}]})
        self.make_client(fetcher).get_ranked_win_loss(123)
        self.clock.now += 23 * SECONDS_PER_HOUR
        self.assertEqual(self.make_client(fetcher).get_ranked_win_loss(123), (10, 5))
        self.assertEqual(len(fetcher.requested_urls), 1)

    def test_win_loss_is_refreshed_after_a_day(self):
        fetcher = FakeFetcher({RANKED_WIN_LOSS_URL: [{"win": 10, "lose": 5}, {"win": 11, "lose": 5}]})
        self.make_client(fetcher).get_ranked_win_loss(123)
        self.clock.now += 24 * SECONDS_PER_HOUR + 1
        self.assertEqual(self.make_client(fetcher).get_ranked_win_loss(123), (11, 5))
        self.assertEqual(len(fetcher.requested_urls), 2)

    def test_win_loss_refresh_interval_is_configurable(self):
        fetcher = FakeFetcher({RANKED_WIN_LOSS_URL: [{"win": 10, "lose": 5}, {"win": 11, "lose": 5}]})
        self.make_client(fetcher, win_loss_max_age_seconds=72 * SECONDS_PER_HOUR).get_ranked_win_loss(123)
        self.clock.now += 48 * SECONDS_PER_HOUR
        self.assertEqual(self.make_client(fetcher, win_loss_max_age_seconds=72 * SECONDS_PER_HOUR).get_ranked_win_loss(123), (10, 5))

    def test_zero_refresh_interval_always_refetches(self):
        fetcher = FakeFetcher({RANKED_WIN_LOSS_URL: [{"win": 10, "lose": 5}, {"win": 11, "lose": 5}]})
        self.make_client(fetcher, win_loss_max_age_seconds=0).get_ranked_win_loss(123)
        self.clock.now += 1
        self.assertEqual(self.make_client(fetcher, win_loss_max_age_seconds=0).get_ranked_win_loss(123), (11, 5))

    def test_recent_matches_are_always_fetched_fresh(self):
        fetcher = FakeFetcher({RANKED_MATCHES_URL: [[{"match_id": 1}], [{"match_id": 2}, {"match_id": 1}]]})
        self.make_client(fetcher).get_recent_ranked_matches(123, 100)
        matches = self.make_client(fetcher).get_recent_ranked_matches(123, 100)
        self.assertEqual(matches, [{"match_id": 2}, {"match_id": 1}])

    def test_hero_names_are_keyed_by_hero_id(self):
        fetcher = FakeFetcher({
            "https://api.opendota.com/api/heroes": [[
                {"id": 1, "name": "npc_dota_hero_antimage", "localized_name": "Anti-Mage"},
                {"id": 2, "name": "npc_dota_hero_axe", "localized_name": "Axe"},
            ]]
        })
        self.assertEqual(self.make_client(fetcher).get_hero_names(), {1: "Anti-Mage", 2: "Axe"})

    def test_waits_between_consecutive_requests(self):
        fetcher = FakeFetcher({
            RANKED_WIN_LOSS_URL: [{"win": 1, "lose": 1}],
            MATCH_URL: [{"match_id": 555, "players": []}],
        })
        client = self.make_client(fetcher)
        start_time = self.clock.now
        client.get_ranked_win_loss(123)
        client.get_match(555)
        self.assertGreaterEqual(self.clock.now - start_time, 1.0)

    def test_stays_under_fifty_requests_per_minute(self):
        # OpenDota's free tier allows 60 requests per minute; requests at exactly that pace still get 429s.
        match_urls = {
            f"https://api.opendota.com/api/matches/{match_id}": [{"match_id": match_id, "players": []}]
            for match_id in range(51)
        }
        fetcher = FakeFetcher(match_urls, clock=self.clock)
        client = self.make_client(fetcher)
        for match_id in range(51):
            client.get_match(match_id)
        self.assertGreaterEqual(fetcher.request_times[50] - fetcher.request_times[0], 60.0)

    def test_waits_a_full_minute_after_rate_limit(self):
        fetcher = FakeFetcher(
            {RANKED_WIN_LOSS_URL: [http_error(RANKED_WIN_LOSS_URL, 429), {"win": 7, "lose": 3}]},
            clock=self.clock,
        )
        self.assertEqual(self.make_client(fetcher).get_ranked_win_loss(123), (7, 3))
        self.assertGreaterEqual(fetcher.request_times[1] - fetcher.request_times[0], 60.0)

    def test_retries_after_rate_limit_and_backs_off(self):
        fetcher = FakeFetcher({
            RANKED_WIN_LOSS_URL: [
                http_error(RANKED_WIN_LOSS_URL, 429),
                http_error(RANKED_WIN_LOSS_URL, 429),
                {"win": 7, "lose": 3},
            ]
        })
        start_time = self.clock.now
        self.assertEqual(self.make_client(fetcher).get_ranked_win_loss(123), (7, 3))
        self.assertEqual(len(fetcher.requested_urls), 3)
        # Plain request spacing alone would be 2.5 seconds for 3 requests.
        self.assertGreater(self.clock.now - start_time, 2.5)

    def test_retries_after_network_error(self):
        fetcher = FakeFetcher({
            RANKED_WIN_LOSS_URL: [urllib.error.URLError("connection reset"), {"win": 7, "lose": 3}]
        })
        self.assertEqual(self.make_client(fetcher).get_ranked_win_loss(123), (7, 3))

    def test_retries_after_the_server_drops_the_connection(self):
        fetcher = FakeFetcher({
            RANKED_WIN_LOSS_URL: [RemoteDisconnected("Remote end closed connection"), {"win": 7, "lose": 3}]
        })
        self.assertEqual(self.make_client(fetcher).get_ranked_win_loss(123), (7, 3))
        self.assertEqual(len(fetcher.requested_urls), 2)

    def test_retries_after_connection_reset_while_reading(self):
        fetcher = FakeFetcher({RANKED_WIN_LOSS_URL: [ConnectionResetError(54, "Connection reset by peer"), {"win": 7, "lose": 3}]})
        self.assertEqual(self.make_client(fetcher).get_ranked_win_loss(123), (7, 3))

    def test_retries_after_incomplete_body(self):
        fetcher = FakeFetcher({RANKED_WIN_LOSS_URL: [IncompleteRead(b"{"), {"win": 7, "lose": 3}]})
        self.assertEqual(self.make_client(fetcher).get_ranked_win_loss(123), (7, 3))

    def test_gives_up_after_too_many_failures(self):
        fetcher = FakeFetcher({RANKED_WIN_LOSS_URL: [http_error(RANKED_WIN_LOSS_URL, 503)]})
        with self.assertRaises(OpenDotaError):
            self.make_client(fetcher).get_ranked_win_loss(123)
        self.assertEqual(len(fetcher.requested_urls), 5)

    def test_does_not_retry_when_resource_is_missing(self):
        fetcher = FakeFetcher({MATCH_URL: [http_error(MATCH_URL, 404)]})
        with self.assertRaises(OpenDotaError):
            self.make_client(fetcher).get_match(555)
        self.assertEqual(len(fetcher.requested_urls), 1)

    def test_does_not_retry_certificate_errors(self):
        certificate_error = urllib.error.URLError(ssl.SSLCertVerificationError("unable to get local issuer certificate"))
        fetcher = FakeFetcher({MATCH_URL: [certificate_error]})
        with self.assertRaises(OpenDotaError):
            self.make_client(fetcher).get_match(555)
        self.assertEqual(len(fetcher.requested_urls), 1)

    def test_failed_request_is_not_cached(self):
        fetcher = FakeFetcher({MATCH_URL: [http_error(MATCH_URL, 404), {"match_id": 555, "players": []}]})
        with self.assertRaises(OpenDotaError):
            self.make_client(fetcher).get_match(555)
        self.assertEqual(self.make_client(fetcher).get_match(555), {"match_id": 555, "players": []})


class SslContextTest(unittest.TestCase):
    @unittest.skipUnless(http.MACOS_SYSTEM_CA_FILE.exists(), "needs the macOS system certificate bundle")
    def test_context_trusts_certificates_from_fallback_file(self):
        context = http.ssl_context_with_fallback(http.MACOS_SYSTEM_CA_FILE)
        self.assertGreater(len(context.get_ca_certs()), 0)

    def test_missing_fallback_file_is_ignored(self):
        context = http.ssl_context_with_fallback(Path("/does/not/exist.pem"))
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)


if __name__ == "__main__":
    unittest.main()
