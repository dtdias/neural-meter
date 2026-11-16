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


def build_exe(dist_path: Path = DIST, work_path: Path = BUILD) -> Path:
    import PyInstaller.__main__

    dist_path.mkdir(parents=True, exist_ok=True)
    work_path.mkdir(parents=True, exist_ok=True)
    exe = dist_path / "NeuralMeter.exe"
    PyInstaller.__main__.run(
        [
            str(SPEC),
            "--noconfirm",
            "--clean",
            "--distpath",
            str(dist_path),
            "--workpath",
            str(work_path),
        ]
    )
    if not exe.is_file():
        raise SystemExit(f"exe nao gerado: {exe}")
    print(f"exe: {exe} ({exe.stat().st_size / 1024 / 1024:.1f} MB)")
    return exe


def run_smoke(exe: Path) -> int:
    temp_root = Path.home() / "AppData" / "Local" / "Temp" / "opencode"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="neuralmeter-exe-smoke-", dir=temp_root) as tmp:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--distpath", type=Path, default=DIST)
    parser.add_argument("--workpath", type=Path, default=BUILD)
    args = parser.parse_args(argv)
    exe = build_exe(args.distpath, args.workpath)
    if args.smoke:
        return run_smoke(exe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
