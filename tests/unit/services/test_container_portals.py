import pytest

from orchestration.models import (
    BridgeConnector,
    EffectiveServiceDefinition,
    Portal,
    PortalBridgeConnection,
    ServiceInformation,
)
from services.container import ContainerService


class TestContainerPortals:
    """Tests for ContainerService portal logic."""

    def test_validate_portals_success(self, mocker):
        """Verify that unique ports pass validation."""
        mocker.patch(
            "config.reader.SystemConfigReader.singleton"
        ).return_value.portals.return_value = [
            Portal(
                name="p1",
                port=80,
                bridge=PortalBridgeConnection(realm="r", connector="c1"),
            ),
            Portal(
                name="p2",
                port=81,
                bridge=PortalBridgeConnection(realm="r", connector="c2"),
            ),
        ]

        # Should not raise
        ContainerService._validate_portals()

    def test_validate_portals_duplicate(self, mocker):
        """Verify that duplicate ports raise ValueError."""
        mocker.patch(
            "config.reader.SystemConfigReader.singleton"
        ).return_value.portals.return_value = [
            Portal(
                name="p1",
                port=80,
                bridge=PortalBridgeConnection(realm="r", connector="c1"),
            ),
            Portal(
                name="p2",
                port=80,
                bridge=PortalBridgeConnection(realm="r", connector="c2"),
            ),
        ]

        with pytest.raises(ValueError, match="Duplicate portal port 80"):
            ContainerService._validate_portals()

    def test_get_container_options__port_with_bridge_connector(self, mocker):
        """Verify -p flag generation with bridge-connector."""
        mocker.patch.dict("os.environ", {"OZWALD_HOST": "localhost"})
        # Mock service info
        svc_info = ServiceInformation(
            name="svc1-instance",
            service="svc1",
            realm="realm1",
            profile="p1",
            variety="v1",
        )

        # Mock portals
        mocker.patch(
            "config.reader.SystemConfigReader.singleton"
        ).return_value.portals.return_value = [
            Portal(
                name="portal1",
                port=7656,
                bridge=PortalBridgeConnection(
                    realm="realm1", connector="conn1"
                ),
            )
        ]

        # Create service instance
        service = ContainerService(svc_info)

        # Mock effective_definition property
        eff_def = EffectiveServiceDefinition(
            image="img1",
            bridge_connector=BridgeConnector(port=80, name="conn1"),
        )
        mocker.patch.object(
            ContainerService,
            "effective_definition",
            new_callable=mocker.PropertyMock,
            return_value=eff_def,
        )

        opts = service.get_container_options__port()
        assert "-p" in opts
        assert "7656:80" in opts
