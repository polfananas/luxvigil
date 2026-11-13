import csv
import json

import pytest

from luxvigil.cli import main
from luxvigil.formats.litellm import prompt_hash
from luxvigil.report import build_report
from luxvigil.schema import LogFormatError, load_logs

# a hashed key, the way LiteLLM stores it (sha256 of the real key)
KEY = "88dc28d0f030c55ed4ab77ed8faf098196cb1c05df778539800c9f1243fe6b4b"
TEMPLATE = "Write a 140 character Etsy title for this product, include the keywords below"


def spend_log(i, end_user="", user="", prompt=None, ip="185.220.101.7", day=1):
    """One row of LiteLLM_SpendLogs, as /spend/logs/v2 returns it."""
    row = {
        "request_id": f"req-{i}",
        "call_type": "acompletion",
        "api_key": KEY,
        "spend": 0.0021,
        "total_tokens": 300,
        "prompt_tokens": 200,
        "completion_tokens": 100,
        "startTime": f"2026-10-{day:02d}T10:{i % 60:02d}:00.123000+00:00",
        "endTime": f"2026-10-{day:02d}T10:{i % 60:02d}:02.000000+00:00",
        "model": "gpt-4o-mini",
        "user": user,
        "end_user": end_user,
        "requester_ip_address": ip,
        "messages": {},
        "metadata": {"user_api_key_alias": "free-tier"},
    }
    if prompt is not None:
        row["messages"] = [{"role": "system", "content": "be nice"},
                           {"role": "user", "content": [{"type": "text", "text": prompt}]}]
    return row


def write_json(tmp_path, rows):
    path = tmp_path / "spend_logs.json"
    path.write_text(json.dumps({"data": rows, "total": len(rows), "page": 1, "page_size": 100}))
    return path


def test_api_export_is_read(tmp_path):
    notes = []
    rows = load_logs(write_json(tmp_path, [spend_log(1, end_user="cust_1")]), fmt="litellm", notes=notes)
    (r,) = rows
    assert r.account_id == "cust_1"
    assert r.api_key_id == KEY  # already a hash in LiteLLM, safe to keep
    assert r.ip == "185.220.101.7"
    assert (r.input_tokens, r.output_tokens, r.cost_usd) == (200, 100, 0.0021)
    assert r.prompt_hash == "" and r.prompt_chars is None
    assert any("no prompts in these logs" in n for n in notes)


def test_account_falls_back_to_key_owner_then_key(tmp_path):
    rows = load_logs(write_json(tmp_path, [spend_log(1, user="u_owner"), spend_log(2)]), fmt="litellm")
    assert [r.account_id for r in rows] == ["u_owner", KEY]


def test_prompts_are_hashed_and_the_text_dropped(tmp_path):
    (r,) = load_logs(write_json(tmp_path, [spend_log(1, end_user="c", prompt=TEMPLATE)]), fmt="litellm")
    assert r.prompt_hash == prompt_hash(TEMPLATE)
    assert r.prompt_chars == len(TEMPLATE)
    assert TEMPLATE not in repr(r)


def test_database_csv_export(tmp_path):
    # Postgres exports come with lowercased headers and "+00" timezones
    path = tmp_path / "spend_logs.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["request_id", "api_key", "spend", "prompt_tokens", "completion_tokens", "starttime",
                    "end_user", "user", "requester_ip_address", "messages"])
        w.writerow(["r1", KEY, "0.5", "10", "20", "2026-10-01 10:00:00.5+00", "cust_9", "", "",
                    json.dumps([{"role": "user", "content": TEMPLATE}])])
    notes = []
    (r,) = load_logs(path, fmt="litellm", notes=notes)
    assert r.account_id == "cust_9" and r.cost_usd == 0.5
    assert r.timestamp.isoformat() == "2026-10-01T10:00:00.500000+00:00"
    assert r.prompt_chars == len(TEMPLATE)
    # no IPs at all is said once, not as a "bad IP" on every row
    assert any("no IPs in these logs" in n for n in notes)
    assert not any("valid IP" in n for n in notes)


def test_bots_and_farms_show_up(tmp_path):
    # 6 free-tier "customers" on one network, each hammering the same template
    rows = [spend_log(i * 10 + k, end_user=f"cust_{i}", prompt=TEMPLATE, ip=f"185.220.101.{i}")
            for i in range(6) for k in range(4)]
    report = build_report(load_logs(write_json(tmp_path, rows), fmt="litellm"))
    assert len(report.bots) == 1 and len(report.bots[0].accounts) == 6
    assert len(report.farms) == 1
    # no emails or countries in LiteLLM logs: said, not hidden
    assert report.skipped["disposable emails"] == "no emails in the logs"
    assert report.skipped["shared or leaked keys"] == "no countries in the logs"


def test_cli(tmp_path, capsys):
    path = write_json(tmp_path, [spend_log(i, end_user=f"c{i}") for i in range(3)])
    assert main(["scan", str(path), "--format", "litellm"]) == 0
    out = capsys.readouterr()
    assert "3 requests" in out.out and "reading LiteLLM spend logs" in out.err


def test_map_doesnt_go_with_litellm(tmp_path):
    with pytest.raises(LogFormatError, match="--map doesn't go with --format litellm"):
        load_logs(write_json(tmp_path, [spend_log(1)]), fmt="litellm", mapping={"email": "x"})


def test_empty_export(tmp_path):
    with pytest.raises(LogFormatError, match="no requests"):
        load_logs(write_json(tmp_path, []), fmt="litellm")
