import pytest

from luxvigil.cli import main
from luxvigil.config import ConfigError, load_config


@pytest.mark.parametrize("flags", [
    ["--price-in", "nan", "--price-out", "1"],
    ["--price-in", "1", "--price-out", "inf"],
    ["--min-farm", "1"],
    ["--min-countries", "0"],
])
def test_nonsense_arguments_are_refused(tmp_path, capsys, flags):
    logs = tmp_path / "logs.csv"
    main(["generate", "--out", str(logs), "--users", "20"])
    assert main(["scan", str(logs), *flags]) == 1
    assert "should be" in capsys.readouterr().err


def test_config_is_checked_before_the_logs(tmp_path, capsys):
    bad = tmp_path / "luxvigil.toml"
    bad.write_text("[nope]\n")
    # the log file doesn't even exist: the config error should still come first
    assert main(["scan", str(tmp_path / "nope.csv"), "--config", str(bad)]) == 1
    assert "bad config" in capsys.readouterr().err


def test_section_that_isnt_a_table(tmp_path):
    path = tmp_path / "luxvigil.toml"
    path.write_text("trusted = 5\n")
    with pytest.raises(ConfigError, match=r"should be a \[trusted\] section"):
        load_config(path)