"""Stratz GraphQL client: all-mode totals for players with hidden match history, and party sizes."""

import json
import time
import urllib.error
from collections.abc import Iterable, Mapping
from http.client import HTTPException
from pathlib import Path

from . import http
from .disk_cache import MISSING, DiskCache

GRAPHQL_URL = "https://api.stratz.com/graphql"
# Stratz answers 403 to requests without this user agent.
STRATZ_USER_AGENT = "STRATZ_API"
TOKEN_ENVIRONMENT_VARIABLE = "STRATZ_TOKEN"
TOKEN_FILE_NAME = ".stratz_token"
TOKEN_HELP = (
    "A Stratz API token is required. Sign in at https://stratz.com/api, copy your token, then either\n"
    f"  export {TOKEN_ENVIRONMENT_VARIABLE}=<your token>\n"
    f"or save it in a file named {TOKEN_FILE_NAME} in the current directory."
)
# Stratz rejects players() calls with more than 5 ids.
MAX_IDS_PER_CALL = 5
# Stratz allows 8 requests per second, 150 per minute and 1500 per hour.
SECONDS_BETWEEN_REQUESTS = 0.5
RATE_LIMIT_WAIT_SECONDS = 60
MAXIMUM_RETRY_WAIT_SECONDS = 60
SECONDS_PER_DAY = 24 * 60 * 60
TOO_MANY_REQUESTS = 429
REJECTED_TOKEN_STATUS_CODES = (401, 403)
TOTALS_QUERY = "query ($ids: [Long]!) { players(steamAccountIds: $ids) { steamAccountId matchCount winCount } }"
MATCH_PARTY_QUERY = "query ($id: Long!) { match(id: $id) { id players { steamAccountId partyId isRadiant } } }"


class StratzError(Exception):
    pass


class StratzAuthenticationError(StratzError):
    pass


def read_token(environment: Mapping[str, str], token_file: Path) -> str | None:
    token = environment.get(TOKEN_ENVIRONMENT_VARIABLE, "").strip()
    if not token and token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
    return token or None


def party_size_from_match(match_players: list[dict], account_id: int) -> int | None:
    me = next((player for player in match_players if player.get("steamAccountId") == account_id), None)
    if me is None:
        return None
    if me.get("partyId") is None:
        # Stratz leaves partyId empty for players who queued alone.
        return 1
    return sum(
        1 for player in match_players
        if player.get("partyId") == me["partyId"] and player.get("isRadiant") == me.get("isRadiant")
    )


class StratzClient:
    def __init__(
        self,
        token: str,
        cache_directory: Path,
        post_json=http.post_json,
        sleep=time.sleep,
        clock=time.time,
        log=http.log_to_stderr,
        totals_max_age_seconds: float = SECONDS_PER_DAY,
        minimum_seconds_between_requests: float = SECONDS_BETWEEN_REQUESTS,
        max_attempts: int = 5,
        first_retry_wait_seconds: float = 5.0,
    ):
        self.token = token
        self.cache = DiskCache(Path(cache_directory), clock=clock)
        self.pacer = http.RequestPacer(minimum_seconds_between_requests, sleep=sleep, clock=clock)
        self.post_json = post_json
        self.sleep = sleep
        self.log = log
        self.totals_max_age_seconds = totals_max_age_seconds
        self.max_attempts = max_attempts
        self.first_retry_wait_seconds = first_retry_wait_seconds

    def get_totals(self, account_ids: Iterable[int]) -> dict[int, tuple[int, int]]:
        """(wins, matches) per account across all game modes; accounts Stratz does not know are left out."""
        totals = {}
        pending = []
        for account_id in dict.fromkeys(account_ids):
            cached = self.cache.read(f"stratz_totals/{account_id}.json", self.totals_max_age_seconds)
            if cached is MISSING:
                pending.append(account_id)
            elif cached is not None:
                totals[account_id] = (cached["winCount"], cached["matchCount"])
        for start in range(0, len(pending), MAX_IDS_PER_CALL):
            batch = pending[start:start + MAX_IDS_PER_CALL]
            players = self._query(TOTALS_QUERY, {"ids": batch}).get("players") or []
            found = {player["steamAccountId"]: player for player in players if player}
            for account_id in batch:
                player = found.get(account_id)
                data = None
                if player is not None and player.get("matchCount"):
                    data = {"matchCount": player["matchCount"], "winCount": player["winCount"]}
                    totals[account_id] = (data["winCount"], data["matchCount"])
                self.cache.write(f"stratz_totals/{account_id}.json", data)
        return totals

    def get_party_size(self, match_id: int, account_id: int) -> int | None:
        cache_name = f"stratz_matches/{match_id}.json"
        players = self.cache.read(cache_name, None)
        if players is MISSING:
            match = self._query(MATCH_PARTY_QUERY, {"id": match_id}).get("match")
            if match is None:
                # Stratz may not have processed the match yet, so this is not cached.
                return None
            players = match["players"]
            self.cache.write(cache_name, players)
        return party_size_from_match(players, account_id)

    def _query(self, query: str, variables: dict) -> dict:
        headers = {"Authorization": f"Bearer {self.token}", "User-Agent": STRATZ_USER_AGENT}
        retry_wait_seconds = self.first_retry_wait_seconds
        for attempt in range(1, self.max_attempts + 1):
            self.pacer.wait()
            wait_seconds = retry_wait_seconds
            try:
                body = self.post_json(GRAPHQL_URL, {"query": query, "variables": variables}, headers)
            except urllib.error.HTTPError as error:
                if error.code in REJECTED_TOKEN_STATUS_CODES:
                    raise StratzAuthenticationError(
                        "Stratz rejected the API token (invalid or expired). Get a new one at https://stratz.com/api."
                    ) from error
                if error.code != TOO_MANY_REQUESTS and error.code < 500:
                    raise StratzError(f"Stratz: HTTP {error.code}") from error
                failure = f"HTTP {error.code}"
                if error.code == TOO_MANY_REQUESTS:
                    wait_seconds = RATE_LIMIT_WAIT_SECONDS
            # Errors raised while reading the response are not wrapped in URLError (see opendota.py).
            except (urllib.error.URLError, OSError, HTTPException, json.JSONDecodeError) as error:
                failure = str(error) or type(error).__name__
            else:
                if body.get("errors"):
                    raise StratzError(f"Stratz: {body['errors'][0].get('message', 'query failed')}")
                return body.get("data") or {}
            if attempt < self.max_attempts:
                self.log(f"  ! Stratz {failure}, retrying in {wait_seconds:.0f}s")
                self.sleep(wait_seconds)
                retry_wait_seconds = min(retry_wait_seconds * 2, MAXIMUM_RETRY_WAIT_SECONDS)
        raise StratzError(f"Stratz: {failure} after {self.max_attempts} attempts")
