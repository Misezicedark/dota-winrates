import json
import unittest
from pathlib import Path

from dota_winrates.analysis import (
    EXCLUDED_NO_COMPARISON,
    EXCLUDED_NOT_LOADED,
    EXCLUDED_PARTY,
    EXCLUDED_PARTY_UNKNOWN,
    EXCLUDED_TOO_CLOSE,
    PARTY_SOURCE_OPENDOTA,
    PARTY_SOURCE_STRATZ,
    PARTY_SOURCE_UNKNOWN,
    SOURCE_ANONYMOUS,
    SOURCE_OPENDOTA,
    SOURCE_STRATZ,
    SOURCE_UNKNOWN,
    MatchRecord,
    compute_winrate,
    evaluate_teams,
    exclusion_funnel,
    hero_name,
    is_anonymous,
    is_too_close,
    mark_not_loaded,
    needs_stratz_lookup,
    player_winrate,
    record_from_list_entry,
    resolve_party,
    summarize,
)

FIXTURE = Path(__file__).parent / "fixtures" / "match_9027311469.json"
ME = 103093152
HERO_NAMES = {43: "Death Prophet", 123: "Hoodwink", 112: "Winter Wyvern", 114: "Monkey King", 18: "Sven",
              41: "Faceless Void", 31: "Lich", 104: "Legion Commander", 35: "Sniper", 26: "Lion"}
# Dire (my team): me 0.60, 06 0.50, 07 hidden → Stratz 0.51, 08 0.30, 09 0.45 → average 0.472
# Radiant (enemy): 01 0.55, 02 0.52, 03 anonymous, 04 0.49, 05 hidden and unknown to Stratz → average 0.52
RANKED_WIN_LOSS = {
    ME: (60, 40), 1000000006: (50, 50), 1000000007: (0, 0), 1000000008: (30, 70), 1000000009: (45, 55),
    1000000001: (55, 45), 1000000002: (52, 48), 1000000004: (49, 51), 1000000005: (0, 0),
}
STRATZ_TOTALS = {1000000007: (510, 1000)}
LIST_ENTRY = {"match_id": 9027311469, "player_slot": 128, "radiant_win": True, "start_time": 1791028019,
              "hero_id": 41, "party_size": 1}


def load_fixture(anonymous_account=1000000003):
    match = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for player in match["players"]:
        if player["account_id"] == anonymous_account:
            player["account_id"] = None
            player.pop("personaname", None)
    return match


def solo_record():
    return record_from_list_entry(LIST_ENTRY, 0, HERO_NAMES)


def evaluate(match=None, min_difference=1.0, ranked=RANKED_WIN_LOSS, stratz=STRATZ_TOTALS):
    return evaluate_teams(solo_record(), match or load_fixture(), ME, ranked, stratz, HERO_NAMES, min_difference)


def kept_record(series, my_average, enemy_average, won, start_time=1_790_000_000):
    return MatchRecord(match_id=start_time, series=series, start_time=start_time, hero="Lion", won=won,
                       party_size=1, party_source=PARTY_SOURCE_OPENDOTA, my_average=my_average, enemy_average=enemy_average)


class WinrateTest(unittest.TestCase):
    def test_winrate_is_wins_over_games(self):
        self.assertAlmostEqual(compute_winrate(3, 1), 0.75)

    def test_winrate_without_games_is_unknown(self):
        self.assertIsNone(compute_winrate(0, 0))

    def test_missing_or_placeholder_account_is_anonymous(self):
        self.assertTrue(is_anonymous(None))
        self.assertTrue(is_anonymous(4294967295))
        self.assertFalse(is_anonymous(ME))

    def test_public_player_uses_ranked_record(self):
        self.assertEqual(player_winrate(1000000001, RANKED_WIN_LOSS, STRATZ_TOTALS).source, SOURCE_OPENDOTA)
        self.assertAlmostEqual(player_winrate(1000000001, RANKED_WIN_LOSS, STRATZ_TOTALS).winrate, 0.55)

    def test_hidden_player_uses_stratz_total(self):
        winrate = player_winrate(1000000007, RANKED_WIN_LOSS, STRATZ_TOTALS)
        self.assertEqual((winrate.source, winrate.wins, winrate.losses), (SOURCE_STRATZ, 510, 490))
        self.assertAlmostEqual(winrate.winrate, 0.51)

    def test_hidden_player_unknown_to_stratz_has_no_winrate(self):
        self.assertEqual(player_winrate(1000000005, RANKED_WIN_LOSS, STRATZ_TOTALS).source, SOURCE_UNKNOWN)

    def test_stratz_is_not_used_when_the_ranked_lookup_is_missing(self):
        self.assertEqual(player_winrate(42, {}, {42: (10, 20)}).source, SOURCE_UNKNOWN)

    def test_anonymous_player_has_no_winrate(self):
        self.assertEqual(player_winrate(None, RANKED_WIN_LOSS, STRATZ_TOTALS), player_winrate(4294967295, {}, {}))
        self.assertEqual(player_winrate(None, RANKED_WIN_LOSS, STRATZ_TOTALS).source, SOURCE_ANONYMOUS)

    def test_only_zero_zero_records_need_stratz(self):
        self.assertTrue(needs_stratz_lookup((0, 0)))
        self.assertFalse(needs_stratz_lookup((1, 0)))

    def test_unknown_hero_gets_a_readable_label(self):
        self.assertEqual(hero_name(HERO_NAMES, 999), "Hero #999")


