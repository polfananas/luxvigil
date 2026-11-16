from datetime import datetime, timedelta, timezone

from luxvigil.checks.spikes import find_spikes
from luxvigil.generate import generate, write_csv
from luxvigil.schema import Request, load_logs

T0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def req(day, cost, account="a", known=True):
    return Request(
        timestamp=T0 + timedelta(days=day),
        account_id=account, email=f"{account}@example.com", api_key_id=f"key_{account}",
        ip="1.2.3.4", country="US", device_id="", prompt_hash="p",
        input_tokens=10, output_tokens=10, cost_usd=cost, cost_known=known,
    )


def unknown(day, account="a"):
    """A request the logs had no cost for: 0.0 in cost_usd, but not free."""
    return req(day, 0.0, account, known=False)


def test_finds_the_planted_spike(tmp_path):
    rows, planted = generate()
    path = tmp_path / "logs.csv"
    write_csv(rows, path)
    spikes = find_spikes(load_logs(path))
    flagged = {s.account for s in spikes}
    assert set(planted["spike"]) <= flagged
    # the leaked key's owner also spikes (that's what a leak looks like from their side)
    victim = {r["account_id"] for r in rows if r["api_key_id"] in planted["shared_key"]}
    assert flagged == set(planted["spike"]) | victim


def test_steady_user_is_fine():
    rows = [req(d, 0.5) for d in range(30)]
    assert find_spikes(rows) == []


def test_new_account_is_never_a_spike():
    rows = [req(0, 50.0), req(1, 80.0)]  # big spender from day one, no history to compare
    assert find_spikes(rows) == []


def test_spike_days_merge_and_only_extra_counts():
    rows = [req(d, 0.2) for d in range(10)]
    rows += [req(10, 5.0), req(11, 3.0)]  # two days in a row
    (spike,) = find_spikes(rows)
    assert spike.usual_daily == 0.2
    assert spike.peak_daily == 5.0
    assert (spike.last_day - spike.first_day).days == 1
    assert round(spike.cost_usd, 2) == 7.6  # (5.0 - 0.2) + (3.0 - 0.2)


def test_dollar_floor():
    rows = [req(d, 0.01) for d in range(10)] + [req(10, 0.5)]  # 50x, but only 50 cents
    assert find_spikes(rows) == []
    assert len(find_spikes(rows, min_daily_usd=0.1)) == 1



def test_a_new_normal_stops_being_a_spike_after_a_week():
    # $0.10/day for 10 days, then a customer starts using it for real at $2/day for 20 days
    rows = [req(d, 0.1) for d in range(10)] + [req(d, 2.0) for d in range(10, 30)]
    (spike,) = find_spikes(rows)
    assert len(spike.days) == 7  # the first week, not all 20 days
    assert spike.sustained and "stayed there" in spike.why
    assert round(spike.cost_usd, 2) == round(7 * 1.9, 2)


def test_spending_from_zero_doesnt_print_inf():
    rows = [req(d, 0.0) for d in range(5)] + [req(5, 5.0)]
    (spike,) = find_spikes(rows)
    assert "inf" not in spike.why
    assert "$0.00/day to $5.00/day on" in spike.why


def test_days_with_no_cost_are_not_a_free_baseline():
    # three days where the logs have no cost, then a normal $2 day: not a spike from $0
    rows = [unknown(d) for d in range(3)] + [req(3, 2.0)]
    assert find_spikes(rows) == []


def test_a_day_with_some_costs_missing_doesnt_count_as_usual():
    # every day $1 known + requests with no cost: the usual spend is unknown, so no
    # baseline at all, even though the known part looks like a steady $1
    rows = [r for d in range(5) for r in (req(d, 1.0), unknown(d))]
    rows += [req(5, 20.0)]
    assert find_spikes(rows) == []


def test_a_free_account_that_starts_spending_is_still_a_spike():
    # known $0 is free, not unknown: that's a real baseline
    rows = [req(d, 0.0) for d in range(3)] + [req(3, 5.0)]
    (spike,) = find_spikes(rows)
    assert spike.usual_daily == 0 and spike.peak_daily == 5.0 and not spike.incomplete


def test_a_spike_on_known_costs_alone_still_counts_and_says_so():
    rows = [req(d, 0.1) for d in range(5)] + [req(5, 5.0), unknown(5)]
    (spike,) = find_spikes(rows)
    assert spike.peak_daily == 5.0 and spike.incomplete
    assert "at least: some of those requests had no cost" in spike.why


def test_a_day_with_no_known_cost_is_skipped_not_a_zero():
    rows = [req(d, 0.1) for d in range(5)] + [unknown(5)] + [req(6, 0.1)]
    assert find_spikes(rows) == []


def test_spikes_are_worth_a_look_in_the_json_like_in_the_totals(tmp_path):
    # the leaked key's owner spikes too, and has likely abuse: the spike itself is still "review"
    from luxvigil.export import to_dict
    from luxvigil.report import build_report

    rows, _ = generate()
    path = tmp_path / "logs.csv"
    write_csv(rows, path)
    report = build_report(load_logs(path))
    spikes = [f for f in to_dict(report)["findings"] if f["check"] == "spend_spike"]
    assert any(s["accounts"][0] in report.flagged_accounts for s in spikes)
    assert {s["severity"] for s in spikes} == {"review"}
