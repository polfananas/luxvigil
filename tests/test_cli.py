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
    assert "looks like abuse" in out
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
