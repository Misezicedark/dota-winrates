import csv
import io
import tempfile
import unittest
from pathlib import Path

from dota_winrates import cli
from dota_winrates.opendota import OpenDotaError
from dota_winrates.stratz import StratzAuthenticationError

ME = 103093152
EVEN = (50, 50)
STRONG = (60, 40)


def list_entry(match_id, party_size):
    return {"match_id": match_id, "player_slot": 0, "radiant_win": True, "start_time": 1_790_000_000 - match_id,
            "hero_id": 1, "party_size": party_size}


def match_details(match_id, teammates, enemies):
    def player(account_id, radiant):
        return {"account_id": account_id, "isRadiant": radiant, "player_slot": 0 if radiant else 128,
                "hero_id": 1, "personaname": f"Player {account_id}"}

    return {"match_id": match_id, "players": [player(ME, True)] + [player(account_id, True) for account_id in teammates]
            + [player(account_id, False) for account_id in enemies]}


class FakeOpenDota:
    def __init__(self, entries, details_by_match, win_loss_by_account):
        self.entries = entries
        self.details_by_match = details_by_match
        self.win_loss_by_account = win_loss_by_account
        self.requested_matches = []

    def get_hero_names(self):
        return {1: "Anti-Mage"}

    def get_recent_ranked_matches(self, account_id, limit):
        return self.entries[:limit]

    def get_match(self, match_id):
        self.requested_matches.append(match_id)
        if match_id not in self.details_by_match:
            raise OpenDotaError(f"match {match_id}: HTTP 404")
        return self.details_by_match[match_id]

    def get_ranked_win_loss(self, account_id):
        return self.win_loss_by_account[account_id]


class FakeStratz:
    def __init__(self, party_sizes=None, totals=None, error=None, totals_error=None):
        self.party_sizes = party_sizes or {}
        self.totals = totals or {}
        self.error = error
        self.totals_error = totals_error
        self.totals_requests = []

    def get_party_size(self, match_id, account_id):
        if self.error:
            raise self.error
        return self.party_sizes.get(match_id)

    def get_totals(self, account_ids):
        self.totals_requests.append(list(account_ids))
        if self.totals_error:
            raise self.totals_error
        return {account_id: self.totals[account_id] for account_id in account_ids if account_id in self.totals}


def scenario():
    """11 kept (they higher), 12 party, 13 Stratz-solo but too close, 14 party unknown, 15 not loaded."""
    entries = [list_entry(11, 1), list_entry(12, 3), list_entry(13, None), list_entry(14, None), list_entry(15, 1)]
    details = {
        11: match_details(11, [1, 2, 3, 4], [5, 6, 7, 8, 9]),
        13: match_details(13, [21, 22, 23, 24], [25, 26, 27, 28, 29]),
    }
    win_loss = {ME: EVEN, **{account: EVEN for account in (1, 2, 3, 4, 21, 22, 23, 24, 26, 27, 28, 29)},
                **{account: STRONG for account in (5, 6, 7, 8, 9)}, 25: (0, 0)}
    return FakeOpenDota(entries, details, win_loss), FakeStratz(party_sizes={13: 1}, totals={25: (505, 1000)})


class CliTestCase(unittest.TestCase):
    """Temporary directories and a helper that runs the CLI against fake clients."""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.messages = []

    def tearDown(self):
        self.temporary_directory.cleanup()

    def run_cli(self, opendota, stratz, extra_arguments=(), environment=None):
        out = io.StringIO()
        code = cli.main(
            ["--account-id", str(ME), "--output-directory", str(self.directory / "output"),
             "--cache-directory", str(self.directory / "cache"), *extra_arguments],
            environment={"STRATZ_TOKEN": "test-token"} if environment is None else environment,
            token_file=self.directory / ".stratz_token",
            opendota_factory=lambda cache_directory, **options: opendota,
            stratz_factory=lambda token, cache_directory, **options: stratz,
            out=out,
            log=self.messages.append,
        )
        return code, out.getvalue()

    def matches_csv(self):
        with open(self.directory / "output" / "matches.csv", encoding="utf-8-sig", newline="") as csv_file:
            return {int(row["match_id"]): row for row in csv.DictReader(csv_file)}


