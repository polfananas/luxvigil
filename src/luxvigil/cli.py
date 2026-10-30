import argparse
import sys

from luxvigil import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="luxvigil",
        description="Find out who's been eating your free AI credits.",
    )
    parser.add_argument("--version", action="version", version=f"luxvigil {__version__}")
    sub = parser.add_subparsers(dest="command")

    scan = sub.add_parser("scan", help="scan a CSV of API request logs for abuse")
    scan.add_argument("logs", help="path to the CSV log file")

    args = parser.parse_args(argv)

    if args.command == "scan":
        print("🕯️ luxvigil: scan isn't built yet, coming soon")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
