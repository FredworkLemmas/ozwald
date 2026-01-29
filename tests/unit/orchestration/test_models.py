from orchestration.models import (
    FootprintConfig,
    ServiceDefinition,
    ServiceDefinitionProfile,
    ServiceDefinitionVariety,
)


class TestFootprintConfig:
    def test_footprint_config_parsing(self):
        """Verify that FootprintConfig correctly handles YAML aliases."""
        data = {"run-time": 60, "run-script": "test.sh"}
        config = FootprintConfig(**data)
        assert config.run_time == 60
        assert config.run_script == "test.sh"

    def test_footprint_config_defaults(self):
        """Verify that FootprintConfig has correct defaults."""
        config = FootprintConfig()
        assert config.run_time is None
        assert config.run_script is None


class TestServiceDefinitionModelsWithFootprint:
    def test_service_definition_profile_footprint(self):
        """Verify FootprintConfig integration in ServiceDefinitionProfile."""
        footprint = FootprintConfig(**{"run-time": 30})
        profile = ServiceDefinitionProfile(
            name="test-profile",
            footprint=footprint,
        )
        assert profile.footprint.run_time == 30

    def test_service_definition_variety_footprint(self):
        """Verify FootprintConfig integration in ServiceDefinitionVariety."""
        footprint = FootprintConfig(**{"run-script": "variety.sh"})
        variety = ServiceDefinitionVariety(
            image="test-image",
            footprint=footprint,
        )
        assert variety.footprint.run_script == "variety.sh"

    def test_service_definition_footprint(self):
        """Verify FootprintConfig integration in ServiceDefinition."""
        footprint = FootprintConfig(**{"run-time": 120})
        service = ServiceDefinition(
            service_name="test-service",
            type="container",
            footprint=footprint,
        )
        assert service.footprint.run_time == 120
