import argparse
import sys

from luxvigil import __version__
from luxvigil.checks import find_disposable_emails, find_farms
from luxvigil.generate import generate, write_csv
from luxvigil.schema import LogFormatError, load_logs


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

    gen = sub.add_parser("generate", help="make a fake log file with abuse planted in it (for demos/tests)")
    gen.add_argument("--out", default="sample-logs.csv", help="where to write the CSV")
    gen.add_argument("--users", type=int, default=200, help="number of normal users")
    gen.add_argument("--days", type=int, default=30, help="days of traffic")
    gen.add_argument("--seed", type=int, default=42, help="random seed, same seed = same file")

    args = parser.parse_args(argv)

    if args.command == "scan":
        try:
            rows = load_logs(args.logs)
        except (OSError, LogFormatError) as e:
            print(f"luxvigil: can't read {args.logs}: {e}", file=sys.stderr)
            return 1
        accounts = len({r.account_id for r in rows})
        print(f"🕯️ luxvigil: read {len(rows):,} requests from {accounts:,} accounts\n")

        farms = find_farms(rows, min_accounts=args.min_farm)
        if farms:
            total = sum(f.cost_usd for f in farms)
            n = sum(len(f.accounts) for f in farms)
            print(f"  🚨 {len(farms)} account farm{'s' if len(farms) > 1 else ''}: {n} accounts, ~${total:,.2f}")
            for f in farms:
                print(f"     - {f.why}  ·  {f.requests:,} requests  ·  ~${f.cost_usd:,.2f}")
        else:
            print("  ✅ no account farms")

        throwaway = find_disposable_emails(rows)
        if throwaway:
            print(f"  📧 {throwaway.why}")
            print(f"     {throwaway.requests:,} requests  ·  ~${throwaway.cost_usd:,.2f}")
        else:
            print("  ✅ no disposable emails")
        print("\n   (more checks coming)")
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
