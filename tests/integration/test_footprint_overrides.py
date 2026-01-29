import pytest
import yaml

from orchestration.models import Cache, ServiceInformation
from services.container import ContainerService


@pytest.mark.integration
def test_complex_footprint_overrides(tmp_path, monkeypatch):
    """Verify multi-level footprint overrides in a realistic config."""
    cfg = {
        "services": [
            {
                "name": "app",
                "type": "container",
                "footprint": {
                    "run-time": 100,
                    "run-script": "base.sh",
                },
                "profiles": {
                    "prod": {
                        "footprint": {
                            "run-time": 200,
                        },
                    },
                },
                "varieties": {
                    "gpu": {
                        "footprint": {
                            "run-script": "gpu.sh",
                        },
                    },
                },
            },
        ],
    }

    cfg_path = tmp_path / "ozwald.yml"
    cfg_path.write_text(yaml.safe_dump(cfg))

    # Mock OZWALD_HOST
    monkeypatch.setenv("OZWALD_HOST", "localhost")

    # We need a cache for the service
    cache = Cache(type="memory")

    # Initialize reader
    from config.reader import SystemConfigReader

    reader = SystemConfigReader(str(cfg_path))
    monkeypatch.setattr(SystemConfigReader, "singleton", lambda: reader)
    reader.get_service_by_name("app")

    # Mock SystemProvisioner for ContainerService
    class MockProv:
        @staticmethod
        def singleton():
            class S:
                def get_cache(self):
                    return cache

            return S()

    import orchestration.provisioner as prov_mod

    monkeypatch.setattr(prov_mod, "SystemProvisioner", MockProv)

    # Case 1: Base (no profile, no variety)
    si_base = ServiceInformation(name="app-base", service="app")
    cs_base = ContainerService(si_base)
    fp_base = cs_base.effective_definition.footprint
    assert fp_base.run_time == 100
    assert fp_base.run_script == "base.sh"

    # Case 2: Profile 'prod'
    si_prod = ServiceInformation(name="app-prod", service="app", profile="prod")
    cs_prod = ContainerService(si_prod)
    fp_prod = cs_prod.effective_definition.footprint
    assert fp_prod.run_time == 200
    assert fp_prod.run_script == "base.sh"

    # Case 3: Variety 'gpu'
    si_gpu = ServiceInformation(name="app-gpu", service="app", variety="gpu")
    cs_gpu = ContainerService(si_gpu)
    fp_gpu = cs_gpu.effective_definition.footprint
    assert fp_gpu.run_time == 100
    assert fp_gpu.run_script == "gpu.sh"

    # Case 4: Both Profile 'prod' and Variety 'gpu'
    si_both = ServiceInformation(
        name="app-both",
        service="app",
        profile="prod",
        variety="gpu",
    )
    cs_both = ContainerService(si_both)
    fp_both = cs_both.effective_definition.footprint
    assert fp_both.run_time == 200
    assert fp_both.run_script == "gpu.sh"
