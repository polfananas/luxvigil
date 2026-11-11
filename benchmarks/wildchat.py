"""What does luxvigil find in millions of real chats with a free LLM?

WildChat (Allen Institute for AI, ODC-BY) is conversations people had with a
free GPT chatbot. That's the exact thing luxvigil is for: a free AI service,
logged. There are no abuse labels, so this isn't a precision/recall test. It's
"run it on real logs and go read what it flags".

Two versions:
  --dataset 1m    WildChat-1M, ~838k chats from 2023 to April 2024 (the default)
  --dataset 4.8m  WildChat-4.8M, but only the chats after WildChat-1M ends
                  (May 2024 to July 2025). That part was never used to tune
                  anything, so it's the honest check of whatever 1M taught us

There are no logins in WildChat, so each hashed IP counts as one account. And
there are no devices, emails or keys either, so only the copy-paste bot and
spend spike checks get exercised here (the fraud ecommerce benchmark covers farms).
It's also a scale test: millions of requests, the biggest real logs we've run.

Costs are estimates. WildChat doesn't say what anything cost, so tokens are
counted as ~4 characters each and priced at OpenAI's list prices at the time.

Get the data yourself, it's big (~3.4 GB for 1M, ~15 GB for 4.8M):
  huggingface.co/datasets/allenai/WildChat-1M or .../WildChat-4.8M -> data/
  -> put the files in benchmarks/data/wildchat-1m/ or wildchat-4.8m/  (or run with --download)

    pip install pyarrow          # only needed for this script, not for luxvigil
    python benchmarks/wildchat.py
    python benchmarks/wildchat.py --dataset 4.8m --download
"""

import argparse
import csv
import hashlib
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from luxvigil.report import build_report, render_text
from luxvigil.schema import COLUMNS, load_logs

DATASETS = {
    "1m": {"repo": "allenai/WildChat-1M", "files": 14, "after": None},
    # WildChat-1M stops at the end of April 2024, 4.8M carries on to July 2025
    "4.8m": {"repo": "allenai/WildChat-4.8M", "files": 86, "after": datetime(2024, 4, 30)},
}

# $ per 1M tokens, input / output, roughly OpenAI's list prices when the chats
# happened. Most specific name first, since the first match wins. Only there to
# put the spend in the right ballpark: GPT-4 really was ~20x pricier than GPT-3.5.
PRICES = {
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4.1-nano": (0.1, 0.4),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4o": (2.5, 10.0),
    "gpt-4-turbo": (10.0, 30.0),
    "gpt-4": (30.0, 60.0),
    "o1-mini": (3.0, 12.0),
    "o3-mini": (1.1, 4.4),
    "o1": (15.0, 60.0),
    "gpt-3.5": (1.5, 2.0),
}


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


def naive(t):
    # some files have timezones on their timestamps and some don't, compare without
    return t.replace(tzinfo=None) if t is not None and t.tzinfo else t


def files_of(name):
    info = DATASETS[name]
    return [f"train-{i:05d}-of-{info['files']:05d}.parquet" for i in range(info["files"])]


def folder_of(name):
    return Path(__file__).parent / "data" / f"wildchat-{name}"


def download(name):
    folder = folder_of(name)
    folder.mkdir(parents=True, exist_ok=True)
    url = f"https://huggingface.co/datasets/{DATASETS[name]['repo']}/resolve/main/data/"
    for file in files_of(name):
        if not (folder / file).exists():
            print(f"downloading {file}…", file=sys.stderr)
            urllib.request.urlretrieve(url + file, folder / file)


def convert(name, dst):
    """WildChat parquet files -> a luxvigil log, one request per user message."""
    import pyarrow.parquet as pq  # here so `--help` works without pyarrow

    after = DATASETS[name]["after"]
    rows = 0
    with open(dst, "w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(COLUMNS))
        writer.writeheader()
        for file in files_of(name):
            # a batch at a time: the newer files are big and reading one whole runs out of memory
            for batch in pq.ParquetFile(folder_of(name) / file).iter_batches(
                batch_size=2000, columns=["model", "timestamp", "conversation"]
            ):
                for chat in batch.to_pylist():
                    if after and naive(chat["timestamp"]) < after:
                        continue
                    rows += write_chat(writer, chat)
    return rows


def write_chat(writer, chat):
    price_in, price_out = price_of(chat["model"])
    turns = chat["conversation"]
    context = 0  # a chat resends the whole history every turn, so input grows
    written = 0
    for i, turn in enumerate(turns):
        if turn["role"] != "user":
            continue
        reply = turns[i + 1] if i + 1 < len(turns) else None
        answer = reply["content"] if reply else ""
        sent = context + tokens(turn["content"])
        got = tokens(answer) if answer else 0
        # 12 hex chars is plenty to tell the IPs apart, and fits on screen
        account = (turn["hashed_ip"] or "unknown")[:12]
        # the user turn has no time of its own, the reply right after it does
        when = naive((reply or {}).get("timestamp") or chat["timestamp"])
        writer.writerow({
            "timestamp": when.isoformat() + "Z",
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
        written += 1
    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", choices=list(DATASETS), default="1m",
                        help="1m, or 4.8m to check on newer chats that weren't used to tune anything")
    parser.add_argument("--download", action="store_true", help="fetch the files from Hugging Face first")
    args = parser.parse_args()

    if args.download:
        download(args.dataset)
    missing = [f for f in files_of(args.dataset) if not (folder_of(args.dataset) / f).exists()]
    if missing:
        sys.exit(f"missing {len(missing)} files in {folder_of(args.dataset)}, "
                 "get them from Hugging Face or run with --download")

    logs = folder_of(args.dataset) / "luxvigil.csv"
    start = time.perf_counter()
    rows = convert(args.dataset, logs)
    print(f"converted {rows:,} user messages in {time.perf_counter() - start:.0f}s", file=sys.stderr)

    # the same thing `luxvigil scan` does, timed
    start = time.perf_counter()
    report = build_report(load_logs(logs))
    print(f"scanned in {time.perf_counter() - start:.0f}s\n", file=sys.stderr)
    print("\n".join(render_text(report)))


if __name__ == "__main__":
    main()
