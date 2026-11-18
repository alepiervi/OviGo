# OVIGO — piattaforma viaggi di gruppo

Sistema completo: sito pubblico, area cliente, CRM per le agenzie, app dell'accompagnatore, pannello admin e Guide (video e PDF).
I dati sono su un server con database: ogni persona ha il suo account con password e vede solo ciò che le compete.

## Com'è fatto

```
backend/                 il server (Python + FastAPI) e il database (MongoDB)
  server.py              tutte le regole: login, permessi, pagamenti, email
  seed_demo.py           (facoltativo) carica i dati di prova per una presentazione
  demo_seed.json         i dati di prova
  requirements.txt       librerie Python
  .env.example           impostazioni da compilare (diventa .env)
frontend/
  public/index.html      il sito e tutte le aree (una sola pagina)
  public/config.js       indirizzo del server (di solito vuoto)
  public/guide/          video, immagini e PDF delle guide
  package.json           avvio del sito sulla porta 3000
Dockerfile               per mettere tutto online come un unico servizio
```

## Chi fa cosa

| Ruolo | Come entra | Cosa vede |
|---|---|---|
| Visitatore | senza account | viaggi pubblicati, guide |
| Cliente | si registra da solo | le sue prenotazioni, i suoi pagamenti, i gruppi a cui partecipa |
| Agenzia | si registra come azienda, l'admin la verifica | i suoi tour, i suoi clienti, i bonifici in arrivo, il suo catalogo foto |
| Collaboratore | creato dall'agenzia, riceve l'invito via email | i dati della sua agenzia |
| Accompagnatore | creato **solo** dall'admin (Admin → Accompagnatori) o dall'agenzia che organizza il viaggio; riceve l'invito via email | solo i viaggi a cui è assegnato |
| Admin | creato al primo avvio (ADMIN_EMAIL) | tutto, commissioni, verifiche |

Chi si registra dal sito è sempre un cliente: nessuno può diventare accompagnatore da solo.
Un'agenzia può assegnare ai suoi viaggi solo i propri accompagnatori o quelli creati dall'admin come «OVIGO · disponibili per tutte le agenzie».

## Pagamenti (bonifico diviso)

1. Il cliente prenota: il posto è suo, la prenotazione risulta «In attesa del bonifico».
2. Vede **due bonifici** con IBAN e causale già pronti:
   - all'agenzia che organizza: importo meno la commissione;
   - a OVIGO: la commissione (percentuale impostata dall'admin).
3. Clicca «Ho fatto i bonifici».
4. L'agenzia, in **CRM → Bonifici dei clienti**, preme «Ricevuto»: il pagamento risulta e il cliente riceve la notifica.
5. L'admin, in **Pagamenti e commissioni**, conferma le commissioni arrivate e vede quanto deve ancora incassare da ogni agenzia.

In alternativa, l'admin può scegliere «Il cliente paga tutto all'agenzia, OVIGO fattura la commissione all'agenzia»: così il cliente fa un solo bonifico.
Funziona allo stesso modo per acconto, saldo, rate, quota divisa (anche chi riceve il link senza account) e buoni regalo (pagati interamente a OVIGO).

## Foto (Cloudinary + catalogo)

- L'agenzia carica le foto dal PC nel tour (scheda **Foto**) o da **CRM → Catalogo foto**.
- Prima dell'invio il browser porta la foto a massimo 2400 pixel di lato, con qualità alta. Una foto da telefono da 5–15 MB diventa circa 0,5–2,5 MB.
- La foto va **direttamente su Cloudinary**: il server firma solo il permesso, quindi il database non si appesantisce.
- Le pagine chiedono a Cloudinary la misura che serve: 800 px per le card, 1600 px per la pagina del viaggio. Cloudinary manda il formato più leggero che il browser supporta (WebP/AVIF) e usa la compressione `q_auto:good`, che non si vede a occhio.
- **Catalogo**: ogni foto resta nel catalogo dell'agenzia con il luogo o l'argomento ("Matera", "Hotel Subasio"…). Per un altro viaggio si sceglie dal catalogo, senza ricaricarla.
- Se si carica di nuovo la stessa foto, il sistema la riconosce e non la duplica.
- Duplicando un viaggio si copiano anche le foto.
- L'admin può caricare foto «OVIGO» che vedono tutte le agenzie.
- Una foto usata in un viaggio non si può eliminare dal catalogo: prima va tolta dal viaggio.
- Serve la variabile `CLOUDINARY_URL` (su cloudinary.com → Dashboard → *API Environment variable*).

## Email (Brevo)

Partono da sole: conferma dell'indirizzo, password dimenticata, inviti di agenzie e accompagnatori, e ogni notifica (prenotazioni, bonifici, conferme di partenza, sconti di gruppo…).
Servono `BREVO_API_KEY` e `MAIL_FROM` (un mittente verificato su Brevo). Senza chiave il sistema funziona lo stesso, ma non manda email.

## Provarlo sul computer

Servono Python 3.11+ e MongoDB (oppure un database gratuito su MongoDB Atlas).

```
cd backend
pip install -r requirements.txt
cp .env.example .env        # poi compila .env
uvicorn server:app --port 8001
```
Apri http://localhost:8001: il server mostra anche il sito.

## Metterlo online

- **Emergent**: segui `ISTRUZIONI-EMERGENT.md`.
- **Qualsiasi servizio che usa Docker** (Render, Railway, Fly.io, un server tuo): usa il `Dockerfile` e un database MongoDB Atlas; imposta le variabili del file `.env.example`.

## Prima di aprire al pubblico

- Inserisci da **Admin → Pagamenti e commissioni** IBAN, intestatario e percentuale di commissione.
- Scrivi da **Admin → Privacy e condizioni** l'informativa privacy e le condizioni d'uso (meglio se verificate da un consulente).
- Fai verificare al commercialista lo schema di incasso (commissione con bonifico separato o fatturata all'agenzia) e la fatturazione elettronica.
- Ogni agenzia deve inserire il proprio IBAN in **CRM → Bonifici dei clienti**.

## Cosa resta da fare in una fase successiva

- I prezzi sono calcolati nella pagina del cliente: il server impedisce di segnarsi «pagato» e di abbassare il totale dopo la prenotazione, ma l'agenzia deve comunque controllare il totale di ogni nuova prenotazione prima di confermare il bonifico. Il calcolo del prezzo direttamente sul server è il primo miglioramento consigliato.
- Le attività programmate (convocazioni a data e ora, promemoria) partono quando l'agenzia apre il CRM, non da sole a orario.
- Pagamento online con carta: si può aggiungere un servizio con commissioni basse (es. bonifico istantaneo «paga con banca» / PSD2) quando servirà.
- Fatture elettroniche verso lo SDI: i documenti generati sono riepiloghi, non fatture elettroniche.
