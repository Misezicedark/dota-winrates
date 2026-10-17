"""Pure analysis: player winrates, solo detection, team averages, filters and summaries."""

from collections.abc import Iterable
from dataclasses import dataclass, replace

# Steam's placeholder for players who hide their match data.
ANONYMOUS_ACCOUNT_ID = 4294967295
RADIANT_SLOT_LIMIT = 128
SERIES_SIZE = 100

SOURCE_OPENDOTA = "opendota_ranked"
SOURCE_STRATZ = "stratz_total"
SOURCE_ANONYMOUS = "anonymous"
SOURCE_UNKNOWN = "unknown"

PARTY_SOURCE_OPENDOTA = "opendota"
PARTY_SOURCE_STRATZ = "stratz"
PARTY_SOURCE_UNKNOWN = "unknown"

EXCLUDED_NOT_LOADED = "not loaded"
EXCLUDED_PARTY = "party"
EXCLUDED_PARTY_UNKNOWN = "party unknown"
EXCLUDED_NO_COMPARISON = "no comparison"
EXCLUDED_TOO_CLOSE = "too close"
EXCLUSION_ORDER = (EXCLUDED_NOT_LOADED, EXCLUDED_PARTY, EXCLUDED_PARTY_UNKNOWN, EXCLUDED_NO_COMPARISON, EXCLUDED_TOO_CLOSE)


@dataclass(frozen=True)
class PlayerWinrate:
    winrate: float | None
    source: str
    wins: int | None = None
    losses: int | None = None


@dataclass(frozen=True)
class PlayerRow:
    account_id: int | None
    name: str
    hero: str
    is_me: bool
    winrate: PlayerWinrate


@dataclass(frozen=True)
class MatchRecord:
    match_id: int
    series: int
    start_time: int
    hero: str
    won: bool
    party_size: int | None
    party_source: str
    exclusion_reason: str | None = None
    my_team: tuple[PlayerRow, ...] = ()
    enemy_team: tuple[PlayerRow, ...] = ()
    my_average: float | None = None
    enemy_average: float | None = None

    @property
    def my_known(self) -> int:
        return sum(1 for row in self.my_team if row.winrate.winrate is not None)

    @property
    def enemy_known(self) -> int:
        return sum(1 for row in self.enemy_team if row.winrate.winrate is not None)

    @property
    def difference(self) -> float | None:
        """My team average minus the enemy average, in percentage points."""
        if self.my_average is None or self.enemy_average is None:
            return None
        return (self.my_average - self.enemy_average) * 100

    @property
    def included(self) -> bool:
        return self.exclusion_reason is None


@dataclass(frozen=True)
class SeriesSummary:
    series: int | None  # None for the total over all series
    first_start_time: int | None
    last_start_time: int | None
    kept: int
    we_higher: int
    we_higher_wins: int
    they_higher: int
    they_higher_wins: int


def is_anonymous(account_id: int | None) -> bool:
    return account_id is None or account_id == ANONYMOUS_ACCOUNT_ID


def compute_winrate(wins: int, losses: int) -> float | None:
    games = wins + losses
    return None if games == 0 else wins / games


def hero_name(hero_names: dict[int, str], hero_id: int) -> str:
    return hero_names.get(hero_id, f"Hero #{hero_id}")


def series_for_position(position: int) -> int:
    return position // SERIES_SIZE + 1


def is_radiant(player: dict) -> bool:
    if "isRadiant" in player:
        return player["isRadiant"]
    return player["player_slot"] < RADIANT_SLOT_LIMIT


def needs_stratz_lookup(ranked_win_loss: tuple[int, int]) -> bool:
    """OpenDota answers 0 wins and 0 losses for players who hide their match history."""
    return sum(ranked_win_loss) == 0


def player_winrate(
    account_id: int | None,
    ranked_win_loss: dict[int, tuple[int, int]],
    stratz_totals: dict[int, tuple[int, int]],
) -> PlayerWinrate:
    if is_anonymous(account_id):
        return PlayerWinrate(None, SOURCE_ANONYMOUS)
    ranked = ranked_win_loss.get(account_id)
    if ranked is None:
        return PlayerWinrate(None, SOURCE_UNKNOWN)
    if not needs_stratz_lookup(ranked):
        wins, losses = ranked
        return PlayerWinrate(compute_winrate(wins, losses), SOURCE_OPENDOTA, wins, losses)
    totals = stratz_totals.get(account_id)
    if totals is not None and totals[1] > 0:
        wins, matches = totals
        return PlayerWinrate(compute_winrate(wins, matches - wins), SOURCE_STRATZ, wins, matches - wins)
    return PlayerWinrate(None, SOURCE_UNKNOWN)


