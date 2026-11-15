"""Bugs found in the pre-0.2.0 review, each one reproduced so it stays fixed."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from luxvigil.cli import main
from luxvigil.export import to_dict
from luxvigil.report import build_report, render_text
from luxvigil.schema import Request, load_logs, looks_like_secret

T0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def req(account, day=0, cost=0.01, key=None, country="ES", prompt=None, email=None, ip="", device="",
        known=True, hours=0):
    return Request(
        timestamp=T0 + timedelta(days=day, hours=hours),
        account_id=account, email=email or "", api_key_id=key or f"key_{account}",
        ip=ip, country=country, device_id=device,
        prompt_hash=prompt or f"p_{account}_{day}_{hours}_{country}",
        input_tokens=10, output_tokens=10, cost_usd=cost, cost_known=known,
    )


def leaked_key():
    """A key used from ES for 10 days, then from 5 more countries on day 10: $5 from abroad."""
    rows = [req("owner", day=d, cost=0.1) for d in range(10)]
    rows += [req("owner", day=10, cost=1.0, country=c) for c in ["BR", "VN", "NG", "RU", "ID"]]
    return rows


def write_csv(tmp_path, text):
    path = tmp_path / "logs.csv"
    path.write_text(text)
    return path


# A. a request with no country is unknown, not abroad

def test_no_country_isnt_counted_as_abroad():
    rows = leaked_key() + [req("owner", day=10, cost=100.0, country="")]
    report = build_report(rows)
    (key,) = report.keys
    assert key.cost_usd == pytest.approx(5.0)
    assert report.abuse_cost == pytest.approx(key.cost_usd)  # the finding and the total agree


# B. money worth a look is always shown, even on an account that also has likely abuse

def test_worth_a_look_money_shows_even_when_every_account_is_also_likely_abuse():
    rows = leaked_key() + [req("owner", day=d, cost=0.1) for d in range(11, 14)]
    rows.append(req("owner", day=14, cost=50.0))  # the owner's own spike, from home
    report = build_report(rows)
    assert report.keys and report.spikes
    assert report.review_cost > 49
    assert report.review_accounts == {"owner"} and report.flagged_accounts == {"owner"}
    text = "\n".join(render_text(report))
    assert f"~${report.review_cost:,.2f} more is worth a look, 1 account, 1 of them also in likely abuse" in text
    totals = to_dict(report)["totals"]
    assert totals["worth_a_look_usd"] == pytest.approx(report.review_cost)
    assert totals["worth_a_look_accounts"] == 1


# C. timestamps with an offset are converted to UTC

def test_the_same_instant_lands_on_the_same_utc_day(tmp_path):
    path = write_csv(tmp_path, "timestamp,account_id,input_tokens,output_tokens,cost_usd\n"
                               "2026-01-01T23:30:00Z,a,1,1,1\n"
                               "2026-01-02T01:30:00+02:00,a,1,1,1\n"   # same instant as the line above
                               "2026-01-01T23:30:00,a,1,1,1\n"          # no zone: taken as UTC
                               "1767310200,a,1,1,1\n")                  # unix, same instant again
    rows = load_logs(path)
    assert {r.timestamp for r in rows} == {datetime(2026, 1, 1, 23, 30, tzinfo=timezone.utc)}
    assert all(r.timestamp.tzinfo == timezone.utc for r in rows)


def test_an_offset_doesnt_split_a_spike_in_two(tmp_path):
    # $0.10 a day for 5 days, then $20 at 23:00 UTC, logged in Madrid time (00:00 the next day)
    lines = [f"2026-01-0{d}T12:00:00Z,a,1,1,0.1" for d in range(1, 6)]
    lines += ["2026-01-05T23:00:00Z,a,1,1,10", "2026-01-06T00:00:00+01:00,a,1,1,10"]
    path = write_csv(tmp_path, "timestamp,account_id,input_tokens,output_tokens,cost_usd\n" + "\n".join(lines))
    (spike,) = build_report(load_logs(path)).spikes
    assert spike.days == [datetime(2026, 1, 5).date()]
    assert spike.peak_daily == pytest.approx(20.1)


# D. a missing cost is unknown, never free

def test_a_blank_cost_is_unknown_and_an_explicit_zero_is_free(tmp_path):
    path = write_csv(tmp_path, "timestamp,account_id,input_tokens,output_tokens,cost_usd\n"
                               "2026-01-01T10:00:00Z,a,1,1,\n"
                               "2026-01-01T11:00:00Z,a,1,1,0\n"
                               "2026-01-01T12:00:00Z,a,1,1,2.5\n")
    notes = []
    rows = load_logs(path, notes=notes)
    assert [r.cost_known for r in rows] == [False, True, True]
    assert any("1 of 3 requests have no cost" in n for n in notes)
    report = build_report(rows)
    assert "$2.50 spent on the 2 requests with a cost, 1 unknown" in "\n".join(render_text(report))
    assert to_dict(report)["totals"]["requests_with_cost"] == 2


def test_a_blank_cost_is_worked_out_from_prices_when_given(tmp_path):
    path = write_csv(tmp_path, "timestamp,account_id,input_tokens,output_tokens,cost_usd\n"
                               "2026-01-01T10:00:00Z,a,1000000,0,\n")
    notes = []
    (row,) = load_logs(path, price_in=3, price_out=15, notes=notes)
    assert row.cost_known and row.cost_usd == 3
    assert any("worked out from --price-in" in n for n in notes)


def test_no_costs_at_all_still_runs_the_checks_without_dollars(tmp_path, capsys):
    # 5 accounts on one device, and no cost column or prices
    lines = [f"2026-01-0{d}T10:00:00Z,acc{i},10,10,dev1" for i in range(5) for d in range(1, 4)]
    path = write_csv(tmp_path, "timestamp,account_id,input_tokens,output_tokens,device_id\n" + "\n".join(lines))
    report = build_report(load_logs(path))
    assert report.farms and not report.has_costs
    assert report.skipped["spend spikes"] == "no costs in the logs"
    text = "\n".join(render_text(report))
    assert "$" not in text
    assert "no dollar amounts" in text and "5 accounts with strong signals" in text

    assert main(["scan", str(path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["totals"]["spend_usd"] is None and data["totals"]["likely_abuse_usd"] is None
    assert all(f["cost_usd"] is None for f in data["findings"])


# E. something that looks like a raw API key is never kept or shown

def test_a_raw_key_in_a_csv_is_hashed_before_anything_uses_it(tmp_path, capsys):
    raw = "sk-proj-THISisAsecret123"
    lines = [f"2026-01-01T1{i}:00:00Z,a,1,1,0.1,{raw},{c}" for i, c in enumerate(["ES", "BR", "VN", "NG", "RU"])]
    path = write_csv(tmp_path, "timestamp,account_id,input_tokens,output_tokens,cost_usd,api_key_id,country\n"
                     + "\n".join(lines))
    notes = []
    rows = load_logs(path, notes=notes)
    assert all(r.api_key_id.startswith("redacted-") for r in rows)
    assert any("looked like a raw API key" in n for n in notes) and not any(raw in n for n in notes)
    assert build_report(rows).keys  # still one key, still caught
    for args in (["scan", str(path)], ["scan", str(path), "--json"]):
        main(args)
        out = capsys.readouterr()
        assert raw not in out.out + out.err


@pytest.mark.parametrize("value,secret", [
    ("sk-proj-abc", True), ("sk_live_abc", True), ("AKIAIOSFODNN7EXAMPLE", True), ("Bearer abc", True),
    ("key_123", False), ("asia_east", False), ("skeleton", False), ("88dc28d0f030c55ed4ab77ed8faf0981", False),
])
def test_what_looks_like_a_raw_key(value, secret):
    assert looks_like_secret(value) is secret


def test_ids_and_hashes_are_left_alone(tmp_path):
    path = write_csv(tmp_path, "timestamp,account_id,input_tokens,output_tokens,cost_usd,api_key_id\n"
                               "2026-01-01T10:00:00Z,a,1,1,0.1,key_123\n"
                               "2026-01-01T10:00:00Z,b,1,1,0.1,88dc28d0f030c55ed4ab77ed8faf0981\n")
    assert [r.api_key_id for r in load_logs(path)] == ["key_123", "88dc28d0f030c55ed4ab77ed8faf0981"]


# F. a bot's other traffic isn't likely abuse, and nothing is counted twice

def test_only_the_repeated_prompts_of_a_bot_are_likely_abuse():
    rows = [req(f"bot{i}", day=d, cost=1.0, prompt="template") for i in range(5) for d in range(3)]
    rows += [req(f"bot{i}", day=d, cost=2.0) for i in range(5) for d in range(3)]  # their normal use
    rows += [req(f"u{i}", day=d) for i in range(10) for d in range(3)]
    report = build_report(rows)
    (bot,) = report.bots
    assert (bot.signal_requests, bot.signal_cost_usd) == (15, pytest.approx(15.0))
    assert (bot.requests, bot.cost_usd) == (30, pytest.approx(45.0))
    assert report.abuse_cost == pytest.approx(15.0)
    assert report.review_cost == pytest.approx(30.0)
    assert report.abuse_cost + report.review_cost == pytest.approx(bot.cost_usd)  # each request once

    text = "\n".join(render_text(report))
    assert "15 repeated requests  ·  ~$15.00 (+15 other requests, ~$30.00, worth a look)" in text
    (finding,) = to_dict(report)["findings"]
    assert finding["cost_usd"] == 15.0 and finding["cost_basis"] == "requests_with_the_repeated_prompts"
    assert finding["evidence"]["other_cost_usd"] == 30.0


def test_a_farm_is_the_spend_of_its_linked_accounts():
    rows = [req(f"f{i}", day=d, cost=1.0, device="shared") for i in range(5) for d in range(3)]
    report = build_report(rows)
    (finding,) = [f for f in to_dict(report)["findings"] if f["check"] == "account_farm"]
    assert finding["cost_basis"] == "all_requests_of_linked_accounts"
    assert "not a confirmed loss" in "\n".join(render_text(report))


# how much of the logs each check could actually look at

def test_a_check_that_only_saw_part_of_the_logs_says_so():
    rows = leaked_key() + [req(f"u{i}", day=d, country="") for i in range(10) for d in range(3)]
    report = build_report(rows)
    assert report.coverage["shared or leaked keys"] == 15
    assert "  ◐ shared or leaked keys only had data for 33% of requests (15 of 45)" in render_text(report)


def test_ips_alone_cant_link_a_farm_and_the_report_says_so():
    rows = [req(f"u{i}", day=d, ip="10.0.0.1") for i in range(10) for d in range(3)]
    report = build_report(rows)
    assert not report.farms
    assert "IPs only link accounts that already look off" in report.skipped["account farms"]


def test_control_characters_in_ids_dont_reach_the_terminal():
    rows = leaked_key()
    rows = [Request(**{**{f: getattr(r, f) for f in r.__slots__}, "api_key_id": "k\x1b[2J"}) for r in rows]
    text = "\n".join(render_text(build_report(rows)))
    assert "\x1b" not in text and "k?[2J" in text
