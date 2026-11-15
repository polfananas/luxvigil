# luxvigil 🕯️

[![tests](https://github.com/polfananas/luxvigil/actions/workflows/tests.yml/badge.svg)](https://github.com/polfananas/luxvigil/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/luxvigil)](https://pypi.org/project/luxvigil/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/polfananas/luxvigil/blob/main/LICENSE)

**Abuse forensics for AI APIs.** Your free tier is part of your attack surface.

> *Nihil occultum quod non manifestetur.* Nothing stays hidden forever (Luke 8:17).

luxvigil reads the request logs of your AI API (or any app that gives away tokens or credits) and shows you the abuse hiding in them: account farms, throwaway emails, shared or leaked keys, bots sending the same prompts over and over… and roughly how much it cost you.

![luxvigil demo](https://raw.githubusercontent.com/polfananas/luxvigil/main/docs/demo.gif)

> Early days (v0.2). Everything below works, and I'd love to hear what it finds in your logs, or what it gets wrong: [open an issue](https://github.com/polfananas/luxvigil/issues).

## Try it

```
pipx run luxvigil demo        # no install: makes fake logs with abuse in them and scans them
pip install luxvigil          # then: luxvigil scan your-logs.csv
```

Python 3.11+, no dependencies, nothing leaves your machine. Using LiteLLM? `luxvigil scan spend_logs.json --format litellm` reads its spend logs as they come.

## Tested on real logs

There's no public dataset of AI API abuse with labels, so we tested it on the closest things there are:

- **~5M real messages to a free GPT chatbot** ([WildChat](https://huggingface.co/datasets/allenai/WildChat-1M)). It found people using the free chat as an API for their business: a print-on-demand shop generating product titles, autonomous agents stuck in loops, scripts generating training data. It also showed us a mistake: the bot check was flagging normal people who type "more" or "continue" a lot. We fixed it and checked the fix on 3.4M newer chats we never tuned on.
- **Amazon's fraud benchmark** (~150k signups labeled fraud or not). During a wave of fake accounts, 9 out of 10 accounts luxvigil flags are real fraud. Measured exactly the way Amazon's paper does, a simple rule with no training ties their best ML model.

What works, what doesn't and how to run it yourself: [benchmarks/](https://github.com/polfananas/luxvigil/tree/main/benchmarks).

## Why

If you run an AI product with a free tier, you're probably feeding someone who signed up 300 times with fake emails. Each account looks normal on its own, so per-user rate limits don't catch it. All you see is the inference bill going up and conversion going down.

And it's not just a billing problem. Free credits and leaked keys are how a lot of people get anonymous, untraceable access to LLMs: to resell it, to run spam or scam campaigns, or to hammer models with jailbreak attempts without risking their own account. On the attacker side, "free inference" is a resource like any other, and your free tier is part of the attack surface.

luxvigil looks **across** accounts to find the patterns that only show up when you put them side by side.

## What it does

You give it a CSV export of your request logs, it gives you a report. Want to see it before digging out your logs? `luxvigil demo` makes a month of fake logs with two farms, a leaked key and a spend spike planted in them, and scans them:

```
$ luxvigil demo

🕯️ luxvigil · 2026-10-01 to 2026-10-30

  11,405 requests · 285 accounts · $324.60 spent
  💰 ~$195.25 is likely abuse (60% of your spend), 86 accounts
  🔎 ~$22.76 more is worth a look, 1 account (signals that can have an innocent explanation)

  🚨 2 account farms, 85 accounts
     - 60 accounts on 3 shared devices + 1 shared network (185.220.101.0/24, with throwaway emails or template prompts)  ·  3,368 requests  ·  ~$144.89
     - 25 accounts on 1 shared device  ·  968 requests  ·  ~$32.39
  📧 60 of 285 accounts (21%) use throwaway emails: mailinator.com (16), sharklasers.com (12), 10minutemail.com (12)
     3,368 requests  ·  ~$144.89
  🔑 1 key probably shared or leaked
     - key key_eb500b7a7780b4c2: 13 countries in a single day, 401 IPs, home is GB  ·  400 requests from abroad  ·  ~$17.97
  🤖 1 copy-paste bot, 60 accounts
     - 60 accounts sending the same 5 prompts over and over (3,368 times)  ·  3,368 requests  ·  ~$144.89
  ✅ no shared Gmail inboxes
  💸 2 spend spikes (could be a launch or a batch job, check first)
     - acc_0100 went from ~$0.02/day to $7.99/day (368x) on 2026-10-28 to 2026-10-30  ·  302 requests  ·  ~$22.76 extra
     - acc_0007 went from ~$0.02/day to $6.26/day (266x) on 2026-10-23 to 2026-10-25  ·  402 requests  ·  ~$17.93 extra

  The same account can show up in more than one check. The totals at the top count it once.
```

The checks:

- **Account farms**: lots of accounts on the same device, or on the same network when they also use throwaway emails or template prompts (a shared network alone proves nothing: think mobile carriers and universities)
- **Disposable emails**: signups from mailinator & friends
- **Shared / leaked keys**: one key used from many countries in a short window (often a sign it's been stolen and resold)
- **Copy-paste bots**: different accounts sending the same prompts or templates
- **Gmail aliases**: three or more accounts on the same Gmail inbox (`you+1@gmail.com`, `y.o.u@gmail.com`…), the classic way to get lots of free trials with one inbox
- **Spend spikes**: a user suddenly burning 10x their usual

Every flag tells you *why* it fired. No black box.

The report keeps two totals apart. **Likely abuse** is what comes from strong signals: farms, copy-paste bots, shared Gmail inboxes and keys used from all over the world. **Worth a look** is for weak signals that often have an innocent explanation, like a throwaway email on its own (someone testing) or a spend spike (a launch, a batch job). An account that trips both just counts as likely abuse, and nothing is counted twice.

## What it won't do

- It doesn't block anything. It only looks at what already happened.
- It doesn't run in real time or sit in your request path.
- It never sends your logs anywhere. Everything runs on your machine, which matters since prompt logs are usually some of the most sensitive data you have.

## Log format

One row per API request, as a CSV. Only when, who and how many tokens are a
must, everything else is optional. Give it what you have: if a column is
missing, the checks that need it are skipped and the report says so
(`⏭️ disposable emails: skipped, no emails in the logs`) instead of telling you
everything's fine.

| column | what it is | needed for |
|---|---|---|
| `timestamp` | when it happened, ISO 8601 (`2026-10-01T14:03:22Z`) or a unix timestamp | **required** |
| `account_id` | your id for the account | **required** |
| `input_tokens` / `output_tokens` | tokens in and out | **required** |
| `cost_usd` | what the request cost you (or pass prices, see below) | spend, spikes |
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

It will never pick up a column called `api_key`, since in some exports that's the raw key. Hash it first and map the hash with `--map api_key_id=...`.

### Using LiteLLM?

Point luxvigil at your LiteLLM spend logs as they are, no CSV to prepare:

```
$ luxvigil scan spend_logs.json --format litellm
```

Get them from your proxy's `/spend/logs/v2` endpoint (save the JSON, all pages), or export the
`LiteLLM_SpendLogs` table from its database to CSV. Each account is the `end_user` you pass in your
requests (or the key owner when there isn't one). LiteLLM only stores a hash of each API key, so
reading it is safe.

LiteLLM doesn't log emails or countries, so those checks are skipped, and prompts are only there
if you turned on storing them in spend logs. When they are, luxvigil hashes them on your machine
and drops the text.

### JSON

Want to feed it into something else? `--json` prints the same report as data. Every finding has a stable id (same logs, same ids), how strong it is, the evidence behind it, what could explain it innocently and what to check next:

```json
{
  "id": "shared_key-c09f5a151c",
  "check": "shared_key",
  "severity": "likely",
  "summary": "key key_eb500b7a7780b4c2: 13 countries in a single day, 401 IPs, home is GB",
  "accounts": ["acc_0007"],
  "requests": 400,
  "cost_usd": 17.9693,
  "evidence": {"key": "key_eb500b7a7780b4c2", "home_country": "GB", "peak_countries_in_a_day": 13, "...": "..."},
  "innocent_explanation": "a customer running the same key from servers in several regions",
  "next_step": "ask the key owner if they use it from these countries, if not rotate it"
}
```

No raw emails or prompts end up in it, only the ids and hashes already in your logs.

### Bad rows

luxvigil stops at the first row it can't trust (a cost of `nan` or `-5`, negative tokens, an empty account id…) and tells you which line, rather than quietly getting the totals wrong. If your export has a few broken rows and you just want the rest, add `--skip-bad-rows` and it'll tell you how many it skipped. It also warns you about duplicated rows and rows without a valid IP.

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
```

Trusted networks and devices are still checked, they just never link accounts into a farm. Ignored accounts are skipped completely, but still count towards your total spend.

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

About half of that is just reading the CSV. Everything is loaded into memory, so tens of millions of rows will need a big machine or splitting the file by month. Run the script on your own machine to get your numbers.

## What's next

- [x] six checks, terminal report, JSON, config
- [x] tested on real data (WildChat, Amazon's fraud benchmark)
- [x] reads LiteLLM spend logs
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

