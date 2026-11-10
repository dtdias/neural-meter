"""Gera dist/NeuralMeter.exe via PyInstaller; --smoke roda o auto-teste no binario."""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "neuralmeter.spec"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
EXE = DIST / "NeuralMeter.exe"


def build_exe() -> Path:
    import PyInstaller.__main__

    PyInstaller.__main__.run(
        [
            str(SPEC),
            "--noconfirm",
            "--clean",
            "--distpath",
            str(DIST),
            "--workpath",
            str(BUILD),
        ]
    )
    if not EXE.is_file():
        raise SystemExit(f"exe nao gerado: {EXE}")
    print(f"exe: {EXE} ({EXE.stat().st_size / 1024 / 1024:.1f} MB)")
    return EXE


def run_smoke(exe: Path) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "selftest.txt"
        env = dict(os.environ, NEURALMETER_DATA_DIR=tmp)
        proc = subprocess.run(
            [str(exe), "--self-test", "--self-test-out", str(out)],
            env=env,
            timeout=120,
        )
        if out.is_file():
            print(out.read_text(encoding="utf-8"), end="")
        else:
            print("selftest.txt nao gerado")
        print(f"exit code: {proc.returncode}")
        return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    exe = build_exe()
    if args.smoke:
        return run_smoke(exe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
