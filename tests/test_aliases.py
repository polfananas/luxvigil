import json
from datetime import datetime, timezone

from luxvigil.checks.aliases import find_gmail_aliases, gmail_inbox
from luxvigil.export import to_dict
from luxvigil.report import build_report
from luxvigil.schema import Request


def req(account, email, cost=1.0):
    return Request(
        timestamp=datetime(2026, 10, 1, tzinfo=timezone.utc),
        account_id=account, email=email, api_key_id=f"key_{account}",
        ip="1.2.3.4", country="ES", device_id="", prompt_hash="p",
        input_tokens=10, output_tokens=10, cost_usd=cost,
    )


def test_gmail_inbox():
    assert gmail_inbox("pol@gmail.com") == "pol@gmail.com"
    assert gmail_inbox("p.o.l@gmail.com") == "pol@gmail.com"
    assert gmail_inbox("pol+7@gmail.com") == "pol@gmail.com"
    assert gmail_inbox("P.o.l+free@GoogleMail.com") == "pol@gmail.com"
    assert gmail_inbox("pol@outlook.com") is None
    assert gmail_inbox("not an email") is None
    assert gmail_inbox("+@gmail.com") is None


def test_finds_accounts_sharing_an_inbox():
    rows = [req(f"a{i}", f"pol+{i}@gmail.com") for i in range(5)]
    rows += [req("x", "someone@gmail.com")]
    (group,) = find_gmail_aliases(rows)
    assert group.accounts == ["a0", "a1", "a2", "a3", "a4"]
    assert group.addresses == 5
    assert group.cost_usd == 5.0


def test_dots_count_too():
    rows = [req("a", "pol@gmail.com"), req("b", "p.ol@gmail.com"), req("c", "p.o.l@googlemail.com")]
    assert len(find_gmail_aliases(rows)) == 1


def test_two_accounts_is_not_enough():
    rows = [req("personal", "pol@gmail.com"), req("testing", "pol+test@gmail.com")]
    assert find_gmail_aliases(rows) == []


def test_normal_users_are_fine():
    rows = [req(f"u{i}", f"user{i}@gmail.com") for i in range(20)]
    assert find_gmail_aliases(rows) == []


def test_the_inbox_never_shows_up_in_the_text():
    rows = [req(f"a{i}", f"pol+{i}@gmail.com") for i in range(3)]
    (group,) = find_gmail_aliases(rows)
    assert "pol" not in group.why and "@" not in group.why


def test_report_counts_them_as_likely_abuse():
    rows = [req(f"a{i}", f"pol+{i}@gmail.com") for i in range(4)]
    rows += [req(f"u{i}", f"user{i}@gmail.com") for i in range(10)]
    report = build_report(rows)
    assert report.abuse_cost == 4.0
    assert report.flagged_accounts == {"a0", "a1", "a2", "a3"}

    data = to_dict(report)
    (finding,) = [f for f in data["findings"] if f["check"] == "gmail_aliases"]
    assert finding["severity"] == "likely"
    assert "@" not in json.dumps(data)  # no emails leak into the json