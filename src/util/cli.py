from __future__ import annotations

import os
from typing import Any

from .http import (
    get as http_get,
    post as http_post,
)
from .logger import get_logger

logger = get_logger(__name__)


def _auth_headers(system_key: str | None = None) -> dict[str, str]:
    key = system_key or os.environ.get("OZWALD_SYSTEM_KEY")
    if not key:
        raise KeyError(
            "OZWALD_SYSTEM_KEY environment variable is not defined",
        )
    return {"Authorization": f"Bearer {key}"}


def get_configured_services(
    *,
    port: int = 8000,
    system_key: str | None = None,
) -> list[dict[str, Any]]:
    url = f"http://localhost:{port}/srv/services/configured/"
    headers = _auth_headers(system_key)
    resp = http_get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError("Unexpected response format for configured services")
    return data


def get_active_services(
    *,
    port: int = 8000,
    system_key: str | None = None,
) -> list[dict[str, Any]]:
    url = f"http://localhost:{port}/srv/services/active/"
    headers = _auth_headers(system_key)
    resp = http_get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise ValueError("Unexpected response format for active services")
    return data


def get_host_resources(
    *,
    port: int = 8000,
    system_key: str | None = None,
) -> dict[str, Any]:
    url = f"http://localhost:{port}/srv/host/resources"
    headers = _auth_headers(system_key)
    resp = http_get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected response format for host resources")
    return data


def update_services(
    *,
    port: int = 8000,
    body: list[dict[str, Any]] | list[Any],
    system_key: str | None = None,
) -> dict[str, Any]:
    """Call the provisioner update services endpoint.

    Tries the primary path first, then falls back to the legacy path
    if the primary returns 404.
    """
    primary = f"http://localhost:{port}/srv/services/active/update/"
    legacy = f"http://localhost:{port}/srv/services/update/"
    headers = _auth_headers(system_key)

    logger.info("Attempting to update services via %s", primary)
    resp = http_post(primary, headers=headers, json=body)
    if resp.status_code == 404:
        logger.info("Primary endpoint 404'd, falling back to %s", legacy)
        resp = http_post(legacy, headers=headers, json=body)

    if resp.status_code != 202:
        logger.warning(
            "Update services failed: %s %s", resp.status_code, resp.text
        )

    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected response format for update_services")
    return data


def footprint_services(
    *,
    port: int = 8000,
    body: dict[str, Any],
    system_key: str | None = None,
) -> dict[str, Any]:
    """Call the provisioner footprint services endpoint.

    Tries multiple path variations for backward and forward compatibility.
    """
    paths = [
        f"http://localhost:{port}/srv/services/active/footprint/",
        f"http://localhost:{port}/srv/services/footprint/",
        f"http://localhost:{port}/srv/services/footprint",
        f"http://localhost:{port}/srv/services/active/footprint",
    ]
    headers = _auth_headers(system_key)

    last_resp = None
    for url in paths:
        logger.info("Attempting footprint request via %s", url)
        resp = http_post(url, headers=headers, json=body)
        if resp.status_code != 404:
            if resp.status_code != 202:
                logger.warning(
                    "Footprint request failed at %s: %s %s",
                    url,
                    resp.status_code,
                    resp.text,
                )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise ValueError(
                    "Unexpected response format for footprint_services",
                )
            return data
        logger.info("Endpoint %s returned 404", url)
        last_resp = resp

    if last_resp is not None:
        logger.error("All footprinting endpoints failed with 404")
        last_resp.raise_for_status()

    # This should only be reached if paths is empty, which it isn't
    raise RuntimeError("Failed to call footprint services")
