"""Refresh the bundled disposable email list from upstream.

    python scripts/update_disposable.py

Pulls the latest list from github.com/disposable-email-domains (CC0) and rewrites
src/luxvigil/data/disposable_domains.txt. Only needed when cutting a release;
luxvigil itself never goes online.
"""

import json
import urllib.request
from pathlib import Path

REPO = "disposable-email-domains/disposable-email-domains"
LIST_URL = f"https://raw.githubusercontent.com/{REPO}/{{sha}}/disposable_email_blocklist.conf"
COMMIT_URL = f"https://api.github.com/repos/{REPO}/commits/main"
OUT = Path(__file__).resolve().parent.parent / "src" / "luxvigil" / "data" / "disposable_domains.txt"


def get(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read().decode("utf-8")


def main():
    commit = json.loads(get(COMMIT_URL))
    sha, date = commit["sha"], commit["commit"]["committer"]["date"][:10]
    domains = sorted({d.strip().lower() for d in get(LIST_URL.format(sha=sha)).splitlines() if d.strip()})
    header = [
        "# Disposable email domains, one per line.",
        f"# Source: https://github.com/{REPO} (commit {sha[:7]}, {date})",
        "# License: CC0 1.0 (public domain). Refresh with: python scripts/update_disposable.py",
    ]
    OUT.write_text("\n".join(header + domains) + "\n", encoding="utf-8")
    print(f"wrote {len(domains):,} domains to {OUT}")


if __name__ == "__main__":
    main()
