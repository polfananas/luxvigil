from datetime import datetime, timezone

import pytest

from luxvigil.config import Config, ConfigError, load_config
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
