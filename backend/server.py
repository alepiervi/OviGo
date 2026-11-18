"""
OVIGO - server della piattaforma viaggi di gruppo.

FastAPI + MongoDB. Tutte le rotte sono sotto /api.
- Login con email e password (token JWT), ruoli: cliente, organizzatore, accompagnatore, admin.
- Ogni utente riceve solo i dati che può vedere (/api/sync) e può scrivere solo ciò che gli compete (/api/doc).
- Pagamenti con bonifico diviso: commissione a OVIGO, resto all'agenzia che ha creato il viaggio.
- Email automatiche con Brevo.

Variabili d'ambiente: vedi .env.example
"""
import os
import re
import time
import secrets
import logging
import asyncio
import base64
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import bcrypt
import jwt
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).parent
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

log = logging.getLogger("ovigo")
logging.basicConfig(level=logging.INFO)

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "ovigo")
if MONGO_URL.startswith("mongomock"):
    from mongomock_motor import AsyncMongoMockClient as _Client  # solo per i test
    MONGO_URL = "mongodb://localhost"
else:
    from motor.motor_asyncio import AsyncIOMotorClient as _Client
client = _Client(MONGO_URL)
db = client[DB_NAME]

BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
MAIL_FROM = os.environ.get("MAIL_FROM", "")
MAIL_FROM_NAME = os.environ.get("MAIL_FROM_NAME", "OVIGO")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
JWT_SECRET = os.environ.get("JWT_SECRET", "")
TOKEN_DAYS = 30

COLLS = ["tours", "bookings", "users", "orgs", "notifications", "reviews", "settings", "proposte",
         "messaggi", "foto", "eventi", "codici", "cassa", "appelli", "pool", "avvisi", "bozze", "dm",
         "media", "fornitori", "collab", "programmati"]

app = FastAPI(title="OVIGO")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])


# ------------------------------------------------------------------ utilità
def now_ms() -> int:
    return int(time.time() * 1000)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id(p: str) -> str:
    return p + format(now_ms(), "x")[-8:] + secrets.token_hex(3)


