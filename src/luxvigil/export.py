"""The report as plain data, for `luxvigil scan --json`.

Each finding says what was found, why it's suspicious, what could explain it
innocently, and what to check next. Finding ids are stable: the same logs
always give the same ids, so you can diff two runs or track a finding.

No raw emails or prompts end up here, only account ids, key ids, email
domains and prompt hashes, which are already in your logs. (If your account
ids are emails, they're still account ids to us, so they do show up.)

Every finding says what its cost_usd covers (cost_basis), because it's not
the same thing everywhere: a farm is linked accounts, so it's all they spent;
a leaked key is only the requests from abroad. None of it is a confirmed loss.
When the logs have no costs at all, every dollar amount is null, not 0.

Format 2 (luxvigil 0.2.0) changed from 1: dollar amounts can be null, Gmail
inboxes are "review", a bot's cost_usd is only its repeated prompts, and
there's coverage, settings and cost_basis.
"""

import hashlib

from luxvigil import __version__

# Bump this if the shape of the json changes, so scripts reading it can tell.
FORMAT_VERSION = 2

# For every check: the most likely innocent reason it fired. Every finding
# carries one, so nobody bans a customer without thinking twice.
INNOCENT = {
    "account_farm": "a shared office machine or test devices used by your own team",
    "disposable_emails": "developers or privacy-minded people trying the product",
    "shared_key": "a customer running the same key from servers in several regions",
    "copy_paste_bot": "a legit integration sending a fixed template for its users",
    "spend_spike": "a launch, a batch job or a customer that just started using you for real",
    "gmail_aliases": "one person with a couple of test accounts, or a small team sharing an inbox",
}

# For every check: what an analyst should look at next to confirm or rule it out.
NEXT = {
    "account_farm": "check signup dates and payment methods of these accounts, real users rarely share a device",
    "disposable_emails": "see if these accounts also show up in a stronger finding before doing anything",
    "shared_key": "ask the key owner if they use it from these countries, if not rotate it",
    "copy_paste_bot": "look at the prompt behind the hash and when these accounts signed up",
    "spend_spike": "ask the account owner, or check what the requests on those days were",
    "gmail_aliases": "check if these accounts were used one after another to stretch the free credits",
}


def _id(check, *parts):
    """A short id built from the check name and what makes the finding unique
    (its accounts, its key...). Same input, same id, every run."""
    digest = hashlib.sha256("|".join([check, *map(str, parts)]).encode()).hexdigest()
    return f"{check}-{digest[:10]}"


# what each finding's cost_usd is the cost of
BASIS = {
    "account_farm": "all_requests_of_linked_accounts",
    "disposable_emails": "all_requests_of_these_accounts",
    "shared_key": "requests_from_outside_home_country",
    "copy_paste_bot": "requests_with_the_repeated_prompts",
    "spend_spike": "extra_over_usual_daily_spend",
    "gmail_aliases": "all_requests_of_linked_accounts",
}


def _money(x, known=True):
    # 4 decimals is plenty for dollars and keeps float noise out of the json.
    # No costs in the logs: null, so nobody reads it as "free"
    return round(x, 4) if known else None


def _date(t):
    return t.isoformat() if t else None


def _finding(check, severity, summary, accounts, requests, cost, evidence, *id_parts, known=True):
    """Every finding has the same shape, whatever check it comes from."""
    return {
        "id": _id(check, *id_parts),
        "check": check,
        "severity": severity,  # "likely" (strong signal) or "review" (could be innocent)
        "summary": summary,  # the same one-liner as in the terminal report
        "accounts": sorted(accounts),
        "requests": requests,
        "cost_usd": _money(cost, known),
        "cost_basis": BASIS[check],  # what cost_usd and requests cover
        "evidence": evidence,  # check-specific details, see each block below
        "innocent_explanation": INNOCENT[check],
        "next_step": NEXT[check],
    }


