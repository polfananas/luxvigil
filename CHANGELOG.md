# Changelog

## 0.3.0

Fewer false alarms before telling anyone about it: missing costs, chains of shared devices and devices shared over months no longer look like abuse, plus fixes for bot costs, row order and duplicate rows.

- spend spikes: a day with requests that have no cost no longer counts as a $0 day. It never sets
  the usual spend, so three days with missing costs and then a normal day isn't a "spike" anymore.
  A day can still spike on its known costs alone, and the report says "at least" when that happens
- JSON: spend spikes are always `"severity": "review"`, matching the totals (before, a spike on an
  account with likely abuse said "likely" while its money was counted as worth a look).
  New `evidence.some_costs_missing`
- account farms: a farm needs one device or network with `--min-farm` accounts or more on it.
  Accounts only linked in a chain of smaller shares (a family computer, a support agent logging in on
  customers' laptops) are reported as a chain, worth a look, not as likely abuse. Accounts that hang
  off a farm only through smaller shares are listed with it (`evidence.linked_accounts`) but not
  counted in it. New `evidence.largest_share`
- a file it can't parse (binary, cut-off JSON, a single field over 128 KB) gets a one-line error
  instead of a Python traceback. An empty log says "nothing to scan" instead of "no costs"
- errors and notes escape control characters from your logs (an ESC or BEL in a bad cell could
  retitle or recolor the terminal) and cut long values short
- copy-paste bots: a request only counts as the signal if that account itself repeated that prompt.
  Bots that share several templates are one group, and an account sending another member's template
  once (say a $100 request) was counted as signal: $130 reported where the repeated prompts were $30
- disposable emails and network farms look at every email an account used, not the first one in the
  file, so the same logs in a different order give the same answer
- exact duplicate rows (every column the same, request ids included) are counted once, so a doubled
  export can't create a bot or double the cost. `--keep-duplicates` keeps them. LiteLLM's request_id
  tells apart two requests that look identical
- a CSV row with more values than the header is a bad row (it crashed before)
- account farms look at time: a device or network is a burst when 5 accounts (`--min-farm`) were
  first seen on it within 30 days (`[farms] window_days` in luxvigil.toml, 0 = no limit). A farm is
  likely abuse only when it's direct, a burst, and at least half its accounts also have a throwaway
  email or send a bot's prompts. Its cost is then the requests on the shared devices and networks;
  everything else the accounts did is worth a look. A burst with nothing else off (a class, a kiosk),
  a device shared over months, or a chain are reported as "accounts sharing devices or networks",
  worth a look, with all of what they spent. On Amazon's fraud data the 30-day window takes wrongly
  flagged legit accounts from 766 to 181 at the same recall, see benchmarks/
- JSON format 3: account farms have `relation` (direct/chained), `concentrated`, `window_days`,
  `arrived_from`/`arrived_to` (first seen on the shared devices, in these logs), `earlier_use_unknown`,
  `corroborated_accounts`, `all_requests`/`all_cost_usd`, and `cost_basis` depends on the severity.
  `settings.farm_window_days`
- CI: actions pinned to commits, one `all-tests` check, and MAINTAINING.md with the release and recovery steps

## 0.2.0

- `--format litellm`: read LiteLLM spend logs as they come, from the API (JSON) or the database (CSV).
  Accounts are the end user, else `user:<key owner>`, else `key:<key>`, and the notes say how many of each.
  An `api_key` that isn't a LiteLLM hash is hashed before use, so a raw key never shows up

Fixes (found in a review before this release, each one has a test now):

- a request with no country counted as "from abroad" for a leaked key, adding to likely abuse even
  though the key finding didn't count it
- money worth a look wasn't shown when every account it came from also had likely abuse
- timestamps with an offset (`+02:00`) weren't converted to UTC, so the same instant could land
  on two different days (spikes, report period)
- a missing cost was read as $0. Now it's unknown: an empty `cost_usd` (or a LiteLLM row with no
  `spend`) is left out of the dollar totals and the report says how many requests that is. `0` is still free

Changes in what gets counted:

- copy-paste bots: only the requests with the repeated prompts count as likely abuse. Everything
  else those accounts sent is now worth a look. On WildChat-1M likely abuse goes from 5% to 1.1%
- Gmail aliases are worth a look, not likely abuse: one inbox shows the accounts are related, not abused
- the report says the $ is what the flagged requests cost, not a confirmed loss

New:

- no cost column and no prices is fine now: the checks that don't need dollars run, and the spike check is skipped
- an empty cost cell is worked out from `--price-in`/`--price-out` when you pass them
- something in `api_key_id` that looks like a raw key (`sk-...`, `AKIA...`) is hashed before use and never printed
- the report says when a check only had data for part of the logs, and skips the farm check when there's
  nothing that could link accounts (IPs only, and no account that already looks off)
- control characters in ids from your logs are replaced before printing

JSON (`"format": 2`, was 1):

- dollar amounts are `null` when the logs have no costs at all
- every finding has a `cost_basis` saying what its `cost_usd` covers
- copy-paste bots: `cost_usd` and `requests` are only the repeated prompts, the rest is in
  `evidence.other_requests` / `evidence.other_cost_usd`
- Gmail aliases have severity `review`
- `totals.requests_with_cost`, `settings` (thresholds) and `coverage` (requests each check had data for)
- `worth_a_look_accounts` counts every account with something worth a look, including ones that also
  have likely abuse

## 0.1.0

First release.

Checks:

- **account farms**: accounts linked by a shared device, or by a shared network when they also have another red flag
- **disposable emails**: a bundled list of ~10k throwaway domains, works offline
- **shared or leaked keys**: one key used from many countries in a short window
- **copy-paste bots**: accounts sending the same prompts over and over. Phrases everyone types ("more", "continue") are left out, see benchmarks/
- **Gmail aliases**: several accounts on the same Gmail inbox
- **spend spikes**: an account suddenly spending 10x its usual, grouped per account

Report:

- two totals kept apart: likely abuse and worth a look, every request counted once
- `--json` with stable ids, evidence, an innocent explanation and a next step for every finding
- says which checks it had to skip and why when your logs don't have the data for them

Reading your logs:

- only time, account and tokens are required, everything else is optional
- column aliases and `--map` for logs that name things differently, cost from tokens with `--price-in`/`--price-out`
- Excel exports (BOM, `;` separators), bad rows stop the scan or get skipped with `--skip-bad-rows`
- `luxvigil.toml` for trusted networks and devices and ignored accounts

Tested on real data: Amazon's fraud ecommerce benchmark and ~5M real messages from WildChat, see benchmarks/.
