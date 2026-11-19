import json

import pytest

from luxvigil.audit_config import AuditError, audit, interpolate, read_env, yaml_entries
from luxvigil.cli import main

STRONG = "sk-" + "a1b2c3d4" * 8


def write(folder, files):
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder


def checks(folder):
    return sorted(f.check for f in audit([folder]).findings)


# ------------------------------------------------------------ reading


def test_env_quotes_comments_and_export():
    rows = read_env('A="sk-1234"\nexport B=x # note\nC=\n# D=1\nE=a#b\nnot a line\n')
    assert [(name, value) for _, name, value in rows] == [("A", "sk-1234"), ("B", "x"), ("C", ""), ("E", "a#b")]


def test_yaml_paths_lists_and_block_scalars():
    text = """
model_list:
  - model_name: gpt
    litellm_params:
      api_key: os.environ/OPENAI_API_KEY  # comment
general_settings:
  note: |
    master_key: not-this-one
  master_key: "sk-1234"
"""
    entries = [(e.path, e.key, e.value) for e in yaml_entries(text) if e.value is not None]
    assert (("model_list",), "model_name", "gpt") in entries
    assert (("model_list", "litellm_params"), "api_key", "os.environ/OPENAI_API_KEY") in entries
    assert (("general_settings",), "master_key", "sk-1234") in entries
    assert not any(value == "not-this-one" for _, _, value in entries)


def test_compose_interpolation():
    env = {"KEY": "sk-1234", "EMPTY": ""}
    assert interpolate("${KEY}", env) == "sk-1234"
    assert interpolate("$KEY", env) == "sk-1234"
    assert interpolate("${MISSING:-sk-1234}", env) == "sk-1234"
    assert interpolate("${EMPTY:-fallback}", env) == "fallback"
    assert interpolate("${EMPTY-fallback}", env) == ""
    assert interpolate("${MISSING:?set it in .env}", env) is None
    assert interpolate("$${KEY}", env) == "${KEY}"


# ------------------------------------------------------------ the checks


def test_default_master_key_everywhere_is_one_finding(tmp_path):
    write(tmp_path, {
        ".env": 'LITELLM_MASTER_KEY="sk-1234"\n',
        "config.yaml": "general_settings:\n  master_key: sk-1234 # enter your own\n",
        "docker-compose.yml": "services:\n  litellm:\n    environment:\n      - LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY}\n",
    })
    found = audit([tmp_path]).findings
    assert [f.check for f in found] == ["default-master-key"]
    assert len(found[0].where.split(", ")) == 3  # .env, compose and config.yaml (Windows paths have a : too)


def test_default_key_through_os_environ(tmp_path):
    write(tmp_path, {
        ".env": "MY_KEY=sk-1234\n",
        "config.yaml": "general_settings:\n  master_key: os.environ/MY_KEY\n",
    })
    assert "default-master-key" in checks(tmp_path)


def test_default_key_as_compose_default(tmp_path):
    write(tmp_path, {"docker-compose.yml":
                     "services:\n  litellm:\n    environment:\n      LITELLM_MASTER_KEY: ${MASTER:-sk-1234}\n"})
    assert "default-master-key" in checks(tmp_path)


def test_strong_key_from_env_is_fine(tmp_path):
    write(tmp_path, {
        ".env": f"LITELLM_MASTER_KEY={STRONG}\nUI_PASSWORD=a-long-random-password\n",
        "config.yaml": "model_list:\n  - model_name: gpt\n    litellm_params:\n"
                       "      api_key: os.environ/OPENAI_API_KEY\n"
                       "general_settings:\n  master_key: os.environ/LITELLM_MASTER_KEY\n",
    })
    assert checks(tmp_path) == []


@pytest.mark.parametrize("files", [
    {"config.yaml": "general_settings:\n  dangerously_permit_weak_or_unset_master_key: true\n"},
    {".env": "LITELLM_DANGEROUSLY_PERMIT_WEAK_OR_UNSET_MASTER_KEY=True\n"},
])
def test_weak_key_override(tmp_path, files):
    write(tmp_path, files)
    assert "weak-key-override" in checks(tmp_path)


def test_override_off_is_fine(tmp_path):
    write(tmp_path, {"config.yaml": "general_settings:\n  dangerously_permit_weak_or_unset_master_key: false\n"})
    assert checks(tmp_path) == []


def test_secret_in_config_is_masked(tmp_path):
    key = "sk-proj-" + "x" * 40
    write(tmp_path, {"config.yaml": f"model_list:\n  - model_name: gpt\n    litellm_params:\n      api_key: {key}\n"})
    found = audit([tmp_path]).findings
    assert [f.check for f in found] == ["secret-in-config"]
    assert key not in found[0].title + found[0].why + found[0].fix
    assert "sk-p…" in found[0].title


