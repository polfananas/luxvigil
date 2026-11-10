from luxvigil.checks.farms import Farm
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
