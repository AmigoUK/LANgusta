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


def test_cred_add_snmp_v3_via_env_vars_round_trips(tmp_path: Path) -> None:
    h = _home(tmp_path)
    _init(h)
    env = {
        **_env(h),
        "LANGUSTA_CRED_V3_USER": "admin",
        "LANGUSTA_CRED_V3_AUTH_PROTO": "SHA",
        "LANGUSTA_CRED_V3_AUTH_PASS": "authpass-long-enough",
        "LANGUSTA_CRED_V3_PRIV_PROTO": "AES-128",
        "LANGUSTA_CRED_V3_PRIV_PASS": "privpass-long-enough",
    }
    r = runner.invoke(app, ["cred", "add", "--label", "v3-lab", "--kind", "snmp_v3"], env=env)
    assert r.exit_code == 0, r.stdout
    # v3 passphrases must not leak into stdout.
    assert "authpass-long-enough" not in r.stdout
    assert "privpass-long-enough" not in r.stdout

    # The credential round-trips through the vault back into a SnmpV3Auth.
    from langusta.crypto import master_password as mp
    from langusta.scan.snmp.auth import SnmpV3Auth
    from langusta.scan.snmp.credentials import cred_to_snmp_auth

    with connect(h / ".langusta" / "db.sqlite") as conn:
        vault = mp.unlock(conn, password=PW)
        [info] = cred_dal.list_info(conn)
        secret = cred_dal.get_secret(conn, credential_id=info.id, vault=vault)
    assert info.kind == "snmp_v3"
    auth = cred_to_snmp_auth(info, secret)
    assert isinstance(auth, SnmpV3Auth)
    assert auth.username == "admin"
    assert auth.auth_protocol == "SHA"
    assert auth.priv_protocol == "AES-128"
    assert auth.auth_passphrase == "authpass-long-enough"
    assert auth.priv_passphrase == "privpass-long-enough"


def test_cred_add_snmp_v3_rejects_bad_protocol(tmp_path: Path) -> None:
    h = _home(tmp_path)
    _init(h)
    env = {
        **_env(h),
        "LANGUSTA_CRED_V3_USER": "admin",
        "LANGUSTA_CRED_V3_AUTH_PROTO": "SHA-3",  # not valid
        "LANGUSTA_CRED_V3_AUTH_PASS": "x",
        "LANGUSTA_CRED_V3_PRIV_PROTO": "AES-128",
        "LANGUSTA_CRED_V3_PRIV_PASS": "y",
    }
    r = runner.invoke(app, ["cred", "add", "--label", "bad", "--kind", "snmp_v3"], env=env)
    assert r.exit_code != 0
