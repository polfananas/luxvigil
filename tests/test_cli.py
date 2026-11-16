import pytest

from luxvigil.cli import main


def test_generate_then_scan(tmp_path, capsys):
    out = tmp_path / "logs.csv"
    assert main(["generate", "--out", str(out), "--users", "20"]) == 0
    assert main(["scan", str(out)]) == 0
    assert "requests from" in capsys.readouterr().out


def test_scan_missing_file_fails(tmp_path, capsys):
    assert main(["scan", str(tmp_path / "nope.csv")]) == 1


def test_demo(capsys):
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "is likely abuse" in out
    assert "2 account farms" in out
    assert "try it on yours" in out


def test_demo_keep(tmp_path, capsys):
    path = tmp_path / "demo.csv"
    assert main(["demo", "--keep", str(path)]) == 0
    assert path.exists()
    assert str(path) in capsys.readouterr().out


def test_demo_ignores_local_config(tmp_path, monkeypatch, capsys):
    # a toml that hides the whole farm shouldn't change the demo
    (tmp_path / "luxvigil.toml").write_text('[ignore]\nemails = ["*@mailinator.com"]\n')
    monkeypatch.chdir(tmp_path)
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "using luxvigil.toml" not in out
    assert "throwaway emails" in out


@pytest.mark.parametrize("content,fmt", [
    (b"\x00\xff\xfe\x00not a csv", "csv"),  # a binary file
    (b"timestamp,account_id,input_tokens,output_tokens,cost_usd,email\n2026-01-01T00:00:00Z,a,1,1,1,"
     + b"x" * 200_000 + b"\n", "csv"),  # one field bigger than the csv module allows
    (b'[{"startTime": "2026-01-01T00:00:00Z", "end_user": "a"', "litellm"),  # cut-off JSON
])
def test_files_it_cant_parse_get_a_clean_error(tmp_path, capsys, content, fmt):
    path = tmp_path / "logs"
    path.write_bytes(content)
    assert main(["scan", str(path), "--format", fmt]) == 1
    err = capsys.readouterr().err
    assert "can't read" in err and "Traceback" not in err


def test_an_empty_file_says_so(tmp_path, capsys):
    path = tmp_path / "logs.csv"
    path.write_text("timestamp,account_id,input_tokens,output_tokens,cost_usd\n")
    assert main(["scan", str(path)]) == 0
    assert "0 requests · 0 accounts · nothing to scan" in capsys.readouterr().out


@pytest.mark.parametrize("args", [[], ["--skip-bad-rows"]])
def test_control_characters_in_a_bad_value_dont_reach_the_terminal(tmp_path, capsys, args):
    # an ESC/BEL in a cell could retitle the terminal or fake a message, ‮ flips text direction
    bad = "bad\x1b]2;PWN\x07\x1b[31mRED‮"
    path = tmp_path / "logs.csv"
    path.write_text("timestamp,account_id,input_tokens,output_tokens,cost_usd\n"
                    "2026-01-01T00:00:00Z,a,1,1,1\n"
                    f"2026-01-01T00:00:00Z,a,1,1,{bad}\n", encoding="utf-8")
    main(["scan", str(path), *args])
    err = capsys.readouterr().err
    assert "cost_usd should be a number" in err
    assert not any(c in err for c in "\x1b\x07‮")
    assert "\\x1b" in err  # shown escaped, so you can still see something odd was there
