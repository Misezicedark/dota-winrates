# dota-winrates

How often did **your** team have the higher average winrate in your solo ranked Dota 2
matches — and how did those matches end?

Example (the author's account, last 100 ranked matches):

<!-- example:start -->
```
Matches
  fetched                      100
  excluded: not loaded           0
  excluded: party               38
  excluded: party unknown        0
  excluded: no comparison        0
  excluded: too close           44
  kept                          18

Kept: solo matches, both teams with a known winrate, difference of at least 1 pp
  Series   Period                     Kept   We higher (won)   They higher (won)
  1        2026-09-27 → 2026-10-17      18           3 (33%)            15 (20%)
  Total    2026-09-27 → 2026-10-17      18           3 (33%)            15 (20%)

The enemy team had the higher average in 15 of 18 kept matches (83%).
```
<!-- example:end -->

## How it decides

- **Matches:** your newest ranked matches (OpenDota).
- **Solo only:** your party size must be 1. When OpenDota does not know it, Stratz match data is
  used; when neither knows, the match is left out.
- **Player winrate:** ranked wins / (wins + losses) over the whole career (OpenDota). Players
  who hide their match history get their all-mode wins / matches from Stratz. Anonymous
  players (no account id in the match) are left out.
- **Team average:** the mean of the known winrates on a team; you count on your team.
- **Close matches are left out:** when the two averages differ by less than
  `--min-difference` percentage points (default 1.0).
- **Series:** results are grouped in blocks of 100 fetched matches, newest first.

## Setup

Python 3.10 or newer; nothing else is needed for the terminal and CSV output.

1. Get a free Stratz API token: sign in at <https://stratz.com/api> and copy the token.
2. Give it to the tool, either as an environment variable

   ```bash
   export STRATZ_TOKEN="your token"
   ```

   or in a file named `.stratz_token` in the folder you run the tool from.
   **Never commit this token.**
3. Optional, for the Excel workbook: `pip install openpyxl`.

## Usage

```bash
python -m dota_winrates --account-id 103093152
python -m dota_winrates --account-id 103093152 --matches 400 --excel
```

Your account id is the number in your OpenDota or Dotabuff profile link.

| Option | Default | Meaning |
|---|---|---|
| `--matches` | 100 | recent ranked matches to fetch |
| `--min-difference` | 1.0 | leave out matches whose team averages differ by less (pp) |
| `--output-directory` | `output` | where `matches.csv`, `players.csv` and `report.xlsx` go |
| `--cache-directory` | `cache` | cached API answers; reruns are fast |
| `--refresh-hours` | 24 | refetch player winrates older than this |
| `--excel` | off | also write the Excel workbook |

## Limits

- OpenDota's free tier allows about 3000 requests a day; 400 matches need ~1500, 800 need ~3000.
  When the limit is hit the tool stops, writes what it has, and a later run continues from the cache.
- Stratz allows 150 requests a minute; the tool stays below that on its own.

## Privacy

The output files contain other players' names and account ids. Keep them to yourself;
`output/` and `cache/` are git-ignored.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install openpyxl pycel
.venv/bin/python -m unittest discover -s tests
```

## Licence

MIT