def record_from_list_entry(entry: dict, position: int, hero_names: dict[int, str]) -> MatchRecord:
    """A record from one entry of OpenDota's /players/{id}/matches list."""
    party_size = entry.get("party_size")
    return MatchRecord(
        match_id=entry["match_id"],
        series=series_for_position(position),
        start_time=entry["start_time"],
        hero=hero_name(hero_names, entry["hero_id"]),
        won=(entry["player_slot"] < RADIANT_SLOT_LIMIT) == entry["radiant_win"],
        party_size=party_size,
        party_source=PARTY_SOURCE_UNKNOWN if party_size is None else PARTY_SOURCE_OPENDOTA,
    )


def resolve_party(record: MatchRecord, stratz_party_size: int | None) -> MatchRecord:
    if record.party_size is None and stratz_party_size is not None:
        record = replace(record, party_size=stratz_party_size, party_source=PARTY_SOURCE_STRATZ)
    if record.party_size is None:
        return replace(record, exclusion_reason=EXCLUDED_PARTY_UNKNOWN)
    if record.party_size > 1:
        return replace(record, exclusion_reason=EXCLUDED_PARTY)
    return record


def mark_not_loaded(record: MatchRecord) -> MatchRecord:
    return replace(record, exclusion_reason=EXCLUDED_NOT_LOADED)


def team_average(rows: Iterable[PlayerRow]) -> float | None:
    winrates = [row.winrate.winrate for row in rows if row.winrate.winrate is not None]
    return sum(winrates) / len(winrates) if winrates else None


def is_too_close(difference: float, min_difference: float) -> bool:
    # Rounding absorbs float noise such as 0.9999999999999 for a true 1.0 pp difference.
    return round(abs(difference), 9) < min_difference


def evaluate_teams(
    record: MatchRecord,
    match_details: dict,
    account_id: int,
    ranked_win_loss: dict[int, tuple[int, int]],
    stratz_totals: dict[int, tuple[int, int]],
    hero_names: dict[int, str],
    min_difference: float,
) -> MatchRecord:
    """Fills in both teams from OpenDota match details and applies the comparison filters."""
    players = match_details["players"]
    me = next((player for player in players if player.get("account_id") == account_id), None)
    if me is None:
        raise ValueError(f"account {account_id} is not among the players of match {record.match_id}")
    my_side = is_radiant(me)
    my_team, enemy_team = [], []
    for player in players:
        player_account_id = player.get("account_id")
        anonymous = is_anonymous(player_account_id)
        row = PlayerRow(
            account_id=None if anonymous else player_account_id,
            name="Anonymous" if anonymous else (player.get("personaname") or "?"),
            hero=hero_name(hero_names, player["hero_id"]),
            is_me=player_account_id == account_id,
            winrate=player_winrate(player_account_id, ranked_win_loss, stratz_totals),
        )
        (my_team if is_radiant(player) == my_side else enemy_team).append(row)
    evaluated = replace(
        record,
        my_team=tuple(my_team),
        enemy_team=tuple(enemy_team),
        my_average=team_average(my_team),
        enemy_average=team_average(enemy_team),
    )
    if evaluated.difference is None:
        return replace(evaluated, exclusion_reason=EXCLUDED_NO_COMPARISON)
    if is_too_close(evaluated.difference, min_difference):
        return replace(evaluated, exclusion_reason=EXCLUDED_TOO_CLOSE)
    return evaluated


def _summarize_group(series: int | None, records: list[MatchRecord]) -> SeriesSummary:
    kept = [record for record in records if record.included]
    we_higher = [record for record in kept if record.difference > 0]
    they_higher = [record for record in kept if record.difference <= 0]
    start_times = [record.start_time for record in records]
    return SeriesSummary(
        series=series,
        first_start_time=min(start_times, default=None),
        last_start_time=max(start_times, default=None),
        kept=len(kept),
        we_higher=len(we_higher),
        we_higher_wins=sum(1 for record in we_higher if record.won),
        they_higher=len(they_higher),
        they_higher_wins=sum(1 for record in they_higher if record.won),
    )


def summarize(records: list[MatchRecord]) -> list[SeriesSummary]:
    """One summary per series in order, then the total."""
    series_numbers = sorted({record.series for record in records})
    summaries = [_summarize_group(number, [record for record in records if record.series == number]) for number in series_numbers]
    summaries.append(_summarize_group(None, list(records)))
    return summaries


def exclusion_funnel(records: list[MatchRecord]) -> list[tuple[str, int]]:
    steps = [("fetched", len(records))]
    steps += [(reason, sum(1 for record in records if record.exclusion_reason == reason)) for reason in EXCLUSION_ORDER]
    steps.append(("kept", sum(1 for record in records if record.included)))
    return steps
