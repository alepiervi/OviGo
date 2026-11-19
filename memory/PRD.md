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
## Backlog
- P1: add BREVO_API_KEY, MAIL_FROM (emails), CLOUDINARY_URL (photos), PUBLIC_URL after deploy
- P2: original design issues: search form overflows on mobile, header nav alignment, "Ho cliccato il link (simula)" label
