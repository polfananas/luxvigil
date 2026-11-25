import csv

import pytest

from luxvigil.cli import main
from luxvigil.report import build_report
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


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "-5", "abc"])
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


def test_exact_copies_and_bad_ips_are_reported(tmp_path):
    notes = []
    rows = load_logs(write(tmp_path, {}, {}, {"account_id": "b", "ip": "nope"}, {"account_id": "c", "ip": ""}),
                     notes=notes)
    assert len(rows) == 4  # no request id, so the copy could be a real second request: kept
    assert any("1 row is an exact copy" in n and "Kept" in n and "$0.01" in n for n in notes)
    assert any("2 rows without a valid IP" in n for n in notes)


def test_identical_requests_without_a_request_id_are_all_counted(tmp_path):
    # 5 accounts each sending the same template 3 times in the same second, logs with no
    # request id. Counting each row once used to turn 15 requests and $15 into 5 and $5,
    # and lose the bot.
    rows = [{"account_id": f"u{k}", "prompt_hash": "template", "cost_usd": "1"} for k in range(5)]
    path = write(tmp_path, *(rows * 3))
    notes = []
    report = build_report(load_logs(path, notes=notes))
    assert report.requests == 15 and report.bots
    assert report.total_cost == pytest.approx(15.0)
    assert any("10 rows are an exact copy" in n and "$10.00" in n and "--drop-duplicates" in n for n in notes)


def test_a_doubled_export_without_request_ids_can_still_be_dropped(tmp_path):
    # 5 accounts sending one template once each, and the export got tripled: with
    # --drop-duplicates it's 5 requests again, no account repeated anything, no bot
    rows = [{"account_id": f"u{k}", "prompt_hash": "template", "cost_usd": "0.2"} for k in range(5)]
    notes = []
    report = build_report(load_logs(write(tmp_path, *(rows * 3)), drop_duplicates=True, notes=notes))
    assert (report.requests, report.bots) == (5, [])
    assert report.total_cost == pytest.approx(1.0)
    assert any("left out because of --drop-duplicates" in n and "10 rows and $2.00 fewer" in n for n in notes)


def test_a_repeated_request_id_is_the_same_request(tmp_path):
    # with a request id the copies are proven, so they're dropped without asking
    rows = [{"account_id": f"u{k}", "prompt_hash": "template", "cost_usd": "0.2", "request_id": f"r{k}"}
            for k in range(5)]
    notes = []
    path = write(tmp_path, *(rows * 3), header=[*COLUMNS, "request_id"])
    report = build_report(load_logs(path, notes=notes))
    assert (report.requests, report.bots) == (5, [])
    assert any("10 rows repeat a request id" in n and "left out" in n for n in notes)
    kept = build_report(load_logs(path, keep_duplicates=True))
    assert kept.requests == 15


def test_rows_that_differ_only_in_their_request_id_are_not_duplicates(tmp_path):
    # two requests in the same second, everything else the same, are still two
    rows = [{"account_id": "a", "request_id": "r1"}, {"account_id": "a", "request_id": "r2"}]
    notes = []
    assert len(load_logs(write(tmp_path, *rows, header=[*COLUMNS, "request_id"]), notes=notes)) == 2
    assert notes == []


def test_keep_and_drop_duplicates_dont_go_together(tmp_path):
    with pytest.raises(LogFormatError, match="don't go together"):
        load_logs(write(tmp_path, {}), keep_duplicates=True, drop_duplicates=True)
    with pytest.raises(SystemExit):
        main(["scan", str(write(tmp_path, {})), "--keep-duplicates", "--drop-duplicates"])


def test_a_row_with_more_columns_than_the_header_is_a_bad_row(tmp_path):
    path = tmp_path / "logs.csv"
    path.write_text("timestamp,account_id,input_tokens,output_tokens,cost_usd\n"
                    "2026-01-01T00:00:00Z,a,1,1,1,EXTRA\n2026-01-01T00:00:00Z,b,1,1,1\n")
    with pytest.raises(LogFormatError, match="more columns than the header"):
        load_logs(path)
    assert [r.account_id for r in load_logs(path, skip_bad=True)] == ["b"]


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



# exports from Excel and friends, and numbers that aren't really dates

def write_raw(tmp_path, text, encoding="utf-8"):
    path = tmp_path / "raw.csv"
    path.write_bytes(text.encode(encoding))
    return path


HEADER = ",".join(c for c in COLUMNS if c not in ("prompt_chars", "model"))  # ROW below has the 11 classic columns
ROW = "2026-10-01T00:00:00Z,a,a@gmail.com,k,1.2.3.4,ES,,p,10,10,0.01"


def test_excel_bom_is_fine(tmp_path):
    (row,) = load_logs(write_raw(tmp_path, f"{HEADER}\n{ROW}\n", encoding="utf-8-sig"))
    assert row.account_id == "a"


def test_semicolons_and_tabs(tmp_path):
    for sep in (";", "\t"):
        text = f"{HEADER}\n{ROW}\n".replace(",", sep)
        (row,) = load_logs(write_raw(tmp_path, text))
        assert row.email == "a@gmail.com" and row.cost_usd == 0.01


def test_empty_file(tmp_path):
    with pytest.raises(LogFormatError, match="the file is empty"):
        load_logs(write_raw(tmp_path, ""))


@pytest.mark.parametrize("value", ["20261001", "2026", "-5"])
def test_numbers_that_arent_unix_timestamps(tmp_path, value):
    with pytest.raises(LogFormatError, match="doesn't look like a date"):
        load_logs(write(tmp_path, {"timestamp": value}))


# prompt_chars is optional: a number, empty for "don't know", or not there at all

def test_prompt_chars_is_read_when_there(tmp_path):
    (row,) = load_logs(write(tmp_path, {"prompt_chars": "42"}))
    assert row.prompt_chars == 42


def test_prompt_chars_empty_or_missing_means_unknown(tmp_path):
    (row,) = load_logs(write(tmp_path, {"prompt_chars": ""}))
    assert row.prompt_chars is None
    header = [c for c in COLUMNS if c != "prompt_chars"]
    (row,) = load_logs(write(tmp_path, {}, header=header))
    assert row.prompt_chars is None


def test_bad_prompt_chars_is_rejected(tmp_path):
    with pytest.raises(LogFormatError, match="prompt_chars"):
        load_logs(write(tmp_path, {"prompt_chars": "-3"}))
