"""Disposable emails: accounts that signed up with mailinator & friends.

One throwaway signup isn't a crime, but a real customer almost never uses one.
If a chunk of your accounts do, and they're burning credits, that's your free tier
getting farmed.

The domain list ships with luxvigil (from github.com/disposable-email-domains,
public domain), so this runs fully offline. Subdomains count too:
x.mailinator.com is still mailinator.
"""

from collections import Counter
from dataclasses import dataclass, field
from importlib import resources


@dataclass
class DisposableEmails:
    accounts: list[str]  # accounts that used a disposable domain
    total_accounts: int  # all accounts in the logs, to get a %
    domains: Counter = field(default_factory=Counter)  # domain -> how many accounts
    requests: int = 0
    cost_usd: float = 0.0

    @property
    def share(self):
        return len(self.accounts) / self.total_accounts if self.total_accounts else 0.0

    @property
    def why(self):
        top = ", ".join(f"{d} ({n})" for d, n in self.domains.most_common(3))
        return f"{len(self.accounts)} of {self.total_accounts} accounts ({self.share:.0%}) use throwaway emails: {top}"


def load_disposable_domains():
    text = resources.files("luxvigil.data").joinpath("disposable_domains.txt").read_text(encoding="utf-8")
    return {line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")}


def disposable_domain(email, domains):
    """Return the matching disposable domain for `email`, or None."""
    if "@" not in email:
        return None
    host = email.rsplit("@", 1)[1].strip().lower().rstrip(".")
    parts = host.split(".")
    for i in range(len(parts) - 1):  # a.b.mailinator.com -> b.mailinator.com -> mailinator.com
        candidate = ".".join(parts[i:])
        if candidate in domains:
            return candidate
    return None


def find_disposable_emails(requests, domains=None):
    """Return a DisposableEmails report, or None if nobody used one."""
    domains = load_disposable_domains() if domains is None else domains

    account_domain = {}
    all_accounts = set()
    for r in requests:
        all_accounts.add(r.account_id)
        if r.account_id not in account_domain:
            account_domain[r.account_id] = disposable_domain(r.email, domains)

    flagged = {acc: d for acc, d in account_domain.items() if d}
    if not flagged:
        return None

    report = DisposableEmails(
        accounts=sorted(flagged),
        total_accounts=len(all_accounts),
        domains=Counter(flagged.values()),
    )
    for r in requests:
        if r.account_id in flagged:
            report.requests += 1
            report.cost_usd += r.cost_usd
    return report
