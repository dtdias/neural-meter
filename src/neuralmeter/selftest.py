"""Auto-teste sem UI (smoke do binario congelado): resultado via exit code."""

from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen


def _check_qt() -> None:
    import PySide6.QtGui  # noqa: F401
    import PySide6.QtSvg  # noqa: F401
    import PySide6.QtWidgets  # noqa: F401


def _check_db() -> None:
    from neuralmeter.db import connect_db

    conn = connect_db()
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    if version < 2:
        raise RuntimeError(f"user_version={version} < 2")


def _check_icon() -> None:
    from neuralmeter.ui import icons

    if icons.app_icon_path() is None:
        raise RuntimeError("icon.png nao encontrado")


def _check_cli_connectors() -> None:
    import neuralmeter.antigravity  # noqa: F401
    import neuralmeter.claude_cli  # noqa: F401
    import neuralmeter.codex_cli  # noqa: F401
    import neuralmeter.ui.collector_lifecycle  # noqa: F401
    import neuralmeter.ui.connections_dialog  # noqa: F401


def _check_local_web() -> None:
    import json

    from neuralmeter.web import LocalDashboard

    with TemporaryDirectory(prefix="neuralmeter-web-selftest-") as directory:
        dashboard = LocalDashboard(port=0, data_dir=Path(directory))
        try:
            dashboard.start(open_browser=False)
            with urlopen(dashboard.url, timeout=2) as response:
                if response.status != 200 or b"Seu trabalho" not in response.read():
                    raise RuntimeError("local UI failed")
            with urlopen(dashboard.url + "api/snapshot", timeout=2) as response:
                snapshot = json.loads(response.read())
            if any(source["consent"] for source in snapshot["sources"].values()):
                raise RuntimeError("self-test unexpectedly enabled source collection")
            package_dir = Path(__file__).parent
            notice = package_dir / "THIRD_PARTY_NOTICES.txt"
            license_file = package_dir / "LICENSES" / "TokenMeter-MIT.txt"
            if not notice.is_file() or not license_file.is_file():
                raise RuntimeError("third-party license notice missing from package")
        finally:
            dashboard.close()


CHECKS: list[tuple[str, Callable[[], None]]] = [
    ("qt_imports", _check_qt),
    ("db_migrate", _check_db),
    ("app_icon", _check_icon),
    ("cli_connectors", _check_cli_connectors),
    ("local_web_loopback", _check_local_web),
]


def run_self_test(out_path: Path | None = None) -> int:
    """Roda os checks; 0 se todos ok, senao 1. Escreve linhas em out_path se dado."""
    lines: list[str] = []
    failed = False
    for name, fn in CHECKS:
        try:
            fn()
            lines.append(f"{name}: ok")
        except Exception as exc:
            failed = True
            lines.append(f"{name}: FAIL {type(exc).__name__}: {exc}")
    if out_path is not None:
        Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if failed else 0