class ListEntryTest(unittest.TestCase):
    def test_entry_becomes_a_record(self):
        record = solo_record()
        self.assertEqual((record.match_id, record.series, record.hero, record.won), (9027311469, 1, "Faceless Void", False))
        self.assertEqual((record.party_size, record.party_source, record.included), (1, PARTY_SOURCE_OPENDOTA, True))

    def test_position_decides_the_series(self):
        self.assertEqual(record_from_list_entry(LIST_ENTRY, 99, HERO_NAMES).series, 1)
        self.assertEqual(record_from_list_entry(LIST_ENTRY, 100, HERO_NAMES).series, 2)

    def test_win_when_my_side_won(self):
        self.assertTrue(record_from_list_entry({**LIST_ENTRY, "player_slot": 2}, 0, HERO_NAMES).won)

    def test_missing_party_size_is_unknown(self):
        record = record_from_list_entry({**LIST_ENTRY, "party_size": None}, 0, HERO_NAMES)
        self.assertEqual((record.party_size, record.party_source), (None, PARTY_SOURCE_UNKNOWN))


class ResolvePartyTest(unittest.TestCase):
    def unknown_record(self):
        return record_from_list_entry({**LIST_ENTRY, "party_size": None}, 0, HERO_NAMES)

    def test_solo_from_opendota_stays_included(self):
        self.assertTrue(resolve_party(solo_record(), None).included)

    def test_party_from_opendota_is_excluded(self):
        record = resolve_party(record_from_list_entry({**LIST_ENTRY, "party_size": 3}, 0, HERO_NAMES), None)
        self.assertEqual(record.exclusion_reason, EXCLUDED_PARTY)

    def test_stratz_fills_a_missing_solo(self):
        record = resolve_party(self.unknown_record(), 1)
        self.assertEqual((record.party_size, record.party_source, record.included), (1, PARTY_SOURCE_STRATZ, True))

    def test_stratz_fills_a_missing_party(self):
        self.assertEqual(resolve_party(self.unknown_record(), 2).exclusion_reason, EXCLUDED_PARTY)

    def test_unknown_everywhere_is_excluded(self):
        self.assertEqual(resolve_party(self.unknown_record(), None).exclusion_reason, EXCLUDED_PARTY_UNKNOWN)

    def test_opendota_value_wins_over_stratz(self):
        self.assertEqual(resolve_party(solo_record(), 4).party_source, PARTY_SOURCE_OPENDOTA)


