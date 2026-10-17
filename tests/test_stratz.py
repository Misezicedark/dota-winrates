import tempfile
import unittest
from http.client import RemoteDisconnected
from pathlib import Path

from fakes import FakeClock, FakePoster, http_error

from dota_winrates.stratz import (
    StratzAuthenticationError,
    StratzClient,
    StratzError,
    party_size_from_match,
    read_token,
)

TOKEN = "test-token-value"
SECONDS_PER_HOUR = 60 * 60


def totals_response(*players):
    return {"data": {"players": [
        {"steamAccountId": account_id, "matchCount": matches, "winCount": wins} for account_id, wins, matches in players
    ]}}


def match_response(players):
    return {"data": {"match": {"id": 1, "players": players}}}


class ReadTokenTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.token_file = Path(self.temporary_directory.name) / ".stratz_token"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_environment_variable_wins_over_file(self):
        self.token_file.write_text("file-token", encoding="utf-8")
        self.assertEqual(read_token({"STRATZ_TOKEN": "environment-token"}, self.token_file), "environment-token")

    def test_file_is_used_when_environment_variable_is_missing_or_blank(self):
        self.token_file.write_text("  file-token\n", encoding="utf-8")
        self.assertEqual(read_token({}, self.token_file), "file-token")
        self.assertEqual(read_token({"STRATZ_TOKEN": "   "}, self.token_file), "file-token")

    def test_no_token_anywhere_returns_none(self):
        self.assertIsNone(read_token({}, self.token_file))

    def test_empty_file_returns_none(self):
        self.token_file.write_text("\n", encoding="utf-8")
        self.assertIsNone(read_token({}, self.token_file))


class PartySizeFromMatchTest(unittest.TestCase):
    def test_empty_party_id_means_solo(self):
        players = [{"steamAccountId": 7, "partyId": None, "isRadiant": True}, {"steamAccountId": 8, "partyId": 0, "isRadiant": True}]
        self.assertEqual(party_size_from_match(players, 7), 1)

    def test_counts_teammates_sharing_the_party_id(self):
        players = [
            {"steamAccountId": 7, "partyId": 2, "isRadiant": False},
            {"steamAccountId": 8, "partyId": 2, "isRadiant": False},
            {"steamAccountId": 9, "partyId": 3, "isRadiant": False},
            {"steamAccountId": 10, "partyId": 2, "isRadiant": True},
        ]
        self.assertEqual(party_size_from_match(players, 7), 2)

    def test_account_not_in_match_is_unknown(self):
        self.assertIsNone(party_size_from_match([{"steamAccountId": 8, "partyId": None, "isRadiant": True}], 7))


class StratzClientTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.cache_directory = Path(self.temporary_directory.name)
        self.clock = FakeClock()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def make_client(self, poster, **options):
        return StratzClient(
            TOKEN,
            self.cache_directory,
            post_json=poster,
            sleep=self.clock.sleep,
            clock=self.clock.time,
            log=lambda message: None,
            **options,
        )

    def test_totals_are_requested_five_accounts_at_a_time(self):
        poster = FakePoster([
            totals_response((1, 10, 20), (2, 11, 20), (3, 12, 20), (4, 13, 20), (5, 14, 20)),
            totals_response((6, 15, 20), (7, 16, 20)),
        ])
        totals = self.make_client(poster).get_totals([1, 2, 3, 4, 5, 6, 7])
        self.assertEqual([call["payload"]["variables"]["ids"] for call in poster.calls], [[1, 2, 3, 4, 5], [6, 7]])
        self.assertEqual(totals[7], (16, 20))
        self.assertEqual(len(totals), 7)

    def test_requests_carry_the_token_and_the_stratz_user_agent(self):
        poster = FakePoster([totals_response((1, 10, 20))])
        self.make_client(poster).get_totals([1])
        self.assertEqual(poster.calls[0]["url"], "https://api.stratz.com/graphql")
        self.assertEqual(poster.calls[0]["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(poster.calls[0]["headers"]["User-Agent"], "STRATZ_API")

    def test_duplicate_accounts_are_requested_once(self):
        poster = FakePoster([totals_response((1, 10, 20))])
        self.make_client(poster).get_totals([1, 1, 1])
        self.assertEqual(poster.calls[0]["payload"]["variables"]["ids"], [1])

    def test_totals_are_served_from_cache_within_the_refresh_interval(self):
        poster = FakePoster([totals_response((1, 10, 20)), totals_response((1, 11, 21))])
        self.make_client(poster).get_totals([1])
        self.clock.now += 23 * SECONDS_PER_HOUR
        self.assertEqual(self.make_client(poster).get_totals([1]), {1: (10, 20)})
        self.assertEqual(len(poster.calls), 1)

    def test_totals_are_refreshed_after_the_refresh_interval(self):
        poster = FakePoster([totals_response((1, 10, 20)), totals_response((1, 11, 21))])
        self.make_client(poster).get_totals([1])
        self.clock.now += 24 * SECONDS_PER_HOUR + 1
        self.assertEqual(self.make_client(poster).get_totals([1]), {1: (11, 21)})

    def test_accounts_stratz_does_not_know_are_left_out_and_not_refetched(self):
        poster = FakePoster([{"data": {"players": [None, {"steamAccountId": 2, "matchCount": 0, "winCount": 0}]}}])
        self.assertEqual(self.make_client(poster).get_totals([1, 2]), {})
        self.assertEqual(self.make_client(poster).get_totals([1, 2]), {})
        self.assertEqual(len(poster.calls), 1)

    def test_rejected_token_raises_authentication_error_without_the_token(self):
        poster = FakePoster([http_error("https://api.stratz.com/graphql", 401)])
        with self.assertRaises(StratzAuthenticationError) as raised:
            self.make_client(poster).get_totals([1])
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertEqual(len(poster.calls), 1)

    def test_waits_a_full_minute_after_rate_limit(self):
        poster = FakePoster([http_error("https://api.stratz.com/graphql", 429), totals_response((1, 10, 20))], clock=self.clock)
        self.assertEqual(self.make_client(poster).get_totals([1]), {1: (10, 20)})
        self.assertGreaterEqual(poster.call_times[1] - poster.call_times[0], 60.0)

    def test_retries_after_the_server_drops_the_connection(self):
        poster = FakePoster([RemoteDisconnected("Remote end closed connection"), totals_response((1, 10, 20))])
        self.assertEqual(self.make_client(poster).get_totals([1]), {1: (10, 20)})
        self.assertEqual(len(poster.calls), 2)

    def test_graphql_error_is_not_retried(self):
        poster = FakePoster([{"errors": [{"message": "You have surpassed the maximum take value of : 5"}], "data": None}])
        with self.assertRaises(StratzError):
            self.make_client(poster).get_totals([1])
        self.assertEqual(len(poster.calls), 1)

    def test_gives_up_after_too_many_server_errors(self):
        poster = FakePoster([http_error("https://api.stratz.com/graphql", 503)])
        with self.assertRaises(StratzError):
            self.make_client(poster).get_totals([1])
        self.assertEqual(len(poster.calls), 5)

    def test_party_size_comes_from_match_data(self):
        poster = FakePoster([match_response([
            {"steamAccountId": 7, "partyId": 2, "isRadiant": True},
            {"steamAccountId": 8, "partyId": 2, "isRadiant": True},
        ])])
        self.assertEqual(self.make_client(poster).get_party_size(1, 7), 2)

    def test_known_match_is_cached_for_good(self):
        poster = FakePoster([match_response([{"steamAccountId": 7, "partyId": None, "isRadiant": True}])])
        self.make_client(poster).get_party_size(1, 7)
        self.clock.now += 365 * 24 * SECONDS_PER_HOUR
        self.assertEqual(self.make_client(poster).get_party_size(1, 7), 1)
        self.assertEqual(len(poster.calls), 1)

    def test_unknown_match_is_asked_again_next_time(self):
        poster = FakePoster([{"data": {"match": None}}, match_response([{"steamAccountId": 7, "partyId": None, "isRadiant": True}])])
        self.assertIsNone(self.make_client(poster).get_party_size(1, 7))
        self.assertEqual(self.make_client(poster).get_party_size(1, 7), 1)


if __name__ == "__main__":
    unittest.main()
