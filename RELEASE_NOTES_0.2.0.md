# NeuralMeter 0.2.0

Experimental release with scoped provider quota snapshots and safer handling of quota identity and renewal state.

## Included

- Provider-scoped quota snapshots, validity and renewal handling.
- Passive browser-extension capture and local-only application architecture retained.
- Desktop executable, Windows installer and browser extension package.

## Limits and validation status

- Claude capture route and payload schema remain assumptions pending real-browser validation.
- ChatGPT quota values remain estimates; no official production quota validation claimed.
- Supervisor verdict: APROVADO COM RESSALVAS. Real-browser capture gate remains open. Code audit approved with reservations; browser validation has not passed.
- Claude route/schema remain unvalidated; ChatGPT quota values remain estimates. No official production quota validation claimed.
- Performance evidence: USS 30.9 MB, idle CPU 0.007%, ACK p95 6.8 ms, `fails=[]`. ACK result does not measure browser-to-HUD latency.
- Installer and extension ZIP compiled; installer was not installed or uninstalled. Prior `Connection.close()` pending-task warning did not recur in final Python suite; cause remains unknown.
- Extension reads provider traffic passively; no prompts are sent. Data remains local; WebSocket binds to loopback.
