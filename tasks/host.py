from invocate import task


def _ensure_local_registry_data_exists(c):
    """Create the volume for the local registry data"""
    result = c.run(
        "docker volume ls -q -f name=^registry_data$",
        hide=True,
        warn=True,
    )
    if not result.stdout.strip():
        c.run("docker volume create registry_data")


@task(namespace="env.host", name="start-docker-registry")
def start_docker_registry(c):
    """Starts the local docker registry"""
    _ensure_local_registry_data_exists(c)

    # Check if container is already running
    result = c.run("docker ps -q -f name=registry", hide=True, warn=True)
    if result.stdout.strip():
        print("Registry container is already running")
        return

    # Check if container exists but is stopped
    result = c.run("docker ps -aq -f name=registry", hide=True, warn=True)
    if result.stdout.strip():
        print("Starting existing registry container")
        c.run("docker start registry")
    else:
        print("Creating and starting new registry container")
        c.run(
            "docker run -d -p 5000:5000 --restart=always "
            " -v registry_data:/var/lib/registry "
            "--name registry registry:2",
        )
