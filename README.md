Ozwald
======

There is a massive gap between being an LLM consumer and being an enterprise
"LLM puppeteer." Enterprises have the talent and capital to stitch together
game-changing AI services; small businesses and individual developers mostly
don't.

Ozwald is my attempt to fill that void. I think there is a multiverse of opportunity for
AI-enabled services that just hasn't been unlocked yet.

Current status
--------------

Right now, Ozwald is a provisioner for container-based systems and a
collection of handy containers for building AI-enabled systems. I'm focusing
on making the provisioner useful for my own AI experiments first.

The goal is to move the project toward filling the void between the casual
LLM user and the enterprise-scale orchestrator. It's about making it possible
to give a machine a well-defined task in a narrow domain and having it simply
"do the thing."

Ozwald handles the real-world friction of provisioning LLM containers across
mixed hardware (different GPUs or CPU-only) with varying runtime parameters.
It focuses on the glue: a small, well-typed config, a provisioner API, and a
CLI to make starting and stopping services predictable.

Key Ideas
---------

- **Varieties and Profiles:** A clear model for describing services across
  different hardware (nvidia, amdgpu, cpu-only) and parameter sets (fast-gpu,
  no-gpu, etc.).
- **Provisioner API:** Exposes configured and active services, host resources,
  and a small footprinting queue.
- **CLI:** A simple tool for standing up the provisioner and inspecting state
  locally.
- **Library First:** Works best as a dependency that your orchestrator or
  application depends on.


Installation
------------

Add Ozwald to your project's dependencies:

```bash
pip install ozwald
```

Ozwald is typically used as a library by another project (like an orchestrator
service) rather than invoked directly by end users.

Quick start
-----------

1) **Provide a settings file**

Create a YAML configuration (`settings.yml`) that declares hosts,
provisioners, and services:

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
```

2) **Set the mandatory system key**

The `OZWALD_SYSTEM_KEY` environment variable is required for authentication
between the CLI and the Provisioner API.

```bash
export OZWALD_SYSTEM_KEY="your-long-random-token"
```

3) **Start the provisioner**

Use the CLI to spin up the provisioner network and containers:

```bash
ozwald start_provisioner --api-port 8000 --redis-port 6379
ozwald status
```

4) **Call the API**

Query configured or active services:

```bash
curl -H "Authorization: Bearer $OZWALD_SYSTEM_KEY" \
  http://127.0.0.1:8000/srv/services/configured/
```

Configuration reference
-----------------------

- `hosts[]`: Named machines and their IPs.
- `provisioners[]`: Defines where the provisioner runs and its state cache
  (Redis).
- `services[]`: Descriptions of services, including hardware `varieties` and
  runtime `profiles`.

CLI usage
---------

The `ozwald` command handles local development and inspection:

- `start_provisioner`: Start local provisioner network and containers.
- `stop_provisioner`: Stop provisioner containers.
- `status`: Check provisioner health.
- `list_active_services`: See what's currently running.
- `show_host_resources`: Inspect CPU/RAM/GPU/VRAM.

Example:
```bash
ozwald start_provisioner --api-port 8000
ozwald status
ozwald stop_provisioner
```


Provisioner API
---------------

All non-health endpoints require a bearer token:
`Authorization: Bearer <OZWALD_SYSTEM_KEY>`

- `GET /health`: Basic health check (no auth required).
- `GET /srv/services/active/`: List services currently active or transitioning.
- `POST /srv/services/active/update/`: Update the desired set of services.
- `GET /srv/host/resources`: Structured summary of CPU, RAM, GPU, and VRAM.
- `POST /srv/services/footprint`: Queue a footprinting request.

Example:
```bash
curl -H "Authorization: Bearer $OZWALD_SYSTEM_KEY" \
  http://127.0.0.1:8000/srv/services/active/
```

Python usage
------------

```python
import os, requests

base = os.getenv("OZWALD_BASE", "http://127.0.0.1:8000")
headers = {"Authorization": f"Bearer {os.environ['OZWALD_SYSTEM_KEY']}"}

# Activate a service
desired = [{"name": "qwen1.5-vllm", "variety": "cpu-only", "profile": "no-gpu"}]
requests.post(f"{base}/srv/services/active/update/",
              json=desired, headers=headers)
```

Roadmap
-------

The provisioner is the first building block. The compass needle is pointing
toward filling the void between LLM-consumer and enterprise-LLM-puppeteer:

- **Multi-host Orchestration:** Coordinating multiple provisioners.
- **AI Pipelines:** Composable services for ingest, chunking, and indexing.
- **Declarative Ops:** Dry-run planning and explainers for service changes.

License & Contributing
----------------------

Ozwald is open source, designed for adoption while keeping the core free.

1. **The Core (AGPLv3):** The Ozwald engine, orchestrator, and provisioner.
2. **Your Apps (Apache 2.0):** Client SDKs and public interfaces. Build your
   proprietary apps without fear of being forced to open source them.
3. **Contributing:** Requires a signed CLA to ensure sustainability.

For commercial licensing or questions, contact fred@frameworklabs.us.

---
**Author:** Fred McDavid
