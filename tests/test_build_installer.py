import hashlib
import importlib.util
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "build_installer", ROOT / "scripts" / "build_installer.py"
)
bi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bi)

ISS_TEXT = (ROOT / "packaging" / "neuralmeter.iss").read_text(encoding="utf-8")


def test_find_iscc_precedence():
    env = {
        "ISCC_PATH": "C:/a/ISCC.exe",
        "LOCALAPPDATA": "C:/L",
        "ProgramFiles(x86)": "C:/P",
    }
    everything = lambda p: True  # noqa: E731
    assert bi.find_iscc(env, lambda n: "C:/path/iscc.exe", everything) == Path("C:/a/ISCC.exe")
    env.pop("ISCC_PATH")
    assert bi.find_iscc(env, lambda n: "C:/path/iscc.exe", everything) == Path("C:/path/iscc.exe")
    got = bi.find_iscc(env, lambda n: None, everything)
    assert got == Path("C:/L/Programs/Inno Setup 6/ISCC.exe")
    only_pf = lambda p: p.replace("\\", "/").startswith("C:/P")  # noqa: E731
    assert bi.find_iscc(env, lambda n: None, only_pf) == Path("C:/P/Inno Setup 6/ISCC.exe")


def test_find_iscc_none():
    assert bi.find_iscc({}, lambda n: None, lambda p: False) is None


def test_extension_zip_contents_and_determinism(tmp_path):
    a = bi.build_extension_zip(ROOT / "extension", tmp_path / "a.zip")
    b = bi.build_extension_zip(ROOT / "extension", tmp_path / "b.zip")
    with zipfile.ZipFile(a) as zf:
        names = zf.namelist()
    assert "manifest.json" in names
    assert "src/background.js" in names
    assert "icons/icon128.png" in names
    assert names == sorted(names)
    assert not any(n.startswith(("test", "node_modules")) or "package" in n for n in names)
    assert not any(n.endswith(".md") for n in names)
    assert hashlib.sha256(a.read_bytes()).digest() == hashlib.sha256(b.read_bytes()).digest()


def test_iscc_command():
    cmd = bi.iscc_command(Path("ISCC.exe"), Path("x.iss"), bi.__version__)
    assert f"/DAppVersion={bi.__version__}" in cmd
    assert cmd[-1] == "x.iss"


def test_iss_content():
    for needle in (
        "PrivilegesRequired=lowest",
        "{localappdata}\\Programs\\NeuralMeter",
        "uninsdeletevalue",
        "CurrentVersion\\Run",
    ):
        assert needle in ISS_TEXT
    assert "{commonpf}" not in ISS_TEXT
    assert "admin" not in ISS_TEXT.lower()
