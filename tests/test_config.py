from neuralmeter import config


def test_data_dir_env_override_creates_dir(tmp_path, monkeypatch):
    target = tmp_path / "nested" / "data"
    monkeypatch.setenv("NEURALMETER_DATA_DIR", str(target))
    result = config.get_data_dir()
    assert result == target
    assert target.is_dir()


def test_db_path_filename(tmp_path, monkeypatch):
    monkeypatch.setenv("NEURALMETER_DATA_DIR", str(tmp_path))
    path = config.get_db_path()
    assert path.name == "neuralmeter.db"
    assert path.parent == tmp_path


def test_ws_host_is_loopback():
    assert config.WS_HOST == "127.0.0.1"
    assert config.WS_PORT == 8765
