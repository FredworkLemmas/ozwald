from config.reader import ConfigReader
from orchestration.models import ServiceInformation
from services.container import ContainerService


def test_port_mapping_integration(tmp_path, mocker):
    """Verify end-to-end port mapping from config to docker command."""
    mocker.patch.dict("os.environ", {"OZWALD_HOST": "localhost"})

    # Reset singletons to avoid interference between tests
    mocker.patch("orchestration.provisioner._system_provisioner", None)
    mocker.patch("config.reader._system_config_reader", None)

    config_content = """
hosts:
  - name: localhost
    ip: 127.0.0.1
provisioners:
  - name: local
    host: localhost
realms:
  realm1:
    service-definitions:
      - name: svc1
        type: container
        image: 'img1'
        bridge-connector:
          port: 80
          name: conn1

portals:
  - name: portal1
    port: 7656
    bridge:
      realm: realm1
      connector: conn1
"""
    config_file = tmp_path / "config.yml"
    config_file.write_text(config_content)

    # Force the SystemConfigReader to use this config
    reader = ConfigReader(str(config_file))
    mocker.patch(
        "config.reader.SystemConfigReader.singleton"
    ).return_value = reader

    svc_info = ServiceInformation(
        name="svc1-instance",
        service="svc1",
        realm="realm1",
        profile=None,
        variety=None,
    )

    service = ContainerService(svc_info)

    # Mock effective_definition to avoid complex resolution in this test if
    # it needs it, but it should work with the real reader since we patched it.
    cmd = service.get_container_start_command("img1")

    # cmd is a list of strings
    cmd_str = " ".join(cmd)
    assert "-p 7656:80" in cmd_str
