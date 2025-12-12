Ozwald
======

Simple, pragmatic infrastructure for provisioning containerized AI services.

Current release: v0.x — “Ozwald is a provisioner of containerized services.”

Ozwald grew out of real-world friction provisioning LLM containers across mixed hardware (different GPUs, or CPU-only) with varying runtime parameters. It focuses on the glue: a small, well-typed config, a provisioner API, and a CLI to make starting and stopping services predictable.

While this release concentrates on provisioning, Ozwald is designed to evolve into a broader orchestration framework for AI systems, covering pipelines, scheduling, and environments beyond a single host.


Key ideas
---------

- A clear model for describing services using “varieties” (e.g., nvidia, amdgpu, cpu-only) and “profiles” (named parameter sets like fast-gpu, no-gpu, etc.).
- A provisioner API exposing configured and active services, host resources, and a small profiling queue.
- A CLI for standing up the provisioner and inspecting state locally.
- Works best as a library dependency that your orchestrator or application depends on.


Status
------

- Early release. APIs and configuration formats may change.
- Some features are scaffolding that will expand in subsequent releases (orchestration, multi-host scheduling, extensibility hooks).


Installation
------------

- Add to your project’s dependencies (recommended):

```
pip install ozwald
```

Or include in your `pyproject.toml`/`requirements.txt` as you would any other library. Ozwald is typically used by another project (for example, an orchestrator service) rather than invoked directly by end users.


Quick start
-----------

1) Provide a settings file

Create a YAML configuration that declares hosts, provisioners, and services. For example:

```yaml
---
hosts:
  - name: localhost
    ip: 127.0.0.1

provisioners:
  - name: local
    host: localhost
    cache:
      type: redis
      parameters:
        host: ozwald-provisioner-redis
        port: 6379
        db: 0

services:
  - name: qwen1.5-vllm
    type: container
    description: DeepSeek Qwen 1.5B via vLLM
    varieties:
      nvidia:
        image: openai-api-vllm.nvidia
        environment:
          GPU: true
      amdgpu:
        image: openai-api-vllm.amdgpu
        environment:
          GPU: true
      cpu-only:
        image: openai-api-vllm.cpu-only
        environment:
          GPU: false
    environment:
      MODEL_NAME: deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B
    profiles:
      no-gpu:
        environment:
          MAX_MODEL_LEN: 45000
      fast-gpu:
        environment:
          GPU_MEMORY_UTILIZATION: 0.9
          CPU_OFFLOAD_GB: ""

  - name: fiction-sources
    type: source-files
    description: Fiction sources in EPUB format
    environment:
      DIRECTORY: sources/
```

What this expresses:
- hosts: Named machines and their IPs (local or remote).
- provisioners: A provisioner runs on a host and uses a cache (Redis) for state.
- services: Descriptions of containerized services plus non-container resources (like source-files) with an environment.
- varieties: Hardware-specific container images and environment toggles.
- profiles: Named overlays for environment variables to switch runtime behavior.

2) Set a system key

The provisioner API requires a bearer token for access. Set an environment variable before starting your stack:

```
export OZWALD_SYSTEM_KEY="your-long-random-token"
```

3) Start the provisioner (local dev)

From a shell, use the CLI to spin up the provisioner network and containers required for the API and cache:

```
ozwald start_provisioner --api-port 8000 --redis-port 6379
```

Then check status:

```
ozwald status
```

4) Call the API

With the provisioner running, query configured or active services:

```
curl -H "Authorization: Bearer $OZWALD_SYSTEM_KEY" \
  http://127.0.0.1:8000/srv/services/configured/

curl -H "Authorization: Bearer $OZWALD_SYSTEM_KEY" \
  http://127.0.0.1:8000/srv/services/active/
```


Configuration reference
-----------------------

Top-level keys:

- `hosts[]`
  - `name`: string
  - `ip`: string

- `provisioners[]`
  - `name`: string
  - `host`: name of a host
  - `cache`:
    - `type`: currently `redis`
    - `parameters.host`: Redis hostname
    - `parameters.port`: Redis port
    - `parameters.db`: Redis DB index

- `services[]`
  - `name`: unique service name
  - `type`: `container` or other provider types (e.g., `source-files`)
  - `description`: optional
  - `image`: default image for simple cases (optional when using `varieties`)
  - `environment`: base environment map
  - `varieties`:
    - freeform keys such as `nvidia`, `amdgpu`, `cpu-only`, each containing:
      - `image`: container image
      - `environment`: overlay env vars
  - `profiles`:
    - freeform keys such as `fast-gpu`, `no-gpu`, etc., each containing:
      - `environment`: overlay env vars

Example: combining a variety and profile at runtime tells the orchestrator which image to use and which environment overlays to apply when provisioning.


CLI usage
---------

The `ozwald` command provides a small set of actions for local development and inspection:

```
ozwald <action> [--api-port N] [--redis-port N] [--port N] [--no-restart] [--use-api]

Actions:
  start_provisioner        Start local provisioner network and containers
  stop_provisioner         Stop provisioner-related containers
  list_configured_services List services from the provisioner config via API
  list_active_services     List services currently active or activating
  show_host_resources      Show CPU/RAM/GPU/VRAM of the host (optionally via API)
  status                   Summarize provisioner health (network and containers)

Options:
  --api-port N             Port for provisioner API (default: 8000)
  --redis-port N           Port for provisioner Redis (default: 6379)
  --port N                 API port used for list/show actions (default: --api-port)
  --no-restart             Do not restart containers if already running
  --use-api                For show_host_resources, fetch via provisioner API
```

