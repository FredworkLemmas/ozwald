import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from invocate import task

from hosts.resources import HostResources
from orchestration.models import ServiceInformation
from util import (
    cli as ucli,
    openapi as uopenapi,
    services as svc,
)

load_dotenv()

DEFAULT_PROVISIONER_PORT = os.environ.get("OZWALD_PROVISIONER_PORT", 8000)
DEFAULT_PROVISIONER_REDIS_PORT = os.environ.get(
    "OZWALD_PROVISIONER_REDIS_PORT",
    6479,
)

DEFAULT_REL_OR_ABS_OZWALD_CONFIG = os.environ.get(
    "OZWALD_CONFIG",
    "dev/resources/settings.yml",
)
DEFAULT_OZWALD_CONFIG = (
    DEFAULT_REL_OR_ABS_OZWALD_CONFIG
    if DEFAULT_REL_OR_ABS_OZWALD_CONFIG.startswith("/")
    else Path(__file__).parent.parent / DEFAULT_REL_OR_ABS_OZWALD_CONFIG
)
DEFAULT_OZWALD_PROVISIONER = os.environ.get(
    "DEFAULT_OZWALD_PROVISIONER",
    "unconfigured",
)
DEFAULT_OZWALD_HOST = os.environ.get("DEFAULT_OZWALD_HOST", "localhost")

# default ozwald config for dev tasks
if "OZWALD_CONFIG" not in os.environ:
    os.environ["OZWALD_CONFIG"] = str(DEFAULT_OZWALD_CONFIG)


@task(namespace="dev", name="show-host-resources")
def show_host_resources(c, use_api=False, port=DEFAULT_PROVISIONER_PORT):
    """Display host resource information including CPU, RAM, and GPU

    Args:
        use_api: If True, fetch resources from the API endpoint instead
                 of directly
        port: Port where the provisioner API is running (default: 8000)

    """
    if use_api:
        print("[using api for host resources]")
        # Fetch via API through the CLI helper
        try:
            resources_data = ucli.get_host_resources(port=port)
            resources = HostResources(**resources_data)
        except requests.exceptions.RequestException as e:
            print(f"Error calling API: {e}")
            return
    else:
        # Call the module method directly
        resources = HostResources.inspect_host()

    print("\n" + "=" * 60)
    print("HOST RESOURCES")
    print("=" * 60)

    # CPU Information
    print("\nCPU:")
    print(f"  Total cores:     {resources.total_cpu_cores}")
    print(f"  Available cores: {resources.available_cpu_cores}")

    # RAM Information
    print("\nRAM:")
    print(f"  Total:     {resources.total_ram_gb:6.2f} GB")
    print(f"  Available: {resources.available_ram_gb:6.2f} GB")
    used_ram = resources.total_ram_gb - resources.available_ram_gb
    print(f"  Used:      {used_ram:6.2f} GB")

    # GPU Information
    print("\nGPUs:")
    print(f"  Total GPUs:       {resources.total_gpus}")
    print(
        "  Available GPUs:   "
        f"{len(resources.available_gpus)} "
        f"(IDs: {resources.available_gpus})",
    )
    print(f"  Total VRAM:       {resources.total_vram_gb:6.2f} GB")
    print(f"  Available VRAM:   {resources.available_vram_gb:6.2f} GB")

    if resources.gpus:
        print("\n  GPU Details:")
        for gpu in resources.gpus:
            status = "✓" if gpu.id in resources.available_gpus else "✗"
            print(f"    [{status}] GPU {gpu.id}: {gpu.description}")
            print(f"        PCI:       {gpu.pci_device_description}")
            v_avail = gpu.available_vram / 1024
            v_total = gpu.total_vram / 1024
            print(f"        VRAM:      {v_avail:6.2f} GB / {v_total:6.2f} GB")
            usage = (
                (gpu.total_vram - gpu.available_vram) / gpu.total_vram * 100
                if gpu.total_vram
                else 0
            )
            print(f"        Usage:     {usage:5.1f}%")
    else:
        print("    No GPUs detected")

    print("\n" + "=" * 60 + "\n")


@task(namespace="dev", name="build-containers")
def build_containers(c, name=None):
    """Build Docker images by delegating to util.service_definitions."""
    svc.build_containers(name=name)


