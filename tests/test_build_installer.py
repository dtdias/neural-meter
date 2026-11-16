import importlib.util
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


def test_installer_builder_has_no_extension_packaging():
    source = (ROOT / "scripts" / "build_installer.py").read_text(encoding="utf-8")
    assert "build_extension_zip" not in source
    assert "Bridge-" not in source
    assert not (ROOT / "extension").exists()


def test_iscc_command():
    cmd = bi.iscc_command(Path("ISCC.exe"), Path("x.iss"), bi.__version__)
    assert f"/DAppVersion={bi.__version__}" in cmd
    assert cmd[-1] == "x.iss"


def test_iscc_command_supports_isolated_exe_and_output_paths():
    exe = Path("C:/Temp/NeuralMeter.exe")
    output = Path("C:/Temp/evidence setup")
    cmd = bi.iscc_command(Path("ISCC.exe"), Path("x.iss"), "0.2.2", exe, output)
    assert f"/DAppExePath={exe}" in cmd
    assert f"/O{output}" in cmd
    assert cmd[-1] == "x.iss"


def test_iss_content():
    for needle in (
        "PrivilegesRequired=lowest",
        "{localappdata}\\Programs\\NeuralMeter",
        "uninsdeletevalue",
        "CurrentVersion\\Run",
        '#define AppExePath "..\\dist\\NeuralMeter.exe"',
        'Source: "{#AppExePath}";',
    ):
        assert needle in ISS_TEXT
    assert "{commonpf}" not in ISS_TEXT
    assert "admin" not in ISS_TEXT.lower()
    assert "[UninstallRun]" not in ISS_TEXT
    assert "taskkill" not in ISS_TEXT.lower()
