"""Runs every check and puts the results together into one report.

Two totals, never mixed up:

  likely abuse    strong signals that rarely have an innocent explanation
                  - farms and copy-paste bots: every request from those accounts
                  - shared keys: the requests from outside the key's home country

  worth a look    weak signals that often do have one
                  - a throwaway email on its own (devs testing, privacy nerds)
                  - spend spikes (a launch, a batch job, a big customer)
                  An account with a weak signal that also trips a strong one
                  just counts as likely abuse.

The same account often trips several checks, so every request is counted
once, in the stronger bucket it belongs to. Spikes only add the extra over the
usual, minus whatever from that day is already counted.
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
    abuse_cost: float = 0.0  # likely abuse, deduplicated
    review_cost: float = 0.0  # worth a look, not counted in abuse_cost
    flagged_accounts: set = field(default_factory=set)  # likely abuse
    review_accounts: set = field(default_factory=set)  # worth a look only
    ignored_accounts: int = 0  # left out because of luxvigil.toml
    config_path: str = ""

    @property
    def abuse_share(self):
        return self.abuse_cost / self.total_cost if self.total_cost else 0.0

    @property
    def clean(self):
        return not (self.farms or self.emails or self.keys or self.bots or self.spikes)


def build_report(rows, min_farm=5, min_countries=5, config=None):
    all_rows = rows
    if config:
        rows = [r for r in rows if not config.ignores(r)]

    report = Report(
        requests=len(all_rows),
        accounts=len({r.account_id for r in all_rows}),
        total_cost=sum(r.cost_usd for r in all_rows),
        first_seen=min((r.timestamp for r in all_rows), default=None),
        last_seen=max((r.timestamp for r in all_rows), default=None),
        farms=find_farms(rows, min_accounts=min_farm, config=config),
        emails=find_disposable_emails(rows),
        keys=find_shared_keys(rows, min_countries=min_countries),
        bots=find_prompt_bots(rows),
        spikes=find_spikes(rows),
    )

    strong = set()  # accounts whose whole traffic counts as likely abuse
    for farm in report.farms:
        strong.update(farm.accounts)
    for bot in report.bots:
        strong.update(bot.accounts)
    weak = set(report.emails.accounts) - strong if report.emails else set()

    away = {k.key: k.home_country for k in report.keys}
    key_accounts = {a for k in report.keys for a in k.accounts}

    likely = review = 0.0
    counted_on_day = defaultdict(float)  # (account, day) -> already counted, either bucket
    for r in rows:
        home = away.get(r.api_key_id)
        day = (r.account_id, r.timestamp.date())
        if r.account_id in strong or (home is not None and r.country != home):
            likely += r.cost_usd
            counted_on_day[day] += r.cost_usd
        elif r.account_id in weak:
            review += r.cost_usd
            counted_on_day[day] += r.cost_usd

    spend_on_day = defaultdict(float)
    spike_accounts = {s.account for s in report.spikes}
    for r in rows:
        if r.account_id in spike_accounts:
            spend_on_day[(r.account_id, r.timestamp.date())] += r.cost_usd

    for s in report.spikes:
        for day in s.days:
            extra = spend_on_day[(s.account, day)] - s.usual_daily
            review += max(0.0, extra - counted_on_day[(s.account, day)])

    report.abuse_cost = likely
    report.review_cost = review
    report.flagged_accounts = strong | key_accounts
    report.review_accounts = (weak | spike_accounts) - report.flagged_accounts
    report.ignored_accounts = report.accounts - len({r.account_id for r in rows})
    report.config_path = config.path if config else ""
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
    if report.config_path:
        skipped = f", skipping {_plural(report.ignored_accounts, 'account')}" if report.ignored_accounts else ""
        out.append(f"  ⚙️  using {report.config_path}{skipped}")

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
