import sys
import time

from neuralmeter import __version__
from neuralmeter.config import WS_HOST, WS_PORT, get_db_path


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    print(f"NeuralMeter {__version__} - ws://{WS_HOST}:{WS_PORT} - db={get_db_path()}")
    if "--server-only" in argv:
        from neuralmeter.db import connect_db
        from neuralmeter.server import start_ws_server

        server = start_ws_server(
            connect_db(), on_update=lambda kind, payload: print(f"update {kind}: {payload}")
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