class EvaluateTeamsTest(unittest.TestCase):
    def test_teams_follow_my_side(self):
        record = evaluate()
        self.assertEqual([row.account_id for row in record.my_team], [ME, 1000000006, 1000000007, 1000000008, 1000000009])
        self.assertEqual([row.account_id for row in record.enemy_team], [1000000001, 1000000002, None, 1000000004, 1000000005])

    def test_averages_skip_anonymous_and_unknown_players(self):
        record = evaluate()
        self.assertAlmostEqual(record.my_average, 0.472)
        self.assertAlmostEqual(record.enemy_average, 0.52)
        self.assertEqual((record.my_known, record.enemy_known), (5, 3))

    def test_difference_is_my_team_minus_enemy_in_points(self):
        self.assertAlmostEqual(evaluate().difference, -4.8)

    def test_large_difference_is_kept(self):
        self.assertTrue(evaluate().included)

    def test_rows_carry_names_heroes_and_sources(self):
        record = evaluate()
        self.assertEqual(record.my_team[0].name, "Dark")
        self.assertTrue(record.my_team[0].is_me)
        self.assertEqual(record.enemy_team[0].hero, "Death Prophet")
        self.assertEqual(record.enemy_team[2].name, "Anonymous")
        self.assertEqual([row.winrate.source for row in record.enemy_team],
                         [SOURCE_OPENDOTA, SOURCE_OPENDOTA, SOURCE_ANONYMOUS, SOURCE_OPENDOTA, SOURCE_UNKNOWN])

    def test_difference_below_threshold_is_too_close(self):
        self.assertEqual(evaluate(min_difference=5.0).exclusion_reason, EXCLUDED_TOO_CLOSE)

    def test_team_without_known_winrates_has_no_comparison(self):
        match = load_fixture()
        for player in match["players"]:
            if player["isRadiant"]:
                player["account_id"] = None
        record = evaluate(match)
        self.assertEqual(record.exclusion_reason, EXCLUDED_NO_COMPARISON)
        self.assertIsNone(record.difference)

    def test_unknown_hero_in_match_gets_a_label(self):
        match = load_fixture()
        match["players"][0]["hero_id"] = 999
        self.assertEqual(evaluate(match).enemy_team[0].hero, "Hero #999")

    def test_match_without_my_account_raises(self):
        match = load_fixture()
        for player in match["players"]:
            if player["account_id"] == ME:
                player["account_id"] = 1000000010
        with self.assertRaises(ValueError):
            evaluate(match)


class ThresholdTest(unittest.TestCase):
    def test_exactly_the_threshold_is_kept(self):
        self.assertFalse(is_too_close(1.0, 1.0))
        self.assertFalse(is_too_close(-1.0, 1.0))

    def test_just_below_the_threshold_is_too_close(self):
        self.assertTrue(is_too_close(0.99, 1.0))
        self.assertTrue(is_too_close(-0.99, 1.0))

    def test_floating_point_noise_at_the_threshold_is_kept(self):
        self.assertFalse(is_too_close((0.51 - 0.50) * 100, 1.0))

    def test_zero_threshold_keeps_everything(self):
        self.assertFalse(is_too_close(0.0, 0.0))


class SummaryTest(unittest.TestCase):
    def setUp(self):
        not_loaded = mark_not_loaded(kept_record(1, None, None, True))
        self.records = [
            kept_record(1, 0.52, 0.50, True, 1_790_000_300),
            kept_record(1, 0.50, 0.53, False, 1_790_000_200),
            kept_record(1, 0.500, 0.515, True, 1_790_000_100),
            not_loaded,
            kept_record(2, 0.51, 0.50, False, 1_780_000_000),
        ]
        self.summaries = summarize(self.records)

    def test_one_summary_per_series_and_a_total(self):
        self.assertEqual([summary.series for summary in self.summaries], [1, 2, None])

    def test_series_counts_kept_matches_by_side(self):
        first = self.summaries[0]
        self.assertEqual((first.kept, first.we_higher, first.we_higher_wins, first.they_higher, first.they_higher_wins), (3, 1, 1, 2, 1))

    def test_partial_series_reports_its_real_counts_and_period(self):
        second = self.summaries[1]
        self.assertEqual((second.kept, second.we_higher, second.they_higher), (1, 1, 0))
        self.assertEqual((second.first_start_time, second.last_start_time), (1_780_000_000, 1_780_000_000))

    def test_total_adds_up_all_series(self):
        total = self.summaries[-1]
        self.assertEqual((total.kept, total.we_higher, total.we_higher_wins, total.they_higher, total.they_higher_wins), (4, 2, 1, 2, 1))

    def test_funnel_counts_every_step_in_order(self):
        funnel = exclusion_funnel(self.records)
        self.assertEqual(funnel, [("fetched", 5), (EXCLUDED_NOT_LOADED, 1), (EXCLUDED_PARTY, 0), (EXCLUDED_PARTY_UNKNOWN, 0),
                                  (EXCLUDED_NO_COMPARISON, 0), (EXCLUDED_TOO_CLOSE, 0), ("kept", 4)])

    def test_empty_input_has_an_empty_total(self):
        self.assertEqual(summarize([])[-1].kept, 0)


if __name__ == "__main__":
    unittest.main()
