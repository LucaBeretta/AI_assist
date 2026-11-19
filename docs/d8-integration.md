# D8 — rapporto di integrazione

Branch locale: `web-ui-merge`, creato da `web-ui-main` (`41c0505`). Integrato `upstream/main` a `e27ff27`. Nessun push. I branch `web-ui-main`, `web-ui-design` e i worktree di prova non sono stati modificati.

## Commit

| Commit | Risultato |
| --- | --- |
| `dba7e4b` | Merge del core e delle protezioni di Luca nella UI desktop pywebview. Suite verde prima del commit. |
| `4ff3427` | Approvazioni internet, registro connessioni e controlli firewall. |
| `1369359` | Vista Compute, sola lettura e campionamento ogni cinque secondi mentre visibile. |
| `e1c797f` | Check-in orario senza Tk, opt-in e stesse regole temporali. |
| `14ae9b0` | Abbinamento LAN manuale, disattivato a ogni avvio, con servizio figlio soggetto al gate. |
| `73fc8a1` | Correzione emersa dal collaudo nativo: riattivazione dei controlli dopo download conclusi, anche prima del ritorno della chiamata API. |

Un ultimo commit comprende launcher Linux, documentazione e ulteriori verifiche dei confini di bootstrap/abbinamento.

## I 21 conflitti

| File | Risoluzione |
| --- | --- |
| `AIassist.py` | Entrypoint desktop mantenuto; installazione del gate prima di caricare pywebview. Nessun ritorno a Tk. |
| `README.md` | Mantenuta la descrizione della nostra UI; aggiunti core, privacy, Compute, check-in, abbinamento e limiti di collaudo. |
| `Start AI Assist.command` | Mantenuti avvio pywebview e setup privato; conservate le due conferme esplicite di Luca per il bootstrap. |
| `backend_ui.py` | Eliminato il mixin Tk; protocollo/lifecycle nei moduli core e negli adattatori web. |
| `chat_store.py` | Integrata la creazione privata della cartella di Luca, preservando ambienti, versioni, bozze, recupero e progetti. |
| `coding_agent.py` | Conservati controlli dei turni e gate di Luca insieme a diff, checkpoint e metriche; registrato anche l'avvio dei comandi esterni. |
| `deploy_dialog.py` | Eliminato il dialogo Tk; pubblicazione tramite API web e gate prima della CLI. |
| `gui_runtime.py` | Mantenuti diagnostica pywebview, log e avvio staccato; rimossi riferimenti operativi a Tk. |
| `install_runtime.ps1` | Setup privato e dipendenze pywebview mantenuti; conferme e verifiche di Luca conservate. |
| `status_bar.py` | Eliminata la barra Tk; valori in `status_values.py`, privacy autorevole da `app_core.privacy`. |
| `test_gui_runtime.py` | Mantenuti test del runtime desktop pywebview. |
| `test_night_console.py` | Rimossi test della vecchia presentazione Tk; restano test DOM e della UI desktop. |
| `test_update_checker.py` | Integrate le verifiche pure del pacchetto; rimossi solo test del dialogo Tk. |
| `test_workspace.py` | Rimossi test del workspace Tk ritirato; logica e nuove API hanno test separati. |
| `update_checker.py` | Conservato il confronto completo dei file/pacchetti di Luca e il gate; eliminato il componente Tk. |
| `webui/__init__.py` | Pacchetto della nostra UI desktop, senza server HTTP. |
| `webui/static/app.js` | Controller desktop mantenuto, con instradamento dei nuovi eventi. |
| `webui/static/index.html` | Pagina desktop mantenuta, con le quattro aggiunte D8. |
| `webui/static/markdown.js` | Renderer offline sanitizzato della nostra UI mantenuto. |
| `webui/static/styles.css` | Token e Night/Day della nostra UI mantenuti; aggiunti solo stili necessari alle nuove funzioni. |
| `workspace_ui.py` | Eliminato il workspace Tk; compiti, approvazioni e anteprime restano negli adattatori web. |

## Conservato da Luca

- Core di protocollo, streaming/Stop, lock di istanza, percorsi, impostazioni, privacy e provider. Il lock del desktop usa unicamente `app_core.instance_lock`; l'attivazione di una finestra già aperta usa un segnale su file.
- `network_gate.py`, `child_gate/`, `firewall_lock.py`, `compute_telemetry.py`, `compute_routing.py`, `peer_pairing.py`, `sharing_control.py`, `quiet_audio.py`, `app_core/hourly_summary.py` e l'estensione VS Code sono identici a `upstream/main`.
- Fix del motore, validazione dei turni dell'agente, arresto Colibri su Windows, ambiente dei figli, controlli di download e documenti di architettura/sicurezza. Le sole parti dei test legate ai widget eliminati sono state ritirate.
- Formattazione e geometria pure di Compute in `app_core/compute_values.py`; raccolta del check-in spostata accanto a `hourly_summary.py`.

