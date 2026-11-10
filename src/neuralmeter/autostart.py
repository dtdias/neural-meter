"""Iniciar com o Windows: valor REG_SZ em HKCU\\...\\Run (o registro e a fonte de verdade)."""

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "NeuralMeter"


def build_autostart_command(executable: str, frozen: bool) -> str:
    """Frozen -> '"<exe>"'. Dev -> '"<pythonw|exe>" -m neuralmeter'. Sempre com aspas."""
    if frozen:
        return f'"{executable}"'
    exe = Path(executable)
    pythonw = exe.with_name("pythonw.exe")
    target = str(pythonw) if pythonw.exists() else executable
    return f'"{target}" -m neuralmeter'


def default_command() -> str:
    return build_autostart_command(sys.executable, bool(getattr(sys, "frozen", False)))


def is_autostart_enabled(key_path: str = RUN_KEY, name: str = APP_NAME) -> bool:
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, name)
        return True
    except OSError:
        return False


def set_autostart(
    enabled: bool,
    key_path: str = RUN_KEY,
    name: str = APP_NAME,
    command: str | None = None,
) -> bool:
    """Cria/apaga o valor. True se ok (apagar inexistente = ok); False em falha ou fora do win32."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        if enabled:
            cmd = default_command() if command is None else command
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, cmd)
        else:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
                ) as key:
                    winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
        return True
    except OSError:
        return False
