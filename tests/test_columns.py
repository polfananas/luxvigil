import csv
from datetime import datetime, timezone

import pytest

from luxvigil.cli import main
from luxvigil.generate import generate, write_csv
from luxvigil.report import build_report
from luxvigil.schema import LogFormatError, _parse_time, load_logs, resolve_columns

# how a lot of real exports name things (OpenAI-ish usage logs)
RENAMED = {
    "timestamp": "created_at",
    "account_id": "user_id",
    "email": "user_email",
    "api_key_id": "key_id",
    "ip": "client_ip",
    "country": "country_code",
    "device_id": "fingerprint",
    "prompt_hash": "prompt_sha256",
    "input_tokens": "prompt_tokens",
    "output_tokens": "completion_tokens",
}


def write_renamed(rows, path, names, drop=()):
    with open(path, "w", newline="", encoding="utf-8") as f:
        fields = [names.get(c, c) for c in rows[0] if c not in drop]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({names.get(c, c): v for c, v in r.items() if c not in drop})


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    rows, _ = generate()
    d = tmp_path_factory.mktemp("logs")
    write_csv(rows, d / "ours.csv")
    write_renamed(rows, d / "theirs.csv", RENAMED, drop={"cost_usd"})
    return d


def test_renamed_logs_give_the_same_report(sample):
    ours = build_report(load_logs(sample / "ours.csv"))
    notes = []
    # the generator prices at $3 / $15 per 1M tokens
    theirs = build_report(load_logs(sample / "theirs.csv", price_in=3, price_out=15, notes=notes))
    assert theirs.abuse_cost == pytest.approx(ours.abuse_cost, rel=1e-4)
    assert theirs.flagged_accounts == ours.flagged_accounts
    assert "using 'user_email' as email" in notes
    assert len(notes) == len(RENAMED)  # every alias match is reported


def test_explicit_map_wins(tmp_path):
    rows, _ = generate(n_users=10)
    names = {"email": "correo", "account_id": "cliente"}
    write_renamed(rows, tmp_path / "x.csv", names)
    loaded = load_logs(tmp_path / "x.csv", mapping={"email": "correo", "account_id": "cliente"})
    assert len(loaded) == len(rows)
    with pytest.raises(LogFormatError, match="--map account_id=your_column_name"):
        load_logs(tmp_path / "x.csv", mapping={"email": "correo"})


def test_no_cost_and_no_prices_fails_clearly(sample):
    with pytest.raises(LogFormatError, match="--price-in"):
        load_logs(sample / "theirs.csv")


def test_bad_maps():
    with pytest.raises(LogFormatError, match="don't know"):
        resolve_columns(["a"], {"emial": "a"})
    with pytest.raises(LogFormatError, match="no column 'nope'"):
        resolve_columns(["a", "b"], {"email": "nope"})


def test_raw_api_key_column_is_never_picked_up():
    cols, _ = resolve_columns(["api_key", "user_id"])
    assert "api_key_id" not in cols
    assert cols["account_id"] == "user_id"


def test_case_and_spaces_dont_matter():
    cols, notes = resolve_columns(["Timestamp", " EMAIL ", "User_ID"])
    assert cols["timestamp"] == "Timestamp"
    assert cols["email"] == " EMAIL "
    assert cols["account_id"] == "User_ID"
    assert notes == ["using 'User_ID' as account_id"]


def test_timestamps():
    expected = datetime(2026, 10, 1, 14, 3, 22, tzinfo=timezone.utc)
    assert _parse_time("2026-10-01T14:03:22Z") == expected
    assert _parse_time("2026-10-01T14:03:22") == expected  # no timezone = UTC
    assert _parse_time("2026-10-01 14:03:22+00:00") == expected
    assert _parse_time("1790863402") == expected  # unix seconds
    assert _parse_time("1790863402000") == expected  # unix milliseconds


def test_cli_map(sample, capsys):
    assert main(["scan", str(sample / "theirs.csv"), "--price-in", "3", "--price-out", "15"]) == 0
    captured = capsys.readouterr()
    assert "looks like abuse" in captured.out
    assert "note: using 'user_email' as email" in captured.err
    assert main(["scan", str(sample / "theirs.csv"), "--map", "nonsense"]) == 1
