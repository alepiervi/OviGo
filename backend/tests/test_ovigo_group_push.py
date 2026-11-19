# OVIGO "Notifica a tutto il gruppo": GET/POST /api/tour/{tid}/gruppo (permissions, validation, fan-out, storico)
import os
import uuid
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
PW = "Ovigo2026!"
CLIENT = ("giulia.rossi@demo.it", PW)
AGENCY = ("stefania@viaggidelsole.demo", PW)
OTHER_AGENCY = ("gennaro@toursud.demo", PW)
ADMIN = ("admin@ovigo.it", "OvigoAdmin2026!")
TOUR_SMALL = "t_cinqueterre"   # o_sole, 4 iscritti
TOUR_GIULIA = "t_righi"        # o_sole, includes giulia
MARK = "TEST_GP_" + uuid.uuid4().hex[:6]


def tok(email, pw):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"login failed {email}")
    return r.json()["token"]


def H(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(scope="module")
def ag():
    return tok(*AGENCY)


# permissions
def test_get_anon_401():
    assert requests.get(f"{BASE_URL}/api/tour/{TOUR_SMALL}/gruppo", timeout=30).status_code == 401


def test_post_anon_401():
    assert requests.post(f"{BASE_URL}/api/tour/{TOUR_SMALL}/gruppo", json={"testo": "x"}, timeout=30).status_code == 401


def test_other_agency_403():
    t = tok(*OTHER_AGENCY)
    assert requests.get(f"{BASE_URL}/api/tour/t_trentino/gruppo", headers=H(t), timeout=30).status_code == 403
    assert requests.post(f"{BASE_URL}/api/tour/t_trentino/gruppo", json={"testo": MARK}, headers=H(t), timeout=30).status_code == 403


def test_client_403():
    t = tok(*CLIENT)
    assert requests.get(f"{BASE_URL}/api/tour/{TOUR_GIULIA}/gruppo", headers=H(t), timeout=30).status_code == 403
    assert requests.post(f"{BASE_URL}/api/tour/{TOUR_GIULIA}/gruppo", json={"testo": MARK}, headers=H(t), timeout=30).status_code == 403


def test_unknown_tour_403_or_404(ag):
    r = requests.get(f"{BASE_URL}/api/tour/t_nonexistent_xyz/gruppo", headers=H(ag), timeout=30)
    assert r.status_code in (403, 404)


# GET info
def test_get_info(ag):
    r = requests.get(f"{BASE_URL}/api/tour/{TOUR_SMALL}/gruppo", headers=H(ag), timeout=30)
    assert r.status_code == 200
    d = r.json()
    assert d["iscritti"] == 4 and isinstance(d["conApp"], int) and isinstance(d["storico"], list)


# validation
@pytest.mark.parametrize("body", [{"testo": ""}, {"testo": "   "}, {}, {"testo": "x" * 501}])
def test_post_invalid_400(ag, body):
    r = requests.post(f"{BASE_URL}/api/tour/{TOUR_SMALL}/gruppo", json=body, headers=H(ag), timeout=30)
    assert r.status_code == 400, r.text


def test_post_nonjson_400(ag):
    r = requests.post(f"{BASE_URL}/api/tour/{TOUR_SMALL}/gruppo", data="notjson",
                      headers={**H(ag), "Content-Type": "application/json"}, timeout=30)
    assert r.status_code == 400
    r = requests.post(f"{BASE_URL}/api/tour/{TOUR_SMALL}/gruppo", json=["a"], headers=H(ag), timeout=30)
    assert r.status_code == 400


def test_post_empty_group_400(ag):
    r = requests.post(f"{BASE_URL}/api/tour/t_toscana/gruppo", json={"testo": MARK}, headers=H(ag), timeout=30)
    assert r.status_code == 400


def test_post_500_chars_ok_boundary(ag):
    txt = (MARK + " ") + "y" * (500 - len(MARK) - 1)
    r = requests.post(f"{BASE_URL}/api/tour/{TOUR_SMALL}/gruppo", json={"testo": txt}, headers=H(ag), timeout=30)
    assert r.status_code == 200 and r.json()["destinatari"] == 4


# fan-out + client visibility + storico
def test_send_agency_giulia_sees(ag):
    txt = f"{MARK} agency send"
    r = requests.post(f"{BASE_URL}/api/tour/{TOUR_GIULIA}/gruppo", json={"testo": txt}, headers=H(ag), timeout=60)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True and d["destinatari"] == 6 and isinstance(d["conApp"], int)
    time.sleep(1)
    info = requests.get(f"{BASE_URL}/api/tour/{TOUR_GIULIA}/gruppo", headers=H(ag), timeout=30).json()
    assert info["storico"][0]["testo"] == txt and info["storico"][0]["destinatari"] == 6
    assert info["storico"][0]["da"]
    ct = tok(*CLIENT)
    s = requests.get(f"{BASE_URL}/api/sync", headers=H(ct), timeout=60).json()
    mine = [n for n in s["docs"].get("notifications", []) if txt in n.get("testo", "")]
    assert len(mine) == 1
    assert mine[0]["testo"].startswith("📲 Viaggi del Sole per «"), mine[0]["testo"]
    assert mine[0]["tourId"] == TOUR_GIULIA


def test_send_admin(ag):
    at = tok(*ADMIN)
    txt = f"{MARK} admin send"
    r = requests.post(f"{BASE_URL}/api/tour/{TOUR_SMALL}/gruppo", json={"testo": txt}, headers=H(at), timeout=60)
    assert r.status_code == 200 and r.json()["destinatari"] == 4
    g = requests.get(f"{BASE_URL}/api/tour/{TOUR_SMALL}/gruppo", headers=H(at), timeout=30)
    assert g.status_code == 200 and g.json()["storico"][0]["testo"] == txt


def test_other_client_not_receiving(ag):
    # giulia is not on t_cinqueterre -> must not see those messages
    ct = tok(*CLIENT)
    s = requests.get(f"{BASE_URL}/api/sync", headers=H(ct), timeout=60).json()
    assert not any(f"{MARK} admin send" in n.get("testo", "") for n in s["docs"].get("notifications", []))


def test_conapp_counts_subscribed_client(ag):
    from test_ovigo_push import fake_sub
    ct = tok(*CLIENT)
    before = requests.get(f"{BASE_URL}/api/tour/{TOUR_GIULIA}/gruppo", headers=H(ag), timeout=30).json()["conApp"]
    sub = fake_sub()
    requests.post(f"{BASE_URL}/api/push/iscrivi", json=sub, headers=H(ct), timeout=30)
    try:
        after = requests.get(f"{BASE_URL}/api/tour/{TOUR_GIULIA}/gruppo", headers=H(ag), timeout=30).json()["conApp"]
        assert after == before + 1 or (before >= 1 and after == before)
    finally:
        requests.post(f"{BASE_URL}/api/push/disiscrivi", json={"endpoint": sub["endpoint"]}, headers=H(ct), timeout=30)
