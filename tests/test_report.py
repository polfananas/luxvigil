from datetime import datetime, timedelta, timezone

import pytest

from luxvigil.generate import generate, write_csv
from luxvigil.report import build_report, render_text
from luxvigil.schema import Request, load_logs

T0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def req(account, day=0, cost=0.01, email=None, key=None, country="ES", prompt=None, device=""):
    return Request(
        timestamp=T0 + timedelta(days=day),
        account_id=account, email=email or f"{account}@gmail.com", api_key_id=key or f"key_{account}",
        ip=f"10.{sum(map(ord, account)) % 250}.{len(account)}.1",  # a different network per account
        country=country, device_id=device, prompt_hash=prompt or f"p_{account}_{day}",
        input_tokens=10, output_tokens=10, cost_usd=cost,
    )


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    rows, planted = generate()
    path = tmp_path_factory.mktemp("logs") / "logs.csv"
    write_csv(rows, path)
    return rows, planted, build_report(load_logs(path))


def test_total_matches_what_was_planted(sample):
    rows, planted, report = sample
    farm = set(planted["farm_big"]) | set(planted["farm_device"])
    leak = planted["shared_key"][0]
    spiker = planted["spike"][0]
    victim = next(r for r in rows if r["api_key_id"] == leak)
    owner_device = victim["device_id"]  # the owner's first request comes before the leak

    # the big farm has throwaway emails and template prompts: likely abuse. The device farm
    # shows up in a burst too, but nothing else is off about it, so it's only worth a look
    likely = sum(r["cost_usd"] for r in rows if r["account_id"] in planted["farm_big"])
    # leaked requests come from random devices, the owner always uses the same one
    likely += sum(r["cost_usd"] for r in rows if r["api_key_id"] == leak and r["device_id"] != owner_device)
    device_farm = sum(r["cost_usd"] for r in rows if r["account_id"] in planted["farm_device"])
    # spike requests are way bigger than anything a normal user sends (> $0.035 each)
    spike = sum(r["cost_usd"] for r in rows if r["account_id"] == spiker and r["cost_usd"] > 0.035)

    assert report.abuse_cost == pytest.approx(likely, rel=0.02)
    # a spike on its own could be a launch, so it's only worth a look
    assert report.review_cost == pytest.approx(device_farm + spike, rel=0.02)
    assert farm == {a for f in report.farms for a in f.accounts}  # both farms still show up


def test_total_is_not_the_sum_of_the_checks(sample):
    _, _, report = sample
    naive = (
        sum(f.cost_usd for f in report.farms)
        + report.emails.cost_usd
        + sum(k.cost_usd for k in report.keys)
        + sum(b.cost_usd for b in report.bots)
        + sum(s.cost_usd for s in report.spikes)
    )
    assert report.abuse_cost < naive / 2
    assert 0 < report.abuse_share < 1


def test_flagged_accounts(sample):
    rows, planted, report = sample
    leak = planted["shared_key"][0]
    victim = next(r["account_id"] for r in rows if r["api_key_id"] == leak)
    assert report.flagged_accounts == set(planted["farm_big"]) | {victim}
    assert report.review_accounts == set(planted["farm_device"]) | set(planted["spike"])


def test_clean_logs():
    rows = [req(f"u{i}", day=d) for i in range(10) for d in range(5)]
    report = build_report(rows)
    assert report.clean and report.abuse_cost == 0
    assert any("nothing suspicious" in line for line in render_text(report))


def test_account_in_two_checks_counts_once():
    # 5 accounts with throwaway emails that also send the same template 3 times each
    rows = [req(f"bot{i}", day=d, cost=1.0, email=f"bot{i}@mailinator.com", prompt="template")
            for i in range(5) for d in range(3)]
    rows += [req(f"u{i}", day=d) for i in range(10) for d in range(3)]
    report = build_report(rows)
    assert report.emails and report.bots
    assert report.abuse_cost == pytest.approx(15.0)


def test_spike_on_top_of_a_leak_isnt_counted_twice():
    rows = [req("owner", day=d, cost=0.1) for d in range(10)]  # home ES, $0.10/day
    rows += [req("owner", day=10, cost=1.0, country=c) for c in ["BR", "VN", "NG", "RU", "ID"]]
    report = build_report(rows)
    assert report.keys and report.spikes
    assert report.abuse_cost == pytest.approx(5.0)  # the leak, not leak + spike


def test_throwaway_email_alone_is_only_worth_a_look():
    rows = [req(f"u{i}", day=d, cost=1.0) for i in range(9) for d in range(3)]
    rows += [req("dev", day=d, cost=1.0, email="dev@mailinator.com") for d in range(3)]
    report = build_report(rows)
    assert report.abuse_cost == 0
    assert report.review_cost == pytest.approx(3.0)
    assert report.review_accounts == {"dev"}


def test_strong_signal_wins_over_weak():
    # throwaway emails AND template prompts: likely abuse, not double counted in review
    rows = [req(f"bot{i}", day=d, cost=1.0, email=f"bot{i}@mailinator.com", prompt="template")
            for i in range(5) for d in range(3)]
    report = build_report(rows)
    assert report.abuse_cost == pytest.approx(15.0)
    assert report.review_cost == 0
    assert report.review_accounts == set()


def test_buckets_never_exceed_total_spend(sample):
    _, _, report = sample
    assert report.abuse_cost + report.review_cost <= report.total_cost + 1e-9


def test_cheap_accounts_copying_a_customers_prompt_dont_make_it_abuse():
    # 4 accounts spend $0.12 copying a prompt a customer repeats at $100 a go
    rows = [req("customer", cost=100.0, prompt="P") for _ in range(3)]
    rows += [req(f"x{k}", prompt="P") for k in range(4) for _ in range(3)]
    report = build_report(rows)
    assert round(report.abuse_cost, 2) == 0.12
    assert report.review_cost == 300.0
    assert "customer" not in report.flagged_accounts
    assert "could be a real customer whose prompt got copied" in "\n".join(render_text(report))
