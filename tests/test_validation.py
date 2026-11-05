import csv

import pytest

from luxvigil.cli import main
from luxvigil.schema import COLUMNS, LogFormatError, load_logs


def write(tmp_path, *overrides, header=None):
    base = dict(timestamp="2026-10-01T00:00:00Z", account_id="a", email="a@gmail.com", api_key_id="k",
                ip="1.2.3.4", country="ES", device_id="", prompt_hash="p",
                input_tokens="10", output_tokens="10", cost_usd="0.01")
    path = tmp_path / "logs.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header or list(COLUMNS))
        w.writeheader()
        for o in overrides:
            w.writerow({**base, **o})
    return path


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "-5", "abc", ""])
def test_bad_cost_is_rejected(tmp_path, value):
    with pytest.raises(LogFormatError, match="line 2: cost_usd"):
        load_logs(write(tmp_path, {"cost_usd": value}))


@pytest.mark.parametrize("value", ["-100", "12.5", "nan", "lots"])
def test_bad_tokens_are_rejected(tmp_path, value):
    with pytest.raises(LogFormatError, match="input_tokens"):
        load_logs(write(tmp_path, {"input_tokens": value}))


def test_whole_floats_are_fine(tmp_path):
    (row,) = load_logs(write(tmp_path, {"input_tokens": "12.0", "cost_usd": "0"}))
    assert row.input_tokens == 12 and row.cost_usd == 0


def test_error_points_at_the_right_line(tmp_path):
    with pytest.raises(LogFormatError, match="line 4"):
        load_logs(write(tmp_path, {}, {}, {"cost_usd": "nan"}))


def test_empty_account_is_rejected(tmp_path):
    with pytest.raises(LogFormatError, match="account_id is empty"):
        load_logs(write(tmp_path, {"account_id": "  "}))


def test_short_row_is_rejected(tmp_path):
    path = write(tmp_path, {})
    with open(path, "a") as f:
        f.write("2026-10-01T00:00:00Z,b,b@gmail.com\n")
    with pytest.raises(LogFormatError, match="line 3: this row has fewer columns"):
        load_logs(path)


def test_duplicates_and_bad_ips_are_reported(tmp_path):
    notes = []
    rows = load_logs(write(tmp_path, {}, {}, {"account_id": "b", "ip": "nope"}, {"account_id": "c", "ip": ""}),
                     notes=notes)
    assert len(rows) == 4  # nothing dropped, just flagged
    assert any("1 duplicate row (" in n for n in notes)
    assert any("2 rows without a valid IP" in n for n in notes)


def test_clean_file_has_no_warnings(tmp_path):
    notes = []
    load_logs(write(tmp_path, {}, {"account_id": "b"}), notes=notes)
    assert notes == []


def test_skip_bad_rows(tmp_path):
    path = write(tmp_path, {}, {"account_id": "b", "cost_usd": "nan"}, {"account_id": "c", "input_tokens": "-1"})
    with pytest.raises(LogFormatError, match="--skip-bad-rows"):
        load_logs(path)
    notes = []
    rows = load_logs(path, skip_bad=True, notes=notes)
    assert [r.account_id for r in rows] == ["a"]
    assert any("skipped 2 bad rows, the first one was line 3: cost_usd" in n for n in notes)


def test_cli_skip_bad_rows_and_negative_prices(tmp_path, capsys):
    path = write(tmp_path, {}, {"account_id": "b", "cost_usd": "inf"})
    assert main(["scan", str(path)]) == 1
    assert main(["scan", str(path), "--skip-bad-rows"]) == 0
    assert "skipped 1 bad row" in capsys.readouterr().err
    assert main(["scan", str(path), "--price-in", "-1", "--price-out", "1"]) == 1
