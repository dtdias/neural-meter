import sys
import time

from neuralmeter import __version__
from neuralmeter.config import WS_HOST, WS_PORT, get_db_path


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--self-test" in argv:
        from pathlib import Path

        from neuralmeter.selftest import run_self_test

        out = None
        if "--self-test-out" in argv:
            idx = argv.index("--self-test-out")
            if idx + 1 < len(argv):
                out = Path(argv[idx + 1])
        return run_self_test(out)
    if sys.stdout is not None:
        print(f"NeuralMeter {__version__} - ws://{WS_HOST}:{WS_PORT} - db={get_db_path()}")
    if "--server-only" in argv:
        from datetime import UTC, datetime

        from neuralmeter.db import connect_db
        from neuralmeter.privacy import apply_retention
        from neuralmeter.server import start_ws_server
        from neuralmeter.ui.settings import (
            ensure_privacy_salt,
            load_settings,
            parse_privacy_settings,
            parse_weight_params,
            save_settings,
        )

        settings = load_settings()
        if ensure_privacy_salt(settings):
            save_settings(settings)
        privacy = parse_privacy_settings(settings)
        db = connect_db()
        apply_retention(db, datetime.now(UTC), privacy.retention_days)
        server = start_ws_server(
            db,
            on_update=lambda kind, payload: print(f"update {kind}: {payload}"),
            weight_params=parse_weight_params(settings.get("claude_weight")),
            privacy=privacy,
        )
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            server.stop()
        return 0
    from neuralmeter.ui.app import run_app

    return run_app(argv)


if __name__ == "__main__":
    raise SystemExit(main())
