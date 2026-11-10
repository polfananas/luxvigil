"""How well does the farm check do on real, labeled fraud?

There's no public dataset of AI API abuse with labels. Nobody publishes
"these accounts were farming our free tier", so we borrow the closest thing:
the "Fraud ecommerce" set from Amazon's Fraud Dataset Benchmark. ~150k signups,
each one with a device id, an IP and a label saying if it was fraud.

Most of the fraud in there is the same trick as a free-tier farm: one person,
lots of accounts, one device. So we turn it into a luxvigil log, run the real
scan (not a copy of the logic), and check the flagged accounts against the labels.

Get the data yourself, we don't ship it (the license on Kaggle says "unknown"):
  kaggle.com/datasets/vbinh002/fraud-ecommerce -> Download -> unzip into benchmarks/data/

    python benchmarks/fraud_ecommerce.py
    python benchmarks/fraud_ecommerce.py --min-farm 3

What this does NOT test: there are no emails, keys, prompts, tokens or spikes
in this data, so only the farm check gets measured. And it's e-commerce, not
AI API logs. It tells you the farm logic works on real fraud, nothing more.
"""

import argparse
import bisect
import csv
import ipaddress
import sys
from pathlib import Path

from luxvigil.report import build_report
from luxvigil.schema import COLUMNS, load_logs

# benchmarks/data/ is in .gitignore, the datasets never go in the repo
DATA = Path(__file__).parent / "data"


def load_countries(path):
    """IpAddress_to_Country.csv -> three sorted lists (range starts, ends, countries).

    The file is ~140k IP ranges like 16777216-16777471 -> Australia. Keeping the
    starts sorted lets us find the right range with a binary search (bisect)
    instead of going through all 140k for each of the 150k purchases.
    """
    with open(path, newline="") as f:
        ranges = sorted(
            (int(float(r["lower_bound_ip_address"])), int(float(r["upper_bound_ip_address"])), r["country"])
            for r in csv.DictReader(f)
        )
    return [lo for lo, _, _ in ranges], [hi for _, hi, _ in ranges], [c for _, _, c in ranges]


def country_of(ip_number, countries):
    """Which country an IP (as a number) belongs to, or "" if it's in no range."""
    starts, ends, names = countries
    # the last range that starts at or before our IP...
    i = bisect.bisect_right(starts, ip_number) - 1
    # ...and only if the IP is actually inside it (there are gaps between ranges)
    return names[i] if i >= 0 and ip_number <= ends[i] else ""


def convert(src, dst, countries):
    """Fraud_Data.csv -> a luxvigil log, one request per purchase.

    Returns {account: is_fraud}, so we can check the scan against the labels after.
    """
    labels = {}
    with open(src, newline="") as f, open(dst, "w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(COLUMNS))
        writer.writeheader()
        for r in csv.DictReader(f):
            account = f"u{r['user_id']}"
            labels[account] = r["class"] == "1"
            # the IPs come as numbers with decimals, like 732758368.79972. The whole
            # part is the IP itself, so 732758368 -> 43.173.1.96
            ip_number = int(float(r["ip_address"]))
            writer.writerow({
                "timestamp": r["purchase_time"],
                "account_id": account,
                "email": "",  # not in the data, so the email checks just stay quiet
                "api_key_id": account,  # one "key" per account: nothing shared, no key findings
                "ip": str(ipaddress.IPv4Address(ip_number)),
                "country": country_of(ip_number, countries),
                "device_id": r["device_id"],  # this is the one that matters here
                "prompt_hash": account,  # no prompts either, all different so no bots
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": r["purchase_value"],  # what they bought, so the report shows $ at stake
            })
    return labels


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-farm", type=int, default=5, help="same as luxvigil scan --min-farm (default 5)")
    args = parser.parse_args()

    src = DATA / "Fraud_Data.csv"
    if not src.exists():
        sys.exit(f"no {src}. Download it from kaggle.com/datasets/vbinh002/fraud-ecommerce and unzip it into {DATA}")

    # 1. turn the dataset into a normal luxvigil log (also in data/, also ignored)
    countries = load_countries(DATA / "IpAddress_to_Country.csv")
    logs = DATA / "fraud_ecommerce_luxvigil.csv"
    labels = convert(src, logs, countries)

    # 2. run the same scan you'd get with `luxvigil scan`, and take every account in a farm
    report = build_report(load_logs(logs), min_farm=args.min_farm)
    flagged = {acc for farm in report.farms for acc in farm.accounts}

    # 3. check against the labels
    #    precision: of the accounts we flagged, how many really were fraud
    #    recall: of all the fraud in the data, how much we caught
    fraud = {acc for acc, bad in labels.items() if bad}
    caught = flagged & fraud
    precision = len(caught) / len(flagged) if flagged else 0.0
    recall = len(caught) / len(fraud)

    print(f"{len(labels):,} accounts, {len(fraud):,} fraud ({len(fraud) / len(labels):.1%})")
    print(f"min-farm {args.min_farm}: {len(report.farms):,} farms, {len(flagged):,} accounts flagged")
    print(f"  precision {precision:.1%}  (flagged accounts that really are fraud)")
    print(f"  recall    {recall:.1%}  (share of all fraud that got flagged)")
    print(f"  wrongly flagged: {len(flagged - fraud):,} legit accounts")


if __name__ == "__main__":
    main()