"""Disposable emails: accounts that signed up with mailinator & friends.

One throwaway signup isn't a crime, but a real customer almost never uses one.
If a chunk of your accounts do, and they're burning credits, that's your free tier
getting farmed.

The domain list ships with luxvigil (from github.com/disposable-email-domains,
public domain), so this runs fully offline. Subdomains count too:
x.mailinator.com is still mailinator.

On its own this is a weak signal: devs testing your product and privacy-minded
people use these too. That's why an account with only a throwaway email lands
in "worth a look", and only counts as likely abuse if a stronger check agrees.

Limits worth knowing:
  - the list goes stale, new throwaway domains pop up every week
    (refresh it with scripts/update_disposable.py before a release)
  - someone with their own domain and a catch-all inbox looks perfectly normal
"""

from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
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
        """The one-liner that shows up in the report, with the 3 most used domains."""
        top = ", ".join(f"{d} ({n})" for d, n in self.domains.most_common(3))
        return f"{len(self.accounts):,} of {self.total_accounts:,} accounts ({self.share:.0%}) use throwaway emails: {top}"


# ~10k domains: read the file once per run, not once per check that needs it
@lru_cache(maxsize=1)
def load_disposable_domains():
    """The bundled list of disposable domains, skipping blank lines and # comments."""
    text = resources.files("luxvigil.data").joinpath("disposable_domains.txt").read_text(encoding="utf-8")
    return frozenset(line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#"))


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

    # one email per account is enough: the first one we see
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
    # add up everything those accounts spent
    for r in requests:
        if r.account_id in flagged:
            report.requests += 1
            report.cost_usd += r.cost_usd
    return report