# AI Assist: branch `colibri`

Questo branch parte dall'**ultima versione di Luca**, quella con backend Colibri, streaming, impostazioni, anteprima HTML, deploy su Vercel e memoria condivisa, e ci aggiunge sopra il colibri "come l'abbiamo ragionato noi", più alcune funzioni per chat e agente. Tutto resta locale: AI Assist usa il tuo Ollama e il tuo colibri, e va su internet solo per scaricare i modelli.

---

## In breve

| Novità | A cosa serve |
|---|---|
| **colibri gestito dall'app** | AI Assist trova colibri, lo avvia e lo ferma da solo: niente terminale aperto con `coli serve` |
| **Selettore Engine** nella sidebar | Passare da Ollama a Colibri con un clic (resta anche la finestra Settings → AI backend di Luca) |
| **Scheda "colibri engine"** | Consigli di colibri per il tuo PC, installazione dei modelli, scelta del contesto |
| **Chat che riaprono sul motore giusto** | Riaprendo una chat Colibri, l'app torna su Colibri e riseleziona il modello |
| **Pulsante Stop** | Ferma una risposta o l'agente, tenendo il testo già arrivato |
| **Modifiche a blocchi (`edit_file`)** | L'agente cambia solo le righe necessarie invece di riscrivere il file |
| **Limite file a scelta** | L'agente può lavorare su file fino a 1 MB, letti a pezzi, con avvisi se è troppo pesante |
| **Velocità di colibri** | Token al secondo nella sidebar; conferma prima di un agente lento |
| **Supporto Mac** | Consigli corretti sui chip Apple e avvio con doppio clic |
| **Backup delle chat** | Tutte le chat in un unico zip |

---

## 1. colibri gestito da AI Assist

