# Changelog

## 0.2.0

- `--format litellm`: read LiteLLM spend logs as they come, from the API (JSON) or the database (CSV)

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
