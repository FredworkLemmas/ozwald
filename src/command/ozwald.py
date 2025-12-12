import argparse
import os
import subprocess
from typing import Any, Dict, List

from dotenv import load_dotenv

from hosts.resources import HostResources

# Internal library modules we created for reuse
from util import (
    cli as ucli,
    services as svc,
)

load_dotenv()

DEFAULT_OZWALD_SYSTEM_KEY = "jenny8675"
DEFAULT_PROVISIONER_PORT = int(os.environ.get("OZWALD_PROVISIONER_PORT", 8000))
DEFAULT_PROVISIONER_REDIS_PORT = int(
    os.environ.get("OZWALD_PROVISIONER_REDIS_PORT", 6479)
)


def _run(cmd: str, capture: bool = False) -> subprocess.CompletedProcess:
    kwargs: Dict[str, Any] = {"shell": True, "text": True}
    if capture:
        kwargs.update({"stdout": subprocess.PIPE, "stderr": subprocess.PIPE})
    return subprocess.run(cmd, **kwargs)


def _print_host_resources(resources: HostResources) -> None:
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
        f"(IDs: {resources.available_gpus})"
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


def _print_services_list(
    title: str, services_data: List[Dict[str, Any]]
) -> None:
    if not services_data:
        print(f"\nNo {title.lower()} found.")
        return

    print("\n" + "=" * 80)
    print(title.upper())
    print("=" * 80)

    for i, service_data in enumerate(services_data, 1):
        print(f"\n[{i}] Service: {service_data.get('service_name', 'N/A')}")
        print("─" * 80)

        # Basic Information
        print(f"  Type:        {service_data.get('type', 'N/A')}")
        if service_data.get("description"):
            print(f"  Description: {service_data['description']}")

        # Docker-like config
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

        # Profiles
        profiles = service_data.get("profiles", [])
        if profiles:
            print(f"\n  Profiles ({len(profiles)}):")
            for profile in profiles:
                profile_name = profile.get("name", "N/A")
                print(f"    • {profile_name}")
                p_env = profile.get("environment") or {}
                if p_env:
                    for key, value in p_env.items():
                        print(f"        {key}: {value}")

    print("\n" + "=" * 80)
    print(f"Total services: {len(services_data)}")
    print("=" * 80 + "\n")


def action_start_provisioner(
    api_port: int, redis_port: int, restart: bool
) -> int:
    print("Starting provisioner stack: network -> redis -> backend -> api ...")
    svc.ensure_provisioner_network()
    svc.start_provisioner_redis(port=redis_port, restart=restart)
    svc.start_provisioner_backend(restart=restart)
    svc.start_provisioner_api(port=api_port, restart=restart)
    print("✓ Provisioner stack started")
    return 0


def action_stop_provisioner() -> int:
    print("Stopping provisioner stack: api -> backend -> redis ...")
    svc.stop_provisioner_api()
    svc.stop_provisioner_backend()
    svc.stop_provisioner_redis()
    print("✓ Provisioner stack stopped")
    return 0


def action_list_configured_services(port: int) -> int:
    try:
        data = ucli.get_configured_services(port=port)
        _print_services_list("Configured Services", data)
        return 0
    except Exception as e:
        print(f"Error calling API: {e}")
        return 2


def action_list_active_services(port: int) -> int:
    try:
        data = ucli.get_active_services(port=port)
        _print_services_list("Active Services", data)
        return 0
    except Exception as e:
        print(f"Error calling API: {e}")
        return 2


def action_show_host_resources(use_api: bool, port: int) -> int:
    try:
        if use_api:
            print("[using api for host resources]")
            resources_data = ucli.get_host_resources(port=port)
            resources = HostResources(**resources_data)
        else:
            resources = HostResources.inspect_host()
        _print_host_resources(resources)
        return 0
    except Exception as e:
        print(f"Error retrieving host resources: {e}")
        return 2


def _docker_container_running(name: str) -> bool:
    result = _run(
        f"docker ps --filter name={name} --format '{{{{.Names}}}}'",
        capture=True,
    )
    out = (result.stdout or "").strip()
    return out == name


def _docker_network_exists(name: str) -> bool:
    result = _run(
        f"docker network ls --filter name=^{name}$ --format '{{{{.Name}}}}'",
        capture=True,
    )
    return (result.stdout or "").strip() == name


def action_status() -> int:
    network = svc.PROVISIONER_NETWORK
    api = "ozwald-provisioner-api-arch"
    backend = "ozwald-provisioner-backend"
    redis = "ozwald-provisioner-redis"

    net_ok = _docker_network_exists(network)
    api_ok = _docker_container_running(api)
    backend_ok = _docker_container_running(backend)
    redis_ok = _docker_container_running(redis)

    print("\nProvisioner status\n-------------------")
    net_status = "✓ available" if net_ok else "✗ missing"
    red_status = "✓ running" if redis_ok else "✗ stopped"
    be_status = "✓ running" if backend_ok else "✗ stopped"
    api_status = "✓ running" if api_ok else "✗ stopped"

    print(f"Network '{network}':        {net_status}")
    print(f"Container '{redis}':        {red_status}")
    print(f"Container '{backend}':      {be_status}")
    print(f"Container '{api}':          {api_status}")

    all_ok = net_ok and api_ok and backend_ok and redis_ok
    print("\nOverall:", "✓ OK" if all_ok else "✗ NOT OK")
    print()
    return 0 if all_ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ozwald",
        description="Ozwald command line utility",
    )
    parser.add_argument(
        "action",
        help="Action to perform",
        choices=[
            "start_provisioner",
            "stop_provisioner",
            "list_configured_services",
            "list_active_services",
            "show_host_resources",
            "status",
        ],
    )

    # Common/network/API options
    parser.add_argument(
        "--api-port",
        type=int,
        default=DEFAULT_PROVISIONER_PORT,
        help=(
            f"Port for provisioner API (default: {DEFAULT_PROVISIONER_PORT})"
        ),
    )
    parser.add_argument(
        "--redis-port",
        type=int,
        default=DEFAULT_PROVISIONER_REDIS_PORT,
        help=(
            "Port for provisioner Redis (default: "
            f"{DEFAULT_PROVISIONER_REDIS_PORT})"
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PROVISIONER_PORT,
        help=(
            "API port used for list/show actions (default: same as --api-port)"
        ),
    )
    parser.add_argument(
        "--no-restart",
        action="store_true",
        help="Do not restart containers if already running",
    )
    parser.add_argument(
        "--use-api",
        action="store_true",
        help="For show_host_resources, fetch via provisioner API",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # For list/show actions, prefer --port if provided; otherwise
    # fallback to --api-port
    port_for_api = args.port or args.api_port
    restart = not args.no_restart

    if args.action == "start_provisioner":
        return action_start_provisioner(args.api_port, args.redis_port, restart)
    if args.action == "stop_provisioner":
        return action_stop_provisioner()
    if args.action == "list_configured_services":
        return action_list_configured_services(port_for_api)
    if args.action == "list_active_services":
        return action_list_active_services(port_for_api)
    if args.action == "show_host_resources":
        return action_show_host_resources(args.use_api, port_for_api)
    if args.action == "status":
        return action_status()

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
