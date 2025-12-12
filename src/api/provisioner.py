"""
FastAPI application for the Ozwald Provisioner service.

This API allows an orchestrator to control which services are provisioned
and provides information about available resources.
"""

import os
import sys
import uuid
from datetime import datetime
from typing import List

from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from hosts.resources import HostResources
from orchestration.models import (
    ProfilerAction,
    Resource,
    Service,
    ServiceDefinition,
    ServiceInformation,
)
from orchestration.provisioner import SystemProvisioner
from util.active_services_cache import ActiveServicesCache
from util.profiler_request_cache import ProfilerRequestCache

# Security setup
# Use auto_error=False so that missing Authorization headers don't short-circuit
# with a 403 before we can check environment configuration. This allows us to
# return a 500 when OZWALD_SYSTEM_KEY is not configured, per requirements/tests.
security = HTTPBearer(auto_error=False)


def verify_system_key(
    credentials: HTTPAuthorizationCredentials | None = Security(security),
) -> bool:
    """
    Verify the OZWALD_SYSTEM_KEY bearer token.

    Args:
        credentials: The HTTP authorization credentials

    Returns:
        True if authentication successful

    Raises:
        HTTPException: If authentication fails
    """
    expected_key = os.environ.get("OZWALD_SYSTEM_KEY")

    # If the system key isn't configured, treat this as a server
    # misconfiguration -> 500
    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OZWALD_SYSTEM_KEY not configured",
        )

    # With a configured key, require a valid Bearer token
    if credentials is None or credentials.credentials != expected_key:
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
) -> List[ServiceDefinition]:
    """
    Returns all services configured for this provisioner.
    """
    provisioner = SystemProvisioner.singleton()
    return provisioner.get_configured_services()


@app.get(
    "/srv/services/active/",
    response_model=List[Service],
    summary="Get active services",
    description=(
        "List all services which the provisioner has made (or is making) active"
    ),
)
async def get_active_services(
    authenticated: bool = Depends(verify_system_key),
) -> List[Service]:
    """
    Returns all services that are currently active or being
    activated/deactivated.
    """
    provisioner = SystemProvisioner.singleton()
    return provisioner.get_active_services()


@app.post(
    "/srv/services/active/update/",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update services",
    description="Activate and deactivate services",
)
async def update_services(
    service_updates: List[ServiceInformation],
    authenticated: bool = Depends(verify_system_key),
) -> dict:
    """
    Update the active services based on the provided list.

    Services in the list will be activated (or remain active).
    Services not in the list but currently active will be deactivated.

    Args:
        service_updates: List of services to activate

    Returns:
        Acceptance confirmation
    """
    provisioner = SystemProvisioner.singleton()
    try:
        updated = provisioner.update_services(service_updates)
    except ValueError as e:
        # Raised when a referenced service definition does not exist
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        )
    except Exception as e:
        # Unexpected failure while attempting to update
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update services: {e}",
        )

    if not updated:
        # Provisioner couldn't persist update to cache
        # (e.g., cache unavailable or timeout)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service update could not be persisted",
        )

    return {"status": "accepted", "message": "Service update request accepted"}


# Backward-compatible alias for older clients/tests
@app.post(
    "/srv/services/update/",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Update services (legacy endpoint)",
    description="Alias for /srv/services/active/update/",
)
async def update_services_legacy(
    service_updates: List[ServiceInformation],
    authenticated: bool = Depends(verify_system_key),
) -> dict:
    # type: ignore[arg-type]
    return await update_services(service_updates, authenticated)


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
) -> List[Resource]:
    """
    Returns currently available resources on this provisioner's host.

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
    """
    Returns detailed host resource information including CPU, RAM, GPU,
    and VRAM.
    """
    return HostResources.inspect_host()


# Health check endpoint (no authentication required)
@app.get("/health", summary="Health check")
async def health_check() -> dict:
    """
    Simple health check endpoint to verify the service is running.
    """
    return {"status": "healthy"}


# ---------------------------------------------------------------------------
# Profiling API
# ---------------------------------------------------------------------------


@app.get(
    "/srv/services/profile",
    response_model=List[ProfilerAction],
    summary="Get pending profiling requests",
    description="List all pending profiler requests in the cache",
)
async def get_profiler_requests(
    authenticated: bool = Depends(verify_system_key),
) -> List[ProfilerAction]:
    provisioner = SystemProvisioner.singleton()
    profiler_cache = ProfilerRequestCache(provisioner.get_cache())
    return profiler_cache.get_requests()


@app.post(
    "/srv/services/profile",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a profiling request",
    description=(
        "Queue a profiling action. The system must be unloaded (no active "
        "services) or the request will be rejected."
    ),
)
async def post_profiler_request(
    action: ProfilerAction, authenticated: bool = Depends(verify_system_key)
) -> dict:
    provisioner = SystemProvisioner.singleton()

    # Ensure system is unloaded: reject if any active services exist
    active_cache = ActiveServicesCache(provisioner.get_cache())
    if active_cache.get_services():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Profiling requires an unloaded system (no active services)",
        )

    # Prepare action metadata
    action.requested_at = datetime.now()
    action.request_id = action.request_id or uuid.uuid4().hex

    profiler_cache = ProfilerRequestCache(provisioner.get_cache())
    try:
        profiler_cache.add_profile_request(action)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to queue profiling request: {e}",
        )

    return {"status": "accepted", "request_id": action.request_id}


# Allow running this module directly, e.g. `python -m api.provisioner`
if __name__ == "__main__":
    try:
        import uvicorn
    except Exception as exc:
        # Provide a clear error if uvicorn isn't installed when running directly
        raise SystemExit(
            "uvicorn is required to run the provisioner API as a module. "
            "Install it with `pip install uvicorn[standard]`."
        ) from exc

    host = os.environ.get("PROVISIONER_HOST", "127.0.0.1")
    port_str = os.environ.get("PROVISIONER_PORT", "8000")
    try:
        port = int(port_str)
    except ValueError:
        port = 8000

    uvicorn.run(app, host=host, port=port)
