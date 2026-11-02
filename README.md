# luxvigil 🕯️

**Abuse forensics for AI APIs.** Your free tier is part of your attack surface.

> *Nihil occultum quod non manifestetur.* Nothing stays hidden forever (Luke 8:17).

luxvigil reads the request logs of your AI API (or any app that gives away tokens or credits) and shows you the abuse hiding in them: account farms, throwaway emails, shared or leaked keys, bots sending the same prompts over and over… and roughly how much it cost you.

> ⚠️ Early days. All 5 checks and the terminal report work. I'm building it in the open step by step, so star or watch if you want to follow along.
>
> There's no license yet, so for now it's look but don't use. That'll change before v0.1.

## Why

If you run an AI product with a free tier, you're probably feeding someone who signed up 300 times with fake emails. Each account looks normal on its own, so per-user rate limits don't catch it. All you see is the inference bill going up and conversion going down.

And it's not just a billing problem. Free credits and leaked keys are how a lot of people get anonymous, untraceable access to LLMs: to resell it, to run spam or scam campaigns, or to hammer models with jailbreak attempts without risking their own account. On the attacker side, "free inference" is a resource like any other, and your free tier is part of the attack surface.

luxvigil looks **across** accounts to find the patterns that only show up when you put them side by side.

## What it does

You give it a CSV export of your request logs, it gives you a report. Here it is on the fake sample logs (`luxvigil generate`), which have two farms, a leaked key and a spend spike planted in them:

```
$ luxvigil scan sample-logs.csv

🕯️ luxvigil · 2026-10-01 to 2026-10-30

  11,405 requests · 285 accounts · $324.60 spent
  💰 ~$218.01 looks like abuse (67% of your spend), 87 accounts involved

  🚨 2 account farms, 85 accounts
     - 60 accounts on 3 shared devices + 1 shared network (185.220.101.0/24)  ·  3,368 requests  ·  ~$144.89
     - 25 accounts on 1 shared device  ·  968 requests  ·  ~$32.39
  📧 60 of 285 accounts (21%) use throwaway emails: mailinator.com (16), sharklasers.com (12), 10minutemail.com (12)
     3,368 requests  ·  ~$144.89
  🔑 1 key probably shared or leaked
     - key key_eb500b7a7780b4c2: 13 countries in a single day, 401 IPs, home is GB  ·  400 requests from abroad  ·  ~$17.97
  🤖 1 copy-paste bot, 60 accounts
     - 60 accounts sending the same 5 prompts over and over (3,368 times)  ·  3,368 requests  ·  ~$144.89
  💸 2 spend spikes
     - acc_0100 went from ~$0.02/day to $7.99/day (368x) on 2026-10-28 to 2026-10-30  ·  302 requests  ·  ~$22.76 extra
     - acc_0007 went from ~$0.02/day to $6.26/day (266x) on 2026-10-23 to 2026-10-25  ·  402 requests  ·  ~$17.93 extra

  The same account can show up in more than one check. The total at the top counts it once.
```

The checks:

- **Account farms**: lots of accounts coming from the same IP, network or device
- **Disposable emails**: signups from mailinator & friends
- **Shared / leaked keys**: one key used from many countries in a short window (often a sign it's been stolen and resold)
- **Copy-paste bots**: different accounts sending the same prompts or templates
- **Spend spikes**: a user suddenly burning 10x their usual

Every flag tells you *why* it fired. No black box.

## What it won't do

- It doesn't block anything. It only looks at what already happened.
- It doesn't run in real time or sit in your request path.
- It never sends your logs anywhere. Everything runs on your machine, which matters since prompt logs are usually some of the most sensitive data you have.

## Log format

One row per API request, as a CSV with these columns:

| column | what it is |
|---|---|
| `timestamp` | when it happened, ISO 8601 (`2026-10-01T14:03:22Z`) or a unix timestamp |
| `account_id` | your id for the account |
| `email` | email the account signed up with |
| `api_key_id` | id or hash of the key used, **never the key itself** |
| `ip` / `country` | client IP and its 2-letter country |
| `device_id` | device / browser fingerprint (optional, leave empty if you don't have it) |
| `prompt_hash` | hash of the prompt, so repeated prompts show up without storing them |
| `input_tokens` / `output_tokens` | tokens in and out |
| `cost_usd` | what the request cost you (optional, see below) |

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

No logs handy? Make some fake ones with abuse planted on purpose:

```
$ luxvigil generate --out sample-logs.csv
```

## Status

- [x] Name & scope
- [x] Log format + fake log generator (for demos and tests)
- [x] Account farms
- [x] Disposable emails
- [x] Shared / leaked keys
- [x] Copy-paste bots
- [x] Spend spikes
- [x] Terminal report (with a total that counts each account once)
- [ ] HTML report (v0.2)
- [ ] Read LiteLLM / Helicone / gateway exports directly (v0.3)

## Why "luxvigil"?

*Lux* = light, *vigil* = keeping watch through the night. It keeps an eye on what happened while you weren't looking.

---

Built by [@polfananas](https://github.com/polfananas)
