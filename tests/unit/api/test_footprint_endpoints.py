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


class TestFootprintLogs:
    def test_get_footprint_logs_success(self, client, auth_header, mocker):
        mock_svc = mocker.Mock()
        mock_svc.service_name = "test-service"
        mock_svc.profiles = {"prod": {}}
        mock_svc.varieties = {"gpu": {}}

        mock_provisioner = mocker.Mock()
        mock_provisioner.get_configured_services.return_value = [mock_svc]
        mocker.patch(
            "api.provisioner.SystemProvisioner.singleton",
            return_value=mock_provisioner,
        )

        # Mock subprocess.run
        mock_run = mocker.patch("api.provisioner.subprocess.run")
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "line1\nline2\n"
        mock_run.return_value.stderr = ""

        resp = client.get(
            "/srv/services/footprint-logs/container/test-service/",
            params={"profile": "prod", "variety": "gpu"},
            headers=auth_header,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["lines"] == ["line1", "line2"]
        assert data["profile"] == "prod"
        assert data["variety"] == "gpu"

        # Check command
        cmd = mock_run.call_args[0][0]
        assert "service-footprinter--test-service--prod--gpu" in cmd

    def test_get_footprint_logs_validation_error(
        self,
        client,
        auth_header,
        mocker,
    ):
        mock_svc = mocker.Mock()
        mock_svc.service_name = "test-service"
        mock_svc.profiles = {"prod": {}}
        mock_svc.varieties = {}

        mock_provisioner = mocker.Mock()
        mock_provisioner.get_configured_services.return_value = [mock_svc]
        mocker.patch(
            "api.provisioner.SystemProvisioner.singleton",
            return_value=mock_provisioner,
        )

        # Profile required but missing
        resp = client.get(
            "/srv/services/footprint-logs/container/test-service/",
            headers=auth_header,
        )
        assert resp.status_code == 400
        assert "Profile is required" in resp.json()["detail"]

    def test_get_footprint_logs_top_last(self, client, auth_header, mocker):
        mock_svc = mocker.Mock()
        mock_svc.service_name = "test-service"
        mock_svc.profiles = {}
        mock_svc.varieties = {}

        mock_provisioner = mocker.Mock()
        mock_provisioner.get_configured_services.return_value = [mock_svc]
        mocker.patch(
            "api.provisioner.SystemProvisioner.singleton",
            return_value=mock_provisioner,
        )

        mock_run = mocker.patch("api.provisioner.subprocess.run")
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "\n".join([
            f"line{i}" for i in range(10)
        ])
        mock_run.return_value.stderr = ""

        resp = client.get(
            "/srv/services/footprint-logs/container/test-service/",
            params={"top": 3},
            headers=auth_header,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["lines"] == ["line0", "line1", "line2"]
        assert data["is_top_n"] is True

        resp = client.get(
            "/srv/services/footprint-logs/container/test-service/",
            params={"last": 2},
            headers=auth_header,
        )
        assert resp.status_code == 200
        # When last is used, we pass --tail to docker logs
        cmd = mock_run.call_args[0][0]
        assert "--tail" in cmd
        assert "2" in cmd

    def test_get_footprint_runner_logs_success(
        self, client, auth_header, mocker
    ):
        mock_svc = mocker.Mock()
        mock_svc.service_name = "test-service"
        mock_svc.profiles = {}
        mock_svc.varieties = {}

        mock_provisioner = mocker.Mock()
        mock_provisioner.get_configured_services.return_value = [mock_svc]
        mock_provisioner.get_cache.return_value = {}
        mocker.patch(
            "api.provisioner.SystemProvisioner.singleton",
            return_value=mock_provisioner,
        )

        mock_logs_cache = mocker.Mock()
        mock_logs_cache.get_log_lines.return_value = ["runner1", "runner2"]
        mocker.patch(
            "api.provisioner.RunnerLogsCache",
            return_value=mock_logs_cache,
        )

        resp = client.get(
            "/srv/services/footprint-logs/runner/test-service/",
            headers=auth_header,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["lines"] == ["runner1", "runner2"]
        assert data["service_name"] == "test-service"