@pytest.mark.parametrize("image,check", [
    ("ghcr.io/berriai/litellm:main-v1.83.3-stable", "old-version"),
    ("docker.litellm.ai/berriai/litellm:v1.81.0", "old-version"),
    ("ghcr.io/berriai/litellm-database:main-v1.82.1", "old-version"),
    ("docker.litellm.ai/berriai/litellm:latest", "unpinned-image"),
    ("ghcr.io/berriai/litellm", "unpinned-image"),
    ("docker.litellm.ai/berriai/litellm:main-stable", "unpinned-image"),
])
def test_image_versions(tmp_path, image, check):
    write(tmp_path, {"docker-compose.yml": f"services:\n  litellm:\n    image: {image}\n"})
    assert checks(tmp_path) == [check]


@pytest.mark.parametrize("image", [
    "ghcr.io/berriai/litellm:main-v1.84.0-stable",
    "docker.litellm.ai/berriai/litellm:v1.90.2",
    "ghcr.io/berriai/litellm@sha256:" + "0" * 64,
    "postgres:16",
    "localhost:5000/litellm:v1.85.0",
])
def test_images_that_are_fine(tmp_path, image):
    write(tmp_path, {"docker-compose.yml": f"services:\n  litellm:\n    image: {image}\n"})
    assert checks(tmp_path) == []


def test_old_package_in_requirements(tmp_path):
    write(tmp_path, {"requirements.txt": "fastapi\nlitellm[proxy]==1.80.5\n"})
    assert checks(tmp_path) == ["old-version"]


def test_weak_ui_password(tmp_path):
    write(tmp_path, {"docker-compose.yml": "services:\n  litellm:\n    environment:\n"
                                           f"      - LITELLM_MASTER_KEY={STRONG}\n      - UI_PASSWORD=langchain\n"})
    assert checks(tmp_path) == ["weak-ui-password"]


def test_no_ui_password_means_master_key_logs_in(tmp_path):
    write(tmp_path, {".env": f"LITELLM_MASTER_KEY={STRONG}\n"})
    found = audit([tmp_path]).findings
    assert [(f.check, f.level) for f in found] == [("master-key-is-ui-password", "worth a look")]


@pytest.mark.parametrize("files", [
    {".env": f"LITELLM_MASTER_KEY={STRONG}\nUI_PASSWORD=admin\nDISABLE_ADMIN_UI=True\n"},
    {".env": f"LITELLM_MASTER_KEY={STRONG}\nUI_PASSWORD=admin\n",
     "config.yaml": "general_settings:\n  disable_env_credential_login: true\n"},
])
def test_ui_checks_off_when_env_login_is_off(tmp_path, files):
    write(tmp_path, files)
    assert checks(tmp_path) == []


def test_env_file_from_compose_is_read(tmp_path):
    (tmp_path / "deploy").mkdir()
    write(tmp_path, {"secrets.txt": "LITELLM_MASTER_KEY=sk-1234\n"})
    write(tmp_path / "deploy", {"docker-compose.yml":
                                "services:\n  litellm:\n    env_file:\n      - path: ${ENV_FILE:-../secrets.txt}\n"
                                "        required: false\n"})
    found = audit([tmp_path / "deploy"]).findings
    assert [f.check for f in found] == ["default-master-key"]
    assert "secrets.txt" in found[0].where


def test_examples_in_a_folder_are_skipped(tmp_path):
    write(tmp_path, {".env.example": "LITELLM_MASTER_KEY=sk-1234\n", ".env": f"LITELLM_MASTER_KEY={STRONG}\n"})
    assert "default-master-key" not in checks(tmp_path)


def test_nothing_to_read(tmp_path):
    write(tmp_path, {"notes.txt": "hello\n"})
    with pytest.raises(AuditError):
        audit([tmp_path])


# ------------------------------------------------------------ the command


def test_cli_text_and_json(tmp_path, capsys):
    write(tmp_path, {".env": "LITELLM_MASTER_KEY=sk-1234\n"})
    assert main(["audit-config", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "fix now (1)" in out and "sk-1234" in out
    assert main(["audit-config", str(tmp_path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [f["check"] for f in data["findings"]][0] == "default-master-key"


def test_cli_missing_path(tmp_path, capsys):
    assert main(["audit-config", str(tmp_path / "nope")]) == 1
    assert "no such file or folder" in capsys.readouterr().err


def test_cli_wrong_file(tmp_path, capsys):
    write(tmp_path, {"notes.yaml": "hello: world\n"})
    assert main(["audit-config", str(tmp_path / "notes.yaml")]) == 1
    assert "doesn't look like" in capsys.readouterr().err