Examples:

```
ozwald start_provisioner --api-port 8000 --redis-port 6379
ozwald status
ozwald list_configured_services --port 8000
ozwald list_active_services --port 8000
ozwald show_host_resources --use-api --port 8000
ozwald stop_provisioner
```


Provisioner API
---------------

Base URL: `http://<host>:<port>` (default `127.0.0.1:8000`)

Authentication: All non-health endpoints require a bearer token via header:

```
Authorization: Bearer <OZWALD_SYSTEM_KEY>
```

Endpoints:

- `GET /health`
  - No authentication required. Returns `{ "status": "healthy" }`.

- `GET /srv/services/configured/`
  - Returns the list of configured service definitions.

- `GET /srv/services/active/`
  - Returns the list of services currently active (or transitioning).

- `POST /srv/services/active/update/`
  - Body: JSON array of `ServiceInformation` objects expressing the desired active set.
  - Response: `{ "status": "accepted", "message": "..." }` or an error status.

- `GET /srv/resources/available/`
  - Returns a snapshot of currently available resources on the host.

- `GET /srv/host/resources`
  - Returns a structured summary of CPU, RAM, GPU, and VRAM on the host.

- `GET /srv/services/profile`
  - Returns pending profiling requests.

- `POST /srv/services/profile`
  - Queues a profiling request while the system is unloaded.

Example requests:

```
curl -s -H "Authorization: Bearer $OZWALD_SYSTEM_KEY" \
  http://127.0.0.1:8000/srv/host/resources | jq

curl -s -H "Authorization: Bearer $OZWALD_SYSTEM_KEY" \
  http://127.0.0.1:8000/srv/services/active/ | jq

curl -s -X POST -H "Authorization: Bearer $OZWALD_SYSTEM_KEY" \
  -H 'Content-Type: application/json' \
  -d '[{"name":"qwen1.5-vllm","variety":"cpu-only","profile":"no-gpu"}]' \
  http://127.0.0.1:8000/srv/services/active/update/
```


Python usage (API client)
-------------------------

In most projects you will call the HTTP API from your orchestrator. A minimal example using `requests`:

```python
import os
import requests

base = os.getenv("OZWALD_BASE", "http://127.0.0.1:8000")
token = os.environ["OZWALD_SYSTEM_KEY"]
headers = {"Authorization": f"Bearer {token}"}

# List configured services
cfg = requests.get(f"{base}/srv/services/configured/", headers=headers).json()

# Activate a service
desired = [{"name": "qwen1.5-vllm", "variety": "cpu-only", "profile": "no-gpu"}]
r = requests.post(f"{base}/srv/services/active/update/", json=desired, headers=headers)
r.raise_for_status()
```


Extensibility and roadmap
-------------------------

The provisioner is the first building block. The roadmap includes:

- A richer orchestration layer (multi-host, scheduling, and lifecycle policies).
- First-class pipeline support (ingest, chunk, embed, index, serve) as composable services.
- Pluggable backends for cache/state and metrics.
- Declarative operations: dry-run planning, diffs, and explainers for changes to active services.


Development
-----------

The repo includes a small developer environment and tests. Common tasks are under `tasks/` and there are integration and unit tests under `tests/`.

If you want to run the API directly for local development:

```
export OZWALD_SYSTEM_KEY=dev-secret
uvicorn api.provisioner:app --host 127.0.0.1 --port 8000
```


License & Contributing
----------------------

Ozwald is open source software, designed to encourage broad adoption while ensuring the core technology remains free.

1. The Core: AGPLv3

The Ozwald engine, orchestrator, and provisioner code are licensed under the GNU Affero General Public License v3 (AGPLv3).

Copyright: © Fred McDavid.

What this means: You are free to use Ozwald for your own projects or within your company. You can modify it for internal use.

The Restriction: If you modify the Ozwald core and make it available to users over a network (e.g., as a hosted service or SaaS), you must release your modifications to the community under the same AGPLv3 license.

Why: This ensures that improvements to the Ozwald core flow back to the community and prevents proprietary forks of the platform infrastructure.

2. Your Apps: Safe to Build

I want you to build proprietary, commercial applications using Ozwald without fear.

The Boundary: The Ozwald Client SDKs and public interfaces are licensed under the Apache 2.0 License (permissive).

The Result: Linking to Ozwald or using the interfaces to build your AI assistant does not force you to open source your application logic. Your data and your business logic remain yours.

3. Contributing & Copyright

Contributions are welcome! To ensure the project can evolve and remains sustainable, I require all contributors to sign a Contributor License Agreement (CLA).

Why a CLA? This ensures that I (Fred McDavid) retain the copyright to the codebase. This centralization of ownership allows me to defend the project legally and preserves the option to offer commercial licensing in the future.

The Process: A bot will prompt you to sign the CLA via a simple click when you open your first Pull Request.

4. Commercial Licensing

If you require a license that allows for proprietary modification of the core platform, or have specific compliance needs that the AGPL cannot meet, please contact me directly at fred@frameworklabs.us to discuss commercial licensing options.


Attribution
-----------

Author: Fred McDavid
