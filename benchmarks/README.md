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