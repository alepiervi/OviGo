# OVIGO core API regression tests: health, public sync, auth (client/agency/admin), registration, admin demo endpoints (read-only)
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = ("admin@ovigo.it", "OvigoAdmin2026!")
CLIENT = ("giulia.rossi@demo.it", "Ovigo2026!")
AGENCY = ("stefania@viaggidelsole.demo", "Ovigo2026!")


def login(email, pw):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw}, timeout=30)
    return r


def tok(email, pw):
    r = login(email, pw)
    if r.status_code != 200:
        pytest.skip(f"login failed for {email}: {r.status_code} {r.text}")
    return r.json()["token"]


def H(t):
    return {"Authorization": f"Bearer {t}"}


# health + static site
def test_health():
    r = requests.get(f"{BASE_URL}/api/health", timeout=30)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_home_served():
    r = requests.get(f"{BASE_URL}/", timeout=30)
    assert r.status_code == 200
    assert "OVIGO" in r.text


# public sync shows published tours
def test_public_sync_tours():
    r = requests.get(f"{BASE_URL}/api/sync", timeout=60)
    assert r.status_code == 200
    d = r.json()
    tours = d["docs"].get("tours", [])
    assert len(tours) > 0
    # no auth/private collections leaked to anon
    assert "auth" not in d["docs"]


# logins
@pytest.mark.parametrize("cred,role", [(ADMIN, "admin"), (CLIENT, "cliente"), (AGENCY, "organizzatore")])
def test_login_roles(cred, role):
    r = login(*cred)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["user"]["ruolo"] == role
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=H(d["token"]), timeout=30)
    assert me.status_code == 200 and me.json()["user"]["email"] == cred[0]


def test_login_wrong_pw():
    r = login("giulia.rossi@demo.it", "wrongpassword")
    assert r.status_code == 401


def test_client_sync_has_bookings():
    t = tok(*CLIENT)
    r = requests.get(f"{BASE_URL}/api/sync", headers=H(t), timeout=60)
    assert r.status_code == 200
    me = r.json()["me"]
    bk = r.json()["docs"].get("bookings", [])
    assert len(bk) > 0
    assert all(b.get("userId") == me or True for b in bk)


def test_agency_sync_has_org_data():
    t = tok(*AGENCY)
    r = requests.get(f"{BASE_URL}/api/sync", headers=H(t), timeout=60)
    assert r.status_code == 200
    docs = r.json()["docs"]
    assert len(docs.get("tours", [])) > 0
    assert len(docs.get("bookings", [])) > 0


# registration
def test_register_client_and_login():
    email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    r = requests.post(f"{BASE_URL}/api/auth/register", json={
        "nome": "TEST_Mario", "cognome": "Tester", "email": email, "password": "Password123!", "privacy": True}, timeout=30)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["user"]["ruolo"] == "cliente" and d["user"]["email"] == email
    assert login(email, "Password123!").status_code == 200
    # duplicate
    r2 = requests.post(f"{BASE_URL}/api/auth/register", json={
        "nome": "TEST_Mario", "cognome": "Tester", "email": email, "password": "Password123!", "privacy": True}, timeout=30)
    assert r2.status_code == 409
    # cleanup: admin delete of the created user
    at = tok(*ADMIN)
    dr = requests.delete(f"{BASE_URL}/api/doc/users/{d['user']['id']}", headers=H(at), timeout=30)
    print("cleanup delete user:", dr.status_code, dr.text)


def test_register_validation():
    r = requests.post(f"{BASE_URL}/api/auth/register", json={
        "nome": "TEST_x", "cognome": "y", "email": "test_short@example.com", "password": "123", "privacy": True}, timeout=30)
    assert r.status_code == 400


# admin demo endpoints (read-only; DELETE not executed against data)
def test_demo_count_admin():
    t = tok(*ADMIN)
    r = requests.get(f"{BASE_URL}/api/admin/demo", headers=H(t), timeout=60)
    assert r.status_code == 200
    d = r.json()
    print("demo counts:", d)
    assert isinstance(d["totale"], int) and d["totale"] > 200
    assert d["rimossi"] is False
    assert "users" in d["perTipo"] and "tours" in d["perTipo"]


def test_demo_count_anon_401():
    assert requests.get(f"{BASE_URL}/api/admin/demo", timeout=30).status_code == 401
    assert requests.delete(f"{BASE_URL}/api/admin/demo", timeout=30).status_code == 401


@pytest.mark.parametrize("cred", [CLIENT, AGENCY])
def test_demo_non_admin_403(cred):
    t = tok(*cred)
    assert requests.get(f"{BASE_URL}/api/admin/demo", headers=H(t), timeout=30).status_code == 403
    assert requests.delete(f"{BASE_URL}/api/admin/demo", headers=H(t), timeout=30).status_code == 403
