import csv
import json

import pytest

from luxvigil.cli import main
from luxvigil.report import build_report, render_text
from luxvigil.schema import LogFormatError, load_logs

# the bare minimum a log needs: when, who, and how many tokens (plus a cost or prices)
MINIMAL = ["timestamp", "account_id", "input_tokens", "output_tokens", "cost_usd"]


def write(tmp_path, header, rows):
    path = tmp_path / "logs.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


def minimal_logs(tmp_path, accounts=3):
    rows = [[f"2026-10-0{d}T10:00:00Z", f"u{a}", 100, 100, 0.2] for d in range(1, 6) for a in range(accounts)]
    return write(tmp_path, MINIMAL, rows)


def test_minimal_logs_load(tmp_path):
    notes = []
    rows = load_logs(minimal_logs(tmp_path), notes=notes)
    assert len(rows) == 15
    assert rows[0].email == rows[0].ip == rows[0].prompt_hash == rows[0].api_key_id == ""
    # no IP column at all is not the same as bad IPs, so no warning about it
    assert not any("valid IP" in n for n in notes)


@pytest.mark.parametrize("missing", ["timestamp", "account_id", "input_tokens", "output_tokens"])
def test_the_must_haves_are_still_required(tmp_path, missing):
    header = [c for c in MINIMAL if c != missing]
    path = write(tmp_path, header, [["x"] * len(header)])
    with pytest.raises(LogFormatError, match=f"missing columns: {missing}"):
        load_logs(path)


def test_report_says_what_it_skipped(tmp_path):
    lines = render_text(build_report(load_logs(minimal_logs(tmp_path))))
    text = "\n".join(lines)
    assert "⏭️  disposable emails: skipped, no emails in the logs" in text
    assert "⏭️  account farms: skipped, no devices or IPs in the logs" in text
    assert "⏭️  copy-paste bots: skipped, no prompt hashes in the logs" in text
    # never "all clear" for a check that couldn't look
    assert "no disposable emails" not in text


def test_keys_need_countries_too(tmp_path):
    header = MINIMAL + ["api_key_id"]
    rows = [["2026-10-01T10:00:00Z", "u1", 1, 1, 0.1, "key_1"]]
    report = build_report(load_logs(write(tmp_path, header, rows)))
    assert report.skipped["shared or leaked keys"] == "no countries in the logs"


def test_json_lists_skipped_checks(tmp_path, capsys):
    assert main(["scan", str(minimal_logs(tmp_path)), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["skipped_checks"]["shared Gmail inboxes"] == "no emails in the logs"
    assert "spend spikes" not in data["skipped_checks"]  # spikes only need time and cost
