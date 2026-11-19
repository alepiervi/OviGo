# OVIGO – piattaforma viaggi di gruppo (imported from GitHub)
## Problem statement
"Leggi e procedi a creare tutto quanto il progetto che trovi su GitHub che ti ho importato" – set up and run the imported OVIGO project without rewriting it. User choices: load demo data; the admin/developer must be able to delete demo data and see/do everything; email (Brevo) and photos (Cloudinary) later.
## Architecture
- backend/server.py FastAPI (all /api), MongoDB (MONGO_URL, DB_NAME=ovigo), seed from demo_seed.json when SEED_DEMO=true
- frontend/public/index.html single-page app served by `serve` on 3000, calls relative /api
## Done (2026-06)
- backend/.env + frontend/.env created, admin admin@ovigo.it, demo data seeded
- NEW admin section "Dati di prova" (GET/DELETE /api/admin/demo): counts and one-click delete of demo data and linked docs; once deleted, demo data is not loaded again on restart (db.meta demo_rimossa)
- Deleting a user now also removes their login record
- Tested: 15/15 backend tests, main frontend flows, demo delete checked by hand, then demo data loaded again
## Done (2026-06, iteration 2) – telefono, tablet e app installabile
- app-mobile.css/js: menu ☰ sul sito, menu a scomparsa nelle aree riservate, tabelle che scorrono di lato, header centrato, niente pagina che esce dallo schermo
- PWA (manifest, icone, sw.js): installabile su Android/iOS/desktop senza store; modale «Scarica l'app» + banner in basso
- Web Push con VAPID (pywebpush): /api/push/chiave|iscrivi|disiscrivi|stato|prova; ogni nuova notifica (notify() + PUT notifications) arriva anche come push a user/org/admin
- iOS: notifiche solo dopo «Aggiungi alla schermata Home» (iOS 16.4+)
## Done (2026-06, iteration 3) – Notifica al gruppo
- CRM → «📲 Notifica al gruppo» + pulsante «📲 Notifica» in Programmazione gruppi: messaggio (max 500, modelli veloci) a tutti gli iscritti del viaggio → notifica in app + push sul telefono (+ email se Brevo attivo)
- API GET/POST /api/tour/{tid}/gruppo (solo agenzia del viaggio o admin), storico in db.avvisi_gruppo; «Messaggio al gruppo» in Convocazioni usa lo stesso invio
## Backlog
- Messaggi programmati inviati dal server (oggi partono solo quando l'agenzia ha il CRM aperto)
- P1: add BREVO_API_KEY, MAIL_FROM (emails), CLOUDINARY_URL (photos), PUBLIC_URL after deploy
- P2: original design issues: search form overflows on mobile, header nav alignment, "Ho cliccato il link (simula)" label
