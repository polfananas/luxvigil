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

Two ways to score it, and they answer different questions:

- **retrospective** (the default): scan the whole file at once, like you'd scan
  last month's logs. An account counts as sharing a device with accounts that
  showed up *after* it, too
- **causal** (`--causal`): score each account only with what had happened up to
  its own purchase. That's what luxvigil would have said if you'd scanned right
  then, and the only fair way to compare with a model that can't see the future

### All accounts

151,112 accounts, 14,151 of them fraud (9.4%). Retrospective, and with no time
limit on when the accounts showed up (`--window 0`, how luxvigil worked before
0.3.0, see the next sections for what the time window changes):

| --min-farm | accounts flagged | precision | recall | legit accounts flagged |
|---|---|---|---|---|
| 2 | 19,331 | 52.5% | 71.7% | 9,190 |
| 3 | 8,677 | 88.8% | 54.4% | 976 |
| 5 (default) | 8,391 | 90.9% | 53.9% | 766 |
| 10 | 6,548 | 92.0% | 42.5% | 527 |

The default of 5 was set before this benchmark existed, it wasn't picked from
this table. There's no training: the rule is just "these accounts share a device".

The jump between 2 and 3 is the interesting bit. Two accounts on one device is
often a couple or a family computer, about half of those are legit. Three or
more is mostly fraud here, at least in January (see below).

Causal, at the default of 5: 5,375 accounts flagged, precision 85.7%, recall
32.6%. Recall drops because a farm only gets flagged from its 5th account on:
the first four looked fine when they showed up.

### When the fraud happened (read this before quoting the numbers above)

The fraud in this dataset isn't spread evenly. January 2015 is a burst:

| month | fraud |
|---|---|
| January 2015 | 76% of purchases |
| February to December | ~4.5% each month |

That burst is the farms: lots of accounts on the same device, buying within a
second of signing up. 7,745 of the 8,391 accounts flagged above are from
January. Leave January out and it falls apart:

| without January 2015 (141,015 accounts, 6,428 fraud) | flagged | precision | recall |
|---|---|---|---|
| retrospective, --min-farm 5 | 646 | 4.5% | 0.5% |

4.5% is the fraud rate of those months, so outside the burst a shared device is
no better than picking accounts at random. After January the fraud is mostly
one person, one account, and there's nothing shared to link.

So what this shows is that the farm check catches a farm attack when one
happens. It doesn't catch fraud in general, and the 90.9% is really "90.9% in a
month with a farm attack".

### How close together the accounts have to show up (0.3.0)

Since 0.3.0 a shared device only counts as a burst when 5 accounts were first
seen on it within `window_days` (30 by default, `[farms] window_days` in
luxvigil.toml). Same retrospective scan, all accounts, `--min-farm 5`, counting
every account in a burst (`python benchmarks/fraud_ecommerce.py --window N`):

| --window | accounts flagged | precision | recall | legit accounts flagged | flagged outside January |
|---|---|---|---|---|---|
| 0 (no limit, before 0.3.0) | 8,391 | 90.9% | 53.9% | 766 | 646 (4.5% fraud) |
| 90 days | 8,148 | 93.1% | 53.6% | 560 | 426 (3.5% fraud) |
| 30 days (default) | 7,734 | 97.7% | 53.4% | 181 | 32 (none fraud) |
| 7 days | 7,583 | 99.4% | 53.3% | 45 | 0 |

On this data the window does what it's for: most of the legit accounts that
shared a device over the year drop out, and the farms stay, because they're
bursts of accounts made seconds apart. A shorter window looks even better here,
but that's this dataset: every farm in it is instant, so it can't show what a
short window costs, which is missing a farm that signs up over a few weeks.
30 days is still a guess for AI APIs, not a number this table calibrates.

Also: this data has no emails or prompts, so nothing backs a burst up, and in a
real `luxvigil scan` all of these are "worth a look", not likely abuse (see
checks/farms.py). The table measures the burst itself.

