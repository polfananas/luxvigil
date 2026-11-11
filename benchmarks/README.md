# Benchmarks

## Fraud ecommerce (farm check)

There's no public dataset of AI API abuse with labels. Nobody publishes "these
accounts were farming our free tier", so to check the farm logic against real
fraud we use the closest thing out there.

That's the "Fraud ecommerce" set from Amazon's
[Fraud Dataset Benchmark](https://github.com/amazon-science/fraud-dataset-benchmark):
~150k signups from 2015, each with a device id, an IP and a fraud label. We don't
ship the data, you download it yourself (see below).

The script turns it into a luxvigil log, runs the normal scan on all of it and
checks every account flagged as part of a farm against the labels.

### All accounts

151,112 accounts, 14,151 of them fraud (9.4%):

| --min-farm | accounts flagged | precision | recall | legit accounts flagged |
|---|---|---|---|---|
| 2 | 19,331 | 52.5% | 71.7% | 9,190 |
| 3 | 8,677 | 88.8% | 54.4% | 976 |
| 5 (default) | 8,391 | 90.9% | 53.9% | 766 |
| 10 | 6,548 | 92.0% | 42.5% | 527 |

With the default settings, 9 out of 10 accounts luxvigil flags are real fraud.
No ML, no tuning, just "these accounts share a device".

The jump between 2 and 3 is the interesting bit. Two accounts on one device is
often a couple or a family computer, about half of those are legit. Three or
more is almost always fraud. That's why the default is on the careful side.

### When the fraud happened (read this before quoting the numbers above)

The fraud in this dataset isn't spread evenly. January 2015 is a burst:

| month | fraud |
|---|---|
| January 2015 | 76% of purchases |
| February to December | ~4.5% each month |

That burst is the farms: lots of accounts on the same device, buying within a
second of signing up. 7,745 of the 8,391 accounts flagged above are from
January. After that the fraud is mostly one person, one account, and there's
nothing shared to link.

So what the table above really shows is that luxvigil catches a farm attack
when one happens, without making things up when there isn't one. It doesn't
mean it catches half of all fraud in general.

### Next to Amazon's numbers

Amazon's paper scores the last 20% of purchases in time (late August to
December 2015) and reports AUC. `--split amazon` grades exactly that set. For
the AUC we use "how many accounts share this device" as the score, which is the
signal the farm check is built on.

| | AUC on Amazon's test set |
|---|---|
| Amazon's best (Amazon Fraud Detector, from the paper) | 0.646 |
| Their other models (Random Forest, CatBoost, LightGBM, AutoML...) | 0.49 to 0.64 |
| luxvigil's device signal | 0.659 |

About level with their best, with no training at all. But there are hardly any
farms in that period, so with the default settings luxvigil only flags 9
accounts there. That's the honest result: no farms, (almost) no flags.

If you shuffle the data and take a random 20% instead, January ends up in the
test set and the same signal scores ~0.84. That's the number you'll see in
other projects that use a random split. It's not comparable to the paper.

### What this does NOT show

- only the farm check is tested. There are no emails, keys, prompts or tokens
  in this data, so the other checks have nothing to look at
- it's e-commerce, not AI API logs. It tells you the farm logic works on real
  fraud, not how much abuse your API has
- a careful farm with a fresh device per account wouldn't show up

### Run it