def to_dict(report, notes=()):
    """Turn a Report into plain dicts and lists, ready for json.dumps."""
    findings = []
    known = report.has_costs

    # strong signals first: these count towards "likely abuse"
    for f in report.farms:
        # direct: one device or network with min_farm accounts on it. Chained: only small
        # shares linking up in a chain, which innocent setups do too (see farms.py)
        findings.append(_finding(
            "account_farm", "review" if f.chained else "likely", f.why, f.accounts, f.requests, f.cost_usd,
            {
                "shared_devices": f.devices,
                "shared_networks": f.networks,
                "chained": f.chained,
                "largest_share": f.largest_share,  # most accounts on any one device or network
                # linked to the farm only through smaller shares, not in accounts or cost
                "linked_accounts": f.linked,
                "first_seen": _date(f.first_seen),  # when the farm was active
                "last_seen": _date(f.last_seen),
            },
            *sorted(f.accounts), known=known,
        ))
    for b in report.bots:
        findings.append(_finding(
            "copy_paste_bot", "likely", b.why, b.accounts, b.signal_requests, b.signal_cost_usd,
            {
                "prompt_hashes": b.prompts,
                "repeats": b.repeats,
                # everything else these accounts did: worth a look, not likely abuse
                "other_requests": b.requests - b.signal_requests,
                "other_cost_usd": _money(b.cost_usd - b.signal_cost_usd, known),
            },
            *sorted(b.accounts), known=known,
        ))
    for k in report.keys:
        findings.append(_finding(
            "shared_key", "likely", k.why, k.accounts, k.requests, k.cost_usd,
            {
                "key": k.key,
                "home_country": k.home_country,
                "peak_countries_in_a_day": k.peak_countries,
                "requests_by_country": dict(sorted(k.countries.items())),
                "distinct_ips": k.ips,
            },
            k.key, known=known,
        ))

    # weak signals: "worth a look". Requests that also tripped a strong check
    # are counted as likely abuse in the totals, never twice
    if report.emails:
        e = report.emails
        findings.append(_finding(
            "disposable_emails", "review", e.why, e.accounts, e.requests, e.cost_usd,
            {"domains": dict(e.domains.most_common()),
             "accounts_with_no_other_red_flag": sorted(report.email_only_accounts)},
            *sorted(e.accounts), known=known,
        ))
    for g in report.aliases:
        findings.append(_finding(
            "gmail_aliases", "review", g.why, g.accounts, g.requests, g.cost_usd,
            {"spellings_used": g.addresses},  # never the inbox itself, that's an email
            *sorted(g.accounts), known=known,
        ))
    for s in report.spikes:
        # always "review", like in the totals: the extra spend of a spike is worth a look.
        # If the account also has likely abuse (say a leaked key), that's its own finding
        findings.append(_finding(
            "spend_spike", "review", s.why, [s.account], s.requests, s.cost_usd,
            {
                "usual_daily_usd": _money(s.usual_daily),
                "peak_daily_usd": _money(s.peak_daily),
                "days": [str(d) for d in s.days],
                # some requests on those days had no cost: the real extra was higher
                "some_costs_missing": s.incomplete,
            },
            s.account, s.first_day, known=known,
        ))

    return {
        "luxvigil": __version__,
        "format": FORMAT_VERSION,
        "period": {"from": _date(report.first_seen), "to": _date(report.last_seen)},  # UTC
        # same two totals as the terminal report, each request counted once. Dollar
        # totals only cover requests with a known cost. An account can be in both
        "totals": {
            "requests": report.requests,
            "accounts": report.accounts,
            "requests_with_cost": report.requests if report.known_cost_requests is None
            else report.known_cost_requests,
            "spend_usd": _money(report.total_cost, known),
            "likely_abuse_usd": _money(report.abuse_cost, known),
            "likely_abuse_share": round(report.abuse_share, 4) if known else None,
            "likely_abuse_accounts": len(report.flagged_accounts),
            "worth_a_look_usd": _money(report.review_cost, known),
            "worth_a_look_accounts": len(report.review_accounts),
        },
        "config": report.config_path or None,
        "settings": report.settings,  # thresholds used, to compare two runs
        "ignored_accounts": report.ignored_accounts,
        "notes": list(notes),  # warnings from reading the logs (duplicates, bad IPs, identities...)
        "skipped_checks": report.skipped,  # checks these logs had no data for, and why
        # per check, requests it had data for out of the ones checked. How much it could
        # look at, not how much abuse it caught
        "coverage": {"requests_checked": report.checked_requests, "by_check": report.coverage},
        "findings": findings,
    }