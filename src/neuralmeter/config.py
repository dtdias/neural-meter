"""Configuração e caminhos locais do NeuralMeter."""

import os
from pathlib import Path

WS_HOST = "127.0.0.1"
WS_PORT = 8765
DB_FILENAME = "neuralmeter.db"
DATA_DIR_ENV = "NEURALMETER_DATA_DIR"


def get_data_dir() -> Path:
    """Diretório de dados: env override, senão %LOCALAPPDATA%\\NeuralMeter, senão ~/.neuralmeter."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        path = Path(override)
    else:
        local = os.environ.get("LOCALAPPDATA")
        path = Path(local) / "NeuralMeter" if local else Path.home() / ".neuralmeter"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_db_path() -> Path:
    return get_data_dir() / DB_FILENAME
