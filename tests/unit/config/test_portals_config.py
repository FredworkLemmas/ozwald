from config.reader import ConfigReader


class TestPortalsConfigParsing:
    """Tests for parsing portals and bridge-connectors."""

    def test_portals_are_parsed(self, tmp_path):
        """Verify that portals are correctly parsed from config."""
        config_content = """
portals:
  - name: portal1
    port: 8080
    bridge:
      realm: realm1
      connector: conn1
"""
        config_file = tmp_path / "config.yml"
        config_file.write_text(config_content)

        reader = ConfigReader(str(config_file))
        portals = reader.portals()

        assert len(portals) == 1
        assert portals[0].name == "portal1"
        assert portals[0].port == 8080
        assert portals[0].bridge.realm == "realm1"
        assert portals[0].bridge.connector == "conn1"

    def test_bridge_connector_parsing(self, tmp_path):
        """Verify that bridge-connector is parsed at different levels."""
        config_content = """
realms:
  realm1:
    service-definitions:
      - name: svc1
        type: container
        bridge-connector:
          port: 80
          name: conn-base
        varieties:
          v1:
            bridge-connector:
              port: 81
              name: conn-var
        profiles:
          p1:
            bridge-connector:
              port: 82
              name: conn-prof
"""
        config_file = tmp_path / "config.yml"
        config_file.write_text(config_content)

        reader = ConfigReader(str(config_file))
        sd = reader.get_service_by_name("svc1", "realm1")

        assert sd.bridge_connector.port == 80
        assert sd.bridge_connector.name == "conn-base"

        v1 = sd.varieties["v1"]
        assert v1.bridge_connector.port == 81
        assert v1.bridge_connector.name == "conn-var"

        p1 = sd.profiles["p1"]
        assert p1.bridge_connector.port == 82
        assert p1.bridge_connector.name == "conn-prof"

    def test_bridge_connector_merging(self, tmp_path):
        """Verify merging precedence: profile > variety > base."""
        config_content = """
realms:
  realm1:
    service-definitions:
      - name: svc1
        type: container
        bridge-connector:
          port: 80
          name: conn-base
        varieties:
          v1:
            bridge-connector:
              port: 81
              name: conn-var
          v2: {}
        profiles:
          p1:
            bridge-connector:
              port: 82
              name: conn-prof
          p2: {}
"""
        config_file = tmp_path / "config.yml"
        config_file.write_text(config_content)

        reader = ConfigReader(str(config_file))

        # Profile wins
        eff = reader.get_effective_service_definition(
            "svc1", "p1", "v1", "realm1"
        )
        assert eff.bridge_connector.port == 82
        assert eff.bridge_connector.name == "conn-prof"

        # Variety wins if profile doesn't have it
        eff = reader.get_effective_service_definition(
            "svc1", "p2", "v1", "realm1"
        )
        assert eff.bridge_connector.port == 81
        assert eff.bridge_connector.name == "conn-var"

        # Base wins if neither profile nor variety have it
        eff = reader.get_effective_service_definition(
            "svc1", "p2", "v2", "realm1"
        )
        assert eff.bridge_connector.port == 80
        assert eff.bridge_connector.name == "conn-base"
