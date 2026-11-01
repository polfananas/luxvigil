from datetime import datetime, timedelta, timezone

from luxvigil.checks.spikes import find_spikes
from luxvigil.generate import generate, write_csv
from luxvigil.schema import Request, load_logs

T0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def req(day, cost, account="a"):
    return Request(
        timestamp=T0 + timedelta(days=day),
        account_id=account, email=f"{account}@example.com", api_key_id=f"key_{account}",
        ip="1.2.3.4", country="US", device_id="", prompt_hash="p",
        input_tokens=10, output_tokens=10, cost_usd=cost,
    )


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
