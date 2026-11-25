# luxvigil 🕯️

[![tests](https://github.com/polfananas/luxvigil/actions/workflows/tests.yml/badge.svg)](https://github.com/polfananas/luxvigil/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/luxvigil)](https://pypi.org/project/luxvigil/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/polfananas/luxvigil/blob/main/LICENSE)

**Abuse forensics for AI APIs.** Your free tier is part of your attack surface.

> *Nihil occultum quod non manifestetur.* Nothing stays hidden forever (Luke 8:17).

luxvigil reads the request logs of your AI API (or any app that gives away tokens or credits) and shows you the abuse hiding in them: account farms, throwaway emails, shared or leaked keys, bots sending the same prompts over and over… and what the requests behind them cost.

![luxvigil demo](https://raw.githubusercontent.com/polfananas/luxvigil/main/docs/demo.gif)

> Early days (v0.4). Everything below works, and I'd love to hear what it finds in your logs, or what it gets wrong: [open an issue](https://github.com/polfananas/luxvigil/issues).

## Try it

```
pipx run luxvigil demo        # no install: makes fake logs with abuse in them and scans them
pip install luxvigil          # then: luxvigil scan your-logs.csv
```

Python 3.11+, no dependencies, nothing leaves your machine. Using LiteLLM? `luxvigil scan spend_logs.json --format litellm` reads its spend logs as they come.

## Tested on real logs

There's no public dataset of AI API abuse with labels, so we tested it on the closest things there are:

- **~5M real messages to a free GPT chatbot** ([WildChat](https://huggingface.co/datasets/allenai/WildChat-1M)). It found people using the free chat as an API for their business: a print-on-demand shop generating product titles, autonomous agents stuck in loops, scripts generating training data. It also showed us a mistake: the bot check was flagging normal people who type "more" or "continue" a lot. We fixed it, and the first fix held up on 3.4M newer chats. The second fix was worked out on those same chats, so it hasn't had an independent test yet. No labels here, so no precision or recall: we read what got flagged.
- **Amazon's fraud benchmark** (~150k signups labeled fraud or not). During a wave of fake accounts in January 2015, 9 out of 10 accounts luxvigil flags are real fraud. Outside that wave, a shared device is no better than chance. On Amazon's test rows, scoring each account only with what came before it, one simple signal is about level with their best model (AUC 0.642 vs 0.636). It's e-commerce, not AI logs.

What works, what doesn't and how to run it yourself: [benchmarks/](https://github.com/polfananas/luxvigil/tree/main/benchmarks).

## Why

If you run an AI product with a free tier, you're probably feeding someone who signed up 300 times with fake emails. Each account looks normal on its own, so per-user rate limits don't catch it. All you see is the inference bill going up and conversion going down.

And it's not just a billing problem. Free credits and leaked keys are how a lot of people get anonymous, untraceable access to LLMs: to resell it, to run spam or scam campaigns, or to hammer models with jailbreak attempts without risking their own account. On the attacker side, "free inference" is a resource like any other, and your free tier is part of the attack surface.

luxvigil looks **across** accounts to find the patterns that only show up when you put them side by side.

## What it does

You give it a CSV export of your request logs, it gives you a report. Want to see it before digging out your logs? `luxvigil demo` makes a month of fake logs with two farms, a leaked key and a spend spike planted in them, and scans them:

```
$ luxvigil demo

🕯️ luxvigil · 2026-10-01 to 2026-10-30 (UTC)

  11,405 requests · 285 accounts · $324.60 spent
  💰 ~$162.86 is likely abuse (50% of your spend), 61 accounts
  🔎 ~$55.15 more is worth a look, 26 accounts (signals that can have an innocent explanation)

  🚨 1 account farm, 60 accounts
     - 60 accounts first seen on 3 shared devices + 1 shared network (185.220.101.0/24) within 21 days, all with throwaway emails or bot prompts  ·  3,368 requests on them  ·  ~$144.89
  🔗 1 group of accounts sharing devices or networks, 25 accounts (worth a look)
     - 25 accounts first seen on 1 shared device within 20 days, nothing else off about them (a class or a kiosk looks the same)  ·  968 requests  ·  ~$32.39
  📧 60 of 285 accounts (21%) use throwaway emails: mailinator.com (16), sharklasers.com (12), 10minutemail.com (12)
     3,368 requests  ·  ~$144.89
  🔑 1 key used from many countries at once (shared or leaked?)
     - key key_eb500b7a7780b4c2: 13 countries in a single day, 401 IPs, home is GB  ·  400 requests from abroad  ·  ~$17.97
  🤖 1 copy-paste bot, 60 accounts
     - 60 accounts sending the same 5 prompts over and over (3,368 times)  ·  3,368 repeated requests  ·  ~$144.89
  ✅ no shared Gmail inboxes
  💸 2 spend spikes (could be a launch or a batch job, check first)
     - acc_0100 went from ~$0.02/day to $7.99/day (368x) on 2026-10-28 to 2026-10-30  ·  302 requests  ·  ~$22.76 extra
     - acc_0007 went from ~$0.02/day to $6.26/day (266x) on 2026-10-23 to 2026-10-25  ·  402 requests  ·  ~$17.93 extra

  "First seen" is first seen in these logs, which start on 2026-10-01: accounts may have been there before.
  The same account can show up in more than one check. The totals at the top count it once.
  The $ is what the flagged requests cost, not a confirmed loss: check before you act.
```

The checks:

- **Account farms**: 5+ accounts first seen on the same device within a few weeks (or on the same network, when they also use throwaway emails or template prompts: a shared network alone proves nothing, think mobile carriers and universities). It's only likely abuse when at least half of them also have a throwaway email or send a bot's prompts, since a class on the same lab computers looks the same. A device shared over months (a library computer, a second-hand phone) or a chain of small shares (a family computer here, a support agent's login there) shows up as worth a look
- **Disposable emails**: signups from mailinator & friends
- **Shared / leaked keys**: one key used from many countries in a short window (often a sign it's been stolen and resold)
- **Copy-paste bots**: different accounts sending the same prompts or templates
- **Gmail aliases**: three or more accounts on the same Gmail inbox (`you+1@gmail.com`, `y.o.u@gmail.com`…), the classic way to get lots of free trials with one inbox. It shows the accounts are related, so it's "worth a look", not likely abuse
- **Spend spikes**: a user suddenly burning 10x their usual

Every flag tells you *why* it fired. No black box.

The report keeps two totals apart. **Likely abuse** is what comes from strong signals: what a farm's accounts did on the devices and networks they share, the requests a copy-paste bot sent with its repeated prompts, and the requests from abroad on a key used from all over the world. **Worth a look** is for signals that often have an innocent explanation: a throwaway email (someone testing), accounts on one Gmail inbox (someone with a test account), whatever else a bot's or a farm's accounts did, a bot account that spent 10x+ what the rest did on the repeated prompts (more likely a customer whose prompt got copied), accounts sharing a device that don't add up to a farm, and spend spikes (a launch, a batch job). Each request is counted once, in the stronger of the two, so one account can have some of each.

Neither total is money you've lost. They're what the requests behind the signals cost, and someone still has to look at them to say if it was abuse.

## Reading the report

The report doesn't ban anyone, you do. What to check before acting on each finding:

- **🚨 Account farms** and **🔗 accounts sharing devices**: look at when the accounts signed up and how they pay. "First seen" means first seen in your logs, not when the account was created, so if your export starts in the middle of things, check further back. The devices and networks listed are what links them, start there: one shared office laptop can pull two unrelated groups together.
- **🔑 Keys used from many countries**: ask the owner. A customer with servers in several regions looks exactly the same. If it's not them, rotate the key.
- **🤖 Copy-paste bots**: look at the prompt behind the hash. Someone using your free tier as an API, or a legit integration sending a fixed template?
- **📧 Throwaway emails and 📮 shared Gmail inboxes**: on their own, mostly people testing. They matter when the same accounts also show up in a stronger finding.
- **💸 Spend spikes**: a launch, a batch job or a new big customer look the same as abuse here. Check what the requests were.

`--json` has this for every finding: the evidence, the most likely innocent explanation and what to check next. When something turns out to be fine, add it to `luxvigil.toml` and it stops showing up.

## What it won't do

- It doesn't block anything. It only looks at what already happened.
- It doesn't run in real time or sit in your request path.
- It doesn't prove anything on its own. It shows you accounts that are related, behave alike or changed suddenly, with the evidence, so you can decide.
- It never sends your logs anywhere. Everything runs on your machine, which matters since prompt logs are usually some of the most sensitive data you have.
- It doesn't anonymise anything. It reads what's in your logs (with `--format litellm` that includes the prompts, if they're there, to hash them) and prints the ids it finds, so if your account ids are emails, so are the ones in the report. A prompt hash isn't anonymous either: a short or common prompt can be guessed from it.

## Log format

One row per API request, as a CSV. Only when, who and how many tokens are a
must, everything else is optional. Give it what you have: if a column is
missing, the checks that need it are skipped and the report says so
(`⏭️ disposable emails: skipped, no emails in the logs`) instead of telling you
everything's fine.

| column | what it is | needed for |
|---|---|---|
| `timestamp` | when it happened, ISO 8601 (`2026-10-01T14:03:22Z`) or a unix timestamp. Turned into UTC, no timezone means UTC | **required** |
| `account_id` | your id for the account | **required** |
| `input_tokens` / `output_tokens` | tokens in and out | **required** |
| `cost_usd` | what the request cost you (or pass prices, see below). Empty means unknown, `0` means free | dollar amounts, spikes |
| `email` | email the account signed up with | throwaway emails, Gmail aliases |
| `api_key_id` | id or hash of the key used, **never the key itself** | shared / leaked keys |
| `ip` / `country` | client IP and its 2-letter country | farms (IP), shared keys (country) |
| `device_id` | device / browser fingerprint | farms |
| `prompt_hash` | hash of the prompt, so repeated prompts show up without storing them | copy-paste bots |
| `prompt_chars` | length of the prompt in characters | lets the bot check ignore "more", "continue"... |

### Your columns are called something else?

luxvigil already understands the usual suspects (`user_id`, `user_email`, `created_at`, `prompt_tokens`, `completion_tokens`, `client_ip`…) and tells you when it uses one. For anything else, point it at your column with `--map`:

```
$ luxvigil scan logs.csv --map account_id=customer --map email=signup_email
```

No cost column? Give it your prices per 1M tokens and it works it out:

```
$ luxvigil scan logs.csv --price-in 3 --price-out 15
```

No cost and no prices still works: every check that doesn't need dollars runs, the report shows requests instead of money, and the spike check is skipped. Some rows with an empty cost? Those count as unknown, not free, and the report says how many there are.

It will never pick up a column called `api_key`, since in some exports that's the raw key. Hash it first and map the hash with `--map api_key_id=...`. If something in `api_key_id` still looks like a raw key (`sk-...`, `AKIA...` and the like), luxvigil uses a hash of it instead and never prints it. That's a guess based on how common keys look, not a guarantee, so don't rely on it.

### Using LiteLLM?

Point luxvigil at your LiteLLM spend logs as they are, no CSV to prepare:

```
$ luxvigil scan spend_logs.json --format litellm
```

Get them from your proxy's `/spend/logs/v2` endpoint (save the JSON, all pages), or export the
`LiteLLM_SpendLogs` table from its database to CSV. Each account is the `end_user` you pass in your
requests. When there isn't one it falls back to the key owner (`user:...`) and then the key itself
(`key:...`), and tells you how many requests used each, since a key owner or a key can stand for many
people. LiteLLM only stores a hash of each API key. Anything in that field that isn't a hash like
that gets hashed before it's used, so a raw key never shows up. A request with no `spend` is an
unknown cost, not a free one.

LiteLLM doesn't log emails or countries, so those checks are skipped, and prompts are only there
if you turned on storing them in spend logs. When they are, luxvigil hashes them on your machine
and drops the text.

luxvigil only sees what LiteLLM writes down. Some requests succeed without leaving a row in the
spend logs (LiteLLM has open issues about it, like
[image requests to Gemini](https://github.com/BerriAI/litellm/issues/49567)), and those stay
invisible here too. If your spend logs add up to less than your provider's bill, that gap is
worth a look on its own.

### Checking a LiteLLM setup

The logs tell you after the fact. Before that, check the files your proxy runs from:

```
$ luxvigil audit-config path/to/your/litellm
```

It reads `config.yaml`, `.env`, `docker-compose.yml` (and any `env_file` it points to) and
`requirements.txt`, and tells you what to fix now:

- the master key is `sk-1234`, the example from LiteLLM's docs. In February, 294 of 3,074
  LiteLLM gateways open to the internet accepted it
- `dangerously_permit_weak_or_unset_master_key` is on, so the proxy starts without a real key
- provider keys or the master key written straight into `config.yaml` instead of `os.environ/...`
- LiteLLM older than 1.84.0, which fixes the flaws published this year (one of them on CISA's
  list of vulnerabilities exploited in the wild)
- an Admin UI password from the docs' examples (`admin`, `langchain`...)
- an `.env` with keys in it, committed to git
- the example database password (`dbpassword9090`, from LiteLLM's own `docker-compose.yml`) with
  the database port open

And what's worth a look:

- an image that isn't pinned to a version
- no `UI_PASSWORD`, which makes the master key the Admin UI password too
- the proxy's port published on every network interface (`"4000:4000"` instead of
  `"127.0.0.1:4000:4000"`)
- the example database password, or the database port published, on its own
- a database but no `LITELLM_SALT_KEY`, so the keys stored in it are encrypted with the master key

It only reads files. Nothing is sent anywhere and nothing on your network is touched. Keys it finds
are shown cut short (`sk-p… (40 characters)`), never whole. Settings you make somewhere else (a
secret manager, Kubernetes, your shell) aren't in these files, so it can't see them. `--json` prints
the findings as data.

To keep it checking on every push, run it in CI with `--fail-on fix-now`: it exits with 1 when
there's something to fix now (or with `--fail-on worth-a-look`, anything at all), and with 0
otherwise. In GitHub Actions, with your LiteLLM files in `deploy/`:

```yaml
# .github/workflows/litellm-config.yml
name: litellm config
on: [push, pull_request]
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262  # v4.4.0
      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065  # v5.6.0
        with:
          python-version: "3.12"
      - run: pipx run --spec "luxvigil>=0.4.1" luxvigil audit-config deploy/ --fail-on fix-now
```

### JSON

Want to feed it into something else? `--json` prints the same report as data. Every finding has a stable id (same logs, same ids), how strong it is, what its cost covers, the evidence behind it, what could explain it innocently and what to check next:

```json
{
  "id": "shared_key-c09f5a151c",
  "check": "shared_key",
  "severity": "likely",
  "summary": "key key_eb500b7a7780b4c2: 13 countries in a single day, 401 IPs, home is GB",
  "accounts": ["acc_0007"],
  "requests": 400,
  "cost_usd": 17.9693,
  "cost_basis": "requests_from_outside_home_country",
  "evidence": {"key": "key_eb500b7a7780b4c2", "home_country": "GB", "peak_countries_in_a_day": 13, "...": "..."},
  "innocent_explanation": "a customer running the same key from servers in several regions",
  "next_step": "ask the key owner if they use it from these countries, if not rotate it"
}
```

No raw emails or prompts end up in it, only the ids and hashes already in your logs. It also has the settings used, how many requests each check had data for, and the period (in UTC), so two runs can be compared. Dollar amounts are `null` when the logs have no costs. An id is built from the accounts in the finding, so if a farm gains or loses an account, it gets a new id: it's not a campaign tracker. The format is versioned (`"format": 3` since 0.3.0), see the [changelog](https://github.com/polfananas/luxvigil/blob/main/CHANGELOG.md) for what changed.

### Bad rows

luxvigil stops at the first row it can't trust (a cost of `nan` or `-5`, negative tokens, an empty account id…) and tells you which line, rather than quietly getting the totals wrong. If your export has a few broken rows and you just want the rest, add `--skip-bad-rows` and it'll tell you how many it skipped. Duplicates only go when they're proven: a row that repeats a request id already seen (a `request_id` column, or LiteLLM's) is the same request exported twice, so it's counted once (`--keep-duplicates` keeps it). A row that's an exact copy of an earlier one but has no request id is kept, because a doubled export and a bot sending the same thing twice in the same second look exactly the same. You're told how many there are and how many requests and dollars they add, and if you know your export is doubled, `--drop-duplicates` leaves them out. It also warns you about rows without a valid IP.

### Telling it what's normal for you

Your office, your VPN or a big customer behind one network will look like a farm, since lots of accounts share an IP range. Drop a `luxvigil.toml` next to your logs (or pass `--config`):

```toml
[trusted]
networks = ["203.0.113.0/24"]  # office, VPN exit, a customer's office...
devices = ["kiosk-01"]

[ignore]
accounts = ["qa_*"]
emails = ["*@yourcompany.com"]
keys = ["key_ci_*"]

[farms]
window_days = 30  # how close together accounts have to show up on a device (0 = no limit)
```

Trusted networks and devices are still checked, they just never link accounts into a farm. Ignored accounts are skipped completely, but still count towards your total spend. `window_days` is 30 by default: a reasonable guess for how fast farms sign up, not a measured number (see [benchmarks/](https://github.com/polfananas/luxvigil/tree/main/benchmarks) for what changing it does on the one labeled dataset we have).

No logs handy? `luxvigil demo` scans fake ones for you, or make your own to play with:

```
$ luxvigil generate --out sample-logs.csv
```

## How fast is it?

On fake logs, on a small 2-core cloud box with Python 3.13 (`python scripts/bench.py 10000 100000 1000000`):

| requests | time | peak memory |
|---:|---:|---:|
| 10k | 0.1s | ~30 MB |
| 100k | 1.2s | ~90 MB |
| 1M | 18s | ~740 MB |

About half of that is just reading the CSV. On real logs (WildChat, 2M requests) it took 25s and ~1.2 GB, see [benchmarks/](https://github.com/polfananas/luxvigil/tree/main/benchmarks). Everything is loaded into memory, so tens of millions of rows need a big machine. Splitting a file doesn't give the same answer (a farm or a key's 24h window can end up cut in two), so if you have to, scan one whole period at a time. Run the script on your own machine to get your numbers.

## What's next

- [x] six checks, terminal report, JSON, config
- [x] tested on real data (WildChat, Amazon's fraud benchmark)
- [x] reads LiteLLM spend logs
- [x] checks a LiteLLM setup's files for the settings that get keys stolen
- [ ] LLMjacking: stolen cloud keys running models on your AWS bill (reading CloudTrail / Bedrock logs)
- [ ] spotting traffic that's too fast to be human
- [ ] more formats: Helicone, OpenAI usage exports
- [ ] a Markdown / HTML report to share with your team

Want one of these sooner, or something else? [Open an issue](https://github.com/polfananas/luxvigil/issues). Pull requests welcome too, see [CONTRIBUTING](https://github.com/polfananas/luxvigil/blob/main/CONTRIBUTING.md).

## Why "luxvigil"?

*Lux* = light, *vigil* = keeping watch through the night. It keeps an eye on what happened while you weren't looking.

## License

[Apache-2.0](https://github.com/polfananas/luxvigil/blob/main/LICENSE). Use it, change it, ship it, just keep the notice.

The list of throwaway email domains bundled with luxvigil comes from
[disposable-email-domains](https://github.com/disposable-email-domains/disposable-email-domains),
which is public domain (CC0).

---

Built by [@polfananas](https://github.com/polfananas)


