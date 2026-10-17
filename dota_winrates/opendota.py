"""OpenDota API client with a disk cache, request pacing and retries."""

import json
import ssl
import time
import urllib.error
from http.client import HTTPException
from pathlib import Path

from . import http
from .disk_cache import MISSING, DiskCache

API_BASE_URL = "https://api.opendota.com/api"
RANKED_LOBBY_TYPE = 7
SECONDS_PER_DAY = 24 * 60 * 60
HEROES_CACHE_SECONDS = 7 * SECONDS_PER_DAY
TOO_MANY_REQUESTS = 429
# OpenDota's free tier allows 60 requests per minute; requesting at exactly that pace still triggers 429s.
SECONDS_BETWEEN_REQUESTS = 1.25
RATE_LIMIT_WAIT_SECONDS = 60
MAXIMUM_RETRY_WAIT_SECONDS = 60


class OpenDotaError(Exception):
    pass


class OpenDotaClient:
    """OpenDota API access with a disk cache, request pacing and retries."""

    def __init__(
        self,
        cache_directory: Path,
        fetch_json=http.get_json,
        sleep=time.sleep,
        clock=time.time,
        log=http.log_to_stderr,
        win_loss_max_age_seconds: float = SECONDS_PER_DAY,
        minimum_seconds_between_requests: float = SECONDS_BETWEEN_REQUESTS,
        max_attempts: int = 5,
        first_retry_wait_seconds: float = 5.0,
    ):
        self.cache = DiskCache(Path(cache_directory), clock=clock)
        self.pacer = http.RequestPacer(minimum_seconds_between_requests, sleep=sleep, clock=clock)
        self.fetch_json = fetch_json
        self.sleep = sleep
        self.log = log
        self.win_loss_max_age_seconds = win_loss_max_age_seconds
        self.max_attempts = max_attempts
        self.first_retry_wait_seconds = first_retry_wait_seconds

    def get_recent_ranked_matches(self, account_id: int, limit: int) -> list[dict]:
        return self._request(f"/players/{account_id}/matches?lobby_type={RANKED_LOBBY_TYPE}&limit={limit}")

    def get_match(self, match_id: int) -> dict:
        # A finished match never changes, so it is cached for good.
        return self._cached(f"matches/{match_id}.json", f"/matches/{match_id}", max_age_seconds=None)

    def get_ranked_win_loss(self, account_id: int) -> tuple[int, int]:
        win_loss = self._cached(
            f"win_loss/{account_id}.json",
            f"/players/{account_id}/wl?lobby_type={RANKED_LOBBY_TYPE}",
            max_age_seconds=self.win_loss_max_age_seconds,
        )
        return win_loss["win"], win_loss["lose"]

    def get_hero_names(self) -> dict[int, str]:
        heroes = self._cached("heroes.json", "/heroes", max_age_seconds=HEROES_CACHE_SECONDS)
        return {hero["id"]: hero["localized_name"] for hero in heroes}

    def _cached(self, cache_name: str, path: str, max_age_seconds: float | None):
        data = self.cache.read(cache_name, max_age_seconds)
        if data is MISSING:
            data = self._request(path)
            self.cache.write(cache_name, data)
        return data

    def _request(self, path: str):
        url = API_BASE_URL + path
        retry_wait_seconds = self.first_retry_wait_seconds
        for attempt in range(1, self.max_attempts + 1):
            self.pacer.wait()
            wait_seconds = retry_wait_seconds
            try:
                return self.fetch_json(url)
            except urllib.error.HTTPError as error:
                if error.code != TOO_MANY_REQUESTS and error.code < 500:
                    raise OpenDotaError(f"{url}: HTTP {error.code}") from error
                failure = f"HTTP {error.code}"
                if error.code == TOO_MANY_REQUESTS:
                    # The limit is counted per minute, so shorter waits just hit it again.
                    wait_seconds = RATE_LIMIT_WAIT_SECONDS
            # urllib wraps connection errors in URLError, but not those raised while reading the
            # response (RemoteDisconnected, ConnectionResetError, IncompleteRead), so OSError and
            # HTTPException are caught as well.
            except (urllib.error.URLError, OSError, HTTPException, json.JSONDecodeError) as error:
                certificate_error = getattr(error, "reason", error)
                if isinstance(certificate_error, ssl.SSLCertVerificationError):
                    raise OpenDotaError(f"{url}: certificate verification failed ({certificate_error})") from error
                failure = str(error) or type(error).__name__
            if attempt < self.max_attempts:
                self.log(f"  ! {failure} for {url}, retrying in {wait_seconds:.0f}s")
                self.sleep(wait_seconds)
                retry_wait_seconds = min(retry_wait_seconds * 2, MAXIMUM_RETRY_WAIT_SECONDS)
        raise OpenDotaError(f"{url}: {failure} after {self.max_attempts} attempts")
