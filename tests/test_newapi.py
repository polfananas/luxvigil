import csv
import json

import pytest

from luxvigil.cli import main
from luxvigil.report import build_report
from luxvigil.schema import LogFormatError, load_logs


def log(i, user="alice", kind=2, quota=1000, ip="", token=7, minute=None, request_id=None):
    """One row of the `logs` table, as New API returns it."""
    return {
        "id": i,
        "user_id": 40 + i % 3,
        "created_at": 1790000000 + 60 * (i if minute is None else minute),
        "type": kind,
        "content": "模型倍率 0.15，分组倍率 1",
        "username": user,
        "token_name": "default",
        "model_name": "gpt-4o-mini",
        "quota": quota,
        "prompt_tokens": 200,
        "completion_tokens": 100,
        "use_time": 2,
        "is_stream": False,
        "channel": 1,
        "token_id": token,
        "group": "default",
        "ip": ip,
        "request_id": request_id if request_id is not None else f"req-{i}",
        "other": "{}",
    }


def ndjson(tmp_path, rows, trailer=None):
    """New API's /api/log/export: one log per line, then how it went."""
    path = tmp_path / "logs.jsonl"
    lines = [json.dumps(r) for r in rows]
    lines.append(json.dumps(trailer or {"success": True, "message": "", "data": {"count": len(rows)}}))
    path.write_text("\n".join(lines) + "\n")
    return path


def test_the_export_reads_like_our_own_logs(tmp_path):
    notes = []
    rows = load_logs(ndjson(tmp_path, [log(1, quota=500_000), log(2)]), fmt="new-api", notes=notes)
    assert [r.account_id for r in rows] == ["alice", "alice"]
    assert rows[0].api_key_id == "token:7"
    assert rows[0].cost_usd == 1.0  # 500,000 quota is $1
    assert (rows[0].input_tokens, rows[0].output_tokens) == (200, 100)
    assert rows[0].timestamp.year == 2026
    assert any("quota / 500,000" in n for n in notes)


def test_only_usage_rows_are_requests(tmp_path):
    notes = []
    rows = load_logs(ndjson(tmp_path, [log(1), log(2, kind=1), log(3, kind=5), log(4, kind=7)]),
                     fmt="new-api", notes=notes)
    assert len(rows) == 1
    assert any("left out 3 log rows that aren't usage" in n for n in notes)


def test_both_api_shapes_and_a_plain_list(tmp_path):
    rows = [log(1), log(2)]
    shapes = {
        "new-api": {"success": True, "message": "", "data": {"page": 1, "page_size": 100, "total": 2, "items": rows}},
        "one-api": {"success": True, "message": "", "data": rows},
        "list": rows,
    }
    for name, data in shapes.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(data))
        assert len(load_logs(path, fmt="one-api")) == 2, name


