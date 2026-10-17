"""Command line: python -m dota_winrates --account-id <id>."""

import argparse
import os
import sys
from pathlib import Path

from . import analysis, http, reports, stratz
from .opendota import OpenDotaClient, OpenDotaError
from .stratz import StratzAuthenticationError, StratzClient, StratzError

MAX_CONSECUTIVE_FAILED_MATCHES = 3
SECONDS_PER_HOUR = 60 * 60


def positive_integer(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def non_negative_number(text: str) -> float:
    value = float(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def parse_arguments(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dota-winrates",
        description="How often your team had the higher average winrate in your solo ranked Dota 2 matches.",
    )
    parser.add_argument("--account-id", type=int, required=True,
                        help="your Dota 2 account id (the number in your OpenDota or Dotabuff profile link)")
    parser.add_argument("--matches", type=positive_integer, default=100, help="how many recent ranked matches to fetch (default 100)")
    parser.add_argument("--min-difference", type=non_negative_number, default=1.0,
                        help="leave out matches whose team averages differ by less than this many percentage points (default 1.0)")
    parser.add_argument("--output-directory", type=Path, default=Path("output"), help="where reports are written (default ./output)")
    parser.add_argument("--cache-directory", type=Path, default=Path("cache"), help="where API responses are cached (default ./cache)")
    parser.add_argument("--refresh-hours", type=non_negative_number, default=24.0,
                        help="refetch player winrates older than this (default 24)")
    parser.add_argument("--excel", action="store_true", help="also write an Excel workbook (needs: pip install openpyxl)")
    return parser.parse_args(argv)


def stratz_party_size(stratz_client, match_id: int, account_id: int, log) -> int | None:
    try:
        return stratz_client.get_party_size(match_id, account_id)
    except StratzAuthenticationError:
        raise
    except StratzError as error:
        log(f"  ! no party data from Stratz for match {match_id}: {error}")
        return None


def collect_records(arguments, opendota_client, stratz_client, log) -> list[analysis.MatchRecord]:
    hero_names = opendota_client.get_hero_names()
    entries = opendota_client.get_recent_ranked_matches(arguments.account_id, arguments.matches)
    log(f"{len(entries)} ranked matches found.")
    records = []
    for position, entry in enumerate(entries):
        record = analysis.record_from_list_entry(entry, position, hero_names)
        stratz_size = None
        if record.party_size is None:
            stratz_size = stratz_party_size(stratz_client, record.match_id, arguments.account_id, log)
        records.append(analysis.resolve_party(record, stratz_size))

    solo_records = [record for record in records if record.included]
    details_by_match = {}
    ranked_win_loss = {}
    consecutive_failures = 0
    try:
        for index, record in enumerate(solo_records, start=1):
            log(f"[{index}/{len(solo_records)}] match {record.match_id}")
            try:
                details = opendota_client.get_match(record.match_id)
                for player in details["players"]:
                    player_account_id = player.get("account_id")
                    if not analysis.is_anonymous(player_account_id) and player_account_id not in ranked_win_loss:
                        ranked_win_loss[player_account_id] = opendota_client.get_ranked_win_loss(player_account_id)
            except OpenDotaError as error:
                consecutive_failures += 1
                log(f"  ! skipping match {record.match_id}: {error}")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILED_MATCHES:
                    log("  ! Three matches failed in a row (probably the OpenDota daily limit). "
                        "Stopping; run again later to continue from the cache.")
                    break
                continue
            consecutive_failures = 0
            details_by_match[record.match_id] = details
    except KeyboardInterrupt:
        log("\nInterrupted. Writing the results collected so far.")

    hidden_accounts = [account for account, win_loss in ranked_win_loss.items() if analysis.needs_stratz_lookup(win_loss)]
    stratz_totals = {}
    if hidden_accounts:
        log(f"Asking Stratz about {len(hidden_accounts)} players with hidden match history.")
        try:
            stratz_totals = stratz_client.get_totals(hidden_accounts)
        except StratzAuthenticationError:
            raise
        except StratzError as error:
            log(f"  ! Stratz totals unavailable, hidden players stay unknown: {error}")

    evaluated = []
    for record in records:
        if not record.included:
            evaluated.append(record)
            continue
        details = details_by_match.get(record.match_id)
        if details is None:
            evaluated.append(analysis.mark_not_loaded(record))
            continue
        try:
            evaluated.append(analysis.evaluate_teams(record, details, arguments.account_id, ranked_win_loss,
                                                     stratz_totals, hero_names, arguments.min_difference))
        except ValueError as error:
            log(f"  ! {error}")
            evaluated.append(analysis.mark_not_loaded(record))
    return evaluated


def main(argv=None, environment=None, token_file=None, opendota_factory=OpenDotaClient, stratz_factory=StratzClient,
         out=sys.stdout, log=http.log_to_stderr) -> int:
    arguments = parse_arguments(argv)
    token = stratz.read_token(os.environ if environment is None else environment,
                              Path(stratz.TOKEN_FILE_NAME) if token_file is None else token_file)
    if token is None:
        log(stratz.TOKEN_HELP)
        return 2

    refresh_seconds = arguments.refresh_hours * SECONDS_PER_HOUR
    opendota_client = opendota_factory(arguments.cache_directory, win_loss_max_age_seconds=refresh_seconds)
    stratz_client = stratz_factory(token, arguments.cache_directory, totals_max_age_seconds=refresh_seconds)
    try:
        records = collect_records(arguments, opendota_client, stratz_client, log)
    except StratzAuthenticationError as error:
        log(str(error))
        return 2
    except OpenDotaError as error:
        log(f"Could not load the match list from OpenDota: {error}")
        return 1
    if not records:
        log("No ranked matches found for this account. If the id is right, its match data is probably private "
            "(Dota 2 setting 'Expose Public Match Data').")
        return 1

    reports.print_report(records, arguments.min_difference, out=out)
    matches_path = arguments.output_directory / "matches.csv"
    players_path = arguments.output_directory / "players.csv"
    reports.write_csv(matches_path, reports.MATCH_COLUMNS, reports.match_rows(records))
    reports.write_csv(players_path, reports.PLAYER_COLUMNS, reports.player_rows(records))
    print(f"\nSaved {matches_path} and {players_path}", file=out)
    if arguments.excel:
        excel_path = arguments.output_directory / "report.xlsx"
        try:
            reports.write_excel(records, excel_path, arguments.min_difference)
        except reports.ExcelUnavailableError as error:
            log(str(error))
            return 1
        print(f"Saved {excel_path}", file=out)
    return 0
