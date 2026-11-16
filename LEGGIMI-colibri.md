# AI Assist: branch `colibri`

Questo branch aggiunge ad AI Assist un secondo motore locale, **colibri**, e una serie di miglioramenti alla chat e all'agente Developer. Tutto resta sul tuo computer: AI Assist usa il tuo Ollama e il tuo colibri, e va su internet solo per scaricare i modelli.

---

## In breve

| Novità | A cosa serve |
|---|---|
| Motore **colibri** accanto a Ollama | Far girare modelli molto più grandi (anche centinaia di miliardi di parametri) usando RAM e SSD, con o senza GPU |
| Consigli doppi | Una scheda per Ollama (GPU, veloce) e una per colibri (RAM + SSD, modelli più grandi) |
| Risposte in streaming | Il testo compare mentre il modello scrive |
| Pulsante **Stop** | Ferma una risposta o l'agente, tenendo quello che è già arrivato |
| Modifiche a blocchi (`edit_file`) | L'agente cambia solo le righe necessarie invece di riscrivere il file |
| Velocità di colibri | Mostra i token al secondo e chiede conferma prima di lanciare un agente lento |
| Contesto di colibri dall'app | Si sceglie senza usare il terminale |
| Supporto Mac | Consigli corretti sui chip Apple e avvio con doppio clic |
| Backup delle chat | Tutte le chat in un unico zip |

---

## 1. Il motore colibri

[colibri](https://github.com/JustVugg/colibri) fa girare modelli "mixture of experts" enormi (Qwen3-Coder, Qwen3.6, DeepSeek V4, GLM-5.2, Kimi K3…). Tiene in RAM solo la parte del modello che serve sempre e legge il resto dall'SSD mentre risponde. È più lento di un modello che sta tutto nella GPU con Ollama, ma permette di usare modelli molto più grandi.

**Prima installazione (una volta sola)**
1. Scarica colibri da GitHub (Code → Download ZIP, oppure `git clone`).
2. Avvia `START-HERE.bat` (Windows) o `./start-here.sh` (Mac/Linux).

**In AI Assist**
- Nella sidebar c'è il selettore **Engine**: *Ollama* oppure *colibri*.
- AI Assist trova colibri da solo: cerca nella variabile `COLIBRI_DIR`, accanto alla sua cartella, e nelle cartelle Home, Documenti, Scrivania e Download (anche nelle cartelle `colibri-main` create dagli zip). Se non lo trova, nella scheda colibri premi **Choose colibri folder…**.
- colibri tiene attivo **un modello alla volta**. Se è spento, si avvia da solo quando invii un messaggio. *Run all* lo avvia e *Stop all* lo ferma.
- Ogni chat ricorda il motore con cui è stata fatta. Se la riapri, AI Assist torna su quel motore.
- L'agente Developer funziona anche su colibri, con i modelli che gestiscono i tool: Qwen3.6/Qwen3-Coder, Qwen3.8, GLM, DeepSeek V4, Kimi e MiMo.

## 2. Consigli per il tuo PC: due schede

In **Find & download models** ci sono ora due schede di consigli:

- **Recommended for this PC (Ollama, GPU)**: modelli che stanno nella GPU, quindi veloci. È la logica che c'era già.
- **colibri engine (RAM + SSD)**: i consigli di colibri, presi dal suo comando `coli setup --list --json`. Per ogni modello vedi:
  - quanto pesa il download e quanta RAM serve (minima e consigliata);
  - se gira tutto in RAM o legge dal disco;
  - se è adatto all'agente Developer;
  - se ci sta sul tuo PC e, in caso contrario, perché.

In cima alla scheda colibri una riga confronta i due motori, per esempio: *"Con Ollama il modello veloce consigliato è X. Con colibri è consigliato Y e puoi arrivare fino a Z, più lento."*

**Install & use** scarica il modello, prepara colibri per il tuo PC e lo avvia. Se annulli, la volta dopo il download riprende da dove si era fermato.

## 3. Risposte in streaming e pulsante Stop

- Il testo compare mentre il modello lo scrive, sia con Ollama sia con colibri. In alto a destra della risposta vedi prima "thinking…" e poi "writing…".
- Mentre il modello risponde, il pulsante **Send** diventa **Stop**. Funziona anche con l'agente Developer.
- Se fermi una risposta, la parte già scritta resta nella chat e viene salvata.
- Premere Invio durante una risposta non la ferma.

## 4. Agente Developer: modifiche a blocchi

L'agente ha un nuovo strumento, `edit_file`: indica il blocco esatto da sostituire e il testo nuovo, invece di riscrivere tutto il file.

- Genera molti meno token, quindi è più veloce, soprattutto con colibri.
- Rischia meno di rovinare o troncare i file lunghi.
- Il diff da approvare è piccolo e facile da leggere.
- Se il blocco non si trova, o compare più volte, l'agente riceve un errore chiaro e riprova. Nel file non cambia nulla.
- Rispetta gli a capo in stile Windows.

`write_file` resta per i file nuovi o le riscritture complete. Ogni modifica passa sempre dalla tua approvazione (**Apply change** / **Reject**).

## 5. Velocità di colibri e avviso prima dell'agente

- Nella sidebar compare la velocità misurata da colibri sull'ultima risposta, per esempio *"~3.4 tok/s on the last answer"*.
- La prima volta che lanci l'agente Developer su colibri in una sessione, l'app chiede conferma e spiega che può metterci diversi minuti. Non chiede nulla se l'ultima risposta è andata a 15 token al secondo o più.

## 6. Contesto di colibri dall'app

Nella scheda colibri c'è il selettore **Context**: Automatic, 4K, 8K, 16K, 32K, 64K o 128K token.

- Il valore scelto viene usato da **Install & use**.
- **Apply to the current model** cambia il contesto del modello già installato e riavvia colibri.
- Un contesto più lungo richiede più RAM.

## 7. Mac

- **Avvio:** doppio clic su `Start AI Assist.command`. Cerca da solo un Python con Tkinter: quello di python.org va bene, con Homebrew serve anche `brew install python-tk`. Se la prima volta macOS non lo apre, fai clic destro e scegli **Apri**.
- **Chip Apple (M1 e successivi):** la GPU usa la stessa memoria del sistema. AI Assist considera disponibile per la GPU circa 2/3 della RAM (3/4 sopra i 36 GB). In questo modo i consigli sono quelli di un Mac con GPU, non quelli prudenti di un computer senza scheda video.

## 8. Backup delle chat

In **Chat options → Back up all chats…** crei uno zip con:
- `chats/`: i file originali delle chat. Per ripristinarle, copiali nella cartella delle chat con AI Assist chiuso;
- `readable/`: le stesse conversazioni in formato Markdown, leggibili con qualsiasi editor;
- `README.txt`: le istruzioni per il ripristino.

Le chat danneggiate vengono escluse e segnalate.

---

## Dove sono salvati i dati

| Cosa | Dove |
|---|---|
| Chat | Windows: `%LOCALAPPDATA%\AI Assist\chats` · Mac/Linux: `~/.aiassist/chats` (oppure `AI_ASSIST_DATA_DIR`) |
| Cartella di colibri e ultimo motore usato | sottocartella `settings` accanto a `chats` |

Ogni chat è un file JSON a sé, scritto in modo sicuro (un crash a metà non lo rovina). Le chat salvate prima di questo branch vengono considerate chat di Ollama.

## Impostazioni opzionali (variabili d'ambiente)

| Variabile | Effetto |
|---|---|
| `AI_ASSIST_ENGINE` | motore all'avvio: `ollama` o `colibri` (altrimenti viene usato l'ultimo) |
| `COLIBRI_DIR` | cartella di colibri |
| `COLIBRI_HOST` | indirizzo del server colibri (di default quello indicato da `coli status`, normalmente `http://127.0.0.1:8000`) |
| `COLI_API_KEY` | chiave da inviare se il server colibri la richiede |
| `OLLAMA_HOST`, `OLLAMA_MODEL` | come prima |

