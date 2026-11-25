"""The luxvigil command: scan, demo, generate and audit-config.

Everything here is plumbing: read the arguments, load the logs and the config,
build the report and print it. The actual checks live in checks/ and report.py.

Exit codes: 0 when the scan ran (whatever it found), 1 when it couldn't
(bad file, bad config, bad arguments). A scan finding abuse is not an error.
"""

import argparse
import csv
import json
import math
import sys
import tempfile
from pathlib import Path

from luxvigil import __version__
from luxvigil import audit_config
from luxvigil.config import Config, ConfigError, load_config
from luxvigil.export import to_dict
from luxvigil.generate import generate, write_csv
from luxvigil.report import build_report, render_text
from luxvigil.schema import FORMATS, LogFormatError, load_logs, shown


def parse_map(items):
    """['email=user_email', ...] -> {'email': 'user_email', ...}"""
    mapping = {}
    for item in items:
        ours, sep, theirs = item.partition("=")
        if not sep or not ours.strip() or not theirs.strip():
            raise LogFormatError(f"--map wants OURS=THEIRS, like email=user_email (got '{item}')")
        mapping[ours.strip()] = theirs.strip()
    return mapping


def _bad_args(args):
    """Catch arguments that would quietly give nonsense, before reading anything."""
    for name in ("price_in", "price_out"):
        value = getattr(args, name)
        # nan and inf get through float(), and would turn every cost into nan
        if value is not None and (not math.isfinite(value) or value < 0):
            return f"--{name.replace('_', '-')} should be a price of 0 or more, got {value}"
    if args.quota_per_usd is not None and (not math.isfinite(args.quota_per_usd) or args.quota_per_usd <= 0):
        return f"--quota-per-usd should be more than 0, got {args.quota_per_usd}"
    # with 1 every single account would be its own "farm" or shared key
    if args.min_farm < 2:
        return f"--min-farm should be 2 or more, got {args.min_farm}"
    if args.min_countries < 2:
        return f"--min-countries should be 2 or more, got {args.min_countries}"
    return None


def run_demo(seed=42, keep=None):
    """Make a month of fake logs with abuse planted in, and scan them."""
    rows, planted = generate(seed=seed)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(keep) if keep else Path(tmp) / "demo-logs.csv"
        write_csv(rows, path)
        logs = load_logs(path)

    print("🕯️ demo: a month of fake API logs from 200 normal users, with some abuse planted in it:")
    print(f"   two account farms ({len(planted['farm_big'])} + {len(planted['farm_device'])} accounts), "
          "a leaked key and a spend spike. Let's see what luxvigil finds.\n")
    # the demo ignores any luxvigil.toml lying around, so it always shows the same thing
    print("\n".join(render_text(build_report(logs, config=Config()))))
    print()
    if keep:
        print(f"   fake logs saved to {keep}")
    print("   now try it on yours: luxvigil scan your-logs.csv")
    return 0


def _say(text):
    """Messages go to stderr. They can carry bits of your logs (a bad value, a file
    name), so no raw control characters get through to the terminal."""
    print("\n".join(shown(line, limit=1000) for line in str(text).split("\n")), file=sys.stderr)


