"""Gmail aliases: lots of accounts that all land in the same Gmail inbox.

Gmail ignores dots and anything after a +, so pol@gmail.com, p.o.l@gmail.com
and pol+7@gmail.com are all the same inbox. Handy for people, and for anyone
who wants 50 free trials without making 50 Gmail accounts.
"""

from collections import defaultdict
from dataclasses import dataclass

GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


def gmail_inbox(email):
    """'P.o.l+7@googlemail.com' -> 'pol@gmail.com'. Not a Gmail address -> None."""
    if "@" not in email:
        return None
    user, domain = email.strip().lower().rsplit("@", 1)
    if domain not in GMAIL_DOMAINS:
        return None
    user = user.split("+", 1)[0].replace(".", "")
    if not user:
        return None
    return f"{user}@gmail.com"


@dataclass
class AliasGroup:
    inbox: str  # the real inbox, used for grouping, never printed
    accounts: list
    addresses: int  # how many different spellings of it were used
    requests: int = 0
    cost_usd: float = 0.0

    @property
    def why(self):
        if self.addresses == 1:
            how = "all with the exact same address"
        else:
            how = f"using {self.addresses} spellings of it (+tags or dots)"
        return f"{len(self.accounts)} accounts on the same Gmail inbox, {how}"


def find_gmail_aliases(requests, min_accounts=3):
    """Groups of min_accounts+ accounts whose emails all land in one Gmail inbox."""
    accounts_by_inbox = defaultdict(set)
    spellings = defaultdict(set)
    for r in requests:
        inbox = gmail_inbox(r.email)
        if inbox:
            accounts_by_inbox[inbox].add(r.account_id)
            spellings[inbox].add(r.email)

    groups = {}
    for inbox, accounts in accounts_by_inbox.items():
        if len(accounts) >= min_accounts:
            groups[inbox] = AliasGroup(inbox, sorted(accounts), len(spellings[inbox]))

    # add up what each group cost
    owner = {acc: g for g in groups.values() for acc in g.accounts}
    for r in requests:
        g = owner.get(r.account_id)
        if g:
            g.requests += 1
            g.cost_usd += r.cost_usd

    return sorted(groups.values(), key=lambda g: g.cost_usd, reverse=True)