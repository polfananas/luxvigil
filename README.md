# luxvigil 🕯️

**Abuse forensics for AI APIs.** Your free tier is part of your attack surface.

luxvigil reads the request logs of your AI API (or any app that gives away tokens or credits) and shows you the abuse hiding in them: account farms, throwaway emails, shared or leaked keys, bots sending the same prompts over and over… and roughly how much it cost you.

> ⚠️ Super early. Nothing works yet, I'm building it in the open step by step. Star or watch if you want to follow along.

## Why

If you run an AI product with a free tier, you're probably feeding someone who signed up 300 times with fake emails. Each account looks normal on its own, so per-user rate limits don't catch it. All you see is the inference bill going up and conversion going down.

And it's not just a billing problem. Free credits and leaked keys are how a lot of people get anonymous, untraceable access to LLMs: to resell it, to run spam or scam campaigns, or to hammer models with jailbreak attempts without risking their own account. On the attacker side, "free inference" is a resource like any other, and your free tier is part of the attack surface.

luxvigil looks **across** accounts to find the patterns that only show up when you put them side by side.

## What it'll do (v0.1)

You give it a CSV export of your request logs, it gives you a report:

```
$ luxvigil scan logs-october.csv

🕯️ luxvigil · October 2026

  🚨 3 account farms        214 accounts, same device / IP range
  🔑 2 keys in 9+ countries  probably shared or leaked
  📧 18% of signups          disposable email domains
  💸 ~$4,300                 estimated cost of abuse this month
```

Checks planned for v0.1:

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
| `timestamp` | when it happened, ISO 8601 (`2026-10-01T14:03:22Z`) |
| `account_id` | your id for the account |
| `email` | email the account signed up with |
| `api_key_id` | id or hash of the key used, **never the key itself** |
| `ip` / `country` | client IP and its 2-letter country |
| `device_id` | device / browser fingerprint (optional, leave empty if you don't have it) |
| `prompt_hash` | hash of the prompt, so repeated prompts show up without storing them |
| `input_tokens` / `output_tokens` | tokens in and out |
| `cost_usd` | what the request cost you |

No logs handy? Make some fake ones with abuse planted on purpose:

```
$ luxvigil generate --out sample-logs.csv
```

## Status

- [x] Name & scope
- [x] Log format + fake log generator (for demos and tests)
- [ ] The 5 checks
- [ ] Terminal report
- [ ] HTML report (v0.2)
- [ ] Read LiteLLM / Helicone / gateway exports directly (v0.3)

## Why "luxvigil"?

*Lux* = light, *vigil* = keeping watch through the night. It keeps an eye on what happened while you weren't looking.

---

Built by [@polfananas](https://github.com/polfananas)
