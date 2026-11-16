from datetime import datetime, timezone

import pytest

from luxvigil.checks.farms import find_farms, network_of
from luxvigil.export import to_dict
from luxvigil.report import build_report, render_text
from luxvigil.generate import generate, write_csv
from luxvigil.schema import Request, load_logs


def req(account, ip="1.2.3.4", device="", cost=0.01, email=None):
    return Request(
        timestamp=datetime(2026, 10, 1, tzinfo=timezone.utc),
        account_id=account, email=email or f"{account}@example.com", api_key_id=f"key_{account}",
        ip=ip, country="US", device_id=device, prompt_hash="p",
        input_tokens=10, output_tokens=10, cost_usd=cost,
    )


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    rows, planted = generate()
    path = tmp_path_factory.mktemp("logs") / "logs.csv"
    write_csv(rows, path)
    return load_logs(path), planted


def test_finds_both_planted_farms(sample):
    rows, planted = sample
    farms = find_farms(rows)
    found = sorted(set(f.accounts) for f in farms)
    assert found == sorted([set(planted["farm_big"]), set(planted["farm_device"])])


def test_farms_say_why(sample):
    rows, _ = sample
    big, small = sorted(find_farms(rows), key=lambda f: len(f.accounts), reverse=True)
    assert big.networks == ["185.220.101.0/24"]
    assert len(big.devices) == 3
    assert len(small.devices) == 1  # different IPs, same device
    assert big.cost_usd > 0 and small.cost_usd > 0


def test_no_false_alarms_on_normal_users():
    rows = [req(f"acc{i}", ip=f"{i + 10}.0.0.1", device=f"dev{i}") for i in range(50)]
    assert find_farms(rows) == []


def test_small_group_is_not_a_farm():
    # someone with a work + personal account on the same laptop is fine
    rows = [req(f"acc{i}", ip=f"{i + 10}.0.0.1", device="laptop") for i in range(4)]
    assert find_farms(rows) == []
    assert len(find_farms(rows, min_accounts=4)) == 1


def test_links_chain_across_device_and_network():
    # a,b share a device; b,c,d,e share a network (and all look off) -> 5 accounts in a chain,
    # but nothing has 5 on it by itself, so it's a chained group, not a direct farm
    rows = [req("a", ip="9.9.9.9", device="d1"), req("b", ip="5.5.5.1", device="d1", email="b@mailinator.com")]
    rows += [req(x, ip=f"5.5.5.{i + 2}", email=f"{x}@mailinator.com") for i, x in enumerate("cde")]
    (farm,) = find_farms(rows)
    assert farm.accounts == ["a", "b", "c", "d", "e"]
    assert farm.devices == ["d1"] and farm.networks == ["5.5.5.0/24"]
    assert farm.chained and farm.largest_share == 4


def test_one_person_on_three_family_computers_is_not_a_farm():
    # three unrelated couples, each on their own computer, and one support agent who
    # logged in on all three: 7 accounts in a chain, at most 3 on any one device
    rows = []
    for i in range(3):
        rows += [req(f"p{i}a", device=f"home{i}"), req(f"p{i}b", device=f"home{i}"), req("support", device=f"home{i}")]
    (group,) = find_farms(rows)
    assert group.chained and len(group.accounts) == 7 and group.largest_share == 3 and not group.likely
    report = build_report(rows)
    assert report.abuse_cost == 0 and not report.flagged_accounts  # worth a look at most
    assert report.review_cost == pytest.approx(0.09)
    (finding,) = to_dict(report)["findings"]
    assert finding["severity"] == "review" and finding["evidence"]["relation"] == "chained"
    assert "linked in a chain over 3 shared devices (at most 3 on any one)" in finding["summary"]


def test_a_bridge_doesnt_pull_strangers_into_a_farm():
    # 6 accounts with throwaway emails on one device is a farm. One of them also logged in
    # on a home laptop with someone else: that someone is related, not part of the farm or its cost
    rows = [req(f"f{i}", device="farm-phone", cost=1.0, email=f"f{i}@mailinator.com") for i in range(6)]
    rows += [req("f0", ip="9.9.9.9", device="laptop", email="f0@mailinator.com"),
             req("mom", ip="9.9.9.9", device="laptop", cost=50.0)]
    (farm,) = find_farms(rows)
    assert farm.accounts == [f"f{i}" for i in range(6)] and farm.likely
    assert farm.linked == ["mom"]
    report = build_report(rows)
    assert "mom" not in report.flagged_accounts
    assert report.abuse_cost == pytest.approx(6.0)  # the farm's requests on its phone, not mom's $50
    assert "(+1 more related, not counted)" in "\n".join(render_text(report))


def test_a_farm_rotating_through_devices_is_one_farm():
    # 5 accounts per device, and accounts that moved between devices: one direct farm
    rows = [req(f"a{i}", device="d1") for i in range(5)] + [req(f"b{i}", device="d2") for i in range(5)]
    rows += [req("a0", device="d2")]
    (farm,) = find_farms(rows)
    assert not farm.chained and len(farm.accounts) == 10 and farm.devices == ["d1", "d2"]


def test_empty_device_doesnt_link():
    rows = [req(f"acc{i}", ip=f"{i + 10}.0.0.1", device="") for i in range(10)]
    assert find_farms(rows) == []


def test_network_of():
    assert network_of("185.220.101.7") == "185.220.101.0/24"
    assert network_of("2001:db8::1") == "2001:db8::/64"
    assert network_of("not an ip") is None


def test_mobile_carrier_is_not_a_farm():
    # 20 normal people behind the same carrier range: sharing a network proves nothing
    rows = [req(f"m{i}", ip=f"85.50.10.{i + 2}") for i in range(20)]
    assert find_farms(rows) == []


def test_shared_network_plus_throwaway_emails_is_a_farm():
    rows = [req(f"m{i}", ip=f"85.50.10.{i + 2}", email=f"m{i}@mailinator.com") for i in range(6)]
    (farm,) = find_farms(rows)
    assert farm.networks == ["85.50.10.0/24"]
    assert "throwaway emails" in farm.why


def test_only_the_suspicious_ones_on_a_network_get_linked():
    # a carrier range with 20 real users and 5 throwaway signups: only the 5 are the farm
    rows = [req(f"m{i}", ip=f"85.50.10.{i + 2}") for i in range(20)]
    rows += [req(f"x{i}", ip=f"85.50.10.{i + 100}", email=f"x{i}@mailinator.com") for i in range(5)]
    (farm,) = find_farms(rows)
    assert farm.accounts == [f"x{i}" for i in range(5)]
