# dota-winrates — design

## Purpose

A command-line tool anyone can run on their own Dota 2 account. For the player's recent
**solo ranked** matches it answers one question: *how often did my team have a higher average
lifetime winrate than the enemy team, and how did those matches end?*

## Method

These rules are the point of the tool and must not drift between versions.

1. **Match list.** The newest `N` ranked matches of the account (OpenDota
   `/players/{id}/matches?lobby_type=7&limit=N`), newest first.
2. **Solo only.** A match is solo when the player's `party_size` is 1.
   - `party_size` comes from the OpenDota match list (present for ~98% of matches).
   - When it is missing, Stratz match data is used: the player's `partyId` is `null` → solo;
     otherwise the party size is the number of players on the player's team sharing that `partyId`.
   - When neither source knows, the match is excluded as "party unknown".
3. **Player winrate.**
   - Public player: ranked wins / (wins + losses), whole career
     (OpenDota `/players/{id}/wl?lobby_type=7`).
   - Hidden match history (OpenDota returns 0 wins and 0 losses): Stratz
     `winCount / matchCount`, all game modes. On public players this averages +0.2 pp above
     the ranked winrate (mean absolute gap 0.8 pp, measured on 300 players).
   - Anonymous player (no account id, or 4294967295): no winrate; left out.
   - Hidden player Stratz does not know (matchCount 0 or missing): no winrate; left out.
4. **Team average.** Mean of the known winrates on a team. The player is included in their
   own team.
5. **Difference.** `my team average − enemy average`, in percentage points (pp).
   - A match where either team has no known winrate is excluded as "no comparison".
   - A match with `|difference| < --min-difference` (default 1.0 pp) is excluded as
     "too close". Exactly 1.0 pp is kept.
   - `difference > 0` counts as "we higher"; otherwise "they higher".
6. **Series.** The fetched list is cut into consecutive blocks of 100 matches (Series 1 is
   the newest). Filters are applied inside each series, so series stay comparable to the
   unfiltered match history.

## Command line

```
python -m dota_winrates --account-id 103093152 [--matches 100] [--min-difference 1.0]
                        [--output-directory output] [--cache-directory cache]
                        [--refresh-hours 24] [--excel]
```

The Stratz token is read from the `STRATZ_TOKEN` environment variable, otherwise from a
`.stratz_token` file in the current directory. It is required: without it the tool stops
before any request and explains how to get one (https://stratz.com/api).

## Architecture

| Module | Responsibility | Depends on |
|---|---|---|
| `opendota.py` | `OpenDotaClient`: match list, match details, ranked W/L, hero names; disk cache, request pacing, retries, macOS SSL fallback | stdlib |
| `stratz.py` | `StratzClient`: totals for up to 5 accounts per call, party size of a match; token handling, pacing, retries | stdlib |
| `analysis.py` | pure functions: player winrate with source, solo detection, team averages, filters, per-series summary, exclusion funnel | nothing (plain data in, plain data out) |
| `reports.py` | terminal summary, CSV files, optional Excel workbook | stdlib; `openpyxl` only for Excel |
| `cli.py` | arguments, token lookup, orchestration, partial results on failure | the four above |

Clients take injectable `fetch_json`, `sleep` and `clock` so tests run without the network.
The package replaces the single-file `dota_winrates.py`; its `OpenDotaClient` and tests move
into `opendota.py` largely unchanged. A match whose data cannot be loaded (for example a 404)
is skipped and counted as "not loaded".

## Data flow

1. Read the token (fail fast). 2. Fetch the match list. 3. Resolve party size (list, then
Stratz). 4. For solo matches, fetch details and the W/L of every non-anonymous player.
5. Fetch Stratz totals for players with hidden history, 5 per call. 6. Compute averages and
apply the filters. 7. Write the reports.

## Outputs

- **Terminal:** the exclusion funnel (fetched → not loaded → party → party unknown →
  no comparison → too close → kept), then per series and in total: matches kept, "we higher" (wins, win %),
  "they higher" (wins, win %).
- **`matches.csv`:** every fetched match: id, date, series, hero, result, party size and its
  source, both averages and known-player counts, difference, included yes/no, exclusion reason.
- **`players.csv`:** every player of every solo match: match id, team, account id, name, hero,
  wins, losses, winrate, source (`opendota_ranked`, `stratz_total`, `anonymous`, `unknown`).
- **Excel (`--excel`):** Summary sheet built with formulas (per series, total, an overall
  block with shares), Matches sheet with the kept matches coloured green (we higher) or red
  (they higher) and player names on hover, Players sheet. Stratz-based winrates are shown in
  purple italics.

## Caching and API limits

- Match details are cached for good; ranked W/L and Stratz totals for `--refresh-hours`
  (default 24).
- OpenDota free tier: about 48 requests per minute (60 is the limit; 60 still triggers 429s),
  a full minute of waiting after a 429, exponential backoff on network errors and 5xx.
  ~800 matches need ~3000 requests, the free daily allowance.
- Stratz: at most 5 ids per `players` call; pacing below 150/minute and 1500/hour; a minute
  of waiting after a 429.

## Error handling

- Missing token → exit before any request, with instructions. Rejected token (401/403) →
  exit with "invalid or expired token".
- Certificate errors and 404s are not retried.
- Three failed matches in a row → stop (most likely the daily limit), write partial reports,
  tell the user that rerunning continues from the cache.
- Ctrl+C → write partial reports.

## Testing

- Unit tests written before the code, with fake fetchers mirroring real API responses:
  solo detection including the Stratz fallback and the unknown case, the threshold boundary
  (1.0 kept, 0.99 excluded), anonymous and unknown players, batches of 5, missing and rejected
  token, CSV rows, Excel (skipped when `openpyxl` is absent).
- The 44 existing tests move with the code.
- A real run on account 103093152; the funnel and totals are checked for plausibility against
  the October 2026 report (W/L is refetched, so numbers move slightly).

## Publishing and privacy

- `.gitignore`: `.stratz_token`, `cache/`, `output/`, `*.csv`, `*.xlsx`, `raport*.txt`, `~$*`,
  `__pycache__/`. Generated data contains other players' names and account ids and is never
  committed.
- The token is never printed or logged.
- Test fixtures keep the real API response structure, but other players' names and account
  ids are replaced with made-up values before they are committed.
- Public repository `Misezicedark/dota-winrates`, MIT licence, commits authored with the
  account's GitHub noreply address. The repository is created only after the owner has seen
  the exact list of files to be published.

## Out of scope

PyPI packaging, a web interface, rank-based analysis, de-anonymising anonymous players.
