from typing import ClassVar

from orchestration.service import BaseProvisionableService


class ContainerService(BaseProvisionableService):
    service_type: ClassVar[str] = "container"
