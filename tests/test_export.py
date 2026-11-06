import json

import pytest

from luxvigil.cli import main
from luxvigil.export import to_dict
from luxvigil.generate import generate, write_csv
from luxvigil.report import build_report
from luxvigil.schema import load_logs


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    rows, planted = generate()
    path = tmp_path_factory.mktemp("logs") / "logs.csv"
    write_csv(rows, path)
    logs = load_logs(path)
    return path, logs, to_dict(build_report(logs))


def test_totals_match_the_report(sample):
    _, logs, data = sample
    report = build_report(logs)
    t = data["totals"]
    assert t["likely_abuse_usd"] == pytest.approx(report.abuse_cost, abs=1e-3)
    assert t["worth_a_look_usd"] == pytest.approx(report.review_cost, abs=1e-3)
    assert t["likely_abuse_usd"] + t["worth_a_look_usd"] <= t["spend_usd"]


def test_every_finding_is_complete(sample):
    _, logs, data = sample
    accounts = {r.account_id for r in logs}
    assert data["findings"]
    for f in data["findings"]:
        assert f["severity"] in {"likely", "review"}
        assert f["summary"] and f["innocent_explanation"] and f["next_step"]
        assert set(f["accounts"]) <= accounts  # points at real accounts in the logs
        assert f["cost_usd"] >= 0


def test_ids_are_stable_and_unique(sample):
    _, logs, data = sample
    again = to_dict(build_report(logs))
    ids = [f["id"] for f in data["findings"]]
    assert ids == [f["id"] for f in again["findings"]]
    assert len(ids) == len(set(ids))


def test_no_raw_emails_in_the_output(sample):
    _, _, data = sample
    assert "@" not in json.dumps(data)


def test_cli_json(sample, capsys):
    path, _, data = sample
    assert main(["scan", str(path), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["totals"] == data["totals"]
    assert [f["check"] for f in out["findings"]].count("account_farm") == 2