def _get_installed_gpu_drivers(c):
    """Get the list of installed GPU drivers."""
    result = c.run("lsmod", hide=True)
    lsmod_output = result.stdout
    lines = lsmod_output.splitlines()

    # find gpu drivers
    drivers = []
    for line in lines:
        # Skip the header line
        if line.startswith("Module"):
            continue

        # Extract the first column (module name)
        parts = line.split()
        if not parts:
            continue

        module_name = parts[0]

        # Check for AMD or NVIDIA drivers
        if module_name == "amdgpu":
            drivers.append("amdgpu")
        elif module_name == "nvidia":
            drivers.append("nvidia")

    return drivers


# --- Shared Docker network helpers/tasks ---
PROVISIONER_NETWORK = "provisioner_network"


@task(namespace="dev", name="start-provisioner-network")
def start_provisioner_network(c):
    """Create the shared docker network for provisioner containers."""
    svc.ensure_provisioner_network()


@task(namespace="dev", name="stop-provisioner-network")
def stop_provisioner_network(c):
    """Remove the shared docker network for provisioner containers."""
    svc.remove_provisioner_network()


@task(namespace="dev", name="start-provisioner")
def start_provisioner_api(c, port=DEFAULT_PROVISIONER_PORT, restart=True):
    """Start the provisioner-api container via util.service_definitions."""
    svc.start_provisioner_api(port=port, restart=restart)


@task(namespace="dev", name="stop-provisioner-api")
def stop_provisioner_api(c):
    """Stop the provisioner-api container via util.service_definitions."""
    svc.stop_provisioner_api()


