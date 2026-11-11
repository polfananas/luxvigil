"""Runs every check and puts the results together into one report.

Two totals, never mixed up:

  likely abuse    strong signals that rarely have an innocent explanation
                  - farms and copy-paste bots: every request from those accounts
                  - shared keys: the requests from outside the key's home country
                  - gmail aliases: every request from accounts sharing one inbox

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
from luxvigil.checks.aliases import find_gmail_aliases
from luxvigil.checks.farms import suspect_accounts


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
    aliases: list = field(default_factory=list)
    abuse_cost: float = 0.0  # likely abuse, deduplicated
    review_cost: float = 0.0  # worth a look, not counted in abuse_cost
    flagged_accounts: set = field(default_factory=set)  # likely abuse
    review_accounts: set = field(default_factory=set)  # worth a look only
    ignored_accounts: int = 0  # left out because of luxvigil.toml
    config_path: str = ""
    skipped: dict = field(default_factory=dict)  # check -> why it couldn't run on these logs

    @property
    def abuse_share(self):
        return self.abuse_cost / self.total_cost if self.total_cost else 0.0

    @property
    def clean(self):
        return not (self.farms or self.emails or self.keys or self.bots or self.spikes or self.aliases)


def _skipped_checks(rows):
    """Which checks these logs can't feed, and why.

    A column that's missing or empty on every row looks the same to us: there's
    nothing to go on. Better to say "skipped" than to print "no disposable emails"
    for logs that never had an email in them.
    """
    if not rows:
        return {}

    def has(field):
        return any(getattr(r, field) for r in rows)

    skipped = {}
    if not (has("device_id") or has("ip")):
        skipped["account farms"] = "no devices or IPs in the logs"
    if not has("email"):
        skipped["disposable emails"] = "no emails in the logs"
        skipped["shared Gmail inboxes"] = "no emails in the logs"
    if not has("api_key_id"):
        skipped["shared or leaked keys"] = "no API keys in the logs"
    elif not has("country"):
        skipped["shared or leaked keys"] = "no countries in the logs"
    if not has("prompt_hash"):
        skipped["copy-paste bots"] = "no prompt hashes in the logs"
    return skipped


def build_report(rows, min_farm=5, min_countries=5, config=None):
    """Run every check on `rows` and work out the two totals.

    Accounts, keys or emails ignored in the config are left out of the checks,
    but still count towards the overall requests, accounts and spend.
    """
    all_rows = rows
    if config:
        rows = [r for r in rows if not config.ignores(r)]

    # 1. run the checks. Bots go first because farms need them to know which
    #    accounts already look off (see suspect_accounts in farms.py)
    bots = find_prompt_bots(rows)
    report = Report(
        requests=len(all_rows),
        accounts=len({r.account_id for r in all_rows}),
        total_cost=sum(r.cost_usd for r in all_rows),
        first_seen=min((r.timestamp for r in all_rows), default=None),
        last_seen=max((r.timestamp for r in all_rows), default=None),
        farms=find_farms(rows, min_accounts=min_farm, config=config, suspects=suspect_accounts(rows, bots)),
        emails=find_disposable_emails(rows),
        keys=find_shared_keys(rows, min_countries=min_countries),
        bots=bots,
        spikes=find_spikes(rows),
        aliases=find_gmail_aliases(rows),
    )

    # 2. sort accounts by how strong the evidence against them is
    strong = set()  # accounts whose whole traffic counts as likely abuse
    for farm in report.farms:
        strong.update(farm.accounts)
    for bot in report.bots:
        strong.update(bot.accounts)
    for group in report.aliases:
        strong.update(group.accounts)
    # a throwaway email only matters on its own if nothing stronger already caught the account
    weak = set(report.emails.accounts) - strong if report.emails else set()

    away = {k.key: k.home_country for k in report.keys}  # leaked key -> its home country
    key_accounts = {a for k in report.keys for a in k.accounts}

    # 3. walk every request once and put it in at most one bucket. Strong wins
    #    over weak, so nothing is ever counted twice
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

    # 4. spikes are per day, not per request: add only the extra over the usual
    #    that isn't already in a bucket. A leaked key usually shows up as a spike
    #    too, and its requests are counted above already, so the spike adds ~0
    spend_on_day = defaultdict(float)
    spike_accounts = {s.account for s in report.spikes}
    for r in rows:
        if r.account_id in spike_accounts:
            spend_on_day[(r.account_id, r.timestamp.date())] += r.cost_usd

    for s in report.spikes:
        for day in s.days:
            extra = spend_on_day[(s.account, day)] - s.usual_daily
            review += max(0.0, extra - counted_on_day[(s.account, day)])

    # 5. who ends up where. An account in both lists only shows as likely abuse
    report.abuse_cost = likely
    report.review_cost = review
    report.flagged_accounts = strong | key_accounts
    report.review_accounts = (weak | spike_accounts) - report.flagged_accounts
    report.ignored_accounts = report.accounts - len({r.account_id for r in rows})
    report.config_path = config.path if config else ""
    report.skipped = _skipped_checks(rows)
    return report


# on real logs a check can find hundreds of things. The terminal shows the most
# expensive ones (every list is already sorted by cost), --json has all of them
SHOW = 10


def _nothing(report, check):
    """The line for a check that found nothing: either all clear, or it never ran."""
    if check in report.skipped:
        return f"  ⏭️  {check}: skipped, {report.skipped[check]}"
    return f"  ✅ no {check}"


def _plural(n, word):
    """_plural(1, "farm") -> "1 farm", _plural(1500, "farm") -> "1,500 farms"."""
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _top(items, line):
    """One line per item for the first SHOW, then a single "...and N more"."""
    out = [line(x) for x in items[:SHOW]]
    if len(items) > SHOW:
        out.append(f"     …and {len(items) - SHOW:,} more, run with --json to see them all")
    return out


def _spikes_by_account(spikes):
    """[spike, ...] -> [[spikes of one account], ...], most expensive account first.

    One account can spike several times (on WildChat some did 5+ times), and a
    line per spike buries everyone else. So it's one line per account instead.
    """
    groups = {}
    for s in spikes:  # already sorted by cost, so each account's worst spike comes first
        groups.setdefault(s.account, []).append(s)
    return sorted(groups.values(), key=lambda g: sum(s.cost_usd for s in g), reverse=True)


def _spike_line(spikes):
    """The account's worst spike, plus how many more it had, with the totals of all of them."""
    worst, more = spikes[0], len(spikes) - 1
    also = f" (+{more} more spike{'s' if more > 1 else ''}, last on {max(s.last_day for s in spikes)})" if more else ""
    requests = sum(s.requests for s in spikes)
    extra = sum(s.cost_usd for s in spikes)
    return f"     - {worst.why}{also}  ·  {requests:,} requests  ·  ~${extra:,.2f} extra"


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
        # "nothing found" means less if some checks couldn't look
        out += [_nothing(report, check) for check in report.skipped]
        return out

    if report.abuse_cost or report.flagged_accounts:
        out.append(
            f"  💰 ~${report.abuse_cost:,.2f} is likely abuse ({report.abuse_share:.0%} of your spend), "
            f"{_plural(len(report.flagged_accounts), 'account')}"
        )
    else:
        out.append("  💰 nothing that looks like clear abuse")
    if report.review_accounts:
        out.append(
            f"  🔎 ~${report.review_cost:,.2f} more is worth a look, {_plural(len(report.review_accounts), 'account')} "
            "(signals that can have an innocent explanation)"
        )
    out.append("")

    if report.farms:
        n = sum(len(f.accounts) for f in report.farms)
        out.append(f"  🚨 {_plural(len(report.farms), 'account farm')}, {n:,} accounts")
        out += _top(report.farms, lambda f: f"     - {f.why}  ·  {f.requests:,} requests  ·  ~${f.cost_usd:,.2f}")
    else:
        out.append(_nothing(report, "account farms"))

    if report.emails:
        e = report.emails
        out.append(f"  📧 {e.why}")
        out.append(f"     {e.requests:,} requests  ·  ~${e.cost_usd:,.2f}")
        alone = len(set(e.accounts) & report.review_accounts)
        if alone:
            out.append(f"     {_plural(alone, 'account')} with no other red flag, could just be people testing")
    else:
        out.append(_nothing(report, "disposable emails"))

    if report.keys:
        out.append(f"  🔑 {_plural(len(report.keys), 'key')} probably shared or leaked")
        out += _top(report.keys, lambda k: f"     - {k.why}  ·  {k.requests:,} requests from abroad  ·  ~${k.cost_usd:,.2f}")
    else:
        out.append(_nothing(report, "shared or leaked keys"))

    if report.bots:
        n = sum(len(b.accounts) for b in report.bots)
        out.append(f"  🤖 {_plural(len(report.bots), 'copy-paste bot')}, {n:,} accounts")
        out += _top(report.bots, lambda b: f"     - {b.why}  ·  {b.requests:,} requests  ·  ~${b.cost_usd:,.2f}")
    else:
        out.append(_nothing(report, "copy-paste bots"))

    if report.aliases:
        n = sum(len(g.accounts) for g in report.aliases)
        inboxes = len(report.aliases)
        out.append(f"  📮 {inboxes:,} shared Gmail inbox{'es' if inboxes > 1 else ''}, {n:,} accounts")
        out += _top(report.aliases, lambda g: f"     - {g.why}  ·  {g.requests:,} requests  ·  ~${g.cost_usd:,.2f}")
    else:
        out.append(_nothing(report, "shared Gmail inboxes"))

    if report.spikes:
        by_account = _spikes_by_account(report.spikes)
        on = f" on {_plural(len(by_account), 'account')}" if len(by_account) < len(report.spikes) else ""
        out.append(f"  💸 {_plural(len(report.spikes), 'spend spike')}{on} (could be a launch or a batch job, check first)")
        out += _top(by_account, _spike_line)
    else:
        out.append("  ✅ no spend spikes")

    out.append("")
    out.append("  The same account can show up in more than one check. The totals at the top count it once.")
    return out