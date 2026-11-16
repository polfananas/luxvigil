"""Runs every check and puts the results together into one report.

Two totals, never mixed up:

  likely abuse    strong signals that rarely have an innocent explanation
                  - account farms: every request from the accounts on a device or
                    network shared by min_farm or more
                  - shared keys: the requests from a known country other than the key's home
                  - copy-paste bots: the requests that sent the repeated prompts

  worth a look    weak signals that often do have one
                  - a throwaway email (devs testing, privacy nerds)
                  - accounts on one Gmail inbox (someone with a test account)
                  - chained farms: accounts linked only by small shares in a chain
                  - the rest of what copy-paste bot accounts did
                  - spend spikes (a launch, a batch job, a big customer)

Every request is counted once, in the stronger bucket it belongs to, so an
account can have some requests in each. Spikes only add the extra over the
usual, minus whatever from that day is already counted.

Neither total is money you lost. It's what the requests behind the signals
cost, and someone still has to look at them to say whether it was abuse.

When the logs have no cost for some requests, those count as unknown, not as
free: the dollar totals only cover the requests with a known cost, and the
report says how many that is.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from luxvigil.checks import find_disposable_emails, find_farms, find_prompt_bots, find_shared_keys, find_spikes
from luxvigil.checks.aliases import find_gmail_aliases
from luxvigil.checks.farms import suspect_accounts
from luxvigil.checks.keys import is_away

# the checks, in the order the report shows them
CHECKS = ["account farms", "disposable emails", "shared or leaked keys", "copy-paste bots",
          "shared Gmail inboxes", "spend spikes"]


@dataclass
class Report:
    requests: int
    accounts: int
    total_cost: float  # of the requests with a known cost
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
    flagged_accounts: set = field(default_factory=set)  # accounts with requests in likely abuse
    review_accounts: set = field(default_factory=set)  # accounts with something worth a look (can overlap)
    email_only_accounts: set = field(default_factory=set)  # throwaway email and no other finding
    ignored_accounts: int = 0  # left out because of luxvigil.toml
    config_path: str = ""
    skipped: dict = field(default_factory=dict)  # check -> why it couldn't run on these logs
    coverage: dict = field(default_factory=dict)  # check -> requests it had data for
    checked_requests: int = 0  # requests the checks looked at (after luxvigil.toml)
    known_cost_requests: int | None = None  # requests with a cost (in the logs or from prices), None = all
    settings: dict = field(default_factory=dict)  # thresholds used, so two runs can be compared

    @property
    def abuse_share(self):
        return self.abuse_cost / self.total_cost if self.total_cost else 0.0

    @property
    def has_costs(self):
        return self.known_cost_requests is None or self.known_cost_requests > 0

    @property
    def all_costs_known(self):
        return self.known_cost_requests is None or self.known_cost_requests == self.requests

    @property
    def clean(self):
        return not (self.farms or self.emails or self.keys or self.bots or self.spikes or self.aliases)


def _coverage(rows):
    """How many requests each check had something to go on for.

    Not a measure of how much abuse was caught, just of how much of the logs
    each check could look at: a country on 10 rows out of a million is not a
    check of a million requests.
    """
    return {
        "account farms": sum(1 for r in rows if r.device_id or r.ip),
        "disposable emails": sum(1 for r in rows if r.email),
        "shared or leaked keys": sum(1 for r in rows if r.api_key_id and r.country),
        "copy-paste bots": sum(1 for r in rows if r.prompt_hash),
        "shared Gmail inboxes": sum(1 for r in rows if r.email),
        "spend spikes": sum(1 for r in rows if r.cost_known),
    }


def _skipped_checks(rows, coverage, suspects):
    """Which checks these logs can't feed, and why.

    Better to say "skipped" than to print "no disposable emails" for logs that
    never had an email in them.
    """
    if not rows:
        return {}
    skipped = {}
    if not coverage["account farms"]:
        skipped["account farms"] = "no devices or IPs in the logs"
    elif not any(r.device_id for r in rows) and not suspects:
        # IPs alone never link anyone, see farms.py: only accounts that already look off
        skipped["account farms"] = ("no devices in the logs, and IPs only link accounts that already "
                                    "look off (none did)")
    if not coverage["disposable emails"]:
        skipped["disposable emails"] = "no emails in the logs"
        skipped["shared Gmail inboxes"] = "no emails in the logs"
    if not any(r.api_key_id for r in rows):
        skipped["shared or leaked keys"] = "no API keys in the logs"
    elif not coverage["shared or leaked keys"]:
        skipped["shared or leaked keys"] = "no countries in the logs"
    if not coverage["copy-paste bots"]:
        skipped["copy-paste bots"] = "no prompt hashes in the logs"
    if not coverage["spend spikes"]:
        skipped["spend spikes"] = "no costs in the logs"
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
    suspects = suspect_accounts(rows, bots)
    report = Report(
        requests=len(all_rows),
        accounts=len({r.account_id for r in all_rows}),
        total_cost=sum(r.cost_usd for r in all_rows),
        first_seen=min((r.timestamp for r in all_rows), default=None),
        last_seen=max((r.timestamp for r in all_rows), default=None),
        farms=find_farms(rows, min_accounts=min_farm, config=config, suspects=suspects),
        emails=find_disposable_emails(rows),
        keys=find_shared_keys(rows, min_countries=min_countries),
        bots=bots,
        spikes=find_spikes(rows),
        aliases=find_gmail_aliases(rows),
    )

    # 2. sort accounts by how strong the evidence against them is
    # whole traffic is likely abuse, but only for direct farms (see farms.py)
    farm_accounts = {a for farm in report.farms if not farm.chained for a in farm.accounts}
    bot_of = {a: bot for bot in report.bots for a in bot.accounts}  # the repeated prompts are likely abuse
    # whole traffic worth a look: a throwaway email, a shared inbox, or whatever
    # a bot account sent besides the repeated prompts
    weak = set(report.emails.accounts) if report.emails else set()
    weak |= {a for g in report.aliases for a in g.accounts}
    weak |= {a for farm in report.farms if farm.chained for a in farm.accounts}
    weak |= set(bot_of)

    away = {k.key: k.home_country for k in report.keys}  # leaked key -> its home country

    def is_likely(r):
        if r.account_id in farm_accounts:
            return True
        home = away.get(r.api_key_id)
        if home is not None and is_away(r, home):  # the same test the key check uses
            return True
        bot = bot_of.get(r.account_id)
        return bot is not None and bot.is_signal(r)

    # 3. walk every request once and put it in at most one bucket. Strong wins
    #    over weak, so nothing is ever counted twice
    likely = review = 0.0
    flagged, in_review = set(), set()  # accounts with requests (or spike $) in each bucket
    counted_on_day = defaultdict(float)  # (account, day) -> already counted, either bucket
    for r in rows:
        day = (r.account_id, r.timestamp.date())
        if is_likely(r):
            likely += r.cost_usd
            flagged.add(r.account_id)
            counted_on_day[day] += r.cost_usd
        elif r.account_id in weak:
            review += r.cost_usd
            in_review.add(r.account_id)
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
            left = max(0.0, extra - counted_on_day[(s.account, day)])
            review += left
            if left > 0 or s.account not in flagged:
                in_review.add(s.account)

    # 5. who ends up where. An account can be in both: say a leaked key's requests
    #    from abroad are likely abuse, and its owner's own spike is worth a look
    report.abuse_cost = likely
    report.review_cost = review
    report.flagged_accounts = flagged
    report.review_accounts = in_review
    if report.emails:
        others = flagged | spike_accounts | {a for g in report.aliases for a in g.accounts}
        report.email_only_accounts = set(report.emails.accounts) - others
    report.ignored_accounts = report.accounts - len({r.account_id for r in rows})
    report.config_path = config.path if config else ""
    report.checked_requests = len(rows)
    report.known_cost_requests = sum(1 for r in all_rows if r.cost_known)
    report.coverage = _coverage(rows)
    report.skipped = _skipped_checks(rows, report.coverage, suspects)
    report.settings = {"min_farm": min_farm, "min_countries": min_countries}
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


def _usd(report, amount, before="  ·  ~"):
    """A dollar amount for a finding line, or nothing at all when the logs have no costs."""
    return f"{before}${amount:,.2f}" if report.has_costs else ""


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


def _bot_line(report, b):
    line = f"     - {b.why}  ·  {b.signal_requests:,} repeated requests{_usd(report, b.signal_cost_usd)}"
    if b.requests > b.signal_requests:
        rest = _usd(report, b.cost_usd - b.signal_cost_usd, before=", ~")
        line += f" (+{b.requests - b.signal_requests:,} other requests{rest}, worth a look)"
    return line


def _printable(line):
    """Ids come from your logs, so a control character in one could mess with the terminal."""
    return line if line.isprintable() else "".join(c if c.isprintable() else "?" for c in line)


def _summary(report):
    """The lines with the two totals at the top."""
    out = []
    n_flagged, n_review = len(report.flagged_accounts), len(report.review_accounts)
    both = len(report.flagged_accounts & report.review_accounts)
    if not report.has_costs:
        out.append(f"  💰 no dollar amounts, the logs have no cost. {_plural(n_flagged, 'account')} with strong "
                   f"signals, {_plural(n_review, 'account')} worth a look")
        return out
    at_least = "" if report.all_costs_known else "at least "
    if report.abuse_cost or n_flagged:
        share = f"{report.abuse_share:.0%} of your spend"
        out.append(f"  💰 {at_least}~${report.abuse_cost:,.2f} is likely abuse ({share}), {_plural(n_flagged, 'account')}")
    else:
        out.append("  💰 nothing that looks like clear abuse")
    # every dollar worth a look gets shown, even when all its accounts also have likely abuse
    if report.review_cost or n_review:
        overlap = f", {both:,} of them also in likely abuse" if both else ""
        out.append(f"  🔎 {at_least}~${report.review_cost:,.2f} more is worth a look, "
                   f"{_plural(n_review, 'account')}{overlap} (signals that can have an innocent explanation)")
    return out


def _coverage_lines(report):
    """Checks that ran, but only on part of the logs."""
    out = []
    total = report.checked_requests
    for check in CHECKS:
        n = report.coverage.get(check, total)
        if check not in report.skipped and 0 < n < total:
            out.append(f"  ◐ {check} only had data for {n / total:.0%} of requests ({n:,} of {total:,})")
    return out


def render_text(report):
    """The terminal version of the report, as a list of lines."""
    return [_printable(line) for line in _render(report)]


def _render(report):
    out = []
    period = ""
    if report.first_seen:
        start, end = report.first_seen.date(), report.last_seen.date()
        period = f" · {start}" if start == end else f" · {start} to {end} (UTC)"
    out.append(f"🕯️ luxvigil{period}")
    out.append("")
    if not report.requests:
        spent = "nothing to scan"
    elif not report.has_costs:
        spent = "no costs in the logs"
    elif report.all_costs_known:
        spent = f"${report.total_cost:,.2f} spent"
    else:
        spent = (f"${report.total_cost:,.2f} spent on the {report.known_cost_requests:,} requests with a cost, "
                 f"{report.requests - report.known_cost_requests:,} unknown")
    out.append(f"  {_plural(report.requests, 'request')} · {_plural(report.accounts, 'account')} · {spent}")
    if report.config_path:
        skipped = f", skipping {_plural(report.ignored_accounts, 'account')}" if report.ignored_accounts else ""
        out.append(f"  ⚙️  using {report.config_path}{skipped}")

    if report.clean:
        out.append("  ✅ nothing suspicious found")
        # "nothing found" means less if some checks couldn't look, or only looked at part of the logs
        out += [_nothing(report, check) for check in report.skipped]
        out += _coverage_lines(report)
        return out

    out += _summary(report)
    out.append("")

    def farm_line(f):
        return f"     - {f.why}  ·  {f.requests:,} requests{_usd(report, f.cost_usd)}"

    direct = [f for f in report.farms if not f.chained]
    chained = [f for f in report.farms if f.chained]
    if direct:
        n = sum(len(f.accounts) for f in direct)
        out.append(f"  🚨 {_plural(len(direct), 'account farm')}, {n:,} accounts")
        out += _top(direct, farm_line)
    else:
        out.append(_nothing(report, "account farms"))
    if chained:
        n = sum(len(f.accounts) for f in chained)
        out.append(f"  🔗 {_plural(len(chained), 'chain')} of accounts sharing devices or networks, {n:,} accounts "
                   "(no single one shared by many, worth a look)")
        out += _top(chained, farm_line)

    if report.emails:
        e = report.emails
        out.append(f"  📧 {e.why}")
        out.append(f"     {e.requests:,} requests{_usd(report, e.cost_usd)}")
        alone = len(report.email_only_accounts)
        if alone:
            out.append(f"     {_plural(alone, 'account')} with no other red flag, could just be people testing")
    else:
        out.append(_nothing(report, "disposable emails"))

    if report.keys:
        out.append(f"  🔑 {_plural(len(report.keys), 'key')} used from many countries at once (shared or leaked?)")
        out += _top(report.keys, lambda k: f"     - {k.why}  ·  {k.requests:,} requests from abroad"
                                           f"{_usd(report, k.cost_usd)}")
    else:
        out.append(_nothing(report, "shared or leaked keys"))

    if report.bots:
        n = sum(len(b.accounts) for b in report.bots)
        out.append(f"  🤖 {_plural(len(report.bots), 'copy-paste bot')}, {n:,} accounts")
        out += _top(report.bots, lambda b: _bot_line(report, b))
    else:
        out.append(_nothing(report, "copy-paste bots"))

    if report.aliases:
        n = sum(len(g.accounts) for g in report.aliases)
        inboxes = len(report.aliases)
        out.append(f"  📮 {inboxes:,} shared Gmail inbox{'es' if inboxes > 1 else ''}, {n:,} accounts "
                   "(related accounts, worth a look)")
        out += _top(report.aliases, lambda g: f"     - {g.why}  ·  {g.requests:,} requests{_usd(report, g.cost_usd)}")
    else:
        out.append(_nothing(report, "shared Gmail inboxes"))

    if report.spikes:
        by_account = _spikes_by_account(report.spikes)
        on = f" on {_plural(len(by_account), 'account')}" if len(by_account) < len(report.spikes) else ""
        out.append(f"  💸 {_plural(len(report.spikes), 'spend spike')}{on} (could be a launch or a batch job, check first)")
        out += _top(by_account, _spike_line)
    else:
        out.append(_nothing(report, "spend spikes"))

    coverage = _coverage_lines(report)
    if coverage:
        out.append("")
        out += coverage

    out.append("")
    out.append("  The same account can show up in more than one check. The totals at the top count it once.")
    if report.has_costs:
        out.append("  The $ is what the flagged requests cost, not a confirmed loss: check before you act.")
    return out
