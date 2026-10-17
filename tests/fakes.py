"""Offline stand-ins for the network, the clock and sleep."""

import urllib.error


class FakeClock:
    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeFetcher:
    """Returns queued responses per URL; an exception in the queue is raised instead."""

    def __init__(self, responses_by_url, clock=None):
        self.responses_by_url = {url: list(responses) for url, responses in responses_by_url.items()}
        self.clock = clock
        self.requested_urls = []
        self.request_times = []

    def __call__(self, url):
        self.requested_urls.append(url)
        if self.clock is not None:
            self.request_times.append(self.clock.time())
        queue = self.responses_by_url[url]
        response = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(response, Exception):
            raise response
        return response


class FakePoster:
    """Stands in for http.post_json: returns queued responses in order, raising queued exceptions."""

    def __init__(self, responses, clock=None):
        self.responses = list(responses)
        self.clock = clock
        self.calls = []
        self.call_times = []

    def __call__(self, url, payload, headers):
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        if self.clock is not None:
            self.call_times.append(self.clock.time())
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


def http_error(url, status_code):
    return urllib.error.HTTPError(url, status_code, "error", hdrs=None, fp=None)
