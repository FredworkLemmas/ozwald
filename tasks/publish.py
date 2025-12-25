import os
from pathlib import Path

from dotenv import load_dotenv
from invocate import task


@task(namespace="publish", name="build")
def build(c, clean=True, sdist=True, wheel=True):
    """Build the Python package using uv.

    Args:
        clean: remove the dist/ directory before building
        sdist: include source distribution in the build
        wheel: include wheel in the build

    """
    if clean:
        c.run("rm -rf dist", warn=True)

    cmd = "uv build"
    if not sdist:
        cmd += " --no-sdist"
    if not wheel:
        cmd += " --no-wheel"

    c.run(cmd)


def _project_root() -> Path:
    # tasks/ lives under the project root
    return Path(__file__).resolve().parents[1]


def _load_env() -> None:
    # Load .env from project root if present
    env_path = _project_root() / ".env"
    load_dotenv(dotenv_path=env_path)


def _publish_urls(use_testpypi: bool) -> str:
    if use_testpypi:
        return "https://test.pypi.org/legacy/"
    return "https://upload.pypi.org/legacy/"


def _select_token(use_testpypi: bool) -> str:
    key = "TESTPYPI_TOKEN" if use_testpypi else "PYPI_TOKEN"
    token = os.environ.get(key, "").strip()
    if not token:
        target = "TestPyPI" if use_testpypi else "PyPI"
        raise RuntimeError(
            f"Missing {key} in environment for {target} publication",
        )
    return token


@task(namespace="publish", name="release")
def release(
    c,
    use_testpypi=False,
    do_build=True,
):
    """Publish the package to PyPI or TestPyPI using uv.

    Args:
        use_testpypi: if True, publish to TestPyPI instead of PyPI.
        do_build: if True, run "uv build" before publishing.

    """
    _perform_release(c, use_testpypi=use_testpypi, do_build=do_build)


def _perform_release(
    c,
    *,
    use_testpypi: bool,
    do_build: bool,
) -> None:
    """Core implementation for the release task.

    Split for testability without invoking the task wrapper.
    """
    _load_env()

    if do_build:
        c.run("uv build")

    token = _select_token(use_testpypi)
    # repo_url = _publish_urls(use_testpypi)
    index_opt = "--index testpypi" if use_testpypi else ""
    cmd = f"uv publish --token {token} {index_opt}"
    c.run(cmd)
