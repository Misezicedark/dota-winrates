import csv
import io
import tempfile
import unittest
from pathlib import Path

from dota_winrates import reports
from dota_winrates.analysis import (
    EXCLUDED_PARTY,
    PARTY_SOURCE_OPENDOTA,
    SOURCE_ANONYMOUS,
    SOURCE_OPENDOTA,
    SOURCE_STRATZ,
    MatchRecord,
    PlayerRow,
    PlayerWinrate,
)


def row(account_id, name, winrate, source, wins=None, losses=None, is_me=False):
    return PlayerRow(account_id, name, "Lion", is_me, PlayerWinrate(winrate, source, wins, losses))


def kept_match():
    my_team = (row(103093152, "Dark", 0.5, SOURCE_OPENDOTA, 50, 50, is_me=True), row(7, "=HYPERLINK(\"x\")", 0.6, SOURCE_STRATZ, 60, 40))
    enemy_team = (row(None, "Anonymous", None, SOURCE_ANONYMOUS), row(8, "@mention", 0.53, SOURCE_OPENDOTA, 53, 47))
    return MatchRecord(match_id=11, series=1, start_time=1_790_000_000, hero="Faceless Void", won=True, party_size=1,
                       party_source=PARTY_SOURCE_OPENDOTA, my_team=my_team, enemy_team=enemy_team,
                       my_average=0.55, enemy_average=0.53)


def party_match():
    return MatchRecord(match_id=12, series=1, start_time=1_789_000_000, hero="-dash hero", won=False, party_size=3,
                       party_source=PARTY_SOURCE_OPENDOTA, exclusion_reason=EXCLUDED_PARTY)


class CsvTest(unittest.TestCase):
    def test_match_row_for_a_kept_match(self):
        values = reports.match_rows([kept_match()])[0]
        self.assertEqual(dict(zip(reports.MATCH_COLUMNS, values)) | {"date": None}, {
            "match_id": 11, "date": None, "series": 1, "hero": "Faceless Void", "result": "win", "party_size": 1,
            "party_source": "opendota", "my_team_average": 0.55, "my_team_known": 2, "enemy_average": 0.53,
            "enemy_known": 1, "difference_pp": 2.0, "included": "yes", "exclusion_reason": "",
        })

    def test_match_row_for_an_excluded_match_has_blank_averages(self):
        values = dict(zip(reports.MATCH_COLUMNS, reports.match_rows([party_match()])[0]))
        self.assertEqual((values["included"], values["exclusion_reason"], values["my_team_average"], values["my_team_known"]),
                         ("no", "party", "", ""))

    def test_player_rows_cover_both_teams(self):
        rows = reports.player_rows([kept_match()])
        self.assertEqual([dict(zip(reports.PLAYER_COLUMNS, values))["team"] for values in rows], ["my_team", "my_team", "enemy", "enemy"])
        self.assertEqual(dict(zip(reports.PLAYER_COLUMNS, rows[2])),
                         {"match_id": 11, "team": "enemy", "account_id": "", "name": "Anonymous", "hero": "Lion",
                          "wins": "", "losses": "", "winrate": "", "source": "anonymous"})

    def test_formula_like_names_are_written_as_text(self):
        rows = reports.player_rows([kept_match()])
        names = [dict(zip(reports.PLAYER_COLUMNS, values))["name"] for values in rows]
        self.assertEqual(names[1], "'=HYPERLINK(\"x\")")
        self.assertEqual(names[3], "'@mention")
        self.assertEqual(dict(zip(reports.MATCH_COLUMNS, reports.match_rows([party_match()])[0]))["hero"], "'-dash hero")

    def test_ordinary_values_are_untouched(self):
        self.assertEqual(reports.csv_safe("Dark"), "Dark")
        self.assertEqual(reports.csv_safe(-4.8), -4.8)

    def test_csv_file_starts_with_a_bom_and_the_header(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "matches.csv"
            reports.write_csv(path, reports.MATCH_COLUMNS, reports.match_rows([kept_match()]))
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            with open(path, encoding="utf-8-sig", newline="") as csv_file:
                self.assertEqual(next(csv.reader(csv_file)), reports.MATCH_COLUMNS)


class TerminalReportTest(unittest.TestCase):
    def render(self, records, min_difference=1.0):
        out = io.StringIO()
        reports.print_report(records, min_difference, out=out)
        return out.getvalue()

    def test_report_shows_the_funnel(self):
        text = self.render([kept_match(), party_match()])
        self.assertIn("fetched", text)
        self.assertRegex(text, r"excluded: party\s+1")
        self.assertRegex(text, r"kept\s+1")

    def test_report_shows_series_and_total_counts(self):
        text = self.render([kept_match(), party_match()])
        self.assertRegex(text, r"\n  1\s.*\s1\s+1 \(100%\)\s+0 \(-\)")
        self.assertRegex(text, r"\n  Total\s.*\s1\s+1 \(100%\)\s+0 \(-\)")

    def test_report_ends_with_the_headline_share(self):
        self.assertIn("The enemy team had the higher average in 0 of 1 kept matches (0%).", self.render([kept_match()]))

    def test_report_without_kept_matches_has_no_headline(self):
        self.assertNotIn("The enemy team had", self.render([party_match()]))


class ExcelFrontTest(unittest.TestCase):
    def test_missing_openpyxl_raises_a_clear_error(self):
        def missing_openpyxl(module_name):
            raise ImportError("No module named 'openpyxl'")

        with self.assertRaises(reports.ExcelUnavailableError) as raised:
            reports.write_excel([kept_match()], Path("unused.xlsx"), 1.0, import_module=missing_openpyxl)
        self.assertIn("pip install openpyxl", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
