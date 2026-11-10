from neuralmeter import __version__
from neuralmeter.config import WS_HOST, WS_PORT, get_db_path


def main() -> int:
    print(f"NeuralMeter {__version__} — ws://{WS_HOST}:{WS_PORT} — db={get_db_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
