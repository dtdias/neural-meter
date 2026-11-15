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
    if sys.stdout is not None:
        print(f"NeuralMeter {__version__} - conexões locais CLI")
    from neuralmeter.ui.app import run_app

    return run_app(argv)


if __name__ == "__main__":
    raise SystemExit(main())
