from neuralmeter.__main__ import main


def test_self_test_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("NEURALMETER_DATA_DIR", str(tmp_path))
    out = tmp_path / "out.txt"
    assert main(["--self-test", "--self-test-out", str(out)]) == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    assert all(line.endswith(": ok") for line in lines)


def test_self_test_fail_icon(tmp_path, monkeypatch):
    monkeypatch.setenv("NEURALMETER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("neuralmeter.ui.icons.app_icon_path", lambda: None)
    out = tmp_path / "out.txt"
    assert main(["--self-test", "--self-test-out", str(out)]) == 1
    text = out.read_text(encoding="utf-8")
    assert "app_icon: FAIL" in text
    assert text.count(": ok") == 4
