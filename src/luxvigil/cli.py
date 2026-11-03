import argparse
import sys

from luxvigil import __version__
from luxvigil.config import ConfigError, load_config
from luxvigil.generate import generate, write_csv
from luxvigil.report import build_report, render_text
from luxvigil.schema import LogFormatError, load_logs


def parse_map(items):
    """['email=user_email', ...] -> {'email': 'user_email', ...}"""
    mapping = {}
    for item in items:
        ours, sep, theirs = item.partition("=")
        if not sep or not ours.strip() or not theirs.strip():
            raise LogFormatError(f"--map wants OURS=THEIRS, like email=user_email (got '{item}')")
        mapping[ours.strip()] = theirs.strip()
    return mapping


def main(argv=None):
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
    scan.add_argument("--price-in", type=float, metavar="USD",
                      help="$ per 1M input tokens, to work out cost when your logs don't have it")
    scan.add_argument("--price-out", type=float, metavar="USD",
                      help="$ per 1M output tokens, to work out cost when your logs don't have it")

    gen = sub.add_parser("generate", help="make a fake log file with abuse planted in it (for demos/tests)")
    gen.add_argument("--out", default="sample-logs.csv", help="where to write the CSV")
    gen.add_argument("--users", type=int, default=200, help="number of normal users")
    gen.add_argument("--days", type=int, default=30, help="days of traffic")
    gen.add_argument("--seed", type=int, default=42, help="random seed, same seed = same file")

    args = parser.parse_args(argv)

    if args.command == "scan":
        try:
            mapping = parse_map(args.map)
            notes = []
            rows = load_logs(args.logs, mapping=mapping, price_in=args.price_in, price_out=args.price_out,
                             notes=notes)
        except (OSError, LogFormatError) as e:
            print(f"luxvigil: can't read {args.logs}: {e}", file=sys.stderr)
            return 1
        for note in notes:
            print(f"   note: {note}", file=sys.stderr)
        try:
            config = load_config(args.config)
        except (OSError, ConfigError) as e:
            print(f"luxvigil: bad config: {e}", file=sys.stderr)
            return 1
        report = build_report(rows, min_farm=args.min_farm, min_countries=args.min_countries, config=config)
        print("\n".join(render_text(report)))
        return 0

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
