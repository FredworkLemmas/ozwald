from typing import ClassVar

from orchestration.service import BaseProvisionableService


class SimpleTestOneService(BaseProvisionableService):
    """Minimal service used by unit tests for registry discovery.

    Provides only the required `service_type` attribute.
    """

    service_type: ClassVar[str] = "simple_test_one"

    # Keep implementation minimal; BaseProvisionableService provides
    # container-related behavior tested elsewhere. Tests only require
    # discovery and type matching.
