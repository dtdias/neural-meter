"""Gera somente instalador NeuralMeter CLI-only via Inno Setup."""

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from neuralmeter import __version__  # noqa: E402

ISS = ROOT / "packaging" / "neuralmeter.iss"
DIST = ROOT / "dist"
EXE = DIST / "NeuralMeter.exe"
WINGET_HINT = "winget install --id JRSoftware.InnoSetup -e --scope user"


def find_iscc(
    env: Mapping[str, str] = os.environ,
    which: Callable[[str], str | None] = shutil.which,
    exists: Callable[[str], bool] = os.path.exists,
) -> Path | None:
    p = env.get("ISCC_PATH")
    if p and exists(p):
        return Path(p)
    found = which("iscc")
    if found:
        return Path(found)
    candidates = []
    local = env.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Programs" / "Inno Setup 6" / "ISCC.exe")
    pf86 = env.get("ProgramFiles(x86)")
    if pf86:
        candidates.append(Path(pf86) / "Inno Setup 6" / "ISCC.exe")
    for c in candidates:
        if exists(str(c)):
            return c
    return None


def iscc_command(iscc: Path, iss: Path, version: str) -> list[str]:
    return [str(iscc), "/Qp", f"/DAppVersion={version}", str(iss)]


def build_installer(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-exe", action="store_true")
    args = parser.parse_args(argv)

    DIST.mkdir(exist_ok=True)
    if not EXE.is_file():
        if args.skip_exe:
            print(f"erro: {EXE} ausente (rode scripts/build_exe.py)")
            return 1
        rc = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_exe.py")]).returncode
        if rc != 0 or not EXE.is_file():
            print("erro: build do exe falhou")
            return 1

    iscc = find_iscc()
    if iscc is None:
        print(f"ISCC ausente. Instale: {WINGET_HINT}")
        return 2
    proc = subprocess.run(iscc_command(iscc, ISS, __version__))
    setup = DIST / f"NeuralMeter-Setup-{__version__}.exe"
    if proc.returncode != 0 or not setup.is_file():
        print(f"erro: ISCC falhou (exit {proc.returncode})")
        return 1
    print(f"setup: {setup} ({setup.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(build_installer())
