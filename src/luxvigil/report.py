"""Runs every check and puts the results together into one report.

The tricky part is the total. The same account often trips several checks
(a farm account usually also has a throwaway email and sends template prompts),
so adding up each check's cost would count it two or three times.
Instead we mark each abusive request once and add those up:

  - farms, throwaway emails, copy-paste bots: every request from those accounts
  - shared keys: the requests from outside the key's home country
  - spend spikes: the extra over the usual on spike days, minus whatever
    from that same day is already counted by another check
"""

from collections import defaultdict
from dataclasses import dataclass, field

from luxvigil.checks import find_disposable_emails, find_farms, find_prompt_bots, find_shared_keys, find_spikes


@dataclass
class Report:
    requests: int
    accounts: int
    total_cost: float
    first_seen: object
    last_seen: object
    farms: list = field(default_factory=list)
    emails: object = None
    keys: list = field(default_factory=list)
    bots: list = field(default_factory=list)
    spikes: list = field(default_factory=list)
    abuse_cost: float = 0.0  # deduplicated
    flagged_accounts: set = field(default_factory=set)

    @property
    def abuse_share(self):
        return self.abuse_cost / self.total_cost if self.total_cost else 0.0

    @property
    def clean(self):
        return not (self.farms or self.emails or self.keys or self.bots or self.spikes)


def build_report(rows, min_farm=5, min_countries=5):
    report = Report(
        requests=len(rows),
        accounts=len({r.account_id for r in rows}),
        total_cost=sum(r.cost_usd for r in rows),
        first_seen=min((r.timestamp for r in rows), default=None),
        last_seen=max((r.timestamp for r in rows), default=None),
        farms=find_farms(rows, min_accounts=min_farm),
        emails=find_disposable_emails(rows),
        keys=find_shared_keys(rows, min_countries=min_countries),
        bots=find_prompt_bots(rows),
        spikes=find_spikes(rows),
    )

    # accounts whose whole traffic counts as abuse
    whole = set()
    for farm in report.farms:
        whole.update(farm.accounts)
    for bot in report.bots:
        whole.update(bot.accounts)
    if report.emails:
        whole.update(report.emails.accounts)

    away = {k.key: k.home_country for k in report.keys}

    counted = 0.0
    counted_on_day = defaultdict(float)  # (account, day) -> already counted
    for r in rows:
        home = away.get(r.api_key_id)
        if r.account_id in whole or (home is not None and r.country != home):
            counted += r.cost_usd
            counted_on_day[(r.account_id, r.timestamp.date())] += r.cost_usd

    spend_on_day = defaultdict(float)
    spike_accounts = {s.account for s in report.spikes}
    for r in rows:
        if r.account_id in spike_accounts:
            spend_on_day[(r.account_id, r.timestamp.date())] += r.cost_usd

    for s in report.spikes:
        for day in s.days:
            extra = spend_on_day[(s.account, day)] - s.usual_daily
            counted += max(0.0, extra - counted_on_day[(s.account, day)])

    report.abuse_cost = counted
    report.flagged_accounts = (
        whole
        | {a for k in report.keys for a in k.accounts}
        | spike_accounts
    )
    return report


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def render_text(report):
    """The terminal version of the report, as a list of lines."""
    out = []
    period = ""
    if report.first_seen:
        start, end = report.first_seen.date(), report.last_seen.date()
        period = f" · {start}" if start == end else f" · {start} to {end}"
    out.append(f"🕯️ luxvigil{period}")
    out.append("")
    out.append(
        f"  {report.requests:,} requests · {report.accounts:,} accounts · ${report.total_cost:,.2f} spent"
    )

    if report.clean:
        out.append("  ✅ nothing suspicious found")
        return out

    out.append(
        f"  💰 ~${report.abuse_cost:,.2f} looks like abuse ({report.abuse_share:.0%} of your spend), "
        f"{_plural(len(report.flagged_accounts), 'account')} involved"
    )
    out.append("")

    if report.farms:
        n = sum(len(f.accounts) for f in report.farms)
        out.append(f"  🚨 {_plural(len(report.farms), 'account farm')}, {n} accounts")
        for f in report.farms:
            out.append(f"     - {f.why}  ·  {f.requests:,} requests  ·  ~${f.cost_usd:,.2f}")
    else:
        out.append("  ✅ no account farms")

    if report.emails:
        e = report.emails
        out.append(f"  📧 {e.why}")
        out.append(f"     {e.requests:,} requests  ·  ~${e.cost_usd:,.2f}")
    else:
        out.append("  ✅ no disposable emails")

    if report.keys:
        out.append(f"  🔑 {_plural(len(report.keys), 'key')} probably shared or leaked")
        for k in report.keys:
            out.append(f"     - {k.why}  ·  {k.requests:,} requests from abroad  ·  ~${k.cost_usd:,.2f}")
    else:
        out.append("  ✅ no shared or leaked keys")

    if report.bots:
        n = sum(len(b.accounts) for b in report.bots)
        out.append(f"  🤖 {_plural(len(report.bots), 'copy-paste bot')}, {n} accounts")
        for b in report.bots:
            out.append(f"     - {b.why}  ·  {b.requests:,} requests  ·  ~${b.cost_usd:,.2f}")
    else:
        out.append("  ✅ no copy-paste bots")

    if report.spikes:
        out.append(f"  💸 {_plural(len(report.spikes), 'spend spike')}")
        for s in report.spikes:
            out.append(f"     - {s.why}  ·  {s.requests:,} requests  ·  ~${s.cost_usd:,.2f} extra")
    else:
        out.append("  ✅ no spend spikes")

    out.append("")
    out.append("  The same account can show up in more than one check. The total at the top counts it once.")
    return out
