"""When accounts showed up on a shared device matters: farms come in bursts, a library computer doesn't."""

import json
import random
from datetime import datetime, timedelta, timezone

import pytest

from luxvigil.checks.farms import find_farms
from luxvigil.cli import main
from luxvigil.config import Config, ConfigError, load_config
from luxvigil.export import to_dict
from luxvigil.report import build_report, render_text
from luxvigil.schema import Request, load_logs

T0 = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)


def req(account, days=0.0, device="shared", email=None, ip="", cost=1.0):
    return Request(
        timestamp=T0 + timedelta(days=days), account_id=account, email=email or f"{account}@gmail.com",
        api_key_id="", ip=ip, country="", device_id=device, prompt_hash=f"{account}{days}",
        input_tokens=10, output_tokens=10, cost_usd=cost,
    )


def throwaway(account):
    return f"{account}@mailinator.com"


def test_five_people_a_month_apart_are_related_but_not_a_burst():
    rows = [req(f"u{k}", days=31 * k) for k in range(5)]  # a library computer, a second-hand phone
    (group,) = find_farms(rows)
    assert not group.chained and not group.concentrated and not group.likely
    assert "first seen there over 4 months (spread out" in group.why


def test_five_accounts_in_an_hour_are_a_burst_but_not_abuse_on_their_own():
    rows = [req(f"u{k}", days=k / 100) for k in range(5)]
    (group,) = find_farms(rows)
    assert group.concentrated and not group.likely  # a class or a kiosk looks the same
    assert "within the same day, nothing else off about them" in group.why
    # with throwaway emails on top of it, it's a likely farm
    rows = [req(f"u{k}", days=k / 100, email=throwaway(f"u{k}")) for k in range(5)]
    (farm,) = find_farms(rows)
    assert farm.likely and "all with throwaway emails or bot prompts" in farm.why


def test_one_account_a_week_still_fits_in_30_days():
    rows = [req(f"u{k}", days=7 * k, email=throwaway(f"u{k}")) for k in range(5)]  # days 0, 7, 14, 21, 28
    (farm,) = find_farms(rows)
    assert farm.concentrated and farm.likely


@pytest.mark.parametrize("last,inside", [(timedelta(days=30) - timedelta(seconds=1), True),
                                         (timedelta(days=30), True),  # exactly 30 days counts
                                         (timedelta(days=30, seconds=1), False)])
def test_the_window_boundary(last, inside):
    rows = [req(f"u{k}", days=k) for k in range(4)]
    rows.append(Request(T0 + last, "u4", "u4@gmail.com", "", "", "", "shared", "p", 10, 10, 1.0))
    (group,) = find_farms(rows)
    assert group.concentrated is inside


def test_a_burst_over_new_year_is_like_any_other():
    start = datetime(2026, 12, 29, tzinfo=timezone.utc)
    rows = [Request(start + timedelta(days=2 * k), f"u{k}", "", "", "", "", "shared", "p", 10, 10, 1.0)
            for k in range(5)]
    (group,) = find_farms(rows)
    assert group.concentrated and group.arrived_to.year == 2027


def test_row_order_and_duplicates_dont_change_when_they_showed_up():
    rows = [req(f"u{k}", days=k + d) for k in range(5) for d in (0, 40, 80)]
    expected = find_farms(rows)[0]
    for seed in range(5):
        shuffled = rows + rows[:5]  # some rows twice
        random.Random(seed).shuffle(shuffled)
        (group,) = find_farms(shuffled)
        assert (group.accounts, group.arrived_from, group.arrived_to) == (
            expected.accounts, expected.arrived_from, expected.arrived_to)


def test_old_accounts_that_move_to_a_device_together_are_described_by_when_they_arrived():
    # active on their own laptops for months, then all on one device within two days
    rows = [req(f"u{k}", days=d, device=f"own{k}") for k in range(5) for d in range(0, 90, 10)]
    rows += [req(f"u{k}", days=120 + k / 3) for k in range(5)]
    (group,) = find_farms(rows)
    assert group.concentrated
    assert group.arrived_from == T0 + timedelta(days=120)  # on this device, not their first request ever
    assert "first seen on 1 shared device within a day" in group.why


