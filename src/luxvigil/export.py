"""The report as plain data, for `luxvigil scan --json`.

Each finding says what was found, why it's suspicious, what could explain it
innocently, and what to check next. Finding ids are stable: the same logs
always give the same ids, so you can diff two runs or track a finding.

No raw emails or prompts end up here, only account ids, key ids, email
domains and prompt hashes, which are already in your logs.
"""

import hashlib

from luxvigil import __version__

# Bump this if the shape of the json changes, so scripts reading it can tell.
FORMAT_VERSION = 1

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


def _money(x):
    # 4 decimals is plenty for dollars and keeps float noise out of the json
    return round(x, 4)


def _date(t):
    return t.isoformat() if t else None


def _finding(check, severity, summary, accounts, requests, cost, evidence, *id_parts):
    """Every finding has the same shape, whatever check it comes from."""
    return {
        "id": _id(check, *id_parts),
        "check": check,
        "severity": severity,  # "likely" (strong signal) or "review" (could be innocent)
        "summary": summary,  # the same one-liner as in the terminal report
        "accounts": sorted(accounts),
        "requests": requests,
        "cost_usd": _money(cost),
        "evidence": evidence,  # check-specific details, see each block below
        "innocent_explanation": INNOCENT[check],
        "next_step": NEXT[check],
    }


def to_dict(report, notes=()):
    """Turn a Report into plain dicts and lists, ready for json.dumps."""
    findings = []

    # strong signals first: these count towards "likely abuse"
    for f in report.farms:
        findings.append(_finding(
            "account_farm", "likely", f.why, f.accounts, f.requests, f.cost_usd,
            {
                "shared_devices": f.devices,
                "shared_networks": f.networks,
                "first_seen": _date(f.first_seen),  # when the farm was active
                "last_seen": _date(f.last_seen),
            },
            *sorted(f.accounts),
        ))
    for b in report.bots:
        findings.append(_finding(
            "copy_paste_bot", "likely", b.why, b.accounts, b.requests, b.cost_usd,
            {"prompt_hashes": b.prompts, "repeats": b.repeats},
            *sorted(b.accounts),
        ))
    for g in report.aliases:
        findings.append(_finding(
            "gmail_aliases", "likely", g.why, g.accounts, g.requests, g.cost_usd,
            {"spellings_used": g.addresses},  # never the inbox itself, that's an email
            *sorted(g.accounts),
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
            k.key,
        ))

    # weak signals: only "worth a look" unless the account also tripped a strong one
    if report.emails:
        e = report.emails
        alone = sorted(set(e.accounts) & report.review_accounts)
        findings.append(_finding(
            "disposable_emails", "review", e.why, e.accounts, e.requests, e.cost_usd,
            {"domains": dict(e.domains.most_common()), "accounts_with_no_other_red_flag": alone},
            *sorted(e.accounts),
        ))
    for s in report.spikes:
        # a spike on an account that's already likely abuse (e.g. a leaked key) is likely too
        severity = "likely" if s.account in report.flagged_accounts else "review"
        findings.append(_finding(
            "spend_spike", severity, s.why, [s.account], s.requests, s.cost_usd,
            {
                "usual_daily_usd": _money(s.usual_daily),
                "peak_daily_usd": _money(s.peak_daily),
                "days": [str(d) for d in s.days],
            },
            s.account, s.first_day,
        ))

    return {
        "luxvigil": __version__,
        "format": FORMAT_VERSION,
        "period": {"from": _date(report.first_seen), "to": _date(report.last_seen)},
        # same two totals as the terminal report, each request counted once
        "totals": {
            "requests": report.requests,
            "accounts": report.accounts,
            "spend_usd": _money(report.total_cost),
            "likely_abuse_usd": _money(report.abuse_cost),
            "likely_abuse_share": round(report.abuse_share, 4),
            "likely_abuse_accounts": len(report.flagged_accounts),
            "worth_a_look_usd": _money(report.review_cost),
            "worth_a_look_accounts": len(report.review_accounts),
        },
        "config": report.config_path or None,
        "ignored_accounts": report.ignored_accounts,
        "notes": list(notes),  # warnings from reading the logs (duplicates, bad IPs...)
        "findings": findings,
    }