from datetime import date

from luxvigil.checks.farms import Farm
from luxvigil.checks.spikes import Spike
from luxvigil.report import Report, render_text


# real logs can have hundreds of findings: show the top ones, not a wall of text

def many_farms(n):
    farms = [Farm(accounts=[f"a{i}_{j}" for j in range(5)], devices=[f"d{i}"], networks=[],
                  requests=5, cost_usd=float(n - i)) for i in range(n)]
    accounts = {a for f in farms for a in f.accounts}
    return Report(requests=5 * n, accounts=len(accounts), total_cost=float(n), first_seen=None,
                  last_seen=None, farms=farms, flagged_accounts=accounts, abuse_cost=float(n))


def test_long_lists_are_cut_to_the_top_ten():
    lines = render_text(many_farms(250))
    farm_lines = [line for line in lines if line.startswith("     - 5 accounts on")]
    assert len(farm_lines) == 10
    assert "     …and 240 more, run with --json to see them all" in lines


def test_short_lists_stay_as_they_are():
    lines = render_text(many_farms(10))
    assert len([line for line in lines if line.startswith("     - 5 accounts on")]) == 10
    assert not any("more, run with --json" in line for line in lines)


def test_big_numbers_get_a_thousands_separator():
    text = "\n".join(render_text(many_farms(250)))
    assert "250 account farms, 1,250 accounts" in text
    assert "of your spend), 1,250 accounts" in text


# one account can spike again and again: one line per account, not per spike

def spikes_report(spikes):
    return Report(requests=100, accounts=10, total_cost=100.0, first_seen=None, last_seen=None,
                  spikes=spikes, review_accounts={s.account for s in spikes}, review_cost=1.0)


def spike(account, day, cost):
    return Spike(account=account, usual_daily=1.0, peak_daily=20.0, first_day=date(2026, 10, day),
                 last_day=date(2026, 10, day), requests=10, cost_usd=cost, days=[date(2026, 10, day)])


def test_spikes_of_one_account_share_a_line():
    lines = render_text(spikes_report([spike("a", 3, 19.0), spike("a", 10, 15.0), spike("a", 20, 12.0),
                                       spike("b", 5, 30.0)]))
    spike_lines = [line for line in lines if line.startswith("     - ")]
    assert len(spike_lines) == 2
    assert "4 spend spikes on 2 accounts" in "\n".join(lines)
    # b spent the most extra, but a's three spikes add up to more, so a goes first
    assert spike_lines[0].startswith("     - a went from")
    assert "(+2 more spikes, last on 2026-10-20)" in spike_lines[0]
    assert "30 requests  ·  ~$46.00 extra" in spike_lines[0]


def test_one_spike_per_account_looks_like_before():
    lines = render_text(spikes_report([spike("a", 3, 19.0), spike("b", 5, 30.0)]))
    text = "\n".join(lines)
    assert "2 spend spikes (could be" in text
    assert "more spike" not in text
