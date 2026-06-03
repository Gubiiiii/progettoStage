# Sistema gestione iscritti con QR

Applicazione Python per gestire l'iscrizione a un convegno, generare un QR univoco per ogni partecipante e validare gli ingressi all'evento.

## Funzioni

- modulo pubblico di iscrizione;
- gestione di piu eventi;
- capienza massima configurabile per ogni evento;
- posti accessibili configurabili per ogni evento;
- salvataggio iscritti in database SQLite;
- generazione QR personale;
- download biglietto in PNG con QR e codice manuale;
- invio email di conferma con biglietto allegato, se SMTP configurato;
- area staff con password;
- pagina elenco iscritti;
- filtro iscritti per evento;
- eliminazione iscritti da area staff;
- pagina scanner per QR da fotocamera, lettore USB o codice manuale;
- controllo dei QR gia utilizzati.

## Avvio

1. Crea l'ambiente virtuale:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Installa le dipendenze:

```powershell
pip install -r requirements.txt
```

3. Imposta una password staff:

```powershell
$env:ADMIN_PASSWORD="scegli-una-password"
```

Se vuoi inviare email reali di conferma, imposta anche i dati SMTP:

```powershell
$env:SMTP_HOST="smtp.example.com"
$env:SMTP_PORT="587"
$env:SMTP_USERNAME="utente@example.com"
$env:SMTP_PASSWORD="password"
$env:SMTP_FROM="utente@example.com"
```

Se `SMTP_HOST` non e impostato, l'app funziona comunque ma non invia email.

4. Avvia il sito:

```powershell
python run.py
```

Poi apri `http://127.0.0.1:5000`.

Se vuoi provarlo con un telefono collegato alla stessa rete, avvialo cosi:

```powershell
$env:HOST="0.0.0.0"
python run.py
```

Poi apri dal telefono l'indirizzo IP del computer sulla porta `5000`.

## Uso

- Lo staff crea o modifica gli eventi da `Eventi`, impostando capienza totale, posti accessibili e stato aperto/chiuso.
- Gli utenti scelgono l'evento dalla pagina iniziale e ricevono il proprio QR.
- Se i posti totali sono esauriti, l'iscrizione viene bloccata.
- Se l'utente richiede un posto accessibile e quei posti sono esauriti, l'iscrizione accessibile viene bloccata.
- Dal pulsante `Scarica biglietto` viene scaricato un biglietto PNG con QR e codice manuale.
- Se l'invio email e configurato, lo stesso biglietto viene inviato automaticamente all'indirizzo indicato.
- Lo staff entra in `Area staff`.
- Da `Scanner` si puo leggere il QR con la fotocamera, con una pistola scanner collegata al computer, oppure inserire il codice manuale.
- Se il codice e valido viene registrato l'ingresso.
- Se il codice e gia stato usato, il sistema lo segnala.
- Da `Iscritti` lo staff puo cercare ed eliminare partecipanti.

La password predefinita, se non viene impostata la variabile `ADMIN_PASSWORD`, e `admin`.