def test_logs_that_start_with_the_burst_say_the_accounts_may_have_been_there_before():
    rows = [req(f"u{k}", days=k, email=throwaway(f"u{k}")) for k in range(5)]
    report = build_report(rows)
    (farm,) = report.farms
    assert farm.earlier_use_unknown
    assert '"First seen" is first seen in these logs, which start on 2026-03-01' in "\n".join(render_text(report))
    # with two months of logs before it, the burst is real as far as the logs can tell
    rows.append(req("someone", days=-60, device="elsewhere"))
    (farm,) = build_report(rows).farms
    assert not farm.earlier_use_unknown


def test_people_who_show_up_long_after_a_burst_dont_join_the_farm():
    rows = [req(f"f{k}", days=k / 10, email=throwaway(f"f{k}")) for k in range(5)]  # the burst
    rows += [req(f"later{k}", days=60 + 30 * k, cost=10.0) for k in range(20)]  # one a month, for years
    report = build_report(rows)
    (farm,) = report.farms
    assert farm.accounts == [f"f{k}" for k in range(5)] and farm.likely
    assert len(farm.linked) == 20  # listed with it, not part of it
    assert report.abuse_cost == pytest.approx(5.0)
    assert not {f"later{k}" for k in range(20)} & report.flagged_accounts


def test_two_bursts_months_apart_stay_two():
    rows = [req(f"a{k}", days=k / 10, email=throwaway(f"a{k}")) for k in range(5)]
    rows += [req(f"b{k}", days=200 + k / 10, email=throwaway(f"b{k}")) for k in range(5)]
    farms = find_farms(rows)
    assert len(farms) == 2
    assert sorted(f.arrived_from for f in farms) == [T0, T0 + timedelta(days=200)]


def test_arrivals_every_10_days_never_make_a_burst():
    # 19 accounts over 6 months on one device: at most 4 in any 30 days
    rows = [req(f"u{k}", days=10 * k) for k in range(19)]
    (group,) = find_farms(rows)
    assert not group.concentrated and len(group.accounts) == 19


def test_back_to_back_bursts_dont_chain_into_one_long_one():
    # a new account every 5 days for 6 months: several bursts of 7, none longer than 30 days
    rows = [req(f"u{k:02d}", days=5 * k, email=throwaway(f"u{k}")) for k in range(36)]
    farms = find_farms(rows)
    assert len(farms) > 1
    assert all(f.arrived_to - f.arrived_from <= timedelta(days=30) for f in farms)
    assert sum(len(f.accounts) for f in farms) <= 36  # nobody twice


def test_a_slow_chain_over_a_year_is_not_a_burst():
    rows = []
    for k in range(6):  # u0-u1 on d0 in month 0, u1-u2 on d1 in month 2...
        rows += [req(f"u{k}", days=60 * k, device=f"d{k}"), req(f"u{k + 1}", days=60 * k + 1, device=f"d{k}")]
    (group,) = find_farms(rows)
    assert group.chained and not group.likely


def test_a_kiosk_with_ten_people_in_a_day_is_only_worth_a_look():
    rows = [req(f"visitor{k}", days=k / 24, cost=2.0) for k in range(10)]
    report = build_report(rows)
    (group,) = report.farms
    assert group.concentrated and not group.likely
    assert report.abuse_cost == 0 and report.review_cost == pytest.approx(20.0)


def test_nothing_shared_nothing_found():
    rows = [req(f"u{k}", days=k / 10, device=f"own{k}", email=throwaway(f"u{k}")) for k in range(5)]
    assert find_farms(rows) == []


