from datetime import datetime, timezone

from luxvigil.checks.prompts import find_prompt_bots
from luxvigil.generate import generate, write_csv
from luxvigil.schema import Request, load_logs


def req(account, prompt, cost=0.01):
    return Request(
        timestamp=datetime(2026, 10, 1, tzinfo=timezone.utc),
        account_id=account, email=f"{account}@example.com", api_key_id=f"key_{account}",
        ip="1.2.3.4", country="US", device_id="", prompt_hash=prompt,
        input_tokens=10, output_tokens=10, cost_usd=cost,
    )


def test_finds_the_planted_farm(tmp_path):
    rows, planted = generate()
    path = tmp_path / "logs.csv"
    write_csv(rows, path)
    (bot,) = find_prompt_bots(load_logs(path))
    assert set(bot.accounts) == set(planted["farm_big"])
    assert len(bot.prompts) == 5  # the 5 templates, grouped as one bot


def test_common_one_off_prompt_is_fine():
    # 50 real users each typing "hi" once
    rows = [req(f"u{i}", "hash_of_hi") for i in range(50)]
    assert find_prompt_bots(rows) == []


def test_one_account_repeating_itself_is_fine():
    rows = [req("a", "same") for _ in range(100)]
    assert find_prompt_bots(rows) == []


def test_rotating_templates_end_up_in_one_group():
    rows = []
    for i in range(6):
        for template in ("t1", "t2"):
            rows += [req(f"bot{i}", f"{template}_{'x' if i < 3 else 'y'}")] * 4
    rows += [req(f"bot{i}", "shared") for i in range(6) for _ in range(3)]  # ties the halves together
    (bot,) = find_prompt_bots(rows, min_accounts=3)
    assert len(bot.accounts) == 6


def test_thresholds_are_tunable():
    rows = [req(f"a{i}", "p") for i in range(4) for _ in range(2)]
    assert find_prompt_bots(rows) == []
    assert len(find_prompt_bots(rows, min_accounts=4, min_repeats=2)) == 1

def test_casual_users_of_a_bot_prompt_are_not_dragged_in():
    # 5 bots hammering a template, plus 10 real people who sent the same text once
    rows = [req(f"bot{i}", "template") for i in range(5) for _ in range(4)]
    rows += [req(f"u{i}", "template") for i in range(10)]
    (bot,) = find_prompt_bots(rows)
    assert bot.accounts == [f"bot{i}" for i in range(5)]
    assert bot.repeats == 20


def test_one_power_user_doesnt_make_a_bot():
    # one account sends a prompt 100 times, four others once: avg is 20, but it's one person
    rows = [req("power", "p")] * 100 + [req(f"u{i}", "p") for i in range(4)]
    assert find_prompt_bots(rows) == []

