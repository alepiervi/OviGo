# OVIGO PWA + web push API tests: static PWA assets, /api/push/* endpoints, notification creation with failing push
import os
import base64
import uuid
import time
import json
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
CLIENT = ("giulia.rossi@demo.it", "Ovigo2026!")
ADMIN = ("admin@ovigo.it", "OvigoAdmin2026!")
AGENCY = ("stefania@viaggidelsole.demo", "Ovigo2026!")


def b64u(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def fake_sub():
    k = ec.generate_private_key(ec.SECP256R1())
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"endpoint": f"https://fcm.googleapis.com/fcm/send/TEST_fake_{uuid.uuid4().hex}",
            "keys": {"p256dh": b64u(pub), "auth": b64u(os.urandom(16))}}


def tok(email, pw):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"login failed {email}")
    return r.json()["token"], r.json()["user"]


def H(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(scope="module")
def client():
    return tok(*CLIENT)


# static PWA assets
def test_manifest():
    r = requests.get(f"{BASE_URL}/manifest.webmanifest", timeout=30)
    assert r.status_code == 200
    m = r.json()
    assert m["display"] == "standalone" and m["start_url"] and m["name"]
    sizes = {(i["sizes"], i.get("purpose", "any")) for i in m["icons"]}
    assert ("192x192", "any") in sizes and ("512x512", "any") in sizes and ("512x512", "maskable") in sizes
    for i in m["icons"]:
        ir = requests.get(BASE_URL + i["src"], timeout=30)
        assert ir.status_code == 200 and ir.headers["content-type"].startswith("image/png")


@pytest.mark.parametrize("p", ["/sw.js", "/app-mobile.js", "/app-mobile.css", "/apple-touch-icon.png", "/badge-96.png"])
def test_static(p):
    r = requests.get(BASE_URL + p, timeout=30)
    assert r.status_code == 200 and len(r.content) > 50


def test_index_head():
    t = requests.get(BASE_URL + "/", timeout=30).text
    assert 'rel="manifest"' in t and "apple-mobile-web-app-capable" in t or "mobile-web-app-capable" in t
    assert "apple-touch-icon" in t and "app-mobile.js" in t and "viewport" in t


# push API
def test_key():
    r = requests.get(f"{BASE_URL}/api/push/chiave", timeout=30)
    assert r.status_code == 200 and len(r.json()["publicKey"]) > 60


def test_subscribe_anon_401():
    r = requests.post(f"{BASE_URL}/api/push/iscrivi", json=fake_sub(), timeout=30)
    assert r.status_code == 401


@pytest.mark.parametrize("body", [{}, {"endpoint": "http://x.com", "keys": {}}, {"endpoint": "https://x.com"}, {"endpoint": "https://x.com", "keys": "a"}])
def test_subscribe_invalid_400(client, body):
    r = requests.post(f"{BASE_URL}/api/push/iscrivi", json=body, headers=H(client[0]), timeout=30)
    assert r.status_code == 400


def test_subscribe_non_dict_body(client):
    r = requests.post(f"{BASE_URL}/api/push/iscrivi", json=["a"], headers=H(client[0]), timeout=30)
    assert r.status_code == 400


def test_subscribe_malformed_json(client):
    r = requests.post(f"{BASE_URL}/api/push/iscrivi", data="notjson", headers={**H(client[0]), "Content-Type": "application/json"}, timeout=30)
    assert r.status_code in (400, 422), r.status_code


def test_unsubscribe_non_dict_body(client):
    r = requests.post(f"{BASE_URL}/api/push/disiscrivi", json=["a"], headers=H(client[0]), timeout=30)
    assert r.status_code in (200, 400), r.status_code


def test_full_flow(client):
    t, u = client
    h = H(t)
    before = requests.get(f"{BASE_URL}/api/push/stato", headers=h, timeout=30).json()
    assert "dispositivi" in before and before["attivo"] is True
    sub = fake_sub()
    try:
        r = requests.post(f"{BASE_URL}/api/push/iscrivi", json=sub, headers=h, timeout=30)
        assert r.status_code == 200 and r.json()["ok"] is True
        st = requests.get(f"{BASE_URL}/api/push/stato", headers=h, timeout=30).json()
        assert st["dispositivi"] == before["dispositivi"] + 1
        # re-subscribe same endpoint idempotent
        requests.post(f"{BASE_URL}/api/push/iscrivi", json=sub, headers=h, timeout=30)
        assert requests.get(f"{BASE_URL}/api/push/stato", headers=h, timeout=30).json()["dispositivi"] == before["dispositivi"] + 1
        p = requests.post(f"{BASE_URL}/api/push/prova", headers=h, timeout=60)
        assert p.status_code == 200 and isinstance(p.json()["inviate"], int)
        print("prova:", p.json())
    finally:
        r = requests.post(f"{BASE_URL}/api/push/disiscrivi", json={"endpoint": sub["endpoint"]}, headers=h, timeout=30)
        assert r.status_code == 200
    st = requests.get(f"{BASE_URL}/api/push/stato", headers=h, timeout=30).json()
    assert st["dispositivi"] <= before["dispositivi"]


def test_other_user_cannot_unsubscribe(client):
    t, u = client
    sub = fake_sub()
    requests.post(f"{BASE_URL}/api/push/iscrivi", json=sub, headers=H(t), timeout=30)
    try:
        at, _ = tok(*AGENCY)
        requests.post(f"{BASE_URL}/api/push/disiscrivi", json={"endpoint": sub["endpoint"]}, headers=H(at), timeout=30)
        assert requests.get(f"{BASE_URL}/api/push/stato", headers=H(t), timeout=30).json()["dispositivi"] >= 1
    finally:
        requests.post(f"{BASE_URL}/api/push/disiscrivi", json={"endpoint": sub["endpoint"]}, headers=H(t), timeout=30)


def test_notification_with_failing_push():
    # admin creates notification to client who has fake subscription -> must succeed
    ct, cu = tok(*CLIENT)
    at, au = tok(*ADMIN)
    sub = fake_sub()
    requests.post(f"{BASE_URL}/api/push/iscrivi", json=sub, headers=H(ct), timeout=30)
    nid = "TEST_n_" + uuid.uuid4().hex[:10]
    try:
        r = requests.put(f"{BASE_URL}/api/doc/notifications/{nid}",
                         json={"to": f"user:{cu['id']}", "testo": "TEST_ push failing", "letto": False, "createdAt": "2026-01-01T00:00:00Z"},
                         headers=H(at), timeout=30)
        assert r.status_code == 200, r.text
        assert r.json()["doc"]["id"] == nid
        time.sleep(3)
        h = requests.get(f"{BASE_URL}/api/health", timeout=30)
        assert h.status_code == 200
        s = requests.get(f"{BASE_URL}/api/sync", headers=H(ct), timeout=60).json()
        assert any(n["id"] == nid for n in s["docs"].get("notifications", []))
    finally:
        d = requests.delete(f"{BASE_URL}/api/doc/notifications/{nid}", headers=H(at), timeout=30)
        print("delete notif", d.status_code, d.text[:100])
        requests.post(f"{BASE_URL}/api/push/disiscrivi", json={"endpoint": sub["endpoint"]}, headers=H(ct), timeout=30)
