"""Controlled account setup for tests after public registration became neutral."""

from app.db import Store


class FixtureResponse:
    status_code = 201

    def __init__(self, payload: dict):
        self._payload = payload
        self.text = ""

    def json(self) -> dict:
        return self._payload


def register_fixture(client, username: str, password: str, *, role: str = "student",
                     display_name: str = "") -> dict:
    response = client.post("/api/auth/register", json={
        "username": username,
        "password": password,
        "display_name": display_name,
        "role": role,
    })
    assert response.status_code == 201, response.text
    user = response.json()
    if role != "user":
        store = Store()
        saved = store.get_user(user["id"])
        saved.update({
            "role": role,
            "status": "pending" if role == "teacher" else "active",
            "role_application_status": "approved",
        })
        store.save_user(saved)
        user = store.get_user(user["id"])
    return user


def register_fixture_response(client, username: str, password: str, *, role: str = "student",
                              display_name: str = "") -> FixtureResponse:
    return FixtureResponse(register_fixture(
        client, username, password, role=role, display_name=display_name,
    ))
