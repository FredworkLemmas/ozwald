import pytest
from fastapi.testclient import TestClient

from api.provisioner import app
from orchestration.models import FootprintAction


@pytest.fixture
def system_key(monkeypatch):
    key = "test-system-key"
    monkeypatch.setenv("OZWALD_SYSTEM_KEY", key)
    return key


@pytest.fixture
def client(system_key):
    return TestClient(app)


@pytest.fixture
def auth_header(system_key):
    return {"Authorization": f"Bearer {system_key}"}


class TestFootprintEndpoints:
    def test_get_footprint_requests(self, client, auth_header, mocker):
        mock_cache = mocker.Mock()
        mock_cache.get_requests.return_value = [
            FootprintAction(request_id="1", footprint_all_services=True),
        ]
        mocker.patch(
            "api.provisioner.FootprintRequestCache",
            return_value=mock_cache,
        )

        mock_provisioner = mocker.Mock()
        mock_provisioner.get_cache.return_value = {}
        mocker.patch(
            "api.provisioner.SystemProvisioner.singleton",
            return_value=mock_provisioner,
        )

        resp = client.get("/srv/services/footprint", headers=auth_header)

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["request_id"] == "1"

    def test_post_footprint_request(self, client, auth_header, mocker):
        mock_cache = mocker.Mock()
        mocker.patch(
            "api.provisioner.FootprintRequestCache",
            return_value=mock_cache,
        )

        mock_provisioner = mocker.Mock()
        mock_provisioner.get_cache.return_value = {}
        mocker.patch(
            "api.provisioner.SystemProvisioner.singleton",
            return_value=mock_provisioner,
        )

        # Mock ActiveServicesCache to return empty list
        mock_active_cache = mocker.Mock()
        mock_active_cache.get_services.return_value = []
        mocker.patch(
            "api.provisioner.ActiveServicesCache",
            return_value=mock_active_cache,
        )

        action = {"request_id": "2", "footprint_all_services": False}
        resp = client.post(
            "/srv/services/footprint",
            json=action,
            headers=auth_header,
        )

        assert resp.status_code == 202
        mock_cache.add_footprint_request.assert_called_once()
        args = mock_cache.add_footprint_request.call_args[0][0]
        assert isinstance(args, FootprintAction)
        assert args.request_id == "2"
