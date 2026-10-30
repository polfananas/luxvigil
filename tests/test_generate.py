import pytest

from luxvigil.generate import DISPOSABLE_DOMAINS, generate, write_csv
from luxvigil.schema import LogFormatError, load_logs


def test_same_seed_same_logs():
    assert generate(n_users=20, seed=1)[0] == generate(n_users=20, seed=1)[0]


def test_planted_abuse_is_there():
    rows, planted = generate(n_users=20)
    farm = set(planted["farm_big"])
    farm_rows = [r for r in rows if r["account_id"] in farm]
    assert len(farm) == 60
    assert all(r["email"].split("@")[1] in DISPOSABLE_DOMAINS for r in farm_rows)
    assert all(r["ip"].startswith("185.220.101.") for r in farm_rows)

    device_farm = {r["device_id"] for r in rows if r["account_id"] in set(planted["farm_device"])}
    assert len(device_farm) == 1

    leaked = planted["shared_key"][0]
    assert len({r["country"] for r in rows if r["api_key_id"] == leaked}) >= 9


def test_roundtrip_csv(tmp_path):
    rows, _ = generate(n_users=10)
    path = tmp_path / "logs.csv"
    write_csv(rows, path)
    loaded = load_logs(path)
    assert len(loaded) == len(rows)
    assert loaded[0].cost_usd > 0


def test_missing_columns_fail(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("timestamp,account_id\n2026-10-01T00:00:00Z,a\n")
    with pytest.raises(LogFormatError, match="missing columns"):
        load_logs(path)
