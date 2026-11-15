import time

from neuralmeter.ui.connections_dialog import ConnectionsDialog


def pump_until(qapp, predicate, timeout=1):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.002)
    qapp.processEvents()
    assert predicate()


def test_discovery_is_read_only_and_does_not_enable_consent(qapp, monkeypatch):
    monkeypatch.setattr(
        "neuralmeter.ui.connections_dialog.discover_claude", lambda: "C:/claude.exe"
    )
    monkeypatch.setattr(
        "neuralmeter.ui.connections_dialog.discover_codex", lambda: ("C:/codex.exe", None)
    )
    monkeypatch.setattr("neuralmeter.ui.connections_dialog._antigravity_install", lambda: None)
    monkeypatch.setattr(
        "neuralmeter.ui.connections_dialog.antigravity_endpoint_available", lambda *_args: False
    )
    changes = []
    dialog = ConnectionsDialog(
        {"claude": False, "codex": False, "antigravity": False},
        lambda source, enabled: changes.append((source, enabled)),
    )
    dialog.discover()
    pump_until(qapp, lambda: dialog._discovery.isEnabled())
    assert "Instalado" in dialog._rows["claude"]["installation"].text()
    assert "Instalado" in dialog._rows["codex"]["installation"].text()
    assert "Não encontrado" in dialog._rows["antigravity"]["installation"].text()
    assert changes == []
    assert not dialog._enabled["claude"]
    dialog.close()


def test_connect_detected_requires_discovery_and_uses_explicit_opt_in(qapp, monkeypatch):
    monkeypatch.setattr(
        "neuralmeter.ui.connections_dialog.discover_claude", lambda: "C:/claude.exe"
    )
    monkeypatch.setattr("neuralmeter.ui.connections_dialog.discover_codex", lambda: None)
    monkeypatch.setattr("neuralmeter.ui.connections_dialog._antigravity_install", lambda: None)
    monkeypatch.setattr(
        "neuralmeter.ui.connections_dialog.antigravity_endpoint_available", lambda *_args: False
    )
    changes = []
    refreshes = []
    dialog = ConnectionsDialog(
        {"claude": False, "codex": False, "antigravity": False},
        lambda source, enabled: changes.append((source, enabled)),
        on_refresh=refreshes.append,
    )
    assert not dialog._rows["claude"]["action"].isEnabled()
    dialog.discover()
    pump_until(qapp, lambda: dialog._discovery.isEnabled())
    dialog.connect_detected()
    assert changes == [("claude", True)]
    assert dialog._enabled["claude"]
    assert "Autorizada" in dialog._rows["claude"]["consent"].text()
    assert dialog._refresh.isEnabled()
    dialog._refresh.click()
    assert refreshes == [("claude",)]
    dialog._toggle("claude")
    assert changes[-1] == ("claude", False)
    dialog.update_status("claude", "quota_unavailable", "authenticated")
    qapp.processEvents()
    assert "Autenticação: sessão reconhecida" in dialog._rows["claude"]["auth"].text()
    assert "sessão autenticada não prova quota disponível" in dialog._rows["claude"]["quota"].text()
    assert "Coleta: Não autorizada" in dialog._rows["claude"]["consent"].text()
    dialog.close()


def test_small_dialog_uses_scrollable_cards_without_hiding_source_states(qapp):
    dialog = ConnectionsDialog(
        {"claude": False, "codex": False, "antigravity": False}, lambda *_: None
    )
    dialog.resize(400, 320)
    dialog.show()
    qapp.processEvents()
    assert dialog.width() == 400
    assert dialog.height() == 320
    assert dialog._scroll.widgetResizable()
    assert dialog._scroll.verticalScrollBar().maximum() > 0
    assert set(dialog._rows) == {"claude", "codex", "antigravity"}
    assert all(row["installation"].isVisible() for row in dialog._rows.values())
    dialog.close()


def test_persistently_connected_source_reports_install_auth_consent_and_quota_separately(qapp):
    dialog = ConnectionsDialog(
        {"claude": True, "codex": False, "antigravity": False}, lambda *_: None
    )
    dialog.update_status("claude", "available", "authenticated")
    qapp.processEvents()
    row = dialog._rows["claude"]
    assert "Instalação: Instalado" in row["installation"].text()
    assert "Autenticação: sessão reconhecida" in row["auth"].text()
    assert "Coleta: Autorizada" in row["consent"].text()
    assert "Quota: consulta recebida" in row["quota"].text()
    dialog.close()
