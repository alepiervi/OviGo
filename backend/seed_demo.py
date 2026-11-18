"""
Carica i dati dimostrativi (agenzie, tour, clienti di prova) in un database OVIGO VUOTO.
Utile per presentare la piattaforma; NON usarlo sul database di produzione con clienti veri.

Uso:   python seed_demo.py            (usa MONGO_URL e DB_NAME del file .env)
Oppure imposta SEED_DEMO=true nel .env: il server lo carica da solo al primo avvio se il database è vuoto.

Tutti gli utenti di prova hanno la password indicata in DEMO_PASSWORD (predefinita: Ovigo2026!).
"""
import os
import json
import asyncio
from pathlib import Path

DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "Ovigo2026!")


async def seed(db, put, create_auth, colls):
    if await db.tours.count_documents({}) > 0:
        print("Il database contiene già dei viaggi: dati dimostrativi NON caricati.")
        return False
    data = json.loads((Path(__file__).parent / "demo_seed.json").read_text())
    n = 0
    for c, docs in data.items():
        if c not in colls:
            continue
        for id_, d in docs.items():
            d = dict(d)
            d["id"] = d.get("id") or id_
            await put(c, d)
            n += 1
    for u in data.get("users", {}).values():
        if u.get("email") and not await db.auth.find_one({"email": u["email"].lower()}):
            await create_auth(u["id"], u["email"], DEMO_PASSWORD)
    g = await db.settings.find_one({"_id": "general"}) or {}
    g.setdefault("banca", {"intestatario": "OVIGO S.r.l. (esempio)", "iban": "IT60X0542811101000000123456", "banca": "Banca di esempio"})
    g.setdefault("modalitaCommissione", "separato")
    g["_id"] = "general"
    await db.settings.replace_one({"_id": "general"}, g, upsert=True)
    for o in data.get("orgs", {}).values():
        doc = await db.orgs.find_one({"_id": o["id"]})
        if doc and not doc.get("banca"):
            doc["banca"] = {"intestatario": o.get("nome", ""), "iban": "IT60X0542811101000000654321", "banca": "Banca di esempio"}
            await db.orgs.replace_one({"_id": o["id"]}, doc)
    print(f"Caricati {n} documenti dimostrativi. Password di tutti gli utenti di prova: {DEMO_PASSWORD}")
    return True


if __name__ == "__main__":
    import server
    asyncio.run(seed(server.db, server.put, server.create_auth, server.COLLS))
