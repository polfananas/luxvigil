# Benchmarks

## Fraud ecommerce (farm check)

There's no public dataset of AI API abuse with labels. Nobody publishes "these
accounts were farming our free tier", so to check the farm logic against real
fraud we use the closest thing out there.

That's the "Fraud ecommerce" set from Amazon's
[Fraud Dataset Benchmark](https://github.com/amazon-science/fraud-dataset-benchmark):
~150k signups, each with a device id, an IP and a fraud label. Most of the fraud
in it is the same trick as a free-tier farm: one person, lots of accounts, one
device. We don't ship the data, you download it yourself (see below).

The script turns it into a luxvigil log, runs the normal scan and checks every
account flagged as part of a farm against the labels.

151,112 accounts, 14,151 of them fraud (9.4%):

| --min-farm | accounts flagged | precision | recall | legit accounts flagged |
|---|---|---|---|---|
| 2 | 19,331 | 52.5% | 71.7% | 9,190 |
| 3 | 8,677 | 88.8% | 54.4% | 976 |
| 5 (default) | 8,391 | 90.9% | 53.9% | 766 |
| 10 | 6,548 | 92.0% | 42.5% | 527 |

With the default settings, 9 out of 10 accounts luxvigil flags are real fraud,
and it catches a bit over half of all the fraud in the data. No ML, no tuning,
just "these accounts share a device".

The jump between 2 and 3 is the interesting bit. Two accounts on one device is
often a couple or a family computer, about half of those are legit. Three or
more is almost always fraud. That's why the default is on the careful side:
you'd rather miss some fraud than accuse your real users.

What this does NOT show:

- only the farm check is tested. There are no emails, keys, prompts or tokens
  in this data, so the other checks have nothing to look at
- it's e-commerce, not AI API logs. It tells you the farm logic works on real
  fraud, not how much abuse your API has
- this dataset is kind to this signal: a lot of its fraud is bots reusing the
  same device. A careful farm with a fresh device per account wouldn't show up
- these aren't comparable to the AUC numbers in Amazon's paper, they measure
  something different (a ranking score over a fixed test split)

### Run it

1. Get the data from
   [kaggle.com/datasets/vbinh002/fraud-ecommerce](https://www.kaggle.com/datasets/vbinh002/fraud-ecommerce)
   (Download → zip). The license on Kaggle says "unknown", which is why it's not in the repo
2. Unzip it into `benchmarks/data/` (that folder is gitignored)
3. Run it:

```
python benchmarks/fraud_ecommerce.py
python benchmarks/fraud_ecommerce.py --min-farm 3
```

Takes a few seconds.