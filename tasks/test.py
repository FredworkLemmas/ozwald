import os

from dotenv import load_dotenv
from invocate import task
from invoke import call

from tasks import start_provisioner, stop_provisioner

load_dotenv()

DEFAULT_OZWALD_PROVISIONER = os.environ.get(
    "DEFAULT_OZWALD_PROVISIONER", "unconfigured"
)


@task(namespace="test", name="unit")
def unit(c, path="tests/unit/"):
    """Run unit tests."""
    c.run(f"pytest {path}")


@task(
    namespace="test",
    name="integration",
    pre=[stop_provisioner, call(start_provisioner, mount_source_dir=True)],
)
def integration(c, path="tests/integration/"):
    """Run integration tests against running provisioner services.

    This checks that the API server, backend service, and Redis service are
    running. If any are not running, the tests will be skipped with an
    explanatory message.
    """
    # Determine expected container names (as used by dev tasks)
    api_container = os.environ.get(
        "OZWALD_PROVISIONER_API_CONTAINER", "ozwald-provisioner-api-arch"
    )
    backend_container = os.environ.get(
        "OZWALD_PROVISIONER_BACKEND_CONTAINER", "ozwald-provisioner-backend"
    )
    redis_container = os.environ.get(
        "OZWALD_PROVISIONER_REDIS_CONTAINER", "ozwald-provisioner-redis"
    )

    # Helper to check if a container is running
    def _is_container_running(name: str) -> bool:
        result = c.run(
            f"docker ps --filter name={name} --format '{{{{.Names}}}}'",
            hide=True,
            warn=True,
        )
        return result.ok and result.stdout.strip() == name

    missing = []
    if not _is_container_running(api_container):
        missing.append(f"API server container '{api_container}'")
    if not _is_container_running(backend_container):
        missing.append(f"backend service container '{backend_container}'")
    if not _is_container_running(redis_container):
        missing.append(f"Redis container '{redis_container}'")

    # Also verify the API health endpoint is responsive (on host)
    port = int(os.environ.get("OZWALD_PROVISIONER_PORT", 8000))
    system_key = os.environ.get("OZWALD_SYSTEM_KEY", "jenny8675")
    # Curl health with a short timeout; no auth required
    # health_ok = c.run(
    #     f"curl -s -m 2 http://localhost:{port}/health",
    #     hide=True,
    #     warn=True,
    # )
    # if not health_ok.ok:
    #     missing.append(
    #         "API health endpoint on "
    #         f"http://localhost:{port}/health"
    #     )

    if missing:
        print(
            "\nCannot run integration tests because the following required "
            "services are not running:"
        )
        for m in missing:
            print(f" - {m}")
        print(
            "\nPlease start the provisioner stack (API, backend, and Redis) "
            "before running integration tests."
        )
        print(
            "For example:\n  invocate dev.start-provisioner-network\n  "
            "invocate dev.start-provisioner-redis\n  "
            "invocate dev.start-provisioner-backend\n  "
            "invocate dev.start-provisioner-api"
        )
        print("Once the services are running, rerun: invocate test.integration")
        return

    # the integration tests need OZWALD_PROVISIONER set
    os.environ["OZWALD_PROVISIONER"] = DEFAULT_OZWALD_PROVISIONER

    # Run the integration test suite
    # Expose env that tests may rely on
    env = {
        "OZWALD_PROVISIONER_PORT": str(port),
        "OZWALD_SYSTEM_KEY": system_key,
        # Pass through commonly used vars if set
        **{
            k: v
            for k, v in os.environ.items()
            if k
            in (
                "OZWALD_PROVISIONER_REDIS_PORT",
                "DEFAULT_OZWALD_CONFIG",
                "OZWALD_CONFIG",
                "OZWALD_PROVISIONER",
            )
        },
    }

    # Build env export string
    export_cmd = " ".join([f"{k}='{v}'" for k, v in env.items()])
    c.run(f'bash -lc "{export_cmd} pytest {path}"')


@task(namespace="test", name="coverage")
def coverage(
    c, path="tests/unit/", source="src", html=False, xml=False, fail_under=None
):
    """
    Run tests with coverage measurement and print a coverage report.

    Args:
        c: invocate context (passed automatically).
        path: test path or pattern to run (default: "tests/").
        source: comma-separated package or directory paths to measure
            (default: "src").
        html: generate an HTML report (coverage html) if True.
        xml: generate an XML report (coverage xml) if True.
        fail_under: if provided (int/float), fail if total coverage is
            under this percent.
    """
    # Run pytest under coverage, measuring the specified source
    # directories/packages
    c.run(f"coverage run --source={source} -m pytest {path}")

    # Print a terminal report; optionally enforce a minimum coverage threshold
    report_cmd = "coverage report -m"
    if fail_under is not None:
        report_cmd += f" --fail-under={fail_under}"
    c.run(report_cmd)

    # Optionally generate additional report formats
    if html:
        c.run("coverage html")
    if xml:
        c.run("coverage xml")


# ----------------------
# Developer quality tasks
# ----------------------


@task(namespace="test", name="coverage")
def dev_coverage(c):
    """Run pytest with coverage and show missing lines."""
    c.run("pytest --cov=src --cov-report=term-missing", pty=True)


@task(namespace="test", name="tox")
def dev_tox(c):
    """Run tox default environments locally."""
    c.run("tox -q", pty=True)
