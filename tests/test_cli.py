from luxvigil.cli import main


def test_scan_runs(capsys):
    assert main(["scan", "logs.csv"]) == 0
    assert "luxvigil" in capsys.readouterr().out
