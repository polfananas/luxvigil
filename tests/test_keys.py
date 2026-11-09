from datetime import datetime, timedelta, timezone

from luxvigil.checks.keys import find_shared_keys
from luxvigil.generate import generate, write_csv
from luxvigil.schema import Request, load_logs

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def req(key, country, hours=0, account="a", ip="1.2.3.4", cost=0.01):
    return Request(
        timestamp=T0 + timedelta(hours=hours),
        account_id=account, email=f"{account}@example.com", api_key_id=key,
        ip=ip, country=country, device_id="", prompt_hash="p",
        input_tokens=10, output_tokens=10, cost_usd=cost,
    )


def test_finds_the_planted_leak(tmp_path):
    rows, planted = generate()
    path = tmp_path / "logs.csv"
    write_csv(rows, path)
    found = find_shared_keys(load_logs(path))
    assert [k.key for k in found] == planted["shared_key"]
    assert found[0].peak_countries >= 5 and found[0].cost_usd > 0


def test_traveller_is_fine():
    # home in Spain, a trip to France and the UK over a few days
    rows = [req("k", "ES", h) for h in range(0, 48, 4)]
    rows += [req("k", "FR", 60), req("k", "GB", 90)]
    assert find_shared_keys(rows) == []


def test_many_countries_spread_over_weeks_is_fine():
    # 6 countries, but one per week: never 5 in the same day
    rows = [req("k", c, hours=i * 24 * 7) for i, c in enumerate(["ES", "FR", "GB", "DE", "IT", "PT"])]
    assert find_shared_keys(rows) == []


def test_burst_from_many_countries_is_flagged():
    rows = [req("k", "ES", 0)]  # owner, at home
    rows += [req("k", c, hours=10 + i, ip=f"9.9.9.{i}", cost=1.0) for i, c in enumerate(["BR", "VN", "NG", "RU", "ID"])]
    (key,) = find_shared_keys(rows)
    assert key.home_country == "ES"
    assert key.peak_countries == 6
    assert key.requests == 5 and key.cost_usd == 5.0  # only the away traffic counts


def test_window_and_threshold_are_tunable():
    rows = [req("k", c, hours=i * 10) for i, c in enumerate(["ES", "FR", "GB", "DE", "IT"])]
    assert find_shared_keys(rows) == []  # spread over 40h, not inside 24h
    assert len(find_shared_keys(rows, window_hours=48)) == 1
    assert len(find_shared_keys(rows, min_countries=3)) == 1



def test_home_is_where_the_key_usually_lives_not_the_first_request():
    # owner's first request was from a trip to France, then weeks at home in Spain, then a leak
    rows = [req("k", "FR", 0)]
    rows += [req("k", "ES", h) for h in range(24, 24 * 20, 12)]
    rows += [req("k", c, hours=24 * 21 + i, ip=f"9.9.9.{i}", cost=1.0) for i, c in enumerate(["BR", "VN", "NG", "RU", "ID"])]
    (key,) = find_shared_keys(rows)
    assert key.home_country == "ES"
    assert key.requests == 6  # the 5 leaked ones + the trip, not the 38 at home


def test_a_big_leak_cant_steal_home():
    # 3 requests from the owner, then 50 from abroad: home stays with the owner
    rows = [req("k", "ES", h) for h in range(3)]
    rows += [req("k", ["BR", "VN", "NG", "RU", "ID"][i % 5], hours=48 + i * 0.1) for i in range(50)]
    (key,) = find_shared_keys(rows)
    assert key.home_country == "ES"


def test_why_follows_the_window():
    rows = [req("k", c, hours=i * 10) for i, c in enumerate(["ES", "FR", "GB", "DE", "IT"])]
    (key,) = find_shared_keys(rows, window_hours=48)
    assert "in 48h" in key.why
