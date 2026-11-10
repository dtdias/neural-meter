import sys

import pytest

from neuralmeter.autostart import build_autostart_command, is_autostart_enabled, set_autostart

TEST_KEY = r"Software\NeuralMeterTest\Run"


def test_command_frozen():
    assert build_autostart_command(r"C:\app\NeuralMeter.exe", True) == r'"C:\app\NeuralMeter.exe"'


def test_command_dev_without_pythonw(tmp_path):
    exe = tmp_path / "python.exe"
    assert build_autostart_command(str(exe), False) == f'"{exe}" -m neuralmeter'


def test_command_dev_prefers_pythonw(tmp_path):
    (tmp_path / "pythonw.exe").write_text("")
    exe = tmp_path / "python.exe"
    cmd = build_autostart_command(str(exe), False)
    assert cmd == f'"{tmp_path / "pythonw.exe"}" -m neuralmeter'


@pytest.mark.skipif(sys.platform != "win32", reason="winreg")
def test_registry_roundtrip():
    import winreg

    try:
        assert not is_autostart_enabled(TEST_KEY)
        assert set_autostart(False, TEST_KEY)  # apagar inexistente = ok
        assert set_autostart(True, TEST_KEY, command='"x.exe" -m neuralmeter')
        assert is_autostart_enabled(TEST_KEY)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_KEY) as key:
            assert winreg.QueryValueEx(key, "NeuralMeter")[0] == '"x.exe" -m neuralmeter'
        assert set_autostart(False, TEST_KEY)
        assert not is_autostart_enabled(TEST_KEY)
    finally:
        for path in (TEST_KEY, r"Software\NeuralMeterTest"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            except OSError:
                pass
