import os
import subprocess
import sys


def test_the_report_can_go_to_a_file_on_windows():
    # sending the output to a file on Windows uses an old encoding with no emojis in it
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    result = subprocess.run([sys.executable, "-m", "luxvigil.cli", "demo"], env=env, capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert "🕯️ luxvigil" in result.stdout.decode("utf-8")