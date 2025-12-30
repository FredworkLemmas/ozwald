import os

import pytest

from util.services import validate_footprint_data_env


class TestValidateFootprintDataEnv:
    def test_missing_env_var(self, mocker):
        mocker.patch.dict(os.environ, {}, clear=True)
        with pytest.raises(
            RuntimeError,
            match="OZWALD_FOOTPRINT_DATA environment variable is not defined",
        ):
            validate_footprint_data_env()

    def test_path_not_writable(self, mocker, tmp_path):
        footprint_file = tmp_path / "footprints.yml"
        footprint_file.touch()
        # Make it read-only
        footprint_file.chmod(0o444)

        mocker.patch.dict(
            os.environ, {"OZWALD_FOOTPRINT_DATA": str(footprint_file)}
        )

        # In some environments (like root), chmod 444 might still be writable.
        if os.access(footprint_file, os.W_OK):
            pytest.skip("File is still writable even after chmod 444")

        with pytest.raises(RuntimeError, match="is not writable"):
            validate_footprint_data_env()

    def test_parent_not_writable(self, mocker, tmp_path):
        read_only_dir = tmp_path / "readonly"
        read_only_dir.mkdir()
        footprint_file = read_only_dir / "footprints.yml"

        # Make dir read-only
        read_only_dir.chmod(0o555)

        mocker.patch.dict(
            os.environ, {"OZWALD_FOOTPRINT_DATA": str(footprint_file)}
        )

        if os.access(read_only_dir, os.W_OK):
            pytest.skip("Directory is still writable even after chmod 555")

        with pytest.raises(RuntimeError, match="is not writable"):
            validate_footprint_data_env()

    def test_parent_does_not_exist(self, mocker, tmp_path):
        non_existent_dir = tmp_path / "nonexistent"
        footprint_file = non_existent_dir / "footprints.yml"

        mocker.patch.dict(
            os.environ, {"OZWALD_FOOTPRINT_DATA": str(footprint_file)}
        )

        with pytest.raises(RuntimeError, match="does not exist"):
            validate_footprint_data_env()

    def test_success_file_exists(self, mocker, tmp_path):
        footprint_file = tmp_path / "footprints.yml"
        footprint_file.touch()
        mocker.patch.dict(
            os.environ, {"OZWALD_FOOTPRINT_DATA": str(footprint_file)}
        )

        # Should not raise
        validate_footprint_data_env()

    def test_success_parent_exists(self, mocker, tmp_path):
        footprint_file = tmp_path / "footprints.yml"
        mocker.patch.dict(
            os.environ, {"OZWALD_FOOTPRINT_DATA": str(footprint_file)}
        )

        # Should not raise
        validate_footprint_data_env()