def test_the_same_instant_with_different_offsets_gives_the_same_farm(tmp_path):
    lines = [f"2026-03-0{k + 1}T10:00:00Z,u{k},1,1,1,dev" for k in range(5)]
    shifted = [f"2026-03-0{k + 1}T12:00:00+02:00,u{k},1,1,1,dev" for k in range(5)]
    header = "timestamp,account_id,input_tokens,output_tokens,cost_usd,device_id\n"
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text(header + "\n".join(lines))
    b.write_text(header + "\n".join(shifted))
    (fa,), (fb,) = find_farms(load_logs(a)), find_farms(load_logs(b))
    assert (fa.accounts, fa.arrived_from, fa.arrived_to) == (fb.accounts, fb.arrived_from, fb.arrived_to)


def test_a_network_in_a_burst_still_needs_accounts_that_look_off():
    rows = [req(f"u{k}", days=k / 10, device=f"own{k}", ip=f"5.5.5.{k}") for k in range(5)]
    assert find_farms(rows) == []  # same /24, same morning, normal emails: an office


def test_an_account_in_two_bursts_is_counted_once():
    # a0 shows up in the burst on both phones: one farm rotating through two devices
    rows = [req(f"a{k}", days=k / 10, device="phone1", email=throwaway(f"a{k}")) for k in range(5)]
    rows += [req(f"b{k}", days=1 + k / 10, device="phone2", email=throwaway(f"b{k}")) for k in range(4)]
    rows += [req("a0", days=1.5, device="phone2", email=throwaway("a0"))]
    report = build_report(rows)
    (farm,) = report.farms
    assert farm.devices == ["phone1", "phone2"] and len(farm.accounts) == 9
    assert report.abuse_cost == pytest.approx(10.0)  # 10 requests, each once
    assert to_dict(report)["totals"]["likely_abuse_usd"] == 10.0


def test_more_history_can_change_when_accounts_showed_up():
    # in these logs, 5 accounts first seen in a week...
    rows = [req(f"u{k}", days=60 + k) for k in range(5)]
    assert find_farms(rows)[0].concentrated
    # ...but with two more months of logs, three of them were already on it back then. That's
    # the measure doing its job: "first seen" can only be as good as the logs you give it
    rows += [req(f"u{k}", days=0) for k in range(3)]
    assert not find_farms(rows)[0].concentrated


def test_the_window_is_set_in_luxvigil_toml(tmp_path):
    rows = [req(f"u{k}", days=7 * k) for k in range(5)]  # 28 days
    assert find_farms(rows, config=Config())[0].concentrated  # 30 by default
    toml = tmp_path / "luxvigil.toml"
    toml.write_text("[farms]\nwindow_days = 7\n")
    assert not find_farms(rows, config=load_config(toml))[0].concentrated
    toml.write_text("[farms]\nwindow_days = 0\n")  # no time limit at all, like before 0.3.0
    spread = [req(f"u{k}", days=100 * k) for k in range(5)]
    assert find_farms(spread, config=load_config(toml))[0].concentrated
    toml.write_text("[trusted]\ndevices = []\n")  # an older config without [farms] still works
    assert load_config(toml).farm_window_days == 30


@pytest.mark.parametrize("value", ["-1", '"30"', "true", "4000", "7.5"])
def test_bad_window_values_are_an_error(tmp_path, value):
    toml = tmp_path / "luxvigil.toml"
    toml.write_text(f"[farms]\nwindow_days = {value}\n")
    with pytest.raises(ConfigError, match="window_days"):
        load_config(toml)


def test_the_window_is_in_the_json(tmp_path, capsys):
    path = tmp_path / "logs.csv"
    path.write_text("timestamp,account_id,input_tokens,output_tokens,cost_usd,device_id\n"
                    + "\n".join(f"2026-03-0{k + 1}T10:00:00Z,u{k},1,1,1,dev" for k in range(5)))
    assert main(["scan", str(path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == 3 and data["settings"]["farm_window_days"] == 30
    (farm,) = data["findings"]
    ev = farm["evidence"]
    assert (farm["severity"], ev["relation"], ev["concentrated"], ev["window_days"]) == ("review", "direct", True, 30)
    assert ev["arrived_from"].startswith("2026-03-01") and ev["arrived_to"].startswith("2026-03-05")
