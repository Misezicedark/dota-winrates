"""HTTP helpers shared by the API clients."""

import json
import ssl
import sys
import urllib.request
from pathlib import Path

USER_AGENT = "dota-winrates"
REQUEST_TIMEOUT_SECONDS = 30
# Python from python.org ships without CA certificates on macOS; the system bundle works instead.
MACOS_SYSTEM_CA_FILE = Path("/etc/ssl/cert.pem")


def ssl_context_with_fallback(fallback_ca_file: Path) -> ssl.SSLContext:
    context = ssl.create_default_context()
    if not context.get_ca_certs() and fallback_ca_file.exists():
        context.load_verify_locations(cafile=str(fallback_ca_file))
    return context


SSL_CONTEXT = ssl_context_with_fallback(MACOS_SYSTEM_CA_FILE)


def get_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS, context=SSL_CONTEXT) as response:
        return json.load(response)


def post_json(url: str, payload: dict, headers: dict[str, str]):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, **headers},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS, context=SSL_CONTEXT) as response:
        return json.load(response)


def log_to_stderr(message: str) -> None:
    print(message, file=sys.stderr)


class RequestPacer:
    """Keeps at least `minimum_seconds_between_requests` between the starts of two requests."""

    def __init__(self, minimum_seconds_between_requests: float, sleep, clock):
        self.minimum_seconds_between_requests = minimum_seconds_between_requests
        self.sleep = sleep
        self.clock = clock
        self.last_request_time = None

    def wait(self) -> None:
        if self.last_request_time is not None:
            elapsed_seconds = self.clock() - self.last_request_time
            if elapsed_seconds < self.minimum_seconds_between_requests:
                self.sleep(self.minimum_seconds_between_requests - elapsed_seconds)
        self.last_request_time = self.clock()