def test_database_export_as_csv(tmp_path):
    # the `logs` table straight from the database: snake_case columns, channel_id, Postgres-style caps
    path = tmp_path / "logs.csv"
    with open(path, "w", newline="") as f:
        fields = ["ID", "USER_ID", "CREATED_AT", "TYPE", "USERNAME", "TOKEN_ID", "MODEL_NAME", "QUOTA",
                  "PROMPT_TOKENS", "COMPLETION_TOKENS", "CHANNEL_ID", "IP", "REQUEST_ID"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow({"ID": 1, "USER_ID": 9, "CREATED_AT": 1790000000, "TYPE": 2, "USERNAME": "", "TOKEN_ID": 3,
                    "MODEL_NAME": "glm-4", "QUOTA": 250000, "PROMPT_TOKENS": 10, "COMPLETION_TOKENS": 5,
                    "CHANNEL_ID": 1, "IP": "1.2.3.4", "REQUEST_ID": "r1"})
    (row,) = load_logs(path, fmt="new-api")
    assert row.account_id == "id:9"  # no username: the user id
    assert row.cost_usd == 0.5 and row.ip == "1.2.3.4"


def test_another_quota_rate(tmp_path):
    (row,) = load_logs(ndjson(tmp_path, [log(1, quota=1000)]), fmt="new-api", quota_per_usd=1000)
    assert row.cost_usd == 1.0


def test_quota_rate_only_goes_with_these_formats(tmp_path):
    with pytest.raises(LogFormatError, match="--quota-per-usd"):
        load_logs(ndjson(tmp_path, [log(1)]), quota_per_usd=1000)


def test_an_export_that_broke_halfway_is_an_error(tmp_path):
    path = ndjson(tmp_path, [log(1)], trailer={"success": False, "message": "Failed to export logs"})
    with pytest.raises(LogFormatError, match="stopped with an error after 1 rows"):
        load_logs(path, fmt="new-api")


def test_no_ips_is_said_once_not_as_bad_ips(tmp_path):
    notes = []
    load_logs(ndjson(tmp_path, [log(1), log(2)]), fmt="new-api", notes=notes)
    assert any("New API only records them" in n for n in notes)
    assert not any("valid IP" in n for n in notes)


def test_a_request_exported_twice_counts_once(tmp_path):
    rows = load_logs(ndjson(tmp_path, [log(1, request_id="same"), log(1, request_id="same")]), fmt="new-api")
    assert len(rows) == 1


def users_file(tmp_path, users):
    """GET /api/user/ saved as JSON, with the fields we must never need."""
    path = tmp_path / "users.json"
    items = [{"id": i, "username": name, "email": email, "password": "$2a$10$hash", "access_token": "secret",
              "quota": 500000, "aff_code": f"aff{i}", "inviter_id": 0} for i, (name, email) in enumerate(users)]
    path.write_text(json.dumps({"success": True, "data": {"items": items, "total": len(items)}}))
    return path


def test_emails_come_from_the_users_table(tmp_path):
    notes = []
    users = users_file(tmp_path, [("alice", "Alice@Example.com")])
    rows = load_logs(ndjson(tmp_path, [log(1), log(2, user="bob")]), fmt="new-api", users=str(users), notes=notes)
    assert [r.email for r in rows] == ["alice@example.com", ""]
    assert any("found for 1 of 2 accounts" in n for n in notes)


def test_users_only_goes_with_these_formats(tmp_path):
    with pytest.raises(LogFormatError, match="--users"):
        load_logs(ndjson(tmp_path, [log(1)]), users="users.json")


def test_signup_farm_on_one_network_is_found_with_the_users_table(tmp_path):
    # the new-api issue: a pile of accounts from one IP range draining their signup credit.
    # A shared network only links accounts with something else off, so it takes the emails
    bots = [(f"bot{i}", f"bot{i}@mailinator.com") for i in range(8)]
    real = [(f"real{i}", f"real{i}@gmail.com") for i in range(20)]
    rows = [log(i, user=f"bot{i}", ip=f"45.11.2.{i}", token=100 + i, quota=200_000) for i in range(8)]
    rows += [log(50 + i, user=f"real{i}", ip=f"81.{i}.3.4", token=200 + i) for i in range(20)]
    logs = ndjson(tmp_path, rows)
    assert not build_report(load_logs(logs, fmt="new-api")).farms  # logs alone: no way to tell
    report = build_report(load_logs(logs, fmt="new-api", users=str(users_file(tmp_path, bots + real))))
    assert report.farms and len(report.farms[0].accounts) == 8


def test_cli(tmp_path, capsys):
    path = ndjson(tmp_path, [log(i, user=f"u{i}") for i in range(3)])
    assert main(["scan", str(path), "--format", "new-api"]) == 0
    out = capsys.readouterr()
    assert "3 requests · 3 accounts" in out.out
    assert main(["scan", str(path), "--format", "new-api", "--quota-per-usd", "0"]) == 1
