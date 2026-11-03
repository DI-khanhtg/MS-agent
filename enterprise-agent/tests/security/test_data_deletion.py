from fastapi.testclient import TestClient

from tests.integration.test_data_lifecycle import headers, make_app, populate


def test_delete_requires_auth_and_rejects_identity_overrides(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        populate(client, "tenant:bob")
        assert client.delete("/api/me/data").status_code == 401
        assert client.delete("/api/me/data?user_id=bob", headers=headers()).status_code == 422
        assert (
            client.request(
                "DELETE", "/api/me/data", headers=headers(), json={"user_id": "bob"}
            ).status_code
            == 422
        )
        assert (
            client.delete("/api/me/data", headers={**headers(), "X-User-ID": "bob"}).status_code
            == 204
        )
        assert len(client.get("/api/sessions", headers=headers("tenant:bob")).json()) == 1
