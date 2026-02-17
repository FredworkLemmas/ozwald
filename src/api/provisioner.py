"""FastAPI application for the Ozwald Provisioner service.

This API allows an orchestrator to control which service_definitions
are provisioned and provides information about available resources.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime
from typing import Annotated, List

from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from hosts.resources import HostResources
from orchestration.models import (
    FootprintAction,
    FootprintLogLines,
    Resource,
    SecretsUpdate,
    ServiceDefinition,
    ServiceInformation,
)
from orchestration.provisioner import SystemProvisioner
from util.active_services_cache import ActiveServicesCache
from util.footprint_request_cache import FootprintRequestCache
from util.logger import get_logger
from util.runner_logs_cache import RunnerLogsCache

logger = get_logger(__name__)

# Startup validation: Ensure OZWALD_SYSTEM_KEY is defined
if "OZWALD_SYSTEM_KEY" not in os.environ:
    print(
        "CRITICAL: OZWALD_SYSTEM_KEY environment variable is not defined. "
        "The Provisioner API cannot start without this security key.",
        file=sys.stderr,
    )
    sys.exit(1)

# Security setup
# Use auto_error=False so that missing Authorization headers don't short-circuit
# with a 403. This allows us to log unauthorized attempts and return a 401.
security = HTTPBearer(auto_error=False)


def verify_system_key(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(security),
    ] = None,
) -> bool:
    """Verify the OZWALD_SYSTEM_KEY bearer token.

    Args:
        credentials: The HTTP authorization credentials

    Returns:
        True if authentication successful

    Raises:
        HTTPException: If authentication fails

    """
    expected_key = os.environ.get("OZWALD_SYSTEM_KEY")

    # With a configured key, require a valid Bearer token
    if credentials is None or credentials.credentials != expected_key:
        logger.warning(
            "Unauthorized access attempt: invalid or missing bearer token",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return True


# Initialize FastAPI application
app = FastAPI(
    title="Ozwald Provisioner API",
    description="API for managing service provisioning and resources",
    version="1.0.0",
)

# Testing/mocking compatibility: allow tests that patch "src.api.provisioner"
# to resolve to this module (which is actually "api.provisioner").
sys.modules.setdefault("src.api.provisioner", sys.modules[__name__])


@app.get(
    "/srv/services/configured/",
    response_model=List[ServiceDefinition],
    summary="Get configured services",
    description="List all services for which the provisioner is configured",
)
async def get_configured_services(
    authenticated: bool = Depends(verify_system_key),
) -> list[ServiceDefinition]:
    """Returns all services configured for this provisioner."""
    provisioner = SystemProvisioner.singleton()
    return provisioner.get_configured_services()


@app.get(
    "/srv/services/active/",
    response_model=List[ServiceInformation],
    summary="Get active services",
    description=(
        "List all services which the provisioner has made (or is making) active"
    ),
)
async def get_active_services(
    authenticated: bool = Depends(verify_system_key),
) -> list[ServiceInformation]:
    """Returns all services that are currently active or being
    activated/deactivated.
    """
    provisioner = SystemProvisioner.singleton()
    return provisioner.get_active_services()


@app.post(
    "/srv/services/dynamic/update/",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update services (dynamic)",
    description="Activate and deactivate non-persistent services",
)
@app.post(
    "/srv/services/active/update/",
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
)
@app.post(
    "/srv/services/update/",
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,
)
async def update_active_services(
    service_updates: list[ServiceInformation],
    authenticated: bool = Depends(verify_system_key),
) -> dict:
    """Update the active services based on the provided list.

    Services in the list will be activated (or remain active).
    Services not in the list but currently active will be deactivated.

    Args:
        service_updates: List of services to activate

    Returns:
        Acceptance confirmation

    """
    provisioner = SystemProvisioner.singleton()
    try:
        updated = provisioner.update_active_services(
            service_updates,
            persistent=False,
        )
    except ValueError as e:
        # Raised when a referenced service definition does not exist
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except Exception as e:
        # Unexpected failure while attempting to update
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update services: {e}",
        ) from e

    if not updated:
        # Provisioner couldn't persist update to cache
        # (e.g., cache unavailable or timeout)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service update could not be persisted",
        )

    return {"status": "accepted", "message": "Service update request accepted"}


@app.post(
    "/srv/secrets/update/",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update secrets",
    description="Update encrypted secrets in the store",
)
async def update_secrets(
    update: SecretsUpdate,
    authenticated: bool = Depends(verify_system_key),
) -> dict:
    """Update secrets for a realm and locker."""
    from util.crypto import encrypt_payload

    provisioner = SystemProvisioner.singleton()
    try:
        encrypted_blob = encrypt_payload(update.payload, update.token)
        provisioner.set_secret(update.realm, update.locker_name, encrypted_blob)
    except Exception as e:
        logger.error(f"Failed to update secrets: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update secrets: {e}",
        ) from e

    return {"status": "accepted", "message": "Secrets updated"}


@app.get(
    "/srv/resources/available/",
    response_model=List[Resource],
    summary="Get available resources",
    description=(
        "List all currently available resources on this host "
        "(for troubleshooting)"
    ),
)
async def get_available_resources(
    authenticated: bool = Depends(verify_system_key),
) -> list[Resource]:
    """Returns currently available resources on this provisioner's host.

    This endpoint is primarily for troubleshooting. In normal operation,
    a provisioner will notify an orchestrator when resources change.
    """
    provisioner = SystemProvisioner.singleton()
    return provisioner.get_available_resources()


@app.get(
    "/srv/host/resources",
    response_model=HostResources,
    summary="Get host resources",
    description="Get detailed host resource information",
)
async def get_host_resources(
    authenticated: bool = Depends(verify_system_key),
) -> HostResources:
    """Returns detailed host resource information including CPU, RAM, GPU,
    and VRAM.
    """
    return HostResources.inspect_host()


# Health check endpoint (no authentication required)
@app.get("/health", summary="Health check")
async def health_check() -> dict:
    """Simple health check endpoint to verify the service is running."""
    return {"status": "healthy"}


@app.post(
    "/srv/storage/persist",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Persist a temporary volume",
    description=(
        "Create a new versioned encrypted volume from a tmp-writeable volume"
    ),
)
async def persist_volume(
    realm: str,
    volume_name: str,
    destination_source: str,
    encryption_key: str,
    authenticated: bool = Depends(verify_system_key),
) -> dict:
    provisioner = SystemProvisioner.singleton()
    result = provisioner.persist_volume(
        realm=realm,
        volume_name=volume_name,
        destination_source=destination_source,
        encryption_key=encryption_key,
    )
    if not result:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist volume",
        )
    return {"status": "accepted", "image_path": result}


# ---------------------------------------------------------------------------
# Footprinting API
# ---------------------------------------------------------------------------


@app.get(
    "/srv/services/footprint",
    response_model=List[FootprintAction],
    summary="Get pending footprinting requests",
    description="List all pending footprint requests in the cache",
)
async def get_footprint_requests(
    authenticated: bool = Depends(verify_system_key),
) -> list[FootprintAction]:
    provisioner = SystemProvisioner.singleton()
    footprint_cache = FootprintRequestCache(provisioner.get_cache())
    return footprint_cache.get_requests()


@app.post(
    "/srv/services/footprint",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a footprinting request",
    description=(
        "Queue a footprinting action. The system must be unloaded (no active "
        "services) or the request will be rejected."
    ),
)
async def post_footprint_request(
    action: FootprintAction,
    authenticated: bool = Depends(verify_system_key),
) -> dict:
    provisioner = SystemProvisioner.singleton()

    # Ensure system is unloaded: reject if any active services exist
    active_cache = ActiveServicesCache(provisioner.get_cache())
    if active_cache.get_services():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Footprinting requires an unloaded system (no active services)"
            ),
        )

    # Prepare action metadata
    action.requested_at = datetime.now()
    action.request_id = action.request_id or uuid.uuid4().hex

    footprint_cache = FootprintRequestCache(provisioner.get_cache())
    try:
        footprint_cache.add_footprint_request(action)
    except Exception as e:
        logger.warning(f"Failed to queue footprinting request: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to queue footprinting request: {e}",
        ) from e

    return {"status": "accepted", "request_id": action.request_id}


@app.get(
    "/srv/services/launch-logs/{service_name}/",
    response_model=FootprintLogLines,
    summary="Get service launch logs",
    description="Retrieve cached runner logs for the service launch",
)
async def get_service_launch_logs(
    service_name: str,
    realm: str = "default",
    profile: str | None = None,
    variety: str | None = None,
    top: int | None = None,
    last: int | None = None,
) -> FootprintLogLines:
    """Retrieve cached runner logs for the service launch."""
    return await _get_service_runner_logs(
        service_name=service_name,
        realm=realm,
        profile=profile,
        variety=variety,
        top=top,
        last=last,
    )


@app.get(
    "/srv/services/logs/{service_name}/",
    response_model=FootprintLogLines,
    summary="Get service logs",
    description="Retrieve cached runner logs for the service",
)
async def get_service_logs(
    service_name: str,
    realm: str = "default",
    profile: str | None = None,
    variety: str | None = None,
    top: int | None = None,
    last: int | None = None,
) -> FootprintLogLines:
    """Retrieve cached runner logs for the service."""
    return await _get_service_runner_logs(
        service_name=service_name,
        realm=realm,
        profile=profile,
        variety=variety,
        top=top,
        last=last,
    )


async def _get_service_runner_logs(
    service_name: str,
    realm: str = "default",
    profile: str | None = None,
    variety: str | None = None,
    top: int | None = None,
    last: int | None = None,
) -> FootprintLogLines:
    provisioner = SystemProvisioner.singleton()
    cache = provisioner.get_cache()

    # Try to resolve container name from active service_definitions
    active_cache = ActiveServicesCache(cache)
    active_services = active_cache.get_services()

    instance_name = service_name
    found_realm = realm
    for s in active_services:
        if s.service == service_name and s.realm == realm:
            if profile and s.profile != profile:
                continue
            if variety and s.variety != variety:
                continue
            instance_name = s.name
            found_realm = s.realm
            break

    container_name = f"ozsvc--{found_realm}--{instance_name}"
    runner_logs_cache = RunnerLogsCache(cache)
    lines = runner_logs_cache.get_log_lines(container_name)

    if top is not None:
        lines = lines[:top]
    if last is not None:
        lines = lines[-last:]

    return FootprintLogLines(
        service_name=service_name,
        profile=profile,
        variety=variety,
        request_datetime=datetime.now(),
        is_top_n=top is not None,
        is_bottom_n=last is not None,
        lines=lines,
    )


@app.get(
    "/srv/services/footprint-logs/container/{service_name}/",
    response_model=FootprintLogLines,
    summary="Get footprint container logs",
    description="Retrieve docker logs for the footprint run of the container",
)
async def get_footprint_container_logs(
    service_name: str,
    realm: str = "default",
    profile: str | None = None,
    variety: str | None = None,
    top: int | None = None,
    last: int | None = None,
    # authenticated: bool = Depends(verify_system_key),
) -> FootprintLogLines:
    """Retrieve docker logs for the footprint run of a service."""
    provisioner = SystemProvisioner.singleton()
    service_def = provisioner.config_reader.get_service_by_name(
        service_name,
        realm,
    )
    if not service_def:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service {service_name} not found in realm {realm}",
        )

    # Validation
    has_profiles = bool(service_def.profiles)
    has_varieties = bool(service_def.varieties)

    if not has_profiles and profile is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Service {service_name} does not have profiles",
        )
    if has_profiles and profile is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Profile is required for service {service_name}",
        )
    if profile and profile not in (service_def.profiles or {}):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Profile {profile} not found for service {service_name}",
        )

    if not has_varieties and variety is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Service {service_name} does not have varieties",
        )
    if has_varieties and variety is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Variety is required for service {service_name}",
        )
    if variety and variety not in (service_def.varieties or {}):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Variety {variety} not found for service {service_name}",
        )

    # ozwald-<image-name>

    inst_name = f"footprinter--{service_name}--{profile}--{variety}"
    container_name = f"ozsvc--{realm}--{inst_name}"

    cmd = ["docker", "logs"]
    if last is not None:
        cmd.extend(["--tail", str(last)])
    cmd.append(container_name)

    logger.info(f"fetching logs for {container_name}: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            # Container might not exist
            return FootprintLogLines(
                service_name=service_name,
                profile=profile,
                variety=variety,
                request_datetime=datetime.now(),
                is_top_n=top is not None,
                is_bottom_n=last is not None,
                lines=[],
            )

        logs = result.stdout + result.stderr
        lines = logs.splitlines()

        if top is not None:
            lines = lines[:top]

        return FootprintLogLines(
            service_name=service_name,
            profile=profile,
            variety=variety,
            request_datetime=datetime.now(),
            is_top_n=top is not None,
            is_bottom_n=last is not None,
            lines=lines,
        )
    except Exception as e:
        logger.error(f"Failed to retrieve logs for {container_name}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve logs: {str(e)}",
        ) from e


@app.get(
    "/srv/services/footprint-logs/runner/{service_name}/",
    response_model=FootprintLogLines,
    summary="Get footprint runner logs",
    description="Retrieve cached runner logs for the footprint run",
)
async def get_footprint_runner_logs(
    service_name: str,
    realm: str = "default",
    profile: str | None = None,
    variety: str | None = None,
    top: int | None = None,
    last: int | None = None,
) -> FootprintLogLines:
    """Retrieve cached runner logs for the footprint run of a service."""

    logger.info(
        f"Retrieving footprint runner logs for service: {service_name} "
        f"in realm: {realm}"
    )

    provisioner = SystemProvisioner.singleton()
    service_def = provisioner.config_reader.get_service_by_name(
        service_name,
        realm,
    )
    if not service_def:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service {service_name} not found in realm {realm}",
        )

    # Validation
    if bool(service_def.profiles) and profile is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Profile is required for service {service_name}",
        )
    if bool(service_def.varieties) and variety is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Variety is required for service {service_name}",
        )

    inst_name = f"footprinter--{service_name}--{profile}--{variety}"
    container_name = f"ozsvc--{realm}--{inst_name}"

    cache = provisioner.get_cache()
    runner_logs_cache = RunnerLogsCache(cache)
    lines = runner_logs_cache.get_log_lines(container_name)

    logger.info(f"fetching logs for {container_name}: {lines}")

    if top is not None:
        lines = lines[:top]
    if last is not None:
        lines = lines[-last:]

    return FootprintLogLines(
        service_name=service_name,
        profile=profile,
        variety=variety,
        request_datetime=datetime.now(),
        is_top_n=top is not None,
        is_bottom_n=last is not None,
        lines=lines,
    )


# Allow running this module directly, e.g. `python -m api.provisioner`
if __name__ == "__main__":
    try:
        import uvicorn
    except Exception as exc:
        # Provide a clear error if uvicorn isn't installed when running directly
        raise SystemExit(
            "uvicorn is required to run the provisioner API as a module. "
            "Install it with `pip install uvicorn[standard]`.",
        ) from exc

    host = os.environ.get("PROVISIONER_HOST", "127.0.0.1")
    port_str = os.environ.get("PROVISIONER_PORT", "8000")
    try:
        port = int(port_str)
    except ValueError:
        port = 8000

    uvicorn.run(app, host=host, port=port)