### Next to Amazon's numbers

Amazon's benchmark tests on the last 20% of purchases in time (late August to
December 2015) and reports AUC. `--split amazon` grades exactly those rows.
For the AUC we use "how many accounts share this device" as the score, the
signal the farm check is built on. That's one score, not the whole scan: at
the default threshold the scan flags no accounts there with the 30-day window
(9 without it, precision 33%, recall 0.2%).

Same rows is not the same protocol. Their models train on the first 80% and
then score the test set. Their preprocessing passes the device id as the
"entity", which is what the best one (Amazon Fraud Detector's "TFI" model)
builds its history features from, as far as we can tell from their code.
A retrospective scan instead counts devices across the whole file, later
purchases included, which no model in the benchmark could do. The causal
number is the one to compare:

| | AUC on Amazon's test set |
|---|---|
| Amazon Fraud Detector TFI, their best ([results table in their repo](https://github.com/amazon-science/fraud-dataset-benchmark#benchmark-results)) | 0.636 |
| Their other models (AFD OFI, AutoGluon, H2O, Auto-sklearn) | 0.515 to 0.522 |
| device signal, causal (`--split amazon --causal`) | 0.642 |
| device signal, retrospective (`--split amazon`) | 0.659, sees the future, not comparable |

Causal, it's about level with their best, with one feature and no training.
That's not a win: the difference is tiny, we haven't computed any error bars
(accounts on the same device aren't independent, so a naive one would be too
narrow), and there are hardly any farms in that period for either side to find.

If you shuffle the data and take a random 20% instead, January ends up in the
test set and the same signal scores ~0.84. That's the number you'll see in
other projects that use a random split. It's not comparable either.

### What this does NOT show

- only the farm check is tested. There are no emails, keys, prompts or tokens
  in this data, so the other checks have nothing to look at
- it's e-commerce, not AI API logs. It tells you the farm logic finds real
  farms when there are some, not how precise it'll be on your API
- every account here buys once, so "accounts on a device" is all the scan has.
  On real API logs farms also get linked by network, which this doesn't test
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
python benchmarks/fraud_ecommerce.py --split amazon --causal
```

Takes a few seconds.
## WildChat (copy-paste bots, spend spikes, scale)

[WildChat-1M](https://huggingface.co/datasets/allenai/WildChat-1M) (Allen
Institute for AI, ODC-BY) is ~838k conversations people had with a free GPT-3.5 /
GPT-4 chatbot in 2023-2024. That's exactly what luxvigil is for: a free AI service,
logged. There are no abuse labels, so there's no precision or recall here. The
test is "run it on real logs, then go read what it flags".

How it maps to a luxvigil log:

- one request per user message, ~2M of them. These are rebuilt from
  conversations, not real API calls: the context of a real request (the chat
  so far) is only roughly estimated
- no logins, so each hashed IP counts as one account (188k of them). An IP is
  not a person: a NAT or a proxy puts many people on one, and a changing IP
  splits one person in several
- the prompt hash is a hash of the message, so the copy-paste bot check has something real to chew on
- tokens and cost are estimates (~4 characters per token, priced at what OpenAI charged back then),
  so every dollar below is a ballpark, not a bill
- no devices, emails or keys, so farms, throwaway emails, keys and Gmail aliases can't be tested here.
  The device field is left empty on purpose: the browser header is the closest thing,
  but thousands of people share the same Chrome, and using it would invent farms

### Speed

1,960,074 requests in a 190 MB CSV, on a 2-core cloud box with 7 GB of RAM (luxvigil 0.2.0):

| step | time |
|---|---|
| reading and checking the CSV | 17s |
| all the checks and the report | 8s |
| peak memory | ~1.2 GB |

Everything is held in memory, so memory grows with the number of rows: plan
for roughly 0.6 GB per million requests on a CSV like this one. A LiteLLM JSON
export takes more, since the whole JSON is read in first. Turning the parquet
files into that CSV takes a couple of minutes more, that's the benchmark
script, not luxvigil.

Splitting a big file and scanning the pieces is not the same scan: a farm can
end up split across pieces, a leaked key's 24h window can be cut in two, the
20% rule of the bot check gets a different denominator and spikes lose their
history. If your logs don't fit in memory, scan one period at a time and say so.

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

### Newer chats (WildChat-4.8M): a check of the 20% rule, not a holdout for what came after

Everything above was worked out looking at WildChat-1M, so it could just be
fitted to that data. To check, we ran the same luxvigil (20% rule and all, no
changes) on [WildChat-4.8M](https://huggingface.co/datasets/allenai/WildChat-4.8M),
keeping only the chats from after WildChat-1M ends: May 2024 to July 2025, newer
models (GPT-4o, o1, GPT-4.1), not looked at before that run.

The order matters for what this proves. For the 20% rule, these chats were
new data. Then we read the 41 groups it flagged, and that reading is what the
length rule (next section) came from. So for the length rule, this is the data
it was developed on, not an independent test. A real holdout needs a frozen
version run once on data nobody has read yet, and we haven't done that.

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
prompt is: "more" can't be a template. See the next section.

Two more things this data showed:

- far more distinct hashed IPs (1.67M, about 2 messages each), so the IP hash
  looks less stable in newer WildChat data than in 1M. Fewer repeats per
  "account" makes every per-account check quieter
- 574 spend spikes on 388 accounts, same picture as before: heavy human users

### The prompt length rule

Since "more" can't be a template, luxvigil now takes an optional `prompt_chars`
column (the length of the prompt, nothing about what it says), and prompts
under 20 characters are never treated as a bot template. Logs without the
column work exactly as before. The script above fills it in for WildChat.

Share of the (estimated) spend flagged as likely abuse, as the bot check changed:

| | first version | + 20% rule | + length rule | + only the repeated prompts count (0.2.0) |
|---|---|---|---|---|
| WildChat-1M | 34% | 20% | 5% | 1.1% |
| WildChat-4.8M, new chats only | – | 15% | 8% | not re-run |

The last column is a change in what gets counted, not in what gets found:
up to the length rule, everything a bot's accounts sent was likely abuse. From
0.2.0 only the requests with the repeated prompts are, and the rest of what
those accounts did is "worth a look". On WildChat-1M that's $675 of likely
abuse plus $2,386 worth a look, from the same 14 groups ($699 and $2,362 in
0.2.0, which also counted a request as repeated when another account in the
group was the one repeating it). We didn't keep the
4.8M data around to re-run that column.

On WildChat-1M, 14 bot groups (298 accounts) are left, and nearly all of them
read like automation: the agent loops, a translation pipeline, the
print-on-demand titles, data generation scripts, a jailbreak template. On the
newer 4.8M chats it keeps 12 of the 13 automation groups we'd found by hand (it
drops one that only sends a short command) and goes from 1,211 flagged accounts
to 381. Keep in mind those 13 are groups we found by reading what was flagged,
so "12 of 13" says the rule didn't throw away what we'd already found. It's
not a recall: nobody went through the unflagged traffic looking for bots
the check missed.

Still not perfect. On the newer chats the most expensive group left (16
accounts, ~$1,450) is long, repeated writing requests that read more like busy
people than a script, and it's most of what's flagged in dollars there. So the
advice stays: read the bot groups before quoting the total.

All the reading was done once, by one reviewer, with no second opinion and
no written protocol. And "looks automated" is not "broke the rules": using a free chatbot from a script may or may not be against its terms,
and we can't tell from the logs.

### Run it

```
pip install pyarrow                                      # only for this script, luxvigil itself doesn't need it
python benchmarks/wildchat.py --download                  # WildChat-1M, ~3.4 GB
python benchmarks/wildchat.py --dataset 4.8m --download   # the newer chats, ~15 GB to download
```