def _utf8_output():
    """Windows writes to files and pipes in its old encoding, which has no emojis,
    so `luxvigil scan logs.csv > report.txt` would crash. Write UTF-8 instead."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass  # not a normal text stream (tests, some IDEs), leave it alone


def main(argv=None):
    _utf8_output()
    parser = argparse.ArgumentParser(
        prog="luxvigil",
        description="Abuse forensics for AI APIs.",
    )
    parser.add_argument("--version", action="version", version=f"luxvigil {__version__}")
    sub = parser.add_subparsers(dest="command")

    scan = sub.add_parser("scan", help="scan a CSV of API request logs for abuse")
    scan.add_argument("logs", help="path to the CSV log file")
    scan.add_argument("--min-farm", type=int, default=5,
                      help="how many linked accounts it takes to call it a farm (default 5)")
    scan.add_argument("--min-countries", type=int, default=5,
                      help="countries in one day before a key counts as shared (default 5)")
    scan.add_argument("--config", metavar="PATH",
                      help="trusted networks and ignored accounts (default: ./luxvigil.toml if it exists)")
    scan.add_argument("--map", action="append", default=[], metavar="OURS=THEIRS",
                      help="your column name for one of ours, e.g. --map email=user_email (repeat as needed)")
    scan.add_argument("--json", action="store_true",
                      help="print the report as JSON instead (findings with ids, evidence and next steps)")
    scan.add_argument("--format", choices=FORMATS, default="csv",
                      help="csv with our columns (default), or the logs of a gateway as they come: litellm "
                           "(LiteLLM spend logs), new-api or one-api (their usage logs)")
    scan.add_argument("--skip-bad-rows", action="store_true",
                      help="skip rows that can't be read instead of stopping (you're told how many)")
    dupes = scan.add_mutually_exclusive_group()
    dupes.add_argument("--keep-duplicates", action="store_true",
                       help="keep rows that repeat a request id already seen (by default they're counted once)")
    dupes.add_argument("--drop-duplicates", action="store_true",
                       help="also drop rows that are an exact copy of an earlier one when there's no request id "
                            "(by default they're kept, since they could be real repeated requests)")
    scan.add_argument("--users", metavar="PATH",
                      help="with --format new-api/one-api: the users table (CSV, or /api/user/ as JSON), "
                           "to bring in emails. Only id, username and email are read")
    scan.add_argument("--quota-per-usd", type=float, metavar="QUOTA",
                      help="with --format new-api/one-api: how much quota is $1, if you changed QuotaPerUnit "
                           "(default 500000)")
    scan.add_argument("--price-in", type=float, metavar="USD",
                      help="$ per 1M input tokens, to work out cost when your logs don't have it "
                           "(one price for every model: for several, use [prices] in luxvigil.toml)")
    scan.add_argument("--price-out", type=float, metavar="USD",
                      help="$ per 1M output tokens, to work out cost when your logs don't have it")

    demo = sub.add_parser("demo", help="try it without your own logs: makes fake ones and scans them")
    demo.add_argument("--seed", type=int, default=42, help="random seed, same seed = same logs")
    demo.add_argument("--keep", metavar="PATH", help="also save the fake logs here, to poke at them")

    gen = sub.add_parser("generate", help="make a fake log file with abuse planted in it (for demos/tests)")
    gen.add_argument("--out", default="sample-logs.csv", help="where to write the CSV")
    gen.add_argument("--users", type=int, default=200, help="number of normal users")
    gen.add_argument("--days", type=int, default=30, help="days of traffic")
    gen.add_argument("--seed", type=int, default=42, help="random seed, same seed = same file")

    audit = sub.add_parser("audit-config",
                           help="check a LiteLLM setup's files for settings that get its keys stolen")
    audit.add_argument("paths", nargs="*", default=["."], metavar="PATH",
                       help="folders or files: config.yaml, .env, docker-compose.yml, requirements.txt "
                            "(default: this folder)")
    audit.add_argument("--json", action="store_true", help="print the findings as JSON instead")
    audit.add_argument("--fail-on", choices=["fix-now", "worth-a-look"], default=None,
                       help="exit with 1 when there's a finding at this level or above, for CI "
                            "(by default it exits with 0 whatever it finds)")

    args = parser.parse_args(argv)

    if args.command == "scan":
        problem = _bad_args(args)
        if problem:
            _say(f"luxvigil: {problem}")
            return 1
        # config first: it's tiny, and a typo in it shouldn't make you wait for a big file to load first
        try:
            config = load_config(args.config)
        except (OSError, ConfigError) as e:
            _say(f"luxvigil: bad config: {e}")
            return 1
        try:
            mapping = parse_map(args.map)
            notes = []
            rows = load_logs(args.logs, mapping=mapping, price_in=args.price_in, price_out=args.price_out,
                             notes=notes, skip_bad=args.skip_bad_rows, fmt=args.format,
                             keep_duplicates=args.keep_duplicates, drop_duplicates=args.drop_duplicates,
                             prices=config.prices, quota_per_usd=args.quota_per_usd,
                             users=args.users)
        except (OSError, LogFormatError) as e:
            _say(f"luxvigil: can't read {args.logs}: {e}")
            return 1
        except (csv.Error, UnicodeDecodeError, json.JSONDecodeError) as e:
            # a broken export, a binary file, a single field of megabytes...: say so, no traceback
            kind = {"litellm": "LiteLLM export", "new-api": "New API export",
                    "one-api": "One API export"}.get(args.format, "UTF-8 CSV")
            _say(f"luxvigil: can't read {args.logs}, it doesn't look like a valid {kind}: {e}")
            return 1
        # notes go to stderr so --json output stays clean to pipe into jq
        for note in notes:
            _say(f"   note: {note}")
        report = build_report(rows, min_farm=args.min_farm, min_countries=args.min_countries, config=config)
        if args.json:
            print(json.dumps(to_dict(report, notes), indent=2, ensure_ascii=False))
        else:
            print("\n".join(render_text(report)))
        return 0

    if args.command == "audit-config":
        try:
            result = audit_config.audit(args.paths)
        except (OSError, audit_config.AuditError) as e:
            _say(f"luxvigil: {e}")
            return 1
        if args.json:
            print(json.dumps(audit_config.to_dict(result), indent=2, ensure_ascii=False))
        else:
            print("\n".join(shown(line, limit=1000) for line in audit_config.render_text(result)))
        failing = {"fix-now": result.fix_now, "worth-a-look": result.findings}.get(args.fail_on, [])
        if failing:
            _say(f"luxvigil: {len(failing)} finding{'s' if len(failing) != 1 else ''} at "
                 f"--fail-on {args.fail_on} or above")
            return 1
        return 0

    if args.command == "demo":
        return run_demo(seed=args.seed, keep=args.keep)

    if args.command == "generate":
        rows, planted = generate(n_users=args.users, days=args.days, seed=args.seed)
        write_csv(rows, args.out)
        print(f"🕯️ wrote {len(rows):,} requests to {args.out}")
        print("   planted on purpose:")
        print(f"   - big account farm:   {len(planted['farm_big'])} accounts")
        print(f"   - same-device farm:   {len(planted['farm_device'])} accounts")
        print(f"   - leaked key:         {len(planted['shared_key'])}")
        print(f"   - spend spike:        {len(planted['spike'])} account")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())