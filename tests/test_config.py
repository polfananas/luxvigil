import ipaddress
from datetime import datetime, timezone

import pytest

from luxvigil.checks.farms import find_farms
from luxvigil.cli import main
from luxvigil.config import Config, ConfigError, load_config
from luxvigil.report import build_report, render_text
from luxvigil.schema import Request


def req(account="a", email="a@gmail.com", key="k"):
    return Request(
        timestamp=datetime(2026, 10, 1, tzinfo=timezone.utc),
        account_id=account, email=email, api_key_id=key,
        ip="1.2.3.4", country="ES", device_id="", prompt_hash="p",
        input_tokens=1, output_tokens=1, cost_usd=0.01,
    )


def write(tmp_path, text):
    path = tmp_path / "luxvigil.toml"
    path.write_text(text)
    return path


def test_full_config(tmp_path):
    cfg = load_config(write(tmp_path, """
[trusted]
networks = ["203.0.113.0/24", "198.51.100.7"]
devices = ["kiosk-01"]

[ignore]
accounts = ["qa_*"]
emails = ["*@acme.io"]
keys = ["key_ci_*"]
"""))
    assert cfg.trusts_ip("203.0.113.55")
    assert cfg.trusts_ip("198.51.100.7")
    assert not cfg.trusts_ip("198.51.100.8")
    assert cfg.trusted_devices == {"kiosk-01"}
    assert cfg.ignores(req(account="qa_bob"))
    assert cfg.ignores(req(email="pol@ACME.io"))
    assert cfg.ignores(req(key="key_ci_42"))
    assert not cfg.ignores(req())


def test_no_file_means_empty_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_config() == Config()


def test_picks_up_luxvigil_toml_in_cwd(tmp_path, monkeypatch):
    write(tmp_path, '[ignore]\naccounts = ["test"]\n')
    monkeypatch.chdir(tmp_path)
    assert load_config().ignore_accounts == ["test"]


def test_typos_are_caught(tmp_path):
    with pytest.raises(ConfigError, match="unknown section"):
        load_config(write(tmp_path, '[trust]\nnetworks = []\n'))
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(write(tmp_path, '[ignore]\naccount = ["x"]\n'))
    with pytest.raises(ConfigError, match="list of strings"):
        load_config(write(tmp_path, '[ignore]\naccounts = "x"\n'))


def test_bad_network(tmp_path):
    with pytest.raises(ConfigError, match="isn't a valid IP"):
        load_config(write(tmp_path, '[trusted]\nnetworks = ["10.0.0.0/33"]\n'))


def test_bad_toml(tmp_path):
    with pytest.raises(ConfigError, match="isn't valid TOML"):
        load_config(write(tmp_path, "[trusted\n"))


# --- with the checks ---


def office(n=20):
    """n real people behind the same office network"""
    rows = []
    for i in range(n):
        r = req(account=f"emp{i}", email=f"emp{i}@acme.io", key=f"k{i}")
        r.ip = f"203.0.113.{i + 10}"
        rows.append(r)
    return rows


def test_trusted_network_never_links():
    # even throwaway signups don't get linked through a network you trust
    rows = office()
    for r in rows:
        r.email = f"{r.account_id}@mailinator.com"
    assert len(find_farms(rows)) == 1
    cfg = Config(trusted_networks=[ipaddress.ip_network("203.0.113.0/24")])
    assert find_farms(rows, config=cfg) == []


def test_trusted_device_doesnt_link():
    rows = [req(account=f"a{i}") for i in range(6)]
    for i, r in enumerate(rows):
        r.ip = f"10.{i}.0.1"
        r.device_id = "kiosk-01"
    assert len(find_farms(rows)) == 1
    assert find_farms(rows, config=Config(trusted_devices={"kiosk-01"})) == []


def test_ignored_accounts_still_count_in_total_spend():
    rows = office()
    cfg = Config(ignore_emails=["*@acme.io"], path="luxvigil.toml")
    report = build_report(rows, config=cfg)
    assert report.clean
    assert report.accounts == 20 and report.total_cost == sum(r.cost_usd for r in rows)
    assert report.ignored_accounts == 20
    assert any("skipping 20 accounts" in line for line in render_text(report))


def test_cli_config(tmp_path, capsys):
    logs = tmp_path / "logs.csv"
    main(["generate", "--out", str(logs), "--users", "20"])
    bad = write(tmp_path, "[nope]\n")
    assert main(["scan", str(logs), "--config", str(bad)]) == 1
    assert "bad config" in capsys.readouterr().err
    good = write(tmp_path, '[ignore]\nemails = ["*@mailinator.com"]\n')
    assert main(["scan", str(logs), "--config", str(good)]) == 0
    assert "using" in capsys.readouterr().out
