# AI Assist: Ollama e Colibri nella nuova interfaccia

AI Assist usa un'unica interfaccia desktop web con pywebview: WebView2 su Windows, WKWebView su macOS. Il motore resta Python e le chat già salvate si riaprono senza conversioni. I modelli lavorano sul server configurato; download, controllo aggiornamenti e pubblicazione Vercel usano internet quando richiesti.

## In breve

| Schermata | A cosa serve |
|---|---|
| **Chat** | Conversazioni, streaming e Stop, allegati, contesto sempre visibile, compattazione e versioni delle risposte |
| **Developer** | Progetto, file fissati, piano, approvazioni di modifiche e comandi, Activity e punti di ripristino |
| **Models / Modelli** | Hardware, modelli installati con ciò che sanno fare (leggere immagini, usare strumenti, ragionare), modelli dei computer abbinati, consigli, download Ollama e gestione del motore Colibri |
| **Settings / Impostazioni** | Lingua, tema, memoria condivisa, prompt salvati, scaricamento dalla memoria, aggiornamenti e backup |

## 1. Avvio

Su Windows fai doppio clic su **Start AI Assist.cmd**; su Mac su **Start AI Assist.command**. Il launcher cerca Python con pywebview. Se manca, propone un Python privato nella cartella `runtime`, verifica il checksum e installa `requirements.txt`, senza modificare gli altri Python.

Da terminale:

```sh
python -m pip install -r requirements.txt
python AIassist.py
```

`--web` resta un alias facoltativo. Su Windows l'app parte con `pythonw` separato dal terminale: chiudere il terminale non chiude l'app. Gli errori finiscono in `%LOCALAPPDATA%\AI Assist\last-run.log`. Il primo avvio propone hardware, modello consigliato, download e prima chat; si può saltare.

## 2. Colibri gestito da AI Assist

Installa prima Colibri con le sue istruzioni. In **Modelli → Motore Colibri**, scegli la cartella con **Sfoglia**, carica il catalogo e scegli un modello compatibile. L'app usa i comandi del launcher Colibri per installazione, contesto, avvio e arresto. La cartella può essere trovata anche con `COLIBRI_DIR` o nelle posizioni locali comuni.

Colibri usa un modello alla volta. Le operazioni sul server vengono bloccate mentre una chat o una revisione lo usa. Un server remoto si usa già avviato: installazione e avvio/arresto richiedono una cartella locale conosciuta. `COLIBRI_URL` sceglie il server; `COLI_API_KEY` fornisce la chiave facoltativa senza salvarla nelle impostazioni web.

## 3. Selettore motore e chat

Il selettore nel composer raggruppa i modelli per **Ollama / Colibri**. Le opzioni non disponibili indicano il motivo. Ogni chat conserva motore, modello e contesto. Il menu del contesto permette di compattare o creare una nuova chat nello stesso ambiente. I Progetti raggruppano chat con istruzioni, cartella e impostazioni comuni.

## 4. Pulsante Stop e recupero

Stop interrompe una risposta e conserva il testo arrivato. Invio durante lo streaming non la ferma. Le bozze vengono salvate; dopo un'interruzione si può conservare o scartare una risposta parziale. Le chat in incognito restano solo in memoria e non entrano nel backup né nella memoria condivisa.

## 5. Developer: modifiche e revisione qualità

Il Developer web usa un modello Ollama con strumenti. Scegli una cartella esistente e fissa i file prima del compito. **Prima un piano** richiede l'approvazione del piano; ogni modifica e comando passa poi dalla propria approvazione. I diff possono essere applicati a blocchi e i punti di ripristino recuperano i file del compito. I comandi usano i permessi del tuo account.

Il limite file salvato parte da 64 KB e supporta valori fino a 1 MB; i file grandi possono essere letti per intervalli di righe. Colibri può fare una revisione qualità senza strumenti dopo un solo lavoro Ollama: si configura nelle Impostazioni e si sceglie per il compito. Lo scambio di memoria gestisce solo il modello locale scelto e una cartella Colibri conosciuta. Fermare la sola revisione conserva il lavoro già completato. La revisione è un consiglio, non una prova che tutto funzioni.

## 6. Stato, memoria e scorciatoie

La barra in basso mostra motore, modello, quota GPU/CPU, VRAM, contesto, token al secondo e passo dell'agente quando i dati sono disponibili. Indica anche quando si usa un server remoto. Nelle Impostazioni puoi aggiungere, modificare e dimenticare ricordi; dal menu della chat scegli quelli da usare.

- **Ctrl+K:** cerca chat e comandi.
- **Ctrl+N:** nuova chat; **Ctrl+Shift+N:** chat in incognito.
- **Esc:** Stop; **↑ nel composer vuoto:** modifica l'ultimo messaggio.
- **Ctrl+/:** elenco scorciatoie; **Ctrl+Shift+L:** tema Night/Day.
- **Ctrl+più / Ctrl+meno / Ctrl+0:** zoom; **/** nel composer: prompt salvati.

## 7. Mac

**macOS non è ancora stato collaudato su hardware reale.** Il launcher è `Start AI Assist.command`; la finestra usa WKWebView. Se macOS lo blocca al primo avvio, prova clic destro → **Apri**. I test dei consigli sui chip Apple simulano il rilevamento della memoria unificata: non sostituiscono il collaudo sul Mac.

## 8. Backup delle chat

In **Impostazioni → Esporta** crei un backup di chat e progetti. Le chat in incognito sono escluse. La cartella dati resta quella precedente: `%LOCALAPPDATA%\AI Assist\chats` su Windows, `~/.aiassist/chats` altrove, oppure quella indicata da `AI_ASSIST_DATA_DIR`. Titoli, formato delle chat e preferenze `appearance.settings` restano compatibili.

## Test e verifiche rimaste

```sh
python -m unittest discover
```

Su Windows il runner `python run_windows_tests.py` esegue i test su un desktop separato invisibile. I test di logica non aprono finestre; modelli e comandi esterni sono simulati. I test dei link simbolici richiedono i relativi permessi Windows.

Restano da verificare sul campo modelli Colibri reali, installazioni e download, pubblicazione Vercel e macOS. Il controllo aggiornamenti confronta i file con il repository di Luca: una copia di sviluppo può quindi mostrare differenze anche senza un aggiornamento da installare.
