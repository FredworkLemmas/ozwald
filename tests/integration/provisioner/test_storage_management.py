import os
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from config.reader import SystemConfigReader
from orchestration.models import ServiceInformation, VolumeType
from orchestration.provisioner import SystemProvisioner


@pytest.fixture
def mock_config_reader():
    reader = MagicMock(spec=SystemConfigReader)
    reader.realms = {"test-realm": MagicMock()}
    reader.realms["test-realm"].volumes = []
    reader.provisioners = []
    return reader


@pytest.fixture
def provisioner(mock_config_reader, tmp_path):
    encrypted_dir = tmp_path / "encrypted_storage"
    encrypted_dir.mkdir()

    with patch.dict(
        os.environ,
        {
            "OZWALD_PROVISIONER": "test-p",
            "OZWALD_ENCRYPTED_VOLUME_FILE": str(tmp_path / "storage.img"),
            "OZWALD_SYSTEM_KEY": "system-secret",
        },
    ):
        # Mock the provisioner model in config
        prov_model = MagicMock()
        prov_model.name = "test-p"
        prov_model.encrypted_storage_dir = str(encrypted_dir)
        mock_config_reader.provisioners = [prov_model]

        # Reset singleton to ensure fresh instance
        import orchestration.provisioner

        orchestration.provisioner._system_provisioner = None

        with patch(
            "orchestration.provisioner.SystemConfigReader.singleton",
            return_value=mock_config_reader,
        ):
            p = SystemProvisioner.singleton()
            return p


class TestStorageManagement:
    def test_init_storage_creates_directories(self, provisioner):
        provisioner._init_storage()

        storage_root = pathlib.Path(provisioner.encrypted_storage_dir)
        realm_root = storage_root / "test-realm"
        assert (realm_root / "tmp").exists()
        assert (realm_root / "mounts").exists()

    def test_clear_temporary_volumes(self, provisioner):
        provisioner._init_storage()
        storage_root = pathlib.Path(provisioner.encrypted_storage_dir)
        tmp_vol = storage_root / "test-realm" / "tmp" / "instance1" / "vol1"
        tmp_vol.mkdir(parents=True)
        (tmp_vol / "data.txt").write_text("hello")

        assert tmp_vol.exists()

        provisioner._clear_temporary_volumes()

        assert not tmp_vol.exists()
        assert (storage_root / "test-realm" / "tmp").exists()

    def test_mount_realm_volume_tmp_writeable(self, provisioner):
        from orchestration.models import VolumeDefinition

        vol_def = VolumeDefinition(
            name="scratch", type=VolumeType.TMP_WRITEABLE, source="scratch"
        )
        svc_info = ServiceInformation(
            name="svc-inst-1", service="svc1", realm="test-realm"
        )

        host_path = provisioner._mount_realm_volume(svc_info, vol_def)
        assert host_path is not None
        assert "tmp/svc-inst-1/scratch" in host_path
        assert pathlib.Path(host_path).exists()

    def test_persist_volume(self, provisioner):
        provisioner._init_storage()
        storage_root = pathlib.Path(provisioner.encrypted_storage_dir)

        # Create a fake tmp volume
        tmp_vol = storage_root / "test-realm" / "tmp" / "inst1" / "myvol"
        tmp_vol.mkdir(parents=True)
        (tmp_vol / "state.db").write_text("database content")

        result = provisioner.persist_volume(
            realm="test-realm",
            volume_name="myvol",
            destination_source="persistent-state",
            encryption_key="user-key",
        )

        assert result is not None
        assert (
            result.endswith("persistent-state.202602162330.img")
            or ".img" in result
        )
        assert pathlib.Path(result).exists()

    def test_lifecycle_cleanup_on_shutdown(self, provisioner):
        provisioner._init_storage()
        storage_root = pathlib.Path(provisioner.encrypted_storage_dir)
        tmp_vol = storage_root / "test-realm" / "tmp" / "inst1" / "myvol"
        tmp_vol.mkdir(parents=True)

        # Simulate shutdown
        provisioner._clear_temporary_volumes()
        provisioner._deinit_storage()

        assert not tmp_vol.exists()
