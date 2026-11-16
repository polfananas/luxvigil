from datetime import datetime, timezone

from luxvigil.checks.emails import disposable_domain, find_disposable_emails, load_disposable_domains
from luxvigil.generate import generate, write_csv
from luxvigil.schema import Request, load_logs


def req(account, email, cost=0.01):
    return Request(
        timestamp=datetime(2026, 10, 1, tzinfo=timezone.utc),
        account_id=account, email=email, api_key_id=f"key_{account}",
        ip="1.2.3.4", country="US", device_id="", prompt_hash="p",
        input_tokens=10, output_tokens=10, cost_usd=cost,
    )


def test_bundled_list_loads():
    domains = load_disposable_domains()
    assert len(domains) > 1000
    assert "mailinator.com" in domains
    assert "gmail.com" not in domains
    assert not any(d.startswith("#") for d in domains)


def test_matching():
    domains = {"mailinator.com"}
    assert disposable_domain("bob@mailinator.com", domains) == "mailinator.com"
    assert disposable_domain("bob@x.y.mailinator.com", domains) == "mailinator.com"
    assert disposable_domain("BOB@Mailinator.COM", domains) == "mailinator.com"
    assert disposable_domain("bob@notmailinator.com", domains) is None
    assert disposable_domain("bob@gmail.com", domains) is None
    assert disposable_domain("no-at-sign", domains) is None


def test_counts_accounts_not_requests():
    rows = [req("a", "a@mailinator.com", cost=1.0)] * 3 + [req("b", "b@gmail.com")]
    report = find_disposable_emails(rows, domains={"mailinator.com"})
    assert report.accounts == ["a"]
    assert report.total_accounts == 2
    assert report.share == 0.5
    assert report.requests == 3 and report.cost_usd == 3.0


def test_clean_logs_return_none():
    rows = [req(f"acc{i}", f"user{i}@gmail.com") for i in range(10)]
    assert find_disposable_emails(rows) is None


def test_finds_the_planted_farm(tmp_path):
    rows, planted = generate()
    path = tmp_path / "logs.csv"
    write_csv(rows, path)
    report = find_disposable_emails(load_logs(path))
    assert set(report.accounts) == set(planted["farm_big"])


def test_the_order_of_the_rows_doesnt_change_the_answer():
    # 5 accounts on one network, each with a normal and a throwaway email. Before, only the
    # first email in the file counted: normal first meant no farm, throwaway first meant a farm
    import random
    from datetime import datetime, timezone

    from luxvigil.export import to_dict
    from luxvigil.report import build_report
    from luxvigil.schema import Request

    def req(k, email, hour):
        return Request(datetime(2026, 10, 1, hour, tzinfo=timezone.utc), f"u{k}", email, "", f"5.5.5.{k}", "",
                       "", f"p{k}{hour}", 10, 10, 1.0)

    rows = [req(k, f"u{k}@gmail.com", 1) for k in range(5)] + [req(k, f"u{k}@mailinator.com", 2) for k in range(5)]
    results = set()
    for seed in range(5):
        random.Random(seed).shuffle(rows)
        data = to_dict(build_report(rows))
        results.add(tuple(sorted((f["id"], f["severity"], f["cost_usd"]) for f in data["findings"])))
    assert len(results) == 1
    (findings,) = results
    assert {f[0].split("-")[0] for f in findings} == {"account_farm", "disposable_emails"}
