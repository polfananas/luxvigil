from luxvigil.cli import main


def test_generate_then_scan(tmp_path, capsys):
    out = tmp_path / "logs.csv"
    assert main(["generate", "--out", str(out), "--users", "20"]) == 0
    assert main(["scan", str(out)]) == 0
    assert "requests from" in capsys.readouterr().out


def test_scan_missing_file_fails(tmp_path, capsys):
    assert main(["scan", str(tmp_path / "nope.csv")]) == 1
