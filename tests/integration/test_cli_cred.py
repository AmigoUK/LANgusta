"""Integration tests for `langusta cred add/list/rm` and init-with-password.

Master password sourced via LANGUSTA_MASTER_PASSWORD env var so the tests
don't need a tty. Production users can also pass it this way or be prompted
interactively.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from langusta.cli import app
from langusta.db import credentials as cred_dal
from langusta.db.connection import connect

runner = CliRunner()

PW = "master-password-for-tests-long-enough"


def _env(home: Path, *, with_pw: bool = True) -> dict[str, str]:
    env = {"HOME": str(home)}
    if with_pw:
        env["LANGUSTA_MASTER_PASSWORD"] = PW
    return env


def _home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


def _init(home: Path):
    return runner.invoke(app, ["init"], env=_env(home))


def test_init_with_env_password_sets_master_and_is_idempotent(tmp_path: Path) -> None:
    h = _home(tmp_path)
    r1 = _init(h)
    r2 = _init(h)
    assert r1.exit_code == 0, r1.stdout
    assert r2.exit_code == 0, r2.stdout
    # Master password marker exists.
    from langusta.crypto.master_password import is_set
    with connect(h / ".langusta" / "db.sqlite") as conn:
        assert is_set(conn)


def test_cred_add_stores_encrypted(tmp_path: Path) -> None:
    h = _home(tmp_path)
    _init(h)
    r = runner.invoke(
        app,
        ["cred", "add", "--label", "office-ro", "--kind", "snmp_v2c"],
        env={**_env(h), "LANGUSTA_CRED_SECRET": "public"},
    )
    assert r.exit_code == 0, r.stdout
    with connect(h / ".langusta" / "db.sqlite") as conn:
        rows = cred_dal.list_info(conn)
    assert len(rows) == 1
    assert rows[0].label == "office-ro"
    assert rows[0].kind == "snmp_v2c"


def test_cred_list_hides_secrets(tmp_path: Path) -> None:
    h = _home(tmp_path)
    _init(h)
    runner.invoke(
        app,
        ["cred", "add", "--label", "x", "--kind", "snmp_v2c"],
        env={**_env(h), "LANGUSTA_CRED_SECRET": "super-secret-community"},
    )
    r = runner.invoke(app, ["cred", "list"], env=_env(h))
    assert r.exit_code == 0
    # Label shown, secret NOT shown.
    assert "x" in r.stdout
    assert "snmp_v2c" in r.stdout
    assert "super-secret-community" not in r.stdout


def test_cred_rm_removes(tmp_path: Path) -> None:
    h = _home(tmp_path)
    _init(h)
    runner.invoke(
        app,
        ["cred", "add", "--label", "gone", "--kind", "snmp_v2c"],
        env={**_env(h), "LANGUSTA_CRED_SECRET": "x"},
    )
    with connect(h / ".langusta" / "db.sqlite") as conn:
        [info] = cred_dal.list_info(conn)
    r = runner.invoke(app, ["cred", "rm", str(info.id)], env=_env(h))
    assert r.exit_code == 0
    with connect(h / ".langusta" / "db.sqlite") as conn:
        assert cred_dal.list_info(conn) == []


def test_cred_add_without_password_env_fails(tmp_path: Path) -> None:
    h = _home(tmp_path)
    _init(h)
    # No LANGUSTA_MASTER_PASSWORD on cred add; CliRunner provides no stdin,
    # so Typer prompt should fail.
    r = runner.invoke(
        app,
        ["cred", "add", "--label", "x", "--kind", "snmp_v2c"],
        env={"HOME": str(h), "LANGUSTA_CRED_SECRET": "x"},
    )
    assert r.exit_code != 0


def test_cred_add_with_wrong_password_fails(tmp_path: Path) -> None:
    h = _home(tmp_path)
    _init(h)
    r = runner.invoke(
        app,
        ["cred", "add", "--label", "x", "--kind", "snmp_v2c"],
        env={
            "HOME": str(h),
            "LANGUSTA_MASTER_PASSWORD": "wrong-pw-at-least-12",
            "LANGUSTA_CRED_SECRET": "x",
        },
    )
    assert r.exit_code != 0
    assert "password" in (r.stdout + (r.stderr or "")).lower()


def test_cred_secret_never_appears_in_stdout(tmp_path: Path) -> None:
    """Hard rule from spec §16: credentials never surface in output."""
    h = _home(tmp_path)
    _init(h)
    secret = "community-value-that-must-not-leak"
    r = runner.invoke(
        app,
        ["cred", "add", "--label", "paranoid", "--kind", "snmp_v2c"],
        env={**_env(h), "LANGUSTA_CRED_SECRET": secret},
    )
    assert r.exit_code == 0
    assert secret not in r.stdout
    r2 = runner.invoke(app, ["cred", "list"], env=_env(h))
    assert secret not in r2.stdout
