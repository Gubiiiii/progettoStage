import pytest

from app import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "test.sqlite"),
            "ADMIN_PASSWORD": "secret",
            "WTF_CSRF_ENABLED": False,
        }
    )
    return app.test_client()


def register(client, email="mario.rossi@example.com", event_id=1):
    return client.post(
        f"/events/{event_id}/register",
        data={
            "first_name": "Mario",
            "last_name": "Rossi",
            "email": email,
            "phone": "3331234567",
            "organization": "Scuola",
        },
        follow_redirects=False,
    )


def register_accessible(client, email="accessibile@example.com"):
    return client.post(
        "/events/1/register",
        data={
            "first_name": "Anna",
            "last_name": "Verdi",
            "email": email,
            "accessible_required": "on",
        },
        follow_redirects=False,
    )


def login(client):
    return client.post("/admin", data={"password": "secret"})


def update_event(client, capacity=100, accessible_capacity=0, is_open=True):
    data = {
        "name": "Convegno",
        "event_date": "",
        "venue": "Teatro",
        "capacity": str(capacity),
        "accessible_capacity": str(accessible_capacity),
    }
    if is_open:
        data["is_open"] = "on"
    return client.post("/staff/events/1/edit", data=data)


def create_event(client, name="Secondo evento", capacity=100, accessible_capacity=0):
    return client.post(
        "/staff/events/new",
        data={
            "name": name,
            "event_date": "",
            "venue": "Sala B",
            "capacity": str(capacity),
            "accessible_capacity": str(accessible_capacity),
            "is_open": "on",
        },
        follow_redirects=True,
    )


def test_registration_creates_ticket(client):
    response = register(client)

    assert response.status_code == 302
    assert "/success/" in response.headers["Location"]


def test_duplicate_email_is_rejected(client):
    register(client)
    response = register(client)

    assert response.status_code == 409


def test_checkin_accepts_qr_url_once(client):
    response = register(client)
    token = response.headers["Location"].rsplit("/", 1)[-1]
    login(client)

    first = client.post("/api/checkin", json={"code": f"http://localhost/checkin/{token}"})
    second = client.post("/api/checkin", json={"code": token})

    assert first.status_code == 200
    assert first.json["status"] == "checked"
    assert second.json["status"] == "already_checked"


def test_checkin_accepts_manual_code(client):
    response = register(client)
    token = response.headers["Location"].rsplit("/", 1)[-1]
    ticket = client.get(f"/success/{token}")
    login(client)

    page = ticket.get_data(as_text=True)
    code = page.split("Codice manuale", 1)[1].split("<strong>", 1)[1].split("</strong>", 1)[0]
    response = client.post("/api/checkin", json={"code": code})

    assert response.status_code == 200
    assert response.json["status"] == "checked"


def test_ticket_download_is_png(client):
    response = register(client)
    token = response.headers["Location"].rsplit("/", 1)[-1]

    response = client.get(f"/ticket/{token}")

    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.headers["Content-Disposition"].startswith("attachment;")


def test_admin_can_delete_participant(client):
    register(client)
    login(client)
    page = client.get("/participants").get_data(as_text=True)
    action = page.split('action="', 1)[1].split('"', 1)[0]

    response = client.post(action, follow_redirects=True)

    assert response.status_code == 200
    assert "Nessun iscritto trovato" in response.get_data(as_text=True)


def test_participants_can_be_filtered_by_event(client):
    login(client)
    create_event(client)
    register(client, email="primo@example.com", event_id=1)
    register(client, email="secondo@example.com", event_id=2)

    page = client.get("/participants?event_id=2").get_data(as_text=True)

    assert "secondo@example.com" in page
    assert "primo@example.com" not in page


def test_registration_stops_when_event_is_full(client):
    login(client)
    update_event(client, capacity=1)

    first = register(client, email="uno@example.com")
    second = register(client, email="due@example.com")

    assert first.status_code == 302
    assert second.status_code == 409


def test_accessible_registration_uses_accessible_capacity(client):
    login(client)
    update_event(client, capacity=3, accessible_capacity=1)

    first = register_accessible(client, email="accessibile1@example.com")
    second = register_accessible(client, email="accessibile2@example.com")
    normal = register(client, email="normale@example.com")

    assert first.status_code == 302
    assert second.status_code == 409
    assert normal.status_code == 302


def test_invalid_code_is_rejected(client):
    login(client)
    response = client.post("/api/checkin", json={"code": "not-valid"})

    assert response.status_code == 404
    assert response.json["status"] == "invalid"