@task(namespace="dev", name="list-configured-services")
def list_configured_services(c, port=DEFAULT_PROVISIONER_PORT):
    """List all configured services from the provisioner API

    Args:
        port: Port where the provisioner API is running (default: 8000)

    """
    try:
        services_data = ucli.get_configured_services(port=port)

        if not services_data:
            print("\nNo configured services found.")
            return

        print("\n" + "=" * 80)
        print("CONFIGURED SERVICES")
        print("=" * 80)

        for i, service_data in enumerate(services_data, 1):
            print(f"\n[{i}] Service: {service_data.get('service_name', 'N/A')}")
            print("─" * 80)

            # Basic Information
            print(f"  Type:        {service_data.get('type', 'N/A')}")
            if service_data.get("description"):
                print(f"  Description: {service_data['description']}")

            # Docker-like configuration
            depends_on = service_data.get("depends_on") or []
            if depends_on:
                print("\n  Depends on:")
                for dep in depends_on:
                    print(f"    - {dep}")

            if service_data.get("command") is not None:
                print(f"  Command:   {service_data.get('command')}")
            if service_data.get("entrypoint") is not None:
                print(f"  Entrypoint:{service_data.get('entrypoint')}")

            env_file = service_data.get("env_file") or []
            if env_file:
                print("  Env files:")
                for ef in env_file:
                    print(f"    - {ef}")

            environment = service_data.get("environment") or {}
            if environment:
                print("\n  Environment:")
                for key, value in environment.items():
                    print(f"    {key}: {value}")

            properties = service_data.get("properties") or {}
            if properties:
                print("\n  Properties:")
                for key, value in properties.items():
                    print(f"    {key}: {value}")

            # Profiles
            profiles = service_data.get("profiles", {})
            if profiles:
                print(f"\n  Profiles ({len(profiles)}):")
                for profile in profiles.values():
                    profile_name = profile.get("name", "N/A")
                    print(f"    • {profile_name}")
                    p_env = profile.get("environment") or {}
                    if p_env:
                        for key, value in p_env.items():
                            print(f"        {key}: {value}")
                    p_properties = profile.get("properties") or {}
                    if p_properties:
                        if not p_env:
                            print("        (Properties)")
                        for key, value in p_properties.items():
                            print(f"        {key}: {value}")

            # Varieties
            varieties = service_data.get("varieties", {})
            if varieties:
                print(f"\n  Varieties ({len(varieties)}):")
                for v_name, v_data in varieties.items():
                    print(f"    • {v_name}")
                    if v_data.get("image"):
                        print(f"        Image: {v_data['image']}")
                    v_env = v_data.get("environment") or {}
                    if v_env:
                        for key, value in v_env.items():
                            print(f"        {key}: {value}")
                    v_properties = v_data.get("properties") or {}
                    if v_properties:
                        if not v_env:
                            print("        (Properties)")
                        for key, value in v_properties.items():
                            print(f"        {key}: {value}")

        print("\n" + "=" * 80)
        print(f"Total service_definitions: {len(services_data)}")
        print("=" * 80 + "\n")

    except requests.exceptions.RequestException as e:
        print(f"Error calling API: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"Response status: {e.response.status_code}")
            print(f"Response body: {e.response.text}")


@task(namespace="dev", name="list-api-endpoints")
def list_api_endpoints(c, port=DEFAULT_PROVISIONER_PORT):
    """List all API endpoints from the provisioner API"""
    try:
        data = ucli.get_openapi_spec(port=port)
        openapi_doc = uopenapi.OpenApiDocument(data=data)

        print("\n" + "=" * 80)
        print(f"{'URL':<35} {'METHODS':<10} {'REQUEST':<15} {'RESPONSE':<15}")
        print("-" * 80)

        for endpoint in openapi_doc.endpoints:
            methods = ",".join(endpoint.supported_methods)
            req = endpoint.request_schema or "None"
            resp = endpoint.response_schema or "None"
            print(f"{endpoint.url:<35} {methods:<10} {req:<15} {resp:<15}")

        print("=" * 80 + "\n")

    except Exception as e:
        print(f"Error listing API endpoints: {e}")


@task(namespace="dev", name="show-api-schemas")
def show_api_schemas(c, schemas, port=DEFAULT_PROVISIONER_PORT):
    """Show detailed schema information for a list of schema names.

    Args:
        schemas: Comma-separated list of schema names.
        port: Port where the provisioner API is running.
    """
    try:
        import json

        data = ucli.get_openapi_spec(port=port)
        openapi_doc = uopenapi.OpenApiDocument(data=data)

        schema_names = [s.strip() for s in schemas.split(",")]

        for name in schema_names:
            schema_data = openapi_doc.schemas.get(name)
            print("\n" + "=" * 80)
            print(f"SCHEMA: {name}")
            print("-" * 80)
            if schema_data:
                print(json.dumps(schema_data, indent=2))
            else:
                print("NOT FOUND")
            print("=" * 80)

    except Exception as e:
        print(f"Error showing API schemas: {e}")


@task(namespace="dev", name="list-active-services")
def list_active_services(c, port=DEFAULT_PROVISIONER_PORT):
    """List all active services from the provisioner API

    Args:
        port: Port where the provisioner API is running (default: 8000)

    """
    try:
        services_data = ucli.get_active_services(port=port)

        if not services_data:
            print("\nNo active services found.")
            return

        print("\n" + "=" * 80)
        print("ACTIVE SERVICES")
        print("=" * 80)

        for i, service_data in enumerate(services_data, 1):
            # ServiceInformation uses 'service', ServiceDefinition uses
            # 'service_name'
            svc_name = service_data.get("service") or service_data.get(
                "service_name"
            )
            print(f"\n[{i}] Service: {svc_name or 'N/A'}")
            print("─" * 80)

            # Basic Information
            print(f"  Type:        {service_data.get('type', 'N/A')}")
            if service_data.get("description"):
                print(f"  Description: {service_data['description']}")

            # Docker-like configuration
            depends_on = service_data.get("depends_on") or []
            if depends_on:
                print("\n  Depends on:")
                for dep in depends_on:
                    print(f"    - {dep}")

            if service_data.get("command") is not None:
                print(f"  Command:   {service_data.get('command')}")
            if service_data.get("entrypoint") is not None:
                print(f"  Entrypoint:{service_data.get('entrypoint')}")

            env_file = service_data.get("env_file") or []
            if env_file:
                print("  Env files:")
                for ef in env_file:
                    print(f"    - {ef}")

            environment = service_data.get("environment") or {}
            if environment:
                print("\n  Environment:")
                for key, value in environment.items():
                    print(f"    {key}: {value}")

            properties = service_data.get("properties") or {}
            if properties:
                print("\n  Properties:")
                for key, value in properties.items():
                    print(f"    {key}: {value}")

            # Profiles
            profiles = service_data.get("profiles", {})
            if profiles:
                print(f"\n  Profiles ({len(profiles)}):")
                for profile in profiles.values():
                    profile_name = profile.get("name", "N/A")
                    print(f"    • {profile_name}")
                    p_env = profile.get("environment") or {}
                    if p_env:
                        for key, value in p_env.items():
                            print(f"        {key}: {value}")
                    p_properties = profile.get("properties") or {}
                    if p_properties:
                        if not p_env:
                            print("        (Properties)")
                        for key, value in p_properties.items():
                            print(f"        {key}: {value}")

            # Varieties
            varieties = service_data.get("varieties", {})
            if varieties:
                print(f"\n  Varieties ({len(varieties)}):")
                for v_name, v_data in varieties.items():
                    print(f"    • {v_name}")
                    if v_data.get("image"):
                        print(f"        Image: {v_data['image']}")
                    v_env = v_data.get("environment") or {}
                    if v_env:
                        for key, value in v_env.items():
                            print(f"        {key}: {value}")
                    v_properties = v_data.get("properties") or {}
                    if v_properties:
                        if not v_env:
                            print("        (Properties)")
                        for key, value in v_properties.items():
                            print(f"        {key}: {value}")

        print("\n" + "=" * 80)
        print(f"Total service_definitions: {len(services_data)}")
        print("=" * 80 + "\n")

    except requests.exceptions.RequestException as e:
        print(f"Error calling API: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"Response status: {e.response.status_code}")
            print(f"Response body: {e.response.text}")


@task(namespace="dev", name="update-services", iterable=["service"])
def update_services(c, service, port=DEFAULT_PROVISIONER_PORT):
    """Update active services via the provisioner API

    Args:
        service: Service definition(s) in format: name[type@profile]
                 or name[type]. Can be specified multiple times
                 (e.g., --service svc1[type1@prof1] --service svc2[type2])
        port: Port where the provisioner API is running (default: 8000)

    Examples:
        inv dev.update-services --service some-model[qwen7-vllm@no-gpu]
        inv dev.update-services --service model1[qwen7-vllm] \
                                --service model2[llama-vllm@gpu]

    """
    # validate input
    if not service:
        print("Error: At least one --service argument is required")
        return

    # init vars
    url = f"http://localhost:{port}/srv/services/active/update/"
    system_key = os.environ.get("OZWALD_SYSTEM_KEY")
    headers = {"Authorization": f"Bearer {system_key}"}

    # Parse service definitions
    services_to_update = []
    for svc_def in service:
        try:
            # Parse format: name[type@profile] or name[type]
            if "[" not in svc_def or "]" not in svc_def:
                print(f"Error: Invalid service format: {svc_def}")
                print("Expected format: name[type@profile] or name[type]")
                return

            name_part, rest = svc_def.split("[", 1)
            type_profile = rest.rstrip("]")

            service_name = name_part.strip()

            if "@" in type_profile:
                service_type, profile = type_profile.split("@", 1)
                service_type = service_type.strip()
                profile = profile.strip()
            else:
                service_type = type_profile.strip()
                profile = None

            service_obj = ServiceInformation(
                name=service_name,
                service=service_type,
                profile=profile,
            )

            services_to_update.append(service_obj)

        except Exception as e:
            print(f"Error parsing service definition '{svc_def}': {e}")
            return

    # Make the API request
    try:
        # Convert ServiceInformation objects to JSON-serializable dicts
        services_json = [service.model_dump() for service in services_to_update]

        response = requests.post(url, json=services_json, headers=headers)
        response.raise_for_status()
        result_data = response.json()

        # Check for expected response format
        if result_data.get("status") == "accepted":
            print("\n" + "=" * 80)
            print("✓ SERVICE UPDATE REQUEST ACCEPTED")
            print("=" * 80)
            msg = result_data.get("message", "Service update request accepted")
            print(f"\n{msg}")
            print(
                f"\nRequested service_definitions ({len(services_to_update)}):"
            )
            for i, service in enumerate(services_to_update, 1):
                profile_info = f"@{service.profile}" if service.profile else ""
                print(
                    f"  [{i}] {service.name}[{service.service}{profile_info}]",
                )
            print("\n" + "=" * 80 + "\n")
        else:
            # Unexpected response format
            print(f"\nUnexpected response: {result_data}\n")

    except requests.exceptions.RequestException as e:
        print(f"Error calling API: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"Response status: {e.response.status_code}")
            print(f"Response body: {e.response.text}")


def _user_id():
    return os.getuid()


def _docker_group_id():
    import grp

    try:
        return grp.getgrnam("docker").gr_gid
    except KeyError:
        return None


@task(namespace="dev", name="start-provisioner-backend")
def start_provisioner_backend(c, restart=True):
    """Start the provisioner-backend container via util.service_definitions."""
    try:
        svc.validate_footprint_data_env()
    except RuntimeError as e:
        print(f"Error: {e}")
        return
    svc.start_provisioner_backend(restart=restart)


@task(namespace="dev", name="start-provisioner-redis")
def start_provisioner_redis(
    c,
    port=DEFAULT_PROVISIONER_REDIS_PORT,
    restart=True,
):
    """
    Start a Redis container for the provisioner via util.service_definitions.
    """
    svc.start_provisioner_redis(port=port, restart=restart)


@task(namespace="dev", name="start-provisioner")
def start_provisioner(
    c,
    api_port=DEFAULT_PROVISIONER_PORT,
    redis_port=DEFAULT_PROVISIONER_REDIS_PORT,
    restart=True,
    mount_source_dir=False,
):
    """Start the full provisioner stack (Redis, Backend, API).

    Args:
        api_port: Port to expose the API on (default:
            DEFAULT_PROVISIONER_PORT)
        redis_port: Port to expose Redis on (default:
            DEFAULT_PROVISIONER_REDIS_PORT)
        restart: If True, stop and restart containers if they're already
            running

    """
    try:
        svc.validate_footprint_data_env()
    except RuntimeError as e:
        print(f"Error: {e}")
        return

    print("Starting provisioner stack: network -> redis -> backend -> api ...")
    # Ensure network exists first
    svc.ensure_provisioner_network()
    # Start Redis first so backend and API can connect
    svc.start_provisioner_redis(port=redis_port, restart=restart)
    # Then the backend worker/service_definitions
    svc.start_provisioner_backend(
        restart=restart,
        mount_source_dir=mount_source_dir,
    )
    # Finally the API
    svc.start_provisioner_api(
        port=api_port,
        restart=restart,
        mount_source_dir=mount_source_dir,
    )
    print("✓ Provisioner stack started")


@task(namespace="dev", name="stop-provisioner-backend")
def stop_provisioner_backend(c):
    """Stop the provisioner-backend container via util.service_definitions."""
    svc.stop_provisioner_backend()


@task(namespace="dev", name="stop-provisioner-redis")
def stop_provisioner_redis(c):
    """Stop the provisioner-redis container via util.service_definitions."""
    svc.stop_provisioner_redis()


@task(namespace="dev", name="stop-provisioner")
def stop_provisioner(c):
    """Stop the full provisioner stack (API, Backend, Redis)."""
    print("Stopping provisioner stack: api -> backend -> redis ...")
    svc.stop_provisioner_api()
    svc.stop_provisioner_backend()
    svc.stop_provisioner_redis()
    print("✓ Provisioner stack stopped")


@task(namespace="dev", name="run-docs-server")
def run_mkdocs_server(c):
    """Run mkdocs server locally for development."""
    c.run("cd dev/documentation && mkdocs serve -a 127.0.0.1:8010")


@task(namespace="dev", name="build-docs")
def build_mkdocs_docs(c):
    """Build mkdocs documentation."""
    c.run("cd dev/documentation && mkdocs build")


@task(namespace="dev", name="onboard")
def dev_onboard(c):
    """One-time setup: install dev tools, enable hooks, and run them once."""
    c.run("python -m pip install --upgrade pip", pty=True)
    c.run("pip install -e .[dev]", pty=True)
    c.run("pre-commit install", pty=True)
    c.run("pre-commit run --all-files", pty=True)


@task(namespace="dev", name="checks")
def dev_checks(c):
    """Everyday checks: ruff (lint+format check), mypy, and quick pytest."""
    c.run("ruff check .", pty=True)
    c.run("ruff format --check .", pty=True)
    c.run("mypy src", pty=True)
    c.run("pytest -q", pty=True)