Eccezioni deliberate a “core invariato”: `app_core/ollama.py` accetta il metodo HTTP, gestisce DELETE con risposta vuota e riceve `keep_alive`. Servono alle funzioni desktop già esistenti; trasporto, parsing, limiti e gate restano quelli di Luca. `app_core/runtime.py` è un adattatore sottile di 39 righe, non un secondo motore.

## Ritirato

`ollama_engine.py` duplicato; server/browser preview (`web_workspace.py`, `webui/server.py`, `webui/security.py`, relativi asset/test e `tools/web_e2e.py`); componenti Tk di gate, Compute e check-in e relativo launcher. `dashboard_placement.py` non aveva più utilizzatori dopo la rimozione delle finestre Tk; la nostra finestra conserva il proprio controllo di geometria visibile.

## Confini delle nuove funzioni

Le approvazioni sono serializzate e hanno due passi: il secondo attende tre secondi anche sul lato Python. Invio, Esc e chiusura bloccano. I worker attendono eventi; la finestra resta disponibile. Il registro e il firewall usano i moduli esistenti.

Compute non contatta altri dispositivi e non inventa valori mancanti. I dati di abbinamento non equivalgono a telemetria live. Il check-in resta spento per default: su Windows, dopo opt-in, attende un'ora e venti secondi di quiete e rinvia se chat o Developer sono occupati. Non avvia controlli internet automatici.

La condivisione non crea listener all'avvio e non salva lo stato attivo. Il controller esistente avvia, solo dopo azione e approvazione esplicite, un processo Python soggetto a `child_gate`. Il suo controllo usa pipe locali con messaggi limitati; il servizio offre solo discovery e abbinamento TLS. Il confronto del certificato precede l'invio del PIN; valgono i 120 secondi e i tre tentativi del core. Nessuna API desktop, inferenza, chat, file o comando viene esposto. Chiavi e token non entrano nel bridge né in Compute.

## Verifica

- Merge prima del commit: `Ran 547 tests in 127.034s` / `OK (skipped=11)`.
- Prima verifica integrata D8: `Ran 571 tests in 116.053s` / `OK (skipped=11)`.
- Verifica finale dopo il fix dei download: `Ran 573 tests in 140.337s` / `OK (skipped=11)`, zero errori o fallimenti.
- Node, `vscode-extension/extension.test.js`: un test, passato.
- Import di `webui.app` senza Tk/pywebview a livello modulo: test passato.
- WebView2 reale sul desktop Windows separato e invisibile: un test passato in 17.733 secondi. Gate installato prima delle librerie native; dati di prova in cartella temporanea. Riapertura chat preesistente, risposta reale di `qwen3-coder:30b`, streaming e Stop reali senza approvazioni per Ollama locale, letture live Compute, blocco download tramite Invio, doppia conferma con attesa, impostazioni con LAN/audio spenti, compito Developer.

Nel collaudo nativo il trasferimento del modello è simulato dopo il confine di approvazione, senza download reale. Un test separato con gate installato dimostra che Block impedisce a `/api/pull` di raggiungere il trasporto locale. La risposta del modello nel compito Developer è simulata; il percorso API/agente/bridge/UI è reale. Non sono state cambiate regole firewall né attivato il microfono.

I log locali sono in `../__pycache__/D8-full-suite.log` e `../__pycache__/D8-native-smoke.log`; i driver nativi locali sono nella stessa cartella ignorata. Tutte le finestre del collaudo sono rimaste sul desktop isolato; nessun mouse o tastiera del desktop principale è stato usato.

## Limiti da sottoporre a Claude

- 11 skip: cinque test di link senza privilegi Windows, due di permessi POSIX, quattro TLS per assenza della dipendenza opzionale `cryptography`. La creazione di un'identità LAN mostra un errore esplicito se manca questa dipendenza, senza installarla automaticamente.
- Abbinamento con due dispositivi reali, modifiche firewall/UAC, rilevamento audio reale e interfaccia nativa macOS/Linux non collaudati. Il launcher Linux ha verifiche statiche e installa il backend Qt; le librerie grafiche del sistema restano un prerequisito.
- Il processo figlio per il pairing mantiene invariato il divieto di listener LAN nel processo principale: è un punto da revisionare prima di un push. Non implementa accesso remoto a chat o inferenza.
- Il threat model di Luca è conservato con una sezione iniziale che distingue il bridge desktop attuale dalla vecchia analisi HTTP, rimasta come documentazione storica.

Claude deve revisionare prima di qualsiasi push o PR.
