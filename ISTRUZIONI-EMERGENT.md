# Mettere online OVIGO con Emergent

## 1. Collega il repository
1. In Emergent apri un nuovo progetto.
2. Pulsante **GitHub** nella chat → **Pull from GitHub** → scegli il repository di OVIGO, branch `main`.

## 2. Incolla questo messaggio nella chat di Emergent

> Ho importato da GitHub il progetto OVIGO, già completo e funzionante. NON riscrivere l'app e non cambiare la grafica: va solo messa online.
> - Backend: `backend/server.py` (FastAPI, l'app si chiama `app`, tutte le rotte sono sotto `/api`). Usa MongoDB con le variabili `MONGO_URL` e `DB_NAME`. Installa `backend/requirements.txt`. Deve girare sulla porta 8001.
> - Frontend: è una pagina unica già pronta in `frontend/public/index.html` (più `config.js` e la cartella `guide/`). In `frontend/package.json` `yarn start` la serve sulla porta 3000 e `yarn build` copia `public/` in `build/`. Il sito chiama il server con indirizzi relativi `/api/...`, quindi non serve REACT_APP_BACKEND_URL. Se la tua configurazione lo richiede, adatta solo l'avvio, senza toccare i file in `public/`.
> - Aggiungi al file `backend/.env` queste variabili (i valori te li do io): `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `BREVO_API_KEY`, `MAIL_FROM`, `MAIL_FROM_NAME=OVIGO`, `PUBLIC_URL` (l'indirizzo pubblico del sito dopo il deploy). Vedi `backend/.env.example`.
> - Verifica in anteprima: `/api/health` risponde `{"status":"ok"}`, la home mostra il sito OVIGO, la registrazione di un cliente funziona, l'admin entra con ADMIN_EMAIL.

## 3. Dai a Emergent i valori (in chat o nei "secrets" del progetto)
- **ADMIN_EMAIL / ADMIN_PASSWORD**: l'email e la password con cui entrerai come amministratore (password lunga).
- **BREVO_API_KEY**: su brevo.com → *SMTP & API* → *API Keys* → *Generate*.
- **MAIL_FROM**: l'indirizzo mittente verificato su Brevo (es. `noreply@tuodominio.it`).
- **PUBLIC_URL**: l'indirizzo del sito online (lo aggiungi dopo il primo deploy).

Solo per una presentazione, puoi aggiungere `SEED_DEMO=true`: al primo avvio carica agenzie, tour e clienti di prova (password di tutti: `Ovigo2026!`). Per l'uso vero lascialo a `false`, oppure partite con un database nuovo.

## 4. Pubblica
1. Controlla l'anteprima.
2. Premi **Deploy**.
3. Entra come admin e vai in **Pagamenti e commissioni** per inserire IBAN e commissione, poi in **Privacy e condizioni** per i testi.
4. Se vuoi un dominio tuo (es. `www.ovigo.it`), aggiungilo dalle impostazioni di deploy e aggiorna `PUBLIC_URL`.