---

## File modificati e aggiunti

**Nuovi**
- `colibri_client.py`: trova colibri, usa il suo comando `coli` (stato, consigli, installazione, avvio, arresto, contesto) e comunica con il suo server, traducendo le chiamate ai tool tra il formato di Ollama e quello di OpenAI.
- `colibri_panel.py`: la scheda "colibri engine" con consigli, installazione, contesto e confronto con Ollama.
- `streaming.py`: lettura delle risposte in streaming, con Stop immediato.
- `Start AI Assist.command`: avvio con doppio clic su Mac.

**Modificati**
- `AIassist.py`: scelta del motore, streaming, avvio e arresto di colibri.
- `workspace_ui.py`: selettore Engine, risposta che si riempie mentre arriva, pulsante Stop, avviso per l'agente su colibri, backup.
- `coding_agent.py`: strumento `edit_file` e agente su colibri.
- `chat_store.py`: motore salvato in ogni chat, backup zip.
- `model_advisor.py`, `hardware_panel.py`: chip Apple.
- `model_downloads.py`, `capabilities_view.py`: scheda colibri e capacità del modello colibri.
- `README.md`: documentazione in inglese delle nuove funzioni.

## Test

- **178 test**, tutti superati: i 137 che c'erano già e 41 nuovi. La suite completa è stata ripetuta 12 volte senza errori.
- colibri è simulato con un finto comando `coli` e un finto server nello stesso formato del vero, compreso lo streaming.
- Lettura del catalogo e dello stato verificate sul vero colibri.
- Comandi per lanciare i test:
  ```sh
  python -m unittest discover -v
  python -m compileall -q .
  ```

## Cosa resta da verificare

- **Un modello colibri vero in uso:** è stato provato solo con il server simulato. Sul tuo PC parti dal modello consigliato nella scheda colibri.
- **Mac:** il rilevamento dei chip Apple è testato simulando i comandi di sistema, non su un Mac vero.

## Bug corretti durante il lavoro

- Chiudendo e riaprendo velocemente la finestra dei modelli mentre colibri stava ancora rispondendo, l'app poteva andare in crash. Ora le finestre chiuse restano in memoria finché l'app è aperta, e i lavori in background non tengono più riferimenti all'interfaccia.
- Nella scheda colibri, i messaggi di esito (installazione riuscita, errori, contesto cambiato) sparivano subito dopo l'aggiornamento. Ora restano visibili.
