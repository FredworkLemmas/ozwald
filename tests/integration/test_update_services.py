import json
import os
from pathlib import Path
from typing import Iterator

import pytest
import redis
import requests
import yaml
from dotenv import load_dotenv

load_dotenv()


def _load_settings() -> dict:
    settings_path = os.environ.get("DEFAULT_OZWALD_CONFIG") or os.environ.get(
        "OZWALD_CONFIG",
    )
    if not settings_path:
        raise RuntimeError(
            "DEFAULT_OZWALD_CONFIG (or OZWALD_CONFIG) must point to the "
            "settings YAML for integration tests",
        )
    print(f'settings path: "{settings_path}"')
    p = Path(settings_path)
    if not p.exists():
        raise RuntimeError(f"Settings file not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_provisioner_cache(cfg: dict) -> dict:
    name = os.environ.get("OZWALD_PROVISIONER")
    if not name:
        raise RuntimeError(
            "OZWALD_PROVISIONER must be set to select provisioner in config",
        )
    provs = cfg.get("provisioners", [])
    for prov in provs:
        if prov.get("name") == name:
            return (prov.get("cache") or {}).get("parameters", {})
    raise RuntimeError(f"Provisioner '{name}' not found in settings file")


@pytest.fixture(autouse=True)
def clear_redis_before_each_test() -> Iterator[None]:
    """Clear the provisioner Redis database before each test.

    We read DB/password from the settings YAML (DEFAULT_OZWALD_CONFIG),
    but connect to the container via localhost and the mapped host port
    specified by OZWALD_PROVISIONER_REDIS_PORT (default 6479).
    """
    cfg = _load_settings()
    cache_params = _get_provisioner_cache(cfg)
    host = "localhost"
    port = int(os.environ.get("OZWALD_PROVISIONER_REDIS_PORT", 6479))
    db = int(cache_params.get("db", 0))
    password = cache_params.get("password")

    client = redis.Redis(
        host=host,
        port=port,
        db=db,
        password=password,
        decode_responses=True,
    )
    client.flushdb()
    try:
        yield
    finally:
        client.flushdb()


def _api_base() -> str:
    port = int(os.environ.get("OZWALD_PROVISIONER_PORT", 8000))
    return f"http://localhost:{port}"


def _auth_headers() -> dict:
    key = os.environ.get("OZWALD_SYSTEM_KEY")
    return {"Authorization": f"Bearer {key}"}


def _pick_a_service_from_settings() -> tuple[str, str]:
    """
    Return (service_name, profile_name) from settings service_definitions list
    in the default realm.
    """
    cfg = _load_settings()
    realms = cfg.get("realms") or {}
    default_realm = realms.get("default") or {}
    services = default_realm.get("service-definitions") or []
    if not services:
        raise RuntimeError("No service-definitions configured in settings file")
    svc = services[0]
    service_name = svc["name"]
    profiles = svc.get("profiles") or []
    profile_name = profiles[0]["name"] if profiles else "default"
    return service_name, profile_name


def test_update_services_persists_to_redis():
    """POST to update endpoint should store expected payload.

    A POST to the active service_definitions update endpoint should update the
    active_services key in Redis.
    """
    service_name, profile_name = _pick_a_service_from_settings()
    body = [
        {
            "name": f"it-{service_name}-1",
            "service": service_name,
            "profile": profile_name,
        },
    ]

    resp = requests.post(
        _api_base() + "/srv/services/dynamic/update/",
        headers=_auth_headers(),
        json=body,
        timeout=5,
    )
    assert resp.status_code == 202, resp.text

    # Verify Redis contents
    cfg = _load_settings()
    cache_params = _get_provisioner_cache(cfg)
    r = redis.Redis(
        host="localhost",
        port=int(os.environ.get("OZWALD_PROVISIONER_REDIS_PORT", 6479)),
        db=int(cache_params.get("db", 0)),
        password=cache_params.get("password"),
        decode_responses=True,
    )
    data = r.get("active_services")
    assert data is not None, "active_services key not found in Redis"
    parsed = json.loads(data)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    item = parsed[0]
    assert item["name"] == f"it-{service_name}-1"
    assert item["service"] == service_name
    assert item["profile"] == profile_name
    # Status should be set to 'starting' by the provisioner when persisting
    assert item.get("status") == "starting"


def test_update_services_rejects_invalid_payload_shape():
    """Sending a non-list payload should yield a 422 from FastAPI validation.

    A POST to the active service_definitions update endpoint should return a 422
    if the payload is not a list.
    """
    resp = requests.post(
        _api_base() + "/srv/services/dynamic/update/",
        headers=_auth_headers(),
        json={"not": "a list"},
        timeout=5,
    )
    assert resp.status_code == 422


def test_update_services_rejects_unknown_service():
    """Unknown service name should return 400 with a helpful message.

    A POST to the active service_definitions update endpoint should return a 400
    if the service name is not found in the settings.
    """
    body = [
        {
            "name": "it-unknown-1",
            "service": "service-does-not-exist",
            "profile": "default",
        },
    ]
    resp = requests.post(
        _api_base() + "/srv/services/dynamic/update/",
        headers=_auth_headers(),
        json=body,
        timeout=5,
    )
    assert resp.status_code == 400
    j = resp.json()
    assert "not found" in j.get("detail", "").lower()