[colibri](https://github.com/JustVugg/colibri) fa girare modelli molto grandi (Qwen3-Coder, Qwen3.6, DeepSeek V4, GLM-5.2, Kimi K3…). Tiene in RAM la parte del modello che serve sempre e legge il resto dall'SSD mentre risponde: è più lento, ma i modelli possono essere molto più grandi.

**Prima installazione (una volta sola):** scarica colibri da GitHub ed esegui `START-HERE.bat` (Windows) oppure `./start-here.sh` (Mac/Linux).

**Nella versione di Luca** colibri va avviato a mano (`coli serve`) e AI Assist si collega al server. **In questo branch**, quando AI Assist trova la cartella di colibri e il server è su questo computer:
- scegliendo **Engine → Colibri**, AI Assist avvia colibri da solo;
- **Run all** lo avvia e **Stop all** lo ferma; se è spento, si avvia anche inviando un messaggio;
- colibri tiene attivo **un modello alla volta**;
- la cartella viene trovata da sola in `COLIBRI_DIR`, accanto ad AI Assist, oppure in Home, Documenti, Scrivania o Download (anche `colibri-main` estratto da uno ZIP). In alternativa si sceglie con **Choose colibri folder…**.

Un colibri **su un altro computer**, o senza cartella conosciuta, funziona come nella versione di Luca: si indica l'indirizzo in Settings → AI backend e l'app lo usa così com'è.

## 2. Scheda "colibri engine (RAM + SSD)"

In **Find & download models**, con Engine su Colibri la finestra si apre direttamente su questa scheda. Accanto c'è quella di Ollama.

- I **consigli di colibri per il tuo PC**, presi dal suo comando `coli setup --list --json`. Per ogni modello vedi download e RAM necessaria, se gira tutto in RAM o legge dal disco, se è adatto all'agente Developer, e se ci sta sul tuo PC (o perché no).
- Una riga in cima confronta i due motori: il modello veloce consigliato con Ollama e quello consigliato con colibri.
- **Install & use** scarica il modello, prepara colibri e lo avvia. Se annulli, la volta dopo il download riprende da dove era arrivato.
- **Context** sceglie il contesto di colibri, sia per un'installazione nuova sia, con **Apply to the current model**, per il modello attuale (colibri si riavvia da solo).

## 3. Selettore Engine e chat

- Nella sidebar c'è **ENGINE: Ollama / Colibri**. È la stessa scelta della finestra Settings → AI backend di Luca: le due restano sincronizzate.
- Cambiare motore apre una chat nuova; quella di prima resta salvata.
- **Riaprendo una chat** fatta con l'altro motore, l'app ci passa da sola e riseleziona il modello appena il motore risponde. Nella versione di Luca bisognava cambiare motore a mano.
- Se cambi motore mentre l'app sta facendo un controllo in background, il cambio viene messo in coda e applicato appena il controllo finisce, invece di essere rifiutato.

## 4. Pulsante Stop

Mentre il modello risponde, **Send** diventa **Stop**. Vale per le risposte (Ollama e Colibri) e per l'agente.
- Se fermi una risposta, la parte già scritta resta nella chat e viene salvata.
- Premere Invio durante una risposta non la ferma.

## 5. Agente Developer: modifiche a blocchi e file grandi

**`edit_file`:** l'agente indica il blocco esatto da sostituire e il testo nuovo. Il diff da approvare è piccolo, è più veloce (soprattutto con colibri) e rispetta gli a capo di Windows. `write_file` resta per i file nuovi o le riscritture complete. Ogni modifica passa sempre dalla tua approvazione.

**Limite file a scelta** (Developer → scheda Files → *Largest file the agent reads*): 64 KB (predefinito, come prima), 128 KB, 256 KB, 512 KB o 1 MB.
- Prima, oltre i 64.000 byte l'agente si fermava con *"file exceeds the 64,000-byte limit"*.
- Scegliendo un limite alto, l'app mostra cosa significa per il **contesto del modello** e per la **memoria libera** del computer, e chiede conferma.
- Un file più grande di circa un quarto del contesto viene dato all'agente **a pezzi**, con l'indicazione delle righe da leggere dopo. Nell'Activity compare l'avviso *"…the agent reads it in parts"*.
- La scelta viene ricordata.

## 6. Velocità di colibri e avviso prima dell'agente

- Nella sidebar compare la velocità misurata da colibri sull'ultima risposta, per esempio *"~3.4 tok/s on the last answer"*.
- La prima volta che lanci l'agente su colibri in una sessione, l'app chiede conferma se colibri è lento (sotto i 15 token al secondo).
- Se il modello colibri appartiene a una famiglia che non supporta i tool, l'agente viene bloccato subito con un messaggio chiaro.

## 7. Mac

- **Avvio:** doppio clic su `Start AI Assist.command`. Cerca da solo un Python con Tkinter; con Homebrew serve `brew install python-tk`. La prima volta, se macOS lo blocca: clic destro → **Apri**.
- **Chip Apple:** la GPU usa la stessa memoria del sistema. AI Assist considera disponibile per la GPU circa 2/3 della RAM (3/4 sopra i 36 GB), quindi i consigli sono quelli di un Mac con GPU.

## 8. Backup delle chat

In **Chat options → Back up all chats…** crei uno zip con le chat originali in `chats/` (si ripristinano copiandole nella cartella delle chat ad app chiusa), le stesse in Markdown in `readable/`, e un `README.txt`. Sono incluse anche le chat ancora in fase di salvataggio.

---

## Cosa è stato cambiato rispetto alla versione di Luca

- **Nuovi file:** `colibri_launcher.py` (usa il comando `coli` di colibri: stato, consigli, installazione, contesto, avvio e arresto), `colibri_panel.py` (la scheda colibri), `Start AI Assist.command`, questo `LEGGIMI-colibri.md`.
- **Modificati:** `backend_ui.py` (gestione di colibri sopra il suo backend, cambio motore, conferma agente), `workspace_ui.py` (Engine, Stop, riapertura chat, limite file, backup), `AIassist.py` (streaming interrompibile), `coding_agent.py` (`edit_file`, limite a scelta, lettura a pezzi), `chat_store.py` (backup), `model_downloads.py` e `hardware_panel.py` (scheda colibri, Mac), `model_advisor.py` (chip Apple), `README.md`.
- **Il client Colibri di Luca** (`colibri_backend.py`) è usato così com'è per chat e agente.
- **Test di Luca aggiornati in pochi punti**, dove il comportamento concordato è diverso: con Colibri il pulsante dei modelli resta attivo (apre la scheda colibri), e riaprire una chat Colibri cambia motore. Aggiunto inoltre `edit_file` all'elenco dei tool atteso.

## Test

- **259 test superati**: i 228 di Luca più 31 nuovi. La suite completa è stata ripetuta più volte senza errori.
- colibri è simulato con un finto comando `coli` e un finto server nello stesso formato del vero, compreso lo streaming.

```sh
python -m unittest discover -v
python -m compileall -q .
```

## Cosa resta da verificare

- **Un modello colibri vero in uso:** provato solo con il server simulato. Sul tuo PC parti dal modello consigliato nella scheda colibri.
- **Mac:** il rilevamento dei chip Apple è testato simulando i comandi di sistema, non su un Mac vero.
- **Controllo aggiornamenti di Luca:** confronta i file con il suo repository, quindi su questo branch segnalerà sempre "differenze" finché le modifiche non vengono unite.