def out(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    d = {k: v for k, v in doc.items() if not k.startswith("_")}
    d["id"] = doc["_id"]
    return d


async def raw_get(c: str, id_: str) -> Optional[dict]:
    return out(await db[c].find_one({"_id": id_}))


async def put(c: str, d: dict) -> dict:
    body = {k: v for k, v in d.items() if k != "id" and not k.startswith("_")}
    body["_id"] = d["id"]
    body["_upd"] = now_ms()
    await db[c].replace_one({"_id": d["id"]}, body, upsert=True)
    return out(body)


async def delete_doc(c: str, id_: str):
    await db[c].delete_one({"_id": id_})
    await db.deleted.insert_one({"_id": new_id("x"), "c": c, "id": id_, "_upd": now_ms()})


async def all_docs(c: str, q: Optional[dict] = None):
    return [out(d) async for d in db[c].find(q or {})]


def num(v, default=0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def eur(n) -> str:
    n = round(num(n), 2)
    s = f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if s.endswith(",00"):
        s = s[:-3]
    return "€ " + s


def email_ok(e: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e or ""))


def base_url(request: Optional[Request]) -> str:
    if PUBLIC_URL:
        return PUBLIC_URL
    if request is not None:
        o = request.headers.get("origin")
        if o:
            return o.rstrip("/")
        return str(request.base_url).rstrip("/")
    return ""


# ------------------------------------------------------------------ email (Brevo)
def mail_html(titolo: str, testo: str, link: str = "", bottone: str = "Apri OVIGO") -> str:
    esc = lambda s: (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    btn = f'<p style="margin:24px 0"><a href="{link}" style="background:#e2602b;color:#fff;padding:12px 22px;border-radius:999px;text-decoration:none;font-weight:600">{esc(bottone)}</a></p>' if link else ""
    return f"""<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;color:#1d2b2a">
<div style="font-family:Georgia,serif;font-size:26px;font-weight:700;color:#174a46">OVI<span style="color:#e2602b">GO</span></div>
<h2 style="font-size:19px;margin:18px 0 8px">{esc(titolo)}</h2><p style="font-size:15px;line-height:1.5">{esc(testo)}</p>{btn}
<p style="font-size:12px;color:#7a8584;margin-top:30px">Ricevi questa email perché hai un account su OVIGO, la piattaforma dei viaggi di gruppo.</p></div>"""


async def send_mail(to_email: str, to_name: str, subject: str, html: str):
    if not to_email or not email_ok(to_email):
        return
    if not BREVO_API_KEY or not MAIL_FROM:
        log.info("[email non inviata: Brevo non configurato] a %s · %s", to_email, subject)
        return
    try:
        async with httpx.AsyncClient(timeout=15) as cl:
            r = await cl.post("https://api.brevo.com/v3/smtp/email",
                              headers={"api-key": BREVO_API_KEY, "accept": "application/json", "content-type": "application/json"},
                              json={"sender": {"name": MAIL_FROM_NAME, "email": MAIL_FROM},
                                    "to": [{"email": to_email, "name": to_name or to_email}],
                                    "subject": subject, "htmlContent": html})
            if r.status_code >= 300:
                log.warning("Brevo %s: %s", r.status_code, r.text[:300])
    except Exception as e:  # l'email non deve mai bloccare il sistema
        log.warning("Brevo errore: %s", e)


def fire(coro):
    try:
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        pass


async def notify(to: str, testo: str, extra: Optional[dict] = None, url: str = ""):
    n = {"id": new_id("n"), "to": to, "testo": testo, "letto": False, "createdAt": iso_now()}
    n.update(extra or {})
    await put("notifications", n)
    fire(email_for_notification(n, url))


async def email_for_notification(n: dict, url: str = ""):
    to = n.get("to", "")
    dest = []
    if to.startswith("user:"):
        u = await raw_get("users", to[5:])
        if u and (u.get("preferenze") or {}).get("email", True) is not False:
            dest.append((u.get("email"), f"{u.get('nome','')} {u.get('cognome','')}".strip()))
    elif to.startswith("org:"):
        o = await raw_get("orgs", to[4:])
        if o:
            em = o.get("email")
            if not em:
                staff = await all_docs("users", {"orgId": o["id"], "ruolo": "organizzatore"})
                em = staff[0].get("email") if staff else ""
            dest.append((em, o.get("nome", "")))
    elif to == "admin":
        for a in await all_docs("users", {"ruolo": "admin"}):
            dest.append((a.get("email"), a.get("nome", "")))
    link = url or PUBLIC_URL or ""
    for em, name in dest:
        await send_mail(em, name, "OVIGO · " + (n.get("testo", "")[:70]), mail_html("Hai una nuova notifica", n.get("testo", ""), link))


# ------------------------------------------------------------------ autenticazione
_secret_cache = {"v": JWT_SECRET}
_login_tries: dict = {}


async def jwt_secret() -> str:
    if _secret_cache["v"]:
        return _secret_cache["v"]
    m = await db.meta.find_one({"_id": "jwt"})
    if not m:
        m = {"_id": "jwt", "secret": secrets.token_urlsafe(48)}
        await db.meta.insert_one(m)
    _secret_cache["v"] = m["secret"]
    return m["secret"]


def hash_pw(p: str) -> str:
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()


def check_pw(p: str, h: str) -> bool:
    try:
        return bool(h) and bcrypt.checkpw(p.encode(), h.encode())
    except Exception:
        return False


async def make_token(uid: str, v: int) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=TOKEN_DAYS)
    return jwt.encode({"sub": uid, "v": v, "exp": exp}, await jwt_secret(), algorithm="HS256")


async def current_user(request: Request) -> Optional[dict]:
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer "):
        return None
    try:
        p = jwt.decode(h[7:], await jwt_secret(), algorithms=["HS256"])
    except Exception:
        return None
    a = await db.auth.find_one({"_id": p.get("sub")})
    if not a or a.get("v", 1) != p.get("v") or a.get("disattivato"):
        return None
    return await raw_get("users", p["sub"])


async def need_user(request: Request) -> dict:
    u = await current_user(request)
    if not u:
        raise HTTPException(401, "Devi accedere")
    return u


async def create_auth(uid: str, email: str, password: Optional[str] = None, invite_days: int = 0) -> Optional[str]:
    doc = {"_id": uid, "email": email.lower(), "hash": hash_pw(password) if password else "", "v": 1, "createdAt": iso_now()}
    tok = None
    if invite_days:
        tok = secrets.token_urlsafe(32)
        doc["reset"] = {"token": tok, "exp": now_ms() + invite_days * 864e5}
    await db.auth.replace_one({"_id": uid}, doc, upsert=True)
    return tok


async def email_taken(email: str) -> bool:
    return bool(await db.auth.find_one({"email": email.lower()}))


async def send_verification(u: dict, request: Optional[Request]):
    tok = secrets.token_urlsafe(32)
    await db.auth.update_one({"_id": u["id"]}, {"$set": {"verify": tok}})
    link = f"{base_url(request)}/#verifica-{tok}"
    await send_mail(u["email"], u.get("nome", ""), "Conferma la tua email su OVIGO",
                    mail_html(f"Ciao {u.get('nome','')}, benvenuto in OVIGO!", "Conferma il tuo indirizzo email con il pulsante qui sotto.", link, "Conferma email"))


@app.post("/api/auth/register")
async def register(request: Request):
    b = await request.json()
    nome, cognome = (b.get("nome") or "").strip(), (b.get("cognome") or "").strip()
    email, pw = (b.get("email") or "").strip().lower(), b.get("password") or ""
    if not nome or not cognome or not email_ok(email):
        raise HTTPException(400, "Compila nome, cognome ed email")
    if len(pw) < 8:
        raise HTTPException(400, "La password deve avere almeno 8 caratteri")
    if not b.get("privacy"):
        raise HTTPException(400, "Serve il consenso al trattamento dei dati")
    if await email_taken(email):
        raise HTTPException(409, "Email già registrata: usa Accedi")
    org = b.get("org")
    org_id = None
    if org:
        for k in ("nome", "cellulare", "indirizzo", "regione"):
            if not (org.get(k) or "").strip():
                raise HTTPException(400, "Compila i campi obbligatori dell'azienda")
        allowed = ("nome", "categoria", "network", "rete", "cellulare", "piva", "pec", "indirizzo", "regione", "citta")
        o = {k: (org.get(k) or "").strip() for k in allowed}
        o.update({"id": new_id("o"), "referente": f"{nome} {cognome}", "email": email, "verificato": False,
                  "rete": o.get("rete") or "indipendente", "createdAt": iso_now()})
        await put("orgs", o)
        org_id = o["id"]
    u = {"id": new_id("u"), "nome": nome, "cognome": cognome, "email": email, "citta": (b.get("citta") or "").strip(),
         "ruolo": "organizzatore" if org_id else "cliente", "orgId": org_id, "emailVerificata": False, "profilo": {},
         "privacyIl": iso_now(), "marketing": bool(b.get("marketing")), "createdAt": iso_now()}
    await put("users", u)
    await create_auth(u["id"], email, pw)
    if org_id:
        await notify("admin", f"Nuova azienda da verificare: {org['nome']} ({org.get('categoria','')})", {"orgId": org_id})
        await notify("org:" + org_id, "Benvenuto in OVIGO! Completa l'anagrafica e le coordinate bancarie: la tua azienda sarà visibile ai clienti dopo la verifica.")
    else:
        await notify("user:" + u["id"], f"Benvenuto in OVIGO, {nome}! Completa il tuo profilo viaggiatore per ricevere proposte su misura.")
    fire(send_verification(u, request))
    return {"token": await make_token(u["id"], 1), "user": u}


@app.post("/api/auth/login")
async def login(request: Request):
    b = await request.json()
    email, pw = (b.get("email") or "").strip().lower(), b.get("password") or ""
    ip = request.client.host if request.client else "?"
    key = ip + "|" + email
    tries = [t for t in _login_tries.get(key, []) if t > time.time() - 900]
    if len(tries) >= 8:
        raise HTTPException(429, "Troppi tentativi: riprova tra 15 minuti")
    a = await db.auth.find_one({"email": email})
    if not a or a.get("disattivato") or not check_pw(pw, a.get("hash", "")):
        tries.append(time.time())
        _login_tries[key] = tries
        raise HTTPException(401, "Email o password non corretti")
    _login_tries.pop(key, None)
    return {"token": await make_token(a["_id"], a.get("v", 1)), "user": await raw_get("users", a["_id"])}


@app.get("/api/auth/me")
async def auth_me(request: Request):
    return {"user": await need_user(request)}


@app.post("/api/auth/logout-all")
async def logout_all(request: Request):
    u = await need_user(request)
    a = await db.auth.find_one({"_id": u["id"]})
    v = a.get("v", 1) + 1
    await db.auth.update_one({"_id": u["id"]}, {"$set": {"v": v}})
    return {"token": await make_token(u["id"], v)}


@app.post("/api/auth/forgot")
async def forgot(request: Request):
    b = await request.json()
    email = (b.get("email") or "").strip().lower()
    a = await db.auth.find_one({"email": email})
    if a:
        tok = secrets.token_urlsafe(32)
        await db.auth.update_one({"_id": a["_id"]}, {"$set": {"reset": {"token": tok, "exp": now_ms() + 2 * 3600e3}}})
        u = await raw_get("users", a["_id"]) or {}
        fire(send_mail(email, u.get("nome", ""), "Reimposta la password di OVIGO",
                       mail_html("Reimposta la tua password", "Hai chiesto di reimpostare la password. Il link vale 2 ore. Se non sei stato tu, ignora questa email.",
                                 f"{base_url(request)}/#reset-{tok}", "Scegli una nuova password")))
    return {"ok": True}


@app.post("/api/auth/reset")
async def reset(request: Request):
    b = await request.json()
    tok, pw = b.get("token") or "", b.get("password") or ""
    if len(pw) < 8:
        raise HTTPException(400, "La password deve avere almeno 8 caratteri")
    a = await db.auth.find_one({"reset.token": tok}) if tok else None
    if not a or a["reset"].get("exp", 0) < now_ms():
        raise HTTPException(400, "Link scaduto o non valido: richiedine uno nuovo")
    v = a.get("v", 1) + 1
    await db.auth.update_one({"_id": a["_id"]}, {"$set": {"hash": hash_pw(pw), "v": v}, "$unset": {"reset": ""}})
    u = await raw_get("users", a["_id"])
    if u and not u.get("emailVerificata"):
        u["emailVerificata"] = True
        u = await put("users", u)
    return {"token": await make_token(a["_id"], v), "user": u}


@app.post("/api/auth/verify")
async def verify(request: Request):
    b = await request.json()
    a = await db.auth.find_one({"verify": b.get("token") or "-"})
    if not a:
        raise HTTPException(400, "Link non valido o già usato")
    await db.auth.update_one({"_id": a["_id"]}, {"$unset": {"verify": ""}})
    u = await raw_get("users", a["_id"])
    if u:
        u["emailVerificata"] = True
        await put("users", u)
    return {"ok": True}


@app.post("/api/auth/resend")
async def resend(request: Request):
    u = await need_user(request)
    await send_verification(u, request)
    return {"ok": True}


@app.post("/api/auth/password")
async def change_password(request: Request):
    u = await need_user(request)
    b = await request.json()
    a = await db.auth.find_one({"_id": u["id"]})
    if a.get("hash") and not check_pw(b.get("old") or "", a["hash"]):
        raise HTTPException(400, "La password attuale non è corretta")
    if len(b.get("new") or "") < 8:
        raise HTTPException(400, "La nuova password deve avere almeno 8 caratteri")
    v = a.get("v", 1) + 1
    await db.auth.update_one({"_id": u["id"]}, {"$set": {"hash": hash_pw(b["new"]), "v": v}})
    return {"token": await make_token(u["id"], v)}


# ------------------------------------------------------------------ chi vede cosa
class Ctx:
    def __init__(self, u: Optional[dict]):
        self.u = u
        self.uid = u["id"] if u else None
        self.ruolo = (u or {}).get("ruolo") or ("cliente" if u else "anonimo")
        self.admin = self.ruolo == "admin"
        self.org = (u or {}).get("orgId") if self.ruolo == "organizzatore" else None
        self.email = ((u or {}).get("email") or "").lower()
        self.privati = set((u or {}).get("toursPrivati") or [])
        self.tours: dict = {}
        self.my_tours, self.staff_tours, self.ref_tours, self.org_tours = set(), set(), set(), set()
        self.user_level: dict = {}
        self.invited_bookings = set()

    def ids(self):
        s = set()
        if self.uid:
            s.add("user:" + self.uid)
        if self.org:
            s.add("org:" + self.org)
        if self.admin:
            s.add("admin")
        return s


LEVELS = {"anon": 1, "min": 2, "org": 3, "full": 4}


def bump(ctx: Ctx, uid: str, lvl: str):
    if uid and LEVELS[lvl] > LEVELS.get(ctx.user_level.get(uid, ""), 0):
        ctx.user_level[uid] = lvl


async def make_ctx(u: Optional[dict]) -> Ctx:
    ctx = Ctx(u)
    async for t in db.tours.find({}, {"orgId": 1, "stato": 1, "privato": 1, "accompagnatoreId": 1, "referenteUserId": 1}):
        t["id"] = t["_id"]
        ctx.tours[t["id"]] = t
        if ctx.org and t.get("orgId") == ctx.org:
            ctx.org_tours.add(t["id"])
        if ctx.uid and t.get("accompagnatoreId") == ctx.uid:
            ctx.staff_tours.add(t["id"])
        if ctx.uid and t.get("referenteUserId") == ctx.uid:
            ctx.ref_tours.add(t["id"])
    bks = [b async for b in db.bookings.find({}, {"tourId": 1, "userId": 1, "orgId": 1, "rivenditoreOrgId": 1, "camere": 1, "tipo": 1})]
    for b in bks:
        if not ctx.uid:
            break
        if b.get("userId") == ctx.uid:
            ctx.my_tours.add(b.get("tourId"))
        elif ctx.email and any((o or {}).get("invito") == ctx.email for r in (b.get("camere") or []) for o in (r.get("occ") or [])):
            ctx.my_tours.add(b.get("tourId"))
            ctx.invited_bookings.add(b["_id"])
    # livelli di visibilità degli utenti
    if ctx.uid:
        bump(ctx, ctx.uid, "full")
    group_tours = ctx.my_tours | ctx.ref_tours
    for b in bks:
        uid_, tid = b.get("userId"), b.get("tourId")
        if ctx.admin:
            continue
        if ctx.org and (b.get("orgId") == ctx.org or tid in ctx.org_tours or b.get("rivenditoreOrgId") == ctx.org):
            bump(ctx, uid_, "org")
        elif tid in ctx.staff_tours:
            bump(ctx, uid_, "org")
        elif tid in group_tours:
            bump(ctx, uid_, "min")
        elif tour_level(ctx, ctx.tours.get(tid)):
            bump(ctx, uid_, "anon")
    if ctx.org:
        async for p in db.proposte.find({}, {"userId": 1}):
            bump(ctx, p.get("userId"), "min")
    return ctx


def tour_level(ctx: Ctx, t: Optional[dict]) -> Optional[str]:
    if not t:
        return None
    if ctx.admin or (ctx.org and t.get("orgId") == ctx.org):
        return "full"
    if t["id"] in ctx.staff_tours:
        return "full"
    if t["id"] in ctx.my_tours or t["id"] in ctx.ref_tours or t["id"] in ctx.privati:
        return "pub"
    if t.get("stato") == "pubblicato" and not t.get("privato"):
        return "pub"
    return None


def can_tour(ctx: Ctx, tid: str) -> bool:
    """Partecipa al tour (cliente, invitato, referente, accompagnatore, agenzia, admin)."""
    t = ctx.tours.get(tid)
    return bool(t) and (ctx.admin or tid in ctx.my_tours or tid in ctx.ref_tours or tid in ctx.staff_tours or (ctx.org and t.get("orgId") == ctx.org))


def org_of_tour(ctx: Ctx, tid: str) -> bool:
    t = ctx.tours.get(tid)
    return bool(ctx.org and t and t.get("orgId") == ctx.org)


B_PUBLIC = ["tourId", "orgId", "userId", "tipo", "stato", "pax", "seats", "punto", "adulti", "bambini", "sistemazione",
            "camereN", "createdAt", "scadenza", "scadenzaOpzione", "rivenditoreOrgId", "scontoGruppo"]
T_PRIVATE = ["costiReali", "pagamentiFornitori", "noteInterne"]


def booking_level(ctx: Ctx, b: dict) -> Optional[str]:
    tid = b.get("tourId")
    if ctx.admin or (ctx.uid and b.get("userId") == ctx.uid) or b.get("id") in ctx.invited_bookings:
        return "full"
    if ctx.org and (b.get("orgId") == ctx.org or b.get("rivenditoreOrgId") == ctx.org or org_of_tour(ctx, tid)):
        return "full"
    if tid in ctx.staff_tours or tid in ctx.ref_tours:
        return "full"
    if tour_level(ctx, ctx.tours.get(tid)):
        return "red"
    return None


def redact_booking(b: dict) -> dict:
    r = {k: b[k] for k in B_PUBLIC if k in b}
    r["id"] = b["id"]
    r["camere"] = [{"tipo": c.get("tipo"), "condivisa": c.get("condivisa"), "mezziPropri": c.get("mezziPropri"),
                    "occ": [{"eta": o.get("eta", ""), "sesso": o.get("sesso", "")} for o in (c.get("occ") or [])]}
                   for c in (b.get("camere") or [])]
    r["partecipanti"] = [{"eta": o.get("eta", ""), "sesso": o.get("sesso", "")} for o in (b.get("partecipanti") or [])]
    return r


def redact_user(u: dict, lvl: str) -> dict:
    p = u.get("profilo") or {}
    if lvl in ("full", "org"):
        r = dict(u)
        r.pop("toursPrivati", None) if lvl == "org" else None
        return r
    staff = u.get("ruolo") in ("organizzatore", "accompagnatore", "admin")
    r = {"id": u["id"], "ruolo": u.get("ruolo"), "orgId": u.get("orgId"), "citta": u.get("citta", ""),
         "profilo": {"fascia": p.get("fascia"), "sesso": p.get("sesso")}}
    if lvl == "min" or staff:
        r["nome"] = u.get("nome", "")
        r["cognome"] = (u.get("cognome") or "")[:1] + "." if not staff else u.get("cognome", "")
    return r


def visible(ctx: Ctx, c: str, d: dict) -> Optional[dict]:
    a = ctx.admin
    if c == "tours":
        lvl = tour_level(ctx, d)
        if not lvl:
            return None
        return d if lvl == "full" else {k: v for k, v in d.items() if k not in T_PRIVATE}
    if c == "bookings":
        lvl = booking_level(ctx, d)
        return None if not lvl else (d if lvl == "full" else redact_booking(d))
    if c == "users":
        if a:
            return d
        lvl = ctx.user_level.get(d["id"])
        if d.get("ruolo") in ("organizzatore", "accompagnatore") and ctx.org and d.get("orgId") == ctx.org:
            lvl = "org"
        if not lvl and d.get("ruolo") in ("organizzatore", "accompagnatore"):
            lvl = "min"
        return redact_user(d, lvl) if lvl else None
    if c in ("orgs", "reviews", "avvisi"):
        return d
    if c == "settings":
        return {k: v for k, v in d.items() if not k.startswith("priv")}
    if c == "notifications":
        return d if d.get("to") in ctx.ids() else None
    if c == "proposte":
        if a or ctx.org or d.get("tipo") == "domanda" or d.get("userId") == ctx.uid or ctx.uid in (d.get("interessati") or []):
            return d
        return None
    if c in ("messaggi", "foto"):
        return d if can_tour(ctx, d.get("tourId")) else None
    if c == "media":
        return d
    if c == "dm":
        return d if (d.get("from") in ctx.ids() or d.get("to") in ctx.ids() or a) else None
    if c in ("eventi", "cassa", "appelli", "programmati"):
        tid = d.get("tourId")
        if a or org_of_tour(ctx, tid) or tid in ctx.staff_tours or (ctx.org and d.get("orgId") == ctx.org):
            return d
        if c == "eventi" and can_tour(ctx, tid):
            return d
        return None
    if c == "codici":
        if a or (ctx.org and d.get("orgId") == ctx.org):
            return d
        if ctx.uid and ctx.uid in (d.get("promotoreUserId"), d.get("userId"), d.get("daUserId")):
            return d
        if ctx.email and d.get("perEmail") == ctx.email and d.get("attivo") is not False:
            return d
        return None
    if c == "fornitori":
        return d if a or (ctx.org and d.get("orgId") == ctx.org) else None
    if c in ("collab", "pool"):
        return d if a or (ctx.org and ctx.org in (d.get("fromOrgId"), d.get("toOrgId"))) else None
    if c == "bozze":
        return d if a or d.get("userId") == ctx.uid or org_of_tour(ctx, d.get("tourId")) else None
    return d if a else None


@app.get("/api/sync")
async def sync(request: Request, since: int = 0):
    u = await current_user(request)
    ctx = await make_ctx(u)
    now = now_ms() - 3000
    q = {"_upd": {"$gt": since}} if since else {}
    docs = {}
    for c in COLLS:
        arr = []
        async for raw in db[c].find(q):
            r = visible(ctx, c, out(raw))
            if r is not None:
                arr.append(r)
        if arr:
            docs[c] = arr
    deleted = []
    if since:
        deleted = [{"c": d["c"], "id": d["id"]} async for d in db.deleted.find({"_upd": {"$gt": since}})]
    return {"now": now, "docs": docs, "deleted": deleted, "me": ctx.uid}


# ------------------------------------------------------------------ chi scrive cosa
def deny(msg="Operazione non consentita"):
    raise HTTPException(403, msg)


PAY_FIELDS = ["pagato", "versamenti"]
U_PROTECTED = ["ruolo", "orgId", "email", "emailVerificata", "permessi", "toursPrivati", "creatoDa"]


def keep(new: dict, old: Optional[dict], fields):
    for f in fields:
        if old and f in old:
            new[f] = old[f]
        else:
            new.pop(f, None)


def keep_paid_flags(new: dict, old: Optional[dict]):
    for key in ("quote", "rate"):
        arr = new.get(key)
        if not isinstance(arr, list):
            continue
        prev = (old or {}).get(key) or []
        for i, x in enumerate(arr):
            if isinstance(x, dict):
                x["pagata"] = bool(i < len(prev) and isinstance(prev[i], dict) and prev[i].get("pagata"))
                if not x["pagata"]:
                    x.pop("pagataIl", None)


def stato_da_pagato(b: dict, prima: str) -> str:
    if b.get("stato") == "annullata":
        return "annullata"
    pag, tot = num(b.get("pagato")), num(b.get("totale"))
    if tot > 0 and pag >= tot:
        return "saldata"
    if pag > 0:
        return "acconto"
    return prima if prima not in ("saldata", "acconto") else "attesa_pagamento"


async def check_accompagnatore(t: dict):
    """L'accompagnatore di un tour deve essere un accompagnatore vero, creato dall'admin o dall'agenzia che organizza il tour."""
    aid = t.get("accompagnatoreId")
    if not aid:
        return
    a = await raw_get("users", aid)
    if not a or a.get("ruolo") != "accompagnatore":
        deny("L'accompagnatore scelto non è un accompagnatore registrato")
    if a.get("orgId") and a.get("orgId") != t.get("orgId"):
        deny("Questo accompagnatore appartiene a un'altra agenzia")


async def check_write(ctx: Ctx, c: str, old: Optional[dict], new: dict) -> dict:
    if c not in COLLS:
        deny("Archivio sconosciuto")
    if c == "tours" and (not old or new.get("accompagnatoreId") != old.get("accompagnatoreId")):
        await check_accompagnatore(new)
    if c == "users" and not ctx.admin and (new.get("ruolo") == "accompagnatore" or (old or {}).get("ruolo") == "accompagnatore"):
        # solo l'admin o l'agenzia organizzatrice (per i propri accompagnatori)
        if not ctx.org or new.get("orgId") != ctx.org or (old and old.get("orgId") != ctx.org):
            if not (old and old["id"] == ctx.uid):
                deny("Gli accompagnatori li crea solo l'admin o l'agenzia che organizza il viaggio")
    if ctx.admin:
        if c == "users" and old and old.get("ruolo") == "admin" and old["id"] == ctx.uid:
            new["ruolo"] = "admin"
        return new
    uid, org = ctx.uid, ctx.org

    if c == "tours":
        if org and new.get("orgId") == org and (not old or old.get("orgId") == org):
            keep(new, old, ["confermatoIl", "scontoApplicato"])
            return new
        if old and old["id"] in ctx.ref_tours:  # capogruppo: solo campi del gruppo
            m = dict(old)
            for f in ("codiceInviato", "autorizzazioni", "adesioniAperte"):
                if f in new:
                    m[f] = new[f]
            return m
        deny()

    if c == "bookings":
        tid = new.get("tourId") or (old or {}).get("tourId")
        is_org = bool(org) and (new.get("orgId") == org or new.get("rivenditoreOrgId") == org or org_of_tour(ctx, tid) or
                                (old and (old.get("orgId") == org or org_of_tour(ctx, old.get("tourId")))))
        if is_org:
            return new
        if not old:
            if new.get("userId") != uid:
                deny()
            if not tour_level(ctx, ctx.tours.get(tid)):
                deny("Viaggio non disponibile")
            new["pagato"] = 0
            new["versamenti"] = []
            keep_paid_flags(new, None)
            if new.get("tipo") == "prenotazione" and new.get("stato") in ("saldata", "acconto"):
                new["stato"] = "attesa_pagamento"
            t = ctx.tours.get(tid) or {}
            new["orgId"] = t.get("orgId")
            return new
        if old.get("userId") == uid:
            keep(new, old, PAY_FIELDS + ["userId", "orgId", "tourId", "scontoGruppo", "gratuiti", "totaleIntero"])
            keep_paid_flags(new, old)
            if num(new.get("totale")) < num(old.get("totale")) and new.get("stato") != "annullata":
                new["totale"] = old.get("totale")
            new["stato"] = stato_da_pagato(new, old.get("stato", "")) if new.get("tipo") == "prenotazione" else new.get("stato")
            return new
        if tid in ctx.ref_tours:  # capogruppo: gratuità
            m = dict(old)
            for f in ("gratuiti", "totaleIntero", "totale", "autorizzazione", "autorizzazioni"):
                if f in new:
                    m[f] = new[f]
            m["stato"] = stato_da_pagato(m, old.get("stato", ""))
            return m
        if old["id"] in ctx.invited_bookings:  # invitato: accetta o rifiuta il posto
            m = dict(old)
            m["camere"] = new.get("camere", old.get("camere"))
            m["partecipanti"] = new.get("partecipanti", old.get("partecipanti"))
            return m
        if tid in ctx.staff_tours:
            m = dict(old)
            for f in ("presente", "salito", "noteAccompagnatore"):
                if f in new:
                    m[f] = new[f]
            return m
        deny()

    if c == "users":
        if old and old["id"] == uid:
            keep(new, old, U_PROTECTED)
            return new
        if not old and org and new.get("ruolo") == "cliente":
            new["creatoDa"] = "organizzatore"
            new.pop("orgId", None)
            return new
        if org and new.get("orgId") == org and new.get("ruolo") in ("accompagnatore", "organizzatore") and \
                (not old or (old.get("orgId") == org and old.get("ruolo") in ("accompagnatore", "organizzatore"))):
            if old:
                keep(new, old, ["email"])
            return new
        if org and old and ctx.user_level.get(old["id"]) == "org" and old.get("ruolo") == "cliente":
            keep(new, old, U_PROTECTED)
            return new
        deny()

    if c == "orgs":
        if org and old and old["id"] == org:
            keep(new, old, ["verificato", "verificatoIl", "sospeso", "commissione"])
            return new
        deny()

    if c == "notifications":
        if not old:
            return new
        if old.get("to") in ctx.ids():
            m = dict(old)
            m["letto"] = bool(new.get("letto"))
            return m
        deny()

    if c == "reviews":
        if not old and new.get("userId") == uid:
            return new
        if old and old.get("userId") == uid:
            return new
        if old and org and old.get("orgId") == org:
            m = dict(old)
            for k, v in new.items():
                if k.startswith("risposta"):
                    m[k] = v
            return m
        deny()

    if c == "proposte":
        if not old:
            if new.get("userId") != uid:
                deny()
            return new
        if old.get("userId") == uid or org:
            return new
        m = dict(old)
        inter = set(old.get("interessati") or [])
        if uid in (new.get("interessati") or []):
            inter.add(uid)
        else:
            inter.discard(uid)
        m["interessati"] = list(inter)
        return m

    if c == "messaggi":
        if not old and new.get("userId") == uid and can_tour(ctx, new.get("tourId")):
            return new
        deny()

    if c == "dm":
        if not old and new.get("from") in ctx.ids():
            return new
        deny()

    if c == "foto":
        if not old and new.get("userId") == uid and can_tour(ctx, new.get("tourId")):
            return new
        deny()

    if c == "media":
        if org and org_of_tour(ctx, new.get("tourId")):
            return new
        deny()

    if c in ("eventi", "cassa", "appelli", "programmati"):
        tid = new.get("tourId")
        if org_of_tour(ctx, tid) or (org and new.get("orgId") == org) or tid in ctx.staff_tours:
            return new
        if c == "eventi" and not old and can_tour(ctx, tid) and new.get("da") == "cliente":
            return new
        deny()

    if c == "codici":
        if org and new.get("orgId") == org and (not old or old.get("orgId") == org):
            return new
        if not old:
            if new.get("regalo") and new.get("daUserId") == uid:
                new["attivo"] = False
                new["pagamento"] = "da_pagare"
                return new
            if new.get("promotoreUserId") == uid and new.get("orgId"):
                return new
            if new.get("userId") == uid and not new.get("orgId") and num(new.get("sconto")) <= 20:
                return new  # buono da punti fedeltà
            deny()
        if not old.get("usato") and new.get("usatoDa") == uid and new.get("usato"):
            m = dict(old)
            m.update({"usato": True, "usatoDa": uid, "usatoIl": new.get("usatoIl") or iso_now()})
            return m
        deny()

    if c == "fornitori":
        if org and new.get("orgId") == org and (not old or old.get("orgId") == org):
            return new
        deny()

    if c in ("collab", "pool"):
        if org and org in (new.get("fromOrgId"), new.get("toOrgId")) and (not old or org in (old.get("fromOrgId"), old.get("toOrgId"))):
            return new
        deny()

    if c == "bozze":
        if new.get("userId") == uid and (not old or old.get("userId") == uid):
            return new
        if old and org_of_tour(ctx, old.get("tourId")):
            return new
        deny()

    deny()  # settings, avvisi: solo admin


def can_delete(ctx: Ctx, c: str, d: dict) -> bool:
    if ctx.admin:
        return True
    uid, org = ctx.uid, ctx.org
    if c == "bookings":
        return d.get("userId") == uid and d.get("tipo") in ("desiderio", "richiesta", "attesa") or \
            bool(org and (d.get("orgId") == org or org_of_tour(ctx, d.get("tourId"))))
    if c == "notifications":
        return d.get("to") in ctx.ids()
    if c in ("reviews", "foto", "messaggi"):
        return d.get("userId") == uid or org_of_tour(ctx, d.get("tourId"))
    if c in ("bozze",):
        return d.get("userId") == uid
    if c in ("tours", "fornitori", "codici", "programmati", "media", "eventi", "cassa", "appelli"):
        return bool(org and (d.get("orgId") == org or org_of_tour(ctx, d.get("tourId"))))
    if c == "users":
        return bool(org and d.get("orgId") == org and d.get("ruolo") in ("accompagnatore", "organizzatore") and d["id"] != uid)
    if c == "proposte":
        return d.get("userId") == uid
    return False


@app.put("/api/doc/{c}/{id_}")
async def write_doc(c: str, id_: str, request: Request):
    u = await need_user(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Dati non validi")
    body = {k: v for k, v in body.items() if not k.startswith("_")}
    body["id"] = id_
    old = await raw_get(c, id_) if c in COLLS else None
    ctx = await make_ctx(u)
    if c == "users" and not old and ctx.org and body.get("email") and await email_taken(body["email"]):
        a = await db.auth.find_one({"email": body["email"].lower()})
        ex = await raw_get("users", a["_id"])
        if ex and ex.get("ruolo") == "cliente":  # cliente già registrato: si usa il suo account
            return {"doc": redact_user(ex, "org")}
        deny("Email già usata da un altro account")
    new = await check_write(ctx, c, old, body)
    saved = await put(c, new)
    await after_write(ctx, c, old, saved, request)
    ctx2 = await make_ctx(await raw_get("users", u["id"]))
    return {"doc": visible(ctx2, c, saved) or saved}


@app.delete("/api/doc/{c}/{id_}")
async def remove_doc(c: str, id_: str, request: Request):
    u = await need_user(request)
    d = await raw_get(c, id_) if c in COLLS else None
    if not d:
        return {"ok": True}
    ctx = await make_ctx(u)
    if not can_delete(ctx, c, d):
        deny()
    await delete_doc(c, id_)
    if c == "bookings":
        await tour_triggers(d.get("tourId"))
    return {"ok": True}


# ------------------------------------------------------------------ automatismi dopo il salvataggio
async def after_write(ctx: Ctx, c: str, old: Optional[dict], d: dict, request: Request):
    if c == "bookings":
        if not old:
            await booking_invites(d, request)
        await tour_triggers(d.get("tourId"))
    elif c == "users" and not old and d["id"] != ctx.uid and d.get("email") and not await email_taken(d["email"]):
        tok = await create_auth(d["id"], d["email"], None, invite_days=14)
        chi = (await raw_get("orgs", ctx.org) or {}).get("nome", "OVIGO") if ctx.org else "OVIGO"
        ruolo = {"cliente": "cliente", "accompagnatore": "accompagnatore", "organizzatore": "collaboratore"}.get(d.get("ruolo"), "utente")
        fire(send_mail(d["email"], d.get("nome", ""), f"{chi} ti ha creato un account OVIGO",
                       mail_html(f"Ciao {d.get('nome','')}, ti aspettiamo su OVIGO",
                                 f"{chi} ti ha registrato su OVIGO come {ruolo}. Scegli la tua password per entrare: il link vale 14 giorni.",
                                 f"{base_url(request)}/#reset-{tok}", "Scegli la password")))
    elif c == "orgs" and old and ctx.admin and d.get("verificato") and not old.get("verificato"):
        d["verificatoIl"] = iso_now()
        await put("orgs", d)


async def booking_invites(b: dict, request: Request):
    t = await raw_get("tours", b.get("tourId")) or {}
    u = await raw_get("users", b.get("userId")) or {}
    chi = f"{u.get('nome','')} {u.get('cognome','')}".strip() or "Un amico"
    url = base_url(request)
    for r in b.get("camere") or []:
        for o in r.get("occ") or []:
            em = (o.get("invito") or "").lower()
            if not em:
                continue
            a = await db.auth.find_one({"email": em})
            if a:
                await notify("user:" + a["_id"], f"✉️ {chi} ti ha riservato un posto per «{t.get('titolo','')}». Accetta dalla tua area (Inviti).",
                             {"tourId": t.get("id"), "bookingId": b["id"]}, url)
            else:
                fire(send_mail(em, "", f"{chi} ti ha riservato un posto su OVIGO",
                               mail_html(f"{chi} ti ha riservato un posto", f"Viaggio: «{t.get('titolo','')}». Registrati su OVIGO con questa email per accettare l'invito.", url, "Registrati")))
    for q in (b.get("quote") or [])[1:]:
        em = (q.get("invito") or "").lower()
        if em:
            link = f"{url}/#paga-{q.get('codice','')}"
            a = await db.auth.find_one({"email": em})
            if a:
                await notify("user:" + a["_id"], f"💶 {chi} ti ha riservato un posto su «{t.get('titolo','')}»: la tua quota è {eur(q.get('importo'))}. Pagala dal link: #paga-{q.get('codice','')}",
                             {"tourId": t.get("id")}, link)
            else:
                fire(send_mail(em, q.get("nome", ""), f"La tua quota per «{t.get('titolo','')}»",
                               mail_html("La tua quota di viaggio", f"{chi} ti ha riservato un posto su «{t.get('titolo','')}». La tua quota è {eur(q.get('importo'))}: trovi le coordinate per il bonifico al link qui sotto.", link, "Vedi come pagare")))


def active(bks):
    return [b for b in bks if b.get("tipo") == "prenotazione" and b.get("stato") != "annullata"]


async def tour_triggers(tid: Optional[str]):
    """Partenza confermata e sconti «più siamo, meno paghi»: calcolati dal server per tutti."""
    if not tid:
        return
    t = await raw_get("tours", tid)
    if not t:
        return
    bks = active(await all_docs("bookings", {"tourId": tid}))
    occ = sum(int(num(b.get("pax"))) for b in bks)
    changed = False
    if not t.get("confermatoIl") and occ >= max(1, int(num(t.get("minPartecipanti"), 1))):
        t["confermatoIl"] = iso_now()
        changed = True
        for uid_ in {b.get("userId") for b in bks}:
            await notify("user:" + uid_, f"🎉 Partenza confermata per «{t.get('titolo','')}»! Se avevi bloccato il posto a 0 €, ora puoi pagare l'acconto.", {"tourId": tid})
        await notify("org:" + t.get("orgId", ""), f"Il tour «{t.get('titolo','')}» ha raggiunto il minimo di {t.get('minPartecipanti')} partecipanti.", {"tourId": tid})
    sc = sorted([s for s in (t.get("scaglioni") or []) if num(s.get("da")) > 0 and num(s.get("sconto")) > 0], key=lambda s: num(s["da"]))
    lvl = 0.0
    for s in sc:
        if occ >= num(s["da"]):
            lvl = num(s["sconto"])
    if sc and lvl > num(t.get("scontoApplicato")):
        for b in bks:
            prima = num(b.get("scontoGruppo"))
            if prima >= lvl:
                continue
            diff = (lvl - prima) * int(num(b.get("pax")))
            b["scontoGruppo"] = lvl
            b["totale"] = max(0, num(b.get("totale")) - diff)
            if b.get("rate"):
                r = b["rate"]
                if r and not r[-1].get("pagata"):
                    r[-1]["importo"] = max(0, num(r[-1].get("importo")) - diff)
            if num(b.get("pagato")) >= b["totale"] and b.get("stato") not in ("bloccata",):
                b["stato"] = "saldata"
            await put("bookings", b)
            extra = " e la differenza ti viene rimborsata" if num(b.get("pagato")) > b["totale"] else ""
            await notify("user:" + b.get("userId", ""), f"🎉 Il gruppo di «{t.get('titolo','')}» è cresciuto: ora paghi {eur(lvl)} in meno a persona. Il tuo totale scende a {eur(b['totale'])}{extra}.", {"tourId": tid})
        t["scontoApplicato"] = lvl
        changed = True
        await notify("org:" + t.get("orgId", ""), f"«{t.get('titolo','')}»: raggiunto lo scaglione di {occ} iscritti, sconto di {eur(lvl)} a persona applicato a tutti.", {"tourId": tid})
    if changed:
        await put("tours", t)


# ------------------------------------------------------------------ codici e gruppi privati
@app.get("/api/codice/{code}")
async def get_code(code: str):
    d = await db.codici.find_one({"codice": code.strip().upper()})
    d = out(d)
    if not d or d.get("attivo") is False:
        raise HTTPException(404, "Codice non valido")
    return {"doc": {k: v for k, v in d.items() if k not in ("perEmail", "daUserId", "messaggio")}}


@app.post("/api/accesso-privato")
async def private_access(request: Request):
    b = await request.json()
    code = (b.get("codice") or "").strip().upper()
    t = None
    async for x in db.tours.find({"privato": True, "stato": "pubblicato"}):
        if (x.get("codice") or "").upper() == code and code:
            t = out(x)
            break
    if not t:
        raise HTTPException(404, "Codice non valido")
    u = await current_user(request)
    if u and t["id"] not in (u.get("toursPrivati") or []):
        u["toursPrivati"] = (u.get("toursPrivati") or []) + [t["id"]]
        await put("users", u)
    return {"doc": {k: v for k, v in t.items() if k not in T_PRIVATE}}


# ------------------------------------------------------------------ pagamenti con bonifico diviso
async def general_settings() -> dict:
    return await raw_get("settings", "general") or {"id": "general", "commissione": 4, "accontoPct": 30}


def causale_base(b: dict) -> str:
    return "OVIGO " + b["id"][-6:].upper()


async def bank_info(b: dict) -> dict:
    s = await general_settings()
    o = await raw_get("orgs", b.get("orgId")) or {}
    return {"ovigo": s.get("banca") or {}, "agenzia": o.get("banca") or {}, "agenziaNome": o.get("nome", ""),
            "modalita": s.get("modalitaCommissione") or "separato"}


async def new_versamento(b: dict, tipo: str, i: Optional[int], importo: float) -> dict:
    s = await general_settings()
    o = await raw_get("orgs", b.get("orgId")) or {}
    pct = num(o.get("commissione")) if o.get("commissione") not in (None, "") else num(s.get("commissione"), 4)
    importo = round(importo, 2)
    comm = round(importo * pct / 100, 2)
    separato = (s.get("modalitaCommissione") or "separato") == "separato"
    label = {"acconto": "acconto", "saldo": "saldo", "quota": "quota", "rata": "rata"}.get(tipo, "pagamento")
    if tipo in ("quota", "rata") and i is not None:
        label += f" {i + 1}"
    v = {"id": new_id("v"), "tipo": tipo, "i": i, "importo": importo, "commissionePct": pct,
         "quotaOvigo": comm if separato else 0, "quotaAgenzia": round(importo - comm, 2) if separato else importo,
         "commissioneFattura": 0 if separato else comm,
         "causale": f"{causale_base(b)} {label}", "stato": "in_attesa",
         "agenzia": "attesa", "ovigo": "attesa" if separato and comm > 0 else "non_dovuta", "createdAt": iso_now()}
    return v


def pending(b: dict) -> float:
    return sum(num(v.get("importo")) for v in (b.get("versamenti") or []) if v.get("stato") in ("in_attesa", "dichiarato"))


async def request_payment(b: dict, tipo: str, i: Optional[int]) -> dict:
    if b.get("stato") == "annullata" or b.get("tipo") != "prenotazione":
        raise HTTPException(400, "Prenotazione non pagabile")
    for v in b.get("versamenti") or []:
        if v.get("tipo") == tipo and v.get("i") == i and v.get("stato") in ("in_attesa", "dichiarato"):
            return v
    s = await general_settings()
    tot, pag = num(b.get("totale")), num(b.get("pagato"))
    if tipo == "acconto":
        imp = round(tot * num(s.get("accontoPct"), 30) / 100) - pag - pending(b)
    elif tipo == "saldo":
        imp = tot - pag - pending(b)
    elif tipo in ("quota", "rata"):
        arr = b.get("quote" if tipo == "quota" else "rate") or []
        if i is None or i < 0 or i >= len(arr) or arr[i].get("pagata"):
            raise HTTPException(400, "Già pagata o non valida")
        imp = num(arr[i].get("importo"))
    else:
        raise HTTPException(400, "Tipo di pagamento non valido")
    if imp <= 0:
        raise HTTPException(400, "Non c'è nulla da pagare")
    v = await new_versamento(b, tipo, i, imp)
    b["versamenti"] = (b.get("versamenti") or []) + [v]
    await put("bookings", b)
    t = await raw_get("tours", b.get("tourId")) or {}
    await notify("org:" + b.get("orgId", ""), f"💶 In arrivo un bonifico di {eur(v['quotaAgenzia'])} per «{t.get('titolo','')}» (causale «{v['causale']}»). Confermalo quando lo ricevi.",
                 {"tourId": t.get("id"), "bookingId": b["id"]})
    return v


async def load_booking_for(ctx: Ctx, bid: str, need: str = "owner") -> dict:
    b = await raw_get("bookings", bid)
    if not b:
        raise HTTPException(404, "Prenotazione non trovata")
    org_ok = ctx.admin or bool(ctx.org and (b.get("orgId") == ctx.org or org_of_tour(ctx, b.get("tourId"))))
    owner_ok = b.get("userId") == ctx.uid or b.get("tourId") in ctx.ref_tours
    if need == "org" and not org_ok:
        deny()
    if need == "owner" and not (owner_ok or org_ok):
        deny()
    return b


@app.post("/api/pagamenti/richiesta")
async def pay_request(request: Request):
    u = await need_user(request)
    ctx = await make_ctx(u)
    p = await request.json()
    b = await load_booking_for(ctx, p.get("bookingId", ""))
    v = await request_payment(b, p.get("tipo", ""), p.get("i"))
    return {"versamento": v, "banca": await bank_info(b), "booking": b}


async def apply_declare(b: dict, vid: str, nota: str, chi: str):
    for v in b.get("versamenti") or []:
        if v["id"] == vid and v.get("stato") == "in_attesa":
            v["stato"] = "dichiarato"
            v["dichiaratoIl"] = iso_now()
            v["nota"] = (nota or "")[:300]
            await put("bookings", b)
            t = await raw_get("tours", b.get("tourId")) or {}
            await notify("org:" + b.get("orgId", ""), f"{chi} dice di aver fatto il bonifico di {eur(v['quotaAgenzia'])} per «{t.get('titolo','')}» (causale «{v['causale']}»). Verifica e conferma.",
                         {"tourId": t.get("id"), "bookingId": b["id"]})
            if v.get("quotaOvigo"):
                await notify("admin", f"Commissione in arrivo: {eur(v['quotaOvigo'])} (causale «{v['causale']} commissione»).", {"bookingId": b["id"]})
            return v
    raise HTTPException(400, "Versamento non trovato o già segnalato")


@app.post("/api/pagamenti/dichiara")
async def pay_declare(request: Request):
    u = await need_user(request)
    ctx = await make_ctx(u)
    p = await request.json()
    b = await load_booking_for(ctx, p.get("bookingId", ""))
    v = await apply_declare(b, p.get("vid", ""), p.get("nota", ""), f"{u.get('nome','')} {u.get('cognome','')}".strip())
    return {"versamento": v, "booking": b}


def apply_effect(b: dict, v: dict):
    b["pagato"] = round(num(b.get("pagato")) + num(v["importo"]), 2)
    if v["tipo"] in ("quota", "rata") and v.get("i") is not None:
        arr = b.get("quote" if v["tipo"] == "quota" else "rate") or []
        if v["i"] < len(arr):
            arr[v["i"]]["pagata"] = True
            arr[v["i"]]["pagataIl"] = iso_now()
    b["stato"] = stato_da_pagato(b, b.get("stato", ""))


@app.post("/api/pagamenti/conferma")
async def pay_confirm(request: Request):
    """parte = 'agenzia' (l'agenzia ha ricevuto la sua parte) oppure 'ovigo' (l'admin ha ricevuto la commissione)."""
    u = await need_user(request)
    ctx = await make_ctx(u)
    p = await request.json()
    parte = p.get("parte")
    if parte == "ovigo" and not ctx.admin:
        deny()
    b = await load_booking_for(ctx, p.get("bookingId", ""), "org")
    t = await raw_get("tours", b.get("tourId")) or {}
    for v in b.get("versamenti") or []:
        if v["id"] != p.get("vid"):
            continue
        if parte == "agenzia" and v.get("agenzia") != "ricevuto":
            v["agenzia"] = "ricevuto"
            v["agenziaIl"] = iso_now()
            apply_effect(b, v)
            await notify("user:" + b.get("userId", ""), f"✅ {eur(v['quotaAgenzia'])} ricevuti per «{t.get('titolo','')}». Totale pagato: {eur(b['pagato'])} di {eur(b.get('totale'))}.",
                         {"tourId": t.get("id"), "bookingId": b["id"]})
        elif parte == "ovigo" and v.get("ovigo") == "attesa":
            v["ovigo"] = "ricevuto"
            v["ovigoIl"] = iso_now()
        else:
            raise HTTPException(400, "Già confermato")
        if v.get("agenzia") == "ricevuto" and v.get("ovigo") in ("ricevuto", "non_dovuta"):
            v["stato"] = "completato"
        await put("bookings", b)
        return {"versamento": v, "booking": b}
    raise HTTPException(404, "Versamento non trovato")


@app.post("/api/pagamenti/annulla")
async def pay_cancel(request: Request):
    u = await need_user(request)
    ctx = await make_ctx(u)
    p = await request.json()
    b = await load_booking_for(ctx, p.get("bookingId", ""))
    for v in b.get("versamenti") or []:
        if v["id"] == p.get("vid") and v.get("agenzia") != "ricevuto" and v.get("stato") in ("in_attesa", "dichiarato"):
            v["stato"] = "annullato"
            await put("bookings", b)
            return {"booking": b}
    raise HTTPException(400, "Non annullabile")


# quota divisa: chi riceve il link può non essere registrato
async def find_quota(code: str):
    async for raw in db.bookings.find({"quote.codice": code}):
        b = out(raw)
        for i, q in enumerate(b.get("quote") or []):
            if q.get("codice") == code:
                return b, i
    raise HTTPException(404, "Link di pagamento non valido")


@app.get("/api/quota/{code}")
async def quota_info(code: str):
    b, i = await find_quota(code)
    q = b["quote"][i]
    t = await raw_get("tours", b.get("tourId")) or {}
    u = await raw_get("users", b.get("userId")) or {}
    v = next((x for x in (b.get("versamenti") or []) if x.get("tipo") == "quota" and x.get("i") == i and x.get("stato") != "annullato"), None)
    return {"titolo": t.get("titolo", ""), "dataInizio": t.get("dataInizio"), "dataFine": t.get("dataFine"),
            "prenotatoDa": f"{u.get('nome','')} {(u.get('cognome') or '')[:1]}.", "nome": q.get("nome", ""), "importo": q.get("importo"),
            "pagata": bool(q.get("pagata")), "versamento": v, "banca": await bank_info(b)}


@app.post("/api/quota/{code}/richiesta")
async def quota_request(code: str):
    b, i = await find_quota(code)
    v = await request_payment(b, "quota", i)
    return {"versamento": v, "banca": await bank_info(b)}


@app.post("/api/quota/{code}/dichiara")
async def quota_declare(code: str, request: Request):
    b, i = await find_quota(code)
    p = await request.json()
    v = await apply_declare(b, p.get("vid", ""), p.get("nota", ""), b["quote"][i].get("nome", "Un partecipante"))
    return {"versamento": v}


# buoni regalo: si pagano interamente a OVIGO
@app.post("/api/regalo/conferma")
async def gift_confirm(request: Request):
    u = await need_user(request)
    if u.get("ruolo") != "admin":
        deny()
    p = await request.json()
    g = await raw_get("codici", p.get("id", ""))
    if not g or not g.get("regalo"):
        raise HTTPException(404, "Buono non trovato")
    g["attivo"] = True
    g["pagamento"] = "ricevuto"
    g["pagatoIl"] = iso_now()
    await put("codici", g)
    await notify("user:" + g.get("daUserId", ""), f"🎁 Abbiamo ricevuto il bonifico: il buono {g['codice']} da {eur(g.get('sconto'))} è attivo.")
    em = (g.get("perEmail") or "").lower()
    if em:
        a = await db.auth.find_one({"email": em})
        if a:
            await notify("user:" + a["_id"], f"🎁 Hai ricevuto un buono viaggio da {eur(g.get('sconto'))}: codice {g['codice']}. «{g.get('messaggio','')}»")
        else:
            fire(send_mail(em, g.get("perNome", ""), "Hai ricevuto un buono viaggio OVIGO",
                           mail_html(f"Un regalo per te, {g.get('perNome','')}!", f"Hai ricevuto un buono viaggio da {eur(g.get('sconto'))} da usare su qualsiasi partenza OVIGO. Codice: {g['codice']}. «{g.get('messaggio','')}»", base_url(request), "Scegli il viaggio")))
    return {"doc": g}


# ------------------------------------------------------------------ documenti dei viaggiatori (file)
FILE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic", "application/pdf")


async def file_allowed(ctx: Ctx, b: dict) -> bool:
    if not b:
        return False
    return ctx.admin or b.get("userId") == ctx.uid or b.get("id") in ctx.invited_bookings or \
        bool(ctx.org and (b.get("orgId") == ctx.org or org_of_tour(ctx, b.get("tourId")))) or b.get("tourId") in ctx.staff_tours


@app.post("/api/files")
async def upload_file(request: Request):
    u = await need_user(request)
    p = await request.json()
    ctx = await make_ctx(u)
    b = await raw_get("bookings", p.get("bookingId", ""))
    if not await file_allowed(ctx, b):
        deny()
    m = re.match(r"^data:([\w/+.-]+);base64,(.+)$", p.get("data") or "", re.S)
    if not m:
        raise HTTPException(400, "File non valido")
    mime = m.group(1).lower()
    if mime not in FILE_TYPES:
        raise HTTPException(400, "Sono accettati solo foto (JPG, PNG) e PDF")
    if len(m.group(2)) > 11_500_000:
        raise HTTPException(413, "File troppo grande (massimo 8 MB)")
    fid = new_id("f")
    await db.files.insert_one({"_id": fid, "bookingId": b["id"], "userId": u["id"], "nome": (p.get("nome") or "documento")[:120],
                               "mime": mime, "data": m.group(2), "createdAt": iso_now()})
    return {"id": fid}


@app.get("/api/files/{fid}")
async def get_file(fid: str, request: Request):
    u = await need_user(request)
    f = await db.files.find_one({"_id": fid})
    if not f:
        raise HTTPException(404, "File non trovato")
    ctx = await make_ctx(u)
    if not await file_allowed(ctx, await raw_get("bookings", f.get("bookingId", ""))):
        deny()
    nome = re.sub(r'[^\w.\- ]', '_', f.get("nome", "documento"))
    return Response(content=base64.b64decode(f["data"]), media_type=f["mime"], headers={"Content-Disposition": f'inline; filename="{nome}"', "Cache-Control": "private, no-store"})


# ------------------------------------------------------------------ avvio
@app.get("/api/health")
async def health():
    return {"status": "ok", "app": "OVIGO"}


@app.on_event("startup")
async def startup():
    if not await raw_get("settings", "general"):
        await put("settings", {"id": "general", "commissione": 4, "accontoPct": 30, "modalitaCommissione": "separato", "banca": {}})
    if ADMIN_EMAIL and ADMIN_PASSWORD:
        a = await db.auth.find_one({"email": ADMIN_EMAIL})
        if not a:
            u = {"id": new_id("u"), "nome": "Admin", "cognome": "OVIGO", "email": ADMIN_EMAIL, "ruolo": "admin",
                 "emailVerificata": True, "profilo": {}, "createdAt": iso_now()}
            await put("users", u)
            await create_auth(u["id"], ADMIN_EMAIL, ADMIN_PASSWORD)
            log.info("Creato l'amministratore %s", ADMIN_EMAIL)
    if os.environ.get("SEED_DEMO", "").lower() in ("1", "true", "si", "sì", "yes"):
        try:
            from seed_demo import seed
            await seed(db, put, create_auth, COLLS)
        except Exception as e:
            log.warning("dati dimostrativi non caricati: %s", e)
    if not MONGO_URL.startswith("mongomock"):
        try:
            for c in COLLS + ["deleted"]:
                await db[c].create_index("_upd")
            await db.auth.create_index("email", unique=True)
            await db.bookings.create_index("tourId")
        except Exception as e:
            log.warning("indici: %s", e)
    if not BREVO_API_KEY:
        log.warning("BREVO_API_KEY non impostata: le email non vengono inviate")


# il sito (frontend) servito dallo stesso server, se presente
FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", ROOT.parent / "frontend" / "public"))
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="sito")
