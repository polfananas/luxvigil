from datetime import datetime, timezone

import pytest

from luxvigil.checks.farms import find_farms, network_of
from luxvigil.generate import generate, write_csv
from luxvigil.schema import Request, load_logs


def req(account, ip="1.2.3.4", device="", cost=0.01):
    return Request(
        timestamp=datetime(2026, 10, 1, tzinfo=timezone.utc),
        account_id=account, email=f"{account}@example.com", api_key_id=f"key_{account}",
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
    # a,b share a device; b,c,d,e share a network -> one farm of 5
    rows = [req("a", ip="9.9.9.9", device="d1"), req("b", ip="5.5.5.1", device="d1")]
    rows += [req(x, ip=f"5.5.5.{i + 2}") for i, x in enumerate("cde")]
    (farm,) = find_farms(rows)
    assert farm.accounts == ["a", "b", "c", "d", "e"]
    assert farm.devices == ["d1"] and farm.networks == ["5.5.5.0/24"]


def test_empty_device_doesnt_link():
    rows = [req(f"acc{i}", ip=f"{i + 10}.0.0.1", device="") for i in range(10)]
    assert find_farms(rows) == []


def test_network_of():
    assert network_of("185.220.101.7") == "185.220.101.0/24"
    assert network_of("2001:db8::1") == "2001:db8::/64"
    assert network_of("not an ip") is None
