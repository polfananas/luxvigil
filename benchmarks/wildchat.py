"""What does luxvigil find in a million real chats with a free LLM?

WildChat-1M (Allen Institute for AI, ODC-BY) is ~838k conversations people had
with a free GPT-3.5 / GPT-4 chatbot in 2023-2024. That's the exact thing
luxvigil is for: a free AI service, logged. There are no abuse labels, so this
isn't a precision/recall test. It's "run it on real logs and go read what it
flags".

There are no logins in WildChat, so each hashed IP counts as one account. And
there are no devices, emails or keys either, so only the copy-paste bot and
spend spike checks get exercised here (the fraud ecommerce benchmark covers farms).
It's also a scale test: ~2M requests, the biggest real log we've run.

Costs are estimates. WildChat doesn't say what anything cost, so tokens are
counted as ~4 characters each and priced at what OpenAI charged at the time.

Get the data yourself, it's ~3.4 GB:
  huggingface.co/datasets/allenai/WildChat-1M -> the 14 files in data/
  -> put them in benchmarks/data/wildchat/   (or run with --download)

    pip install pyarrow          # only needed for this script, not for luxvigil
    python benchmarks/wildchat.py
"""

import argparse
import csv
import hashlib
import sys
import time
import urllib.request
from pathlib import Path

from luxvigil.report import build_report, render_text
from luxvigil.schema import COLUMNS, load_logs

DATA = Path(__file__).parent / "data" / "wildchat"
SHARDS = [f"train-{i:05d}-of-00014.parquet" for i in range(14)]
URL = "https://huggingface.co/datasets/allenai/WildChat-1M/resolve/main/data/"

# $ per 1M tokens, input / output, roughly what OpenAI charged when the chats happened.
# WildChat doesn't say what anything cost, so this is only to put the spend in the
# right ballpark: GPT-4 really was ~20x pricier than GPT-3.5.
PRICES = {"gpt-4": (30.0, 60.0), "gpt-3.5": (1.5, 2.0)}


def price_of(model):
    for prefix, price in PRICES.items():
        if model.startswith(prefix):
            return price
    return PRICES["gpt-3.5"]


def tokens(text):
    # no tokenizer on purpose (no extra dependency): ~4 characters per token is
    # the usual rule of thumb for English, good enough for a cost estimate
    return max(1, len(text) // 4)


def prompt_hash(text):
    # same idea as a real log: hash the prompt, never keep it. Whitespace and case
    # are squashed so "Hi " and "hi" count as the same prompt
    return hashlib.sha256(" ".join(text.lower().split()).encode()).hexdigest()[:16]


def download():
    DATA.mkdir(parents=True, exist_ok=True)
    for name in SHARDS:
        if not (DATA / name).exists():
            print(f"downloading {name}…", file=sys.stderr)
            urllib.request.urlretrieve(URL + name, DATA / name)


def convert(dst):
    """WildChat parquet files -> a luxvigil log, one request per user message."""
    import pyarrow.parquet as pq  # here so `--help` works without pyarrow

    rows = 0
    with open(dst, "w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(COLUMNS))
        writer.writeheader()
        for name in SHARDS:
            table = pq.read_table(DATA / name, columns=["model", "timestamp", "conversation"])
            for chat in table.to_pylist():
                price_in, price_out = price_of(chat["model"])
                turns = chat["conversation"]
                context = 0  # a chat resends the whole history every turn, so input grows
                for i, turn in enumerate(turns):
                    if turn["role"] != "user":
                        continue
                    reply = turns[i + 1] if i + 1 < len(turns) else None
                    answer = reply["content"] if reply else ""
                    sent = context + tokens(turn["content"])
                    got = tokens(answer) if answer else 0
                    account = (turn["hashed_ip"] or "unknown")[:12]  # 12 hex chars is plenty to tell 200k IPs apart, and fits on screen
                    writer.writerow({
                        # the user turn has no time of its own, the reply right after it does
                        "timestamp": ((reply or {}).get("timestamp") or chat["timestamp"]).isoformat(),
                        "account_id": account,
                        "email": "",  # no logins, no emails
                        "api_key_id": account,  # no keys either, one per "account" keeps that check quiet
                        "ip": "",  # IPs are hashed in WildChat, so the network checks can't run
                        "country": turn["country"] or "",
                        # left empty on purpose: the browser header is the closest thing to a
                        # device, but thousands of people share the same Chrome version, and
                        # using it would link strangers into fake farms
                        "device_id": "",
                        "prompt_hash": prompt_hash(turn["content"]),
                        "input_tokens": sent,
                        "output_tokens": got,
                        "cost_usd": (sent * price_in + got * price_out) / 1_000_000,
                    })
                    context = sent + got
                    rows += 1
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--download", action="store_true", help="fetch the 14 files from Hugging Face first (~3.4 GB)")
    args = parser.parse_args()

    if args.download:
        download()
    missing = [n for n in SHARDS if not (DATA / n).exists()]
    if missing:
        sys.exit(f"missing {len(missing)} of the 14 files in {DATA}, get them from Hugging Face or run with --download")

    logs = DATA / "wildchat_luxvigil.csv"
    start = time.perf_counter()
    rows = convert(logs)
    print(f"converted {rows:,} user messages in {time.perf_counter() - start:.0f}s", file=sys.stderr)

    # the same thing `luxvigil scan` does, timed
    start = time.perf_counter()
    report = build_report(load_logs(logs))
    print(f"scanned in {time.perf_counter() - start:.0f}s\n", file=sys.stderr)
    print("\n".join(render_text(report)))


if __name__ == "__main__":
    main()
