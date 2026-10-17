"""Terminal summary, CSV files and the optional Excel workbook."""

import csv
import importlib
import sys
from datetime import datetime
from pathlib import Path

from . import analysis

MATCH_COLUMNS = [
    "match_id", "date", "series", "hero", "result", "party_size", "party_source",
    "my_team_average", "my_team_known", "enemy_average", "enemy_known", "difference_pp",
    "included", "exclusion_reason",
]
PLAYER_COLUMNS = ["match_id", "team", "account_id", "name", "hero", "wins", "losses", "winrate", "source"]
# Spreadsheet apps run cells starting with these characters as formulas; player names are user-chosen.
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class ExcelUnavailableError(Exception):
    pass


def format_date(start_time: int | None) -> str:
    return "" if start_time is None else datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M")


def csv_safe(value):
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def blank_if_none(value, digits: int | None = None):
    if value is None:
        return ""
    return round(value, digits) if digits is not None else value


def share(part: int, whole: int) -> str:
    return "-" if whole == 0 else f"{part / whole * 100:.0f}%"


def match_rows(records: list[analysis.MatchRecord]) -> list[list]:
    rows = []
    for record in records:
        loaded = bool(record.my_team or record.enemy_team)
        rows.append([
            record.match_id,
            format_date(record.start_time),
            record.series,
            csv_safe(record.hero),
            "win" if record.won else "loss",
            blank_if_none(record.party_size),
            record.party_source,
            blank_if_none(record.my_average, 4),
            record.my_known if loaded else "",
            blank_if_none(record.enemy_average, 4),
            record.enemy_known if loaded else "",
            blank_if_none(record.difference, 2),
            "yes" if record.included else "no",
            record.exclusion_reason or "",
        ])
    return rows


def player_rows(records: list[analysis.MatchRecord]) -> list[list]:
    rows = []
    for record in records:
        for team_label, team in (("my_team", record.my_team), ("enemy", record.enemy_team)):
            for player in team:
                rows.append([
                    record.match_id,
                    team_label,
                    blank_if_none(player.account_id),
                    csv_safe(player.name),
                    csv_safe(player.hero),
                    blank_if_none(player.winrate.wins),
                    blank_if_none(player.winrate.losses),
                    blank_if_none(player.winrate.winrate, 4),
                    player.winrate.source,
                ])
    return rows


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Excel shows player names with special characters correctly.
    with open(path, "w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(header)
        writer.writerows(rows)


def print_report(records: list[analysis.MatchRecord], min_difference: float, out=sys.stdout) -> None:
    def write(line: str = "") -> None:
        print(line, file=out)

    write("Matches")
    for label, count in analysis.exclusion_funnel(records):
        name = label if label in ("fetched", "kept") else f"excluded: {label}"
        write(f"  {name:<26} {count:>5}")
    write()
    write(f"Kept: solo matches, both teams with a known winrate, difference of at least {min_difference:g} pp")
    write(f"  {'Series':<8} {'Period':<25} {'Kept':>5} {'We higher (won)':>17} {'They higher (won)':>19}")
    summaries = analysis.summarize(records)
    for summary in summaries:
        label = "Total" if summary.series is None else str(summary.series)
        period = ""
        if summary.first_start_time is not None:
            period = f"{format_date(summary.first_start_time)[:10]} → {format_date(summary.last_start_time)[:10]}"
        we_higher = f"{summary.we_higher} ({share(summary.we_higher_wins, summary.we_higher)})"
        they_higher = f"{summary.they_higher} ({share(summary.they_higher_wins, summary.they_higher)})"
        write(f"  {label:<8} {period:<25} {summary.kept:>5} {we_higher:>17} {they_higher:>19}")
    total = summaries[-1]
    if total.kept:
        write()
        write(f"The enemy team had the higher average in {total.they_higher} of {total.kept} kept matches "
              f"({share(total.they_higher, total.kept)}).")


def write_excel(records: list[analysis.MatchRecord], path: Path, min_difference: float,
                import_module=importlib.import_module) -> None:
    try:
        excel_report = import_module(f"{__package__}.excel_report")
    except ImportError as error:
        raise ExcelUnavailableError("Excel output needs openpyxl: pip install openpyxl") from error
    excel_report.write_workbook(records, path, min_difference)