1. Get the data from
   [kaggle.com/datasets/vbinh002/fraud-ecommerce](https://www.kaggle.com/datasets/vbinh002/fraud-ecommerce)
   (Download → zip). The license on Kaggle says "unknown", which is why it's not in the repo
2. Unzip it into `benchmarks/data/` (that folder is gitignored)
3. Run it:

```
python benchmarks/fraud_ecommerce.py
python benchmarks/fraud_ecommerce.py --min-farm 3
python benchmarks/fraud_ecommerce.py --split amazon
```

Takes a few seconds.
## WildChat (copy-paste bots, spend spikes, scale)

[WildChat-1M](https://huggingface.co/datasets/allenai/WildChat-1M) (Allen
Institute for AI, ODC-BY) is ~838k conversations people had with a free GPT-3.5 /
GPT-4 chatbot in 2023-2024. That's exactly what luxvigil is for: a free AI service,
logged. There are no abuse labels, so there's no precision or recall here. The
test is "run it on real logs, then go read what it flags".

How it maps to a luxvigil log:

- one request per user message, ~2M of them
- no logins, so each hashed IP counts as one account (188k of them)
- the prompt hash is a hash of the message, so the copy-paste bot check has something real to chew on
- tokens and cost are estimates (~4 characters per token, priced at what OpenAI charged back then)
- no devices, emails or keys, so farms, throwaway emails, keys and Gmail aliases can't be tested here.
  The device field is left empty on purpose: the browser header is the closest thing,
  but thousands of people share the same Chrome, and using it would invent farms

### Speed

1,960,074 requests: 27s to scan on a small 2-core cloud box (plus a couple of minutes to turn
the parquet files into a CSV, that part is the benchmark script, not luxvigil).

### What it found (we read the flagged groups by hand, no message text is published here)

Real automation running on a free chatbot:

- ~170 accounts generating product titles for a print-on-demand shop from the
  same few templates, i.e. someone using the free chat as an API for a business
- ~50 accounts sending the control messages of an autonomous agent framework in a loop
- small groups of scripts generating training data (scripted dialogues, review
  categorisation) and a well-known jailbreak template sent from several accounts

That's the kind of thing luxvigil is meant to surface.

### What it got wrong, and what we changed

The first run said 34% of the spend was likely abuse. It wasn't. The biggest
"bot" was 2,262 accounts tied together by messages like "more", "shorter" or
"hi": thousands of people type those, some of them over and over, and the check
chained them all into one group.

Fix (in `checks/prompts.py`): a prompt only counts as a template if at least 20%
of the accounts that ever sent it are repeating it. A bot template is sent
almost only by the bot, "more" is sent by thousands of people and most of them
don't repeat it. That broke up the fake group and the synthetic demo is unchanged.

It's not fully solved. Follow-ups like "continue" or "go on" (in lots of
languages) are typed so often by the people who use them that they still pass
the 20% rule. After the fix the report says ~20% of the spend is likely abuse,
and the biggest group in there (~310 accounts) is still mostly that. **On
chat-style logs, read the copy-paste bot groups before trusting the dollar total.**

### Spend spikes

1,440 spikes on 803 accounts. The ones we read were mostly heavy, very human
users (long creative writing sessions, homework, coding help) plus one
automated image-prompt generator. That's why spikes only count as "worth a
look". It also showed the report should group repeat spikes by account: 272
accounts spiked more than once.

### Checking it on newer chats nobody tuned on (WildChat-4.8M)

Everything above was worked out looking at WildChat-1M, so it could just be
fitted to that data. To check, we ran the same luxvigil (20% rule and all, no
changes) on [WildChat-4.8M](https://huggingface.co/datasets/allenai/WildChat-4.8M),
keeping only the chats from after WildChat-1M ends: May 2024 to July 2025, newer
models (GPT-4o, o1, GPT-4.1), never looked at before.

3,364,550 requests, scanned in 27s. ~15% of the spend comes out as likely abuse,
from 41 copy-paste bot groups (1,211 accounts). We read all 41 by hand:

- 13 are real automation (157 accounts): an agent stuck in a loop on the same
  error message, product and stock-photo title generators, image prompt
  generators, a script writing help-centre questions, a chat bot being tested,
  a broken script sending the same garbage over and over
- the other 28 are people: "more", "others", "shorter", "go on", "continue" and
  "answer in Chinese" in lots of languages, plus some long prompts shared around
- in money, the real automation is only ~11% of what the bot check flags

So the 20% rule holds up the same on new data, and so does its weak spot: in
chat logs, short follow-ups still dominate the bot total. We tested the "same
size every time" idea on this data too and dropped it: it removes the
follow-ups but also loses the agent loop (its history grows every turn) and
flags greetings like "hello" instead. What does work is looking at how long the
prompt is: "more" can't be a template. That's the next change.

Two more things this data showed:

- far more distinct hashed IPs (1.67M, about 2 messages each), so the IP hash
  looks less stable in newer WildChat data than in 1M. Fewer repeats per
  "account" makes every per-account check quieter
- 574 spend spikes on 388 accounts, same picture as before: heavy human users

### Run it

```
pip install pyarrow                                      # only for this script, luxvigil itself doesn't need it
python benchmarks/wildchat.py --download                  # WildChat-1M, ~3.4 GB
python benchmarks/wildchat.py --dataset 4.8m --download   # the newer chats, ~15 GB to download
```