class CliTest(CliTestCase):
    def test_missing_token_stops_before_any_request(self):
        def refuse(*arguments, **options):
            raise AssertionError("no client may be created without a token")

        code = cli.main(["--account-id", str(ME)], environment={}, token_file=self.directory / "missing",
                        opendota_factory=refuse, stratz_factory=refuse, out=io.StringIO(), log=self.messages.append)
        self.assertEqual(code, 2)
        self.assertIn("https://stratz.com/api", "\n".join(self.messages))

    def test_token_from_file_is_accepted(self):
        (self.directory / ".stratz_token").write_text("file-token", encoding="utf-8")
        opendota, stratz = scenario()
        code, _ = self.run_cli(opendota, stratz, environment={})
        self.assertEqual(code, 0)

    def test_full_run_applies_every_filter(self):
        opendota, stratz = scenario()
        code, output = self.run_cli(opendota, stratz)
        self.assertEqual(code, 0)
        reasons = {match_id: row["exclusion_reason"] for match_id, row in self.matches_csv().items()}
        self.assertEqual(reasons, {11: "", 12: "party", 13: "too close", 14: "party unknown", 15: "not loaded"})
        self.assertIn("The enemy team had the higher average in 1 of 1 kept matches (100%).", output)

    def test_party_matches_are_never_downloaded(self):
        opendota, stratz = scenario()
        self.run_cli(opendota, stratz)
        self.assertNotIn(12, opendota.requested_matches)
        self.assertNotIn(14, opendota.requested_matches)

    def test_stratz_totals_are_asked_only_for_hidden_players(self):
        opendota, stratz = scenario()
        self.run_cli(opendota, stratz)
        self.assertEqual(stratz.totals_requests, [[25]])

    def test_stratz_party_size_is_recorded_with_its_source(self):
        opendota, stratz = scenario()
        self.run_cli(opendota, stratz)
        self.assertEqual(self.matches_csv()[13]["party_source"], "stratz")

    def test_lower_threshold_keeps_the_close_match(self):
        opendota, stratz = scenario()
        self.run_cli(opendota, stratz, ["--min-difference", "0"])
        self.assertEqual(self.matches_csv()[13]["included"], "yes")

    def test_players_csv_is_written(self):
        opendota, stratz = scenario()
        self.run_cli(opendota, stratz)
        with open(self.directory / "output" / "players.csv", encoding="utf-8-sig", newline="") as csv_file:
            sources = [row["source"] for row in csv.DictReader(csv_file) if row["match_id"] == "13"]
        self.assertEqual(sources.count("stratz_total"), 1)

    def test_empty_match_list_explains_privacy(self):
        code, _ = self.run_cli(FakeOpenDota([], {}, {}), FakeStratz())
        self.assertEqual(code, 1)
        self.assertIn("Expose Public Match Data", "\n".join(self.messages))

    def test_rejected_stratz_token_exits_with_code_two(self):
        opendota, _ = scenario()
        code, _ = self.run_cli(opendota, FakeStratz(error=StratzAuthenticationError("Stratz rejected the API token")))
        self.assertEqual(code, 2)
        self.assertIn("rejected", "\n".join(self.messages))

    def test_three_failed_matches_in_a_row_stop_the_run(self):
        entries = [list_entry(match_id, 1) for match_id in (31, 32, 33, 34, 35)]
        opendota = FakeOpenDota(entries, {35: match_details(35, [1, 2, 3, 4], [5, 6, 7, 8, 9])},
                                {ME: EVEN, **{account: EVEN for account in range(1, 10)}})
        code, _ = self.run_cli(opendota, FakeStratz())
        self.assertEqual(code, 0)
        self.assertEqual(opendota.requested_matches, [31, 32, 33])
        self.assertEqual({row["exclusion_reason"] for row in self.matches_csv().values()}, {"not loaded"})

    def test_match_without_my_account_counts_as_not_loaded(self):
        opendota, stratz = scenario()
        opendota.details_by_match[11] = match_details(11, [1, 2, 3, 4], [5, 6, 7, 8, 9])
        opendota.details_by_match[11]["players"][0]["account_id"] = 99
        opendota.win_loss_by_account[99] = EVEN
        self.run_cli(opendota, stratz)
        self.assertEqual(self.matches_csv()[11]["exclusion_reason"], "not loaded")


class InterruptTest(CliTestCase):
    """Ctrl+C at any stage writes the reports for what was collected so far."""

    def test_interrupt_while_asking_stratz_for_totals_still_writes_reports(self):
        opendota, stratz = scenario()
        stratz.totals_error = KeyboardInterrupt()
        code, _ = self.run_cli(opendota, stratz)
        self.assertEqual(code, 0)
        self.assertEqual(self.matches_csv()[11]["exclusion_reason"], "")

    def test_interrupt_while_resolving_party_sizes_still_writes_reports(self):
        opendota, _ = scenario()
        code, _ = self.run_cli(opendota, FakeStratz(error=KeyboardInterrupt()))
        self.assertEqual(code, 0)
        reasons = {match_id: row["exclusion_reason"] for match_id, row in self.matches_csv().items()}
        self.assertEqual(reasons[12], "party")
        self.assertEqual(reasons[13], "party unknown")
        self.assertEqual(reasons[11], "not loaded")

    def test_interrupt_while_loading_matches_skips_the_remaining_steps(self):
        opendota, stratz = scenario()
        original_get_match = opendota.get_match

        def interrupt_on_second_match(match_id):
            if match_id == 13:
                raise KeyboardInterrupt()
            return original_get_match(match_id)

        opendota.get_match = interrupt_on_second_match
        code, _ = self.run_cli(opendota, stratz)
        self.assertEqual(code, 0)
        self.assertEqual(stratz.totals_requests, [])
        self.assertEqual(self.matches_csv()[11]["exclusion_reason"], "")
        self.assertEqual(self.matches_csv()[13]["exclusion_reason"], "not loaded")

    def test_interrupt_before_any_match_is_listed_exits_quietly(self):
        opendota, stratz = scenario()

        def interrupt(account_id, limit):
            raise KeyboardInterrupt()

        opendota.get_recent_ranked_matches = interrupt
        code, _ = self.run_cli(opendota, stratz)
        self.assertEqual(code, 130)
        self.assertFalse((self.directory / "output" / "matches.csv").exists())


class ArgumentTest(unittest.TestCase):
    def test_defaults(self):
        arguments = cli.parse_arguments(["--account-id", "1"])
        self.assertEqual((arguments.matches, arguments.min_difference, arguments.refresh_hours, arguments.excel), (100, 1.0, 24.0, False))

    def test_account_id_is_required(self):
        with self.assertRaises(SystemExit):
            cli.parse_arguments([])

    def test_negative_threshold_is_rejected(self):
        with self.assertRaises(SystemExit):
            cli.parse_arguments(["--account-id", "1", "--min-difference", "-1"])

    def test_zero_matches_is_rejected(self):
        with self.assertRaises(SystemExit):
            cli.parse_arguments(["--account-id", "1", "--matches", "0"])


if __name__ == "__main__":
    unittest.main()
