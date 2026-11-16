import sys

from neuralmeter import __version__


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
    if "--server-only" in argv:
        print("--server-only removido: NeuralMeter agora conecta somente fontes CLI via interface.")
        return 2
    if "--help" in argv or "-h" in argv:
        print(
            "Uso: neuralmeter [--port PORT] [--no-browser]\n"
            "Inicia dashboard local em 127.0.0.1:8766."
        )
        return 0
    if sys.stdout is not None:
        print(f"NeuralMeter {__version__} - dashboard local")
    from neuralmeter.web import LocalDashboard

    port = 8766
    if "--port" in argv:
        index = argv.index("--port")
        try:
            port = int(argv[index + 1])
        except (IndexError, ValueError):
            print("--port exige inteiro entre 0 e 65535.")
            return 2
        if not 0 <= port <= 65535:
            print("--port exige inteiro entre 0 e 65535.")
            return 2
    try:
        dashboard = LocalDashboard(port=port)
    except OSError:
        print(f"Porta local indisponível ({port}); escolha --port PORT ou --port 0.")
        return 2
    try:
        dashboard.start(open_browser="--no-browser" not in argv)
        print(f"Dashboard local: {dashboard.url}")
        dashboard.wait_for_shutdown()
    except KeyboardInterrupt:
        return 0
    finally:
        dashboard.close()


if __name__ == "__main__":
    raise SystemExit(main())
