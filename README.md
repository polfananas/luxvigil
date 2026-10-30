# luxvigil 🕯️

**Find out who's been eating your free AI credits.**

luxvigil reads the logs of your AI API (or any app that gives away tokens/credits) and tells you who's been abusing it: account farms, throwaway emails, shared or leaked keys, bots sending the same prompts over and over… and roughly how much money it cost you.

> ⚠️ Super early. Nothing works yet, I'm building it in the open step by step. Star/watch if you want to follow along.

## Why

If you run an AI product with a free tier, you're probably feeding people who signed up 300 times with fake emails. Each account looks normal on its own, so per-user rate limits don't catch it. You just see the inference bill going up and conversion going down.

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
- **Shared / leaked keys**: one key used from many countries in a short window
- **Copy-paste bots**: different accounts sending the same prompts or templates
- **Spend spikes**: a user suddenly burning 10x their usual

Every flag tells you *why* it fired. No black box.

## What it won't do

- It doesn't block anything. It only looks at what already happened.
- It doesn't run in real time or sit in your request path.
- It never sends your logs anywhere. Everything runs on your machine.

## Status

- [x] Name & scope
- [ ] Log format + fake log generator (for demos and tests)
- [ ] The 5 checks
- [ ] Terminal report
- [ ] HTML report (v0.2)
- [ ] Read LiteLLM / Helicone / gateway exports directly (v0.3)

## Why "luxvigil"?

*Lux* = light, *vigil* = keeping watch through the night. It keeps an eye on what happened while you weren't looking.

---

Built by [@polfananas](https://github.com/polfananas)
