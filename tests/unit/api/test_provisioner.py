"""Test suite for the Provisioner FastAPI application in
`src/api/provisioner.py`.

These tests verify:
- health endpoint requires no authentication,
- authentication behavior for protected endpoints (missing env key,
  invalid token),
- that each endpoint delegates to the appropriate provisioner/host methods, and
- that responses serialize Pydantic models as expected.

Pytest fixtures are used for environment setup and HTTP client creation. The
`mocker` fixture (from pytest-mock) is used for monkeypatching instead of the
`patch` decorator, per requirements.
"""

from typing import List

import pytest
from fastapi.testclient import TestClient

from api.provisioner import app
from hosts.resources import GPUResource, HostResources
from orchestration.models import (
    Resource,
    ResourceType,
    Service,
    ServiceDefinition,
    ServiceInformation,
    ServiceType,
)

# -------------------------
# Fixtures
# -------------------------


@pytest.fixture
def system_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Provide and set a system key in the environment for authenticated calls.

    Returns the configured key so tests can construct an Authorization header
    without relying on magic literals.
    """
    key = "test-system-key"
    monkeypatch.setenv("OZWALD_SYSTEM_KEY", key)
    return key


@pytest.fixture
def client(system_key: str) -> TestClient:  # noqa: ARG001 (system_key ensures env is set)
    """A TestClient bound to the FastAPI application.

    Depends on `system_key` so that protected endpoints can be exercised easily.
    """
    return TestClient(app)


@pytest.fixture
def auth_header(system_key: str) -> dict[str, str]:
    """Authorization header factory using the configured `system_key`.

    Avoids hardcoding bearer tokens; assertions can compare directly against
    this fixture's value if needed.
    """
    return {"Authorization": f"Bearer {system_key}"}


# -------------------------
# Tests grouped by endpoint
# -------------------------


class TestHealth:
    def test_health_check_requires_no_auth(self, client: TestClient) -> None:
        """Health endpoint should be accessible without any authentication."""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy"}


class TestConfiguredServices:
    """Tests for `/srv/services/configured/` endpoint, including auth
    behavior.
    """

    def test_auth_fails_when_env_key_missing(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Return 401 when `OZWALD_SYSTEM_KEY` is not configured."""
        monkeypatch.delenv("OZWALD_SYSTEM_KEY", raising=False)
        resp = client.get("/srv/services/configured/")
        assert resp.status_code == 401
        body = resp.json()
        assert body["detail"] == "Invalid authentication credentials"

    def test_auth_rejects_invalid_token(
        self,
        client: TestClient,
        system_key: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Return 401 when the bearer token is invalid and log a warning."""
        headers = {"Authorization": "Bearer wrong-token"}
        with caplog.at_level("WARNING"):
            resp = client.get("/srv/services/configured/", headers=headers)

        assert resp.status_code == 401
        body = resp.json()
        assert body["detail"] == "Invalid authentication credentials"
        assert (
            "Unauthorized access attempt: invalid or missing bearer token"
            in caplog.text
        )

    def test_get_configured_services_returns_list(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        mocker,
    ) -> None:
        """Return the list from provisioner.get_configured_services()."""
        # Prepare mock return value
        defs: List[ServiceDefinition] = [
            ServiceDefinition(
                service_name="svc-a",
                type=ServiceType.API,
                description="A",
            ),
            ServiceDefinition(
                service_name="svc-b",
                type=ServiceType.SQLITE,
                description=None,
            ),
        ]

        prov = mocker.Mock()
        prov.get_configured_services.return_value = defs
        mocker.patch(
            "src.api.provisioner.SystemProvisioner.singleton",
            return_value=prov,
        )

        resp = client.get("/srv/services/configured/", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json() == [d.model_dump() for d in defs]
        prov.get_configured_services.assert_called_once_with()


class TestActiveServices:
    def test_get_active_services_returns_list(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        mocker,
    ) -> None:
        """`/srv/services/active/` returns the list from
        `provisioner.get_active_services()`.
        """
        active: List[Service] = [
            Service(
                name="inst-1",
                service_name="svc-a",
                host="localhost",
                parameters={},
                profile=None,
            ),
        ]

        prov = mocker.Mock()
        prov.get_active_services.return_value = active
        mocker.patch(
            "src.api.provisioner.SystemProvisioner.singleton",
            return_value=prov,
        )

        resp = client.get("/srv/services/active/", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json() == [s.model_dump() for s in active]
        prov.get_active_services.assert_called_once_with()


class TestUpdateServices:
    def test_update_services_accepts_and_delegates(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        mocker,
    ) -> None:
        """`/srv/services/update/` responds 202 and calls
        `provisioner.update_services(...)` with parsed models.
        """
        # Payload to send (as JSON)
        payload = [
            {
                "name": "inst-1",
                "service": "svc-a",
                "profile": "default",
                "status": None,
                "info": None,
            },
            {
                "name": "inst-2",
                "service": "svc-b",
                "profile": "gpu",
                "status": None,
                "info": {"note": "test"},
            },
        ]

        prov = mocker.Mock()
        mocker.patch(
            "src.api.provisioner.SystemProvisioner.singleton",
            return_value=prov,
        )

        resp = client.post(
            "/srv/services/update/",
            json=payload,
            headers=auth_header,
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "accepted"

        # Verify the call received a list of ServiceInformation models
        # matching the payload
        prov.update_services.assert_called_once()
        (arg_list,), _ = prov.update_services.call_args
        assert isinstance(arg_list, list)

        expected_models = [ServiceInformation(**item) for item in payload]
        # Compare by dict representation to avoid identity or BaseModel eq
        # semantics
        assert [m.model_dump() for m in arg_list] == [
            m.model_dump() for m in expected_models
        ]


class TestUpdateServicesEmptyList:
    def test_empty_list_delegates_and_returns_202(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        mocker,
    ) -> None:
        """Posting an empty list should be accepted (202) and delegated to
        the provisioner with an empty list of ServiceInformation.
        """
        prov = mocker.Mock()
        prov.update_services.return_value = True
        mocker.patch(
            "src.api.provisioner.SystemProvisioner.singleton",
            return_value=prov,
        )

        resp = client.post(
            "/srv/services/active/update/",
            json=[],
            headers=auth_header,
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "accepted"

        prov.update_services.assert_called_once()
        (arg_list,), _ = prov.update_services.call_args
        assert isinstance(arg_list, list)
        assert arg_list == []

    def test_empty_list_persist_failure_yields_503(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        mocker,
    ) -> None:
        """If the provisioner returns False (persistence failure), the API
        should return 503.
        """
        prov = mocker.Mock()
        prov.update_services.return_value = False
        mocker.patch(
            "src.api.provisioner.SystemProvisioner.singleton",
            return_value=prov,
        )

        resp = client.post(
            "/srv/services/active/update/",
            json=[],
            headers=auth_header,
        )
        assert resp.status_code == 503
        assert "persist" in resp.json().get("detail", "").lower()

    def test_empty_list_legacy_endpoint_alias(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        mocker,
    ) -> None:
        """The legacy endpoint should behave the same as the primary one
        when passed an empty list.
        """
        prov = mocker.Mock()
        prov.update_services.return_value = True
        mocker.patch(
            "src.api.provisioner.SystemProvisioner.singleton",
            return_value=prov,
        )

        resp = client.post(
            "/srv/services/update/",
            json=[],
            headers=auth_header,
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "accepted"
        prov.update_services.assert_called_once()

    def test_update_services_value_error_yields_400(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        mocker,
    ) -> None:
        """A ValueError raised by the provisioner should be translated to a
        400 response by the API.
        """
        prov = mocker.Mock()
        prov.update_services.side_effect = ValueError("not found")
        mocker.patch(
            "src.api.provisioner.SystemProvisioner.singleton",
            return_value=prov,
        )

        payload = [
            {
                "name": "inst-x",
                "service": "unknown",
                "profile": "default",
            },
        ]
        resp = client.post(
            "/srv/services/active/update/",
            json=payload,
            headers=auth_header,
        )
        assert resp.status_code == 400
        assert "not found" in resp.json().get("detail", "").lower()


class TestAvailableResources:
    def test_get_available_resources_returns_list(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        mocker,
    ) -> None:
        """`/srv/resources/available/` returns list from
        `provisioner.get_available_resources()`.
        """
        resources: List[Resource] = [
            Resource(
                name="cpu",
                type=ResourceType.CPU,
                unit="cores",
                value=8,
                related_resources=None,
                extended_attributes=None,
            ),
            Resource(
                name="mem",
                type=ResourceType.MEMORY,
                unit="GB",
                value=32.0,
                related_resources=None,
                extended_attributes=None,
            ),
        ]

        prov = mocker.Mock()
        prov.get_available_resources.return_value = resources
        mocker.patch(
            "src.api.provisioner.SystemProvisioner.singleton",
            return_value=prov,
        )

        resp = client.get("/srv/resources/available/", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json() == [r.model_dump() for r in resources]
        prov.get_available_resources.assert_called_once_with()


class TestHostResources:
    def test_get_host_resources_uses_inspect_host(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        mocker,
    ) -> None:
        """`/srv/host/resources` calls `HostResources.inspect_host()` and
        returns its model as JSON.
        """
        model = HostResources(
            total_cpu_cores=16,
            available_cpu_cores=12,
            total_ram_gb=64.0,
            available_ram_gb=48.5,
            total_vram_gb=24.0,
            available_vram_gb=20.0,
            total_gpus=1,
            available_gpus=[0],
            gpus=[
                GPUResource(
                    id=0,
                    total_vram=24576,
                    available_vram=20000,
                    description="Fake GPU",
                    pci_device_description="0000:01:00.0",
                ),
            ],
        )

        spy = mocker.patch(
            "src.api.provisioner.HostResources.inspect_host",
            return_value=model,
        )

        resp = client.get("/srv/host/resources", headers=auth_header)
        assert resp.status_code == 200
        assert resp.json() == model.model_dump()
        spy.assert_called_once_with()
