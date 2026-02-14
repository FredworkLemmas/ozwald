Ozwald
======

There is a chasm between being an LLM consumer and being an enterprise
"LLM puppeteer." Enterprises have the talent and capital to stitch together
game-changing AI services; small businesses and individual developers simply
don't.

Ozwald is an effort to bridge that void and unlock the multiverse of opportunity
that AI-enabled services represent for smaller teams/budgets.


Current status
--------------

Right now, Ozwald is a provisioner for container-based systems, with a primary
focus on making the provisioner useful for my own AI experiments first.

The goal is to move the project toward closing the gap between the casual
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
  different hardware (nvidia, amdgpu, cpu-only) and parameter sets that specify
  model type, context window size, etc.
- ** CLI and API:** A simple CLI tool for iterating on service configurations
  and an API for changing the set of provisioned services.


Why not Docker or Docker Compose?
---------------------------------

Calling raw docker commands in a bash script got to be tough to maintain after
a time or two.  Docker compose ended up being the sort of thing where I would
need a large yaml file to configure a ton of very similar services or I would
need a different yaml file for every configuration I might want to use.

Ozwald uses a similar configuration language to that of Docker Compose, but it's
designed to be more expressive for defining AI-related services that perform
predictably across mixed hardware.

Why not call external APIs?
---------------------------

Sometimes calling an external API isn't an option for cost, privacy, or lack of
connectivity.

Ozwald is designed to be a standalone service that can run local LLMs and/or
other containers in a predictable way that takes into account the available
hardware.

With Ozwald, you will be able to build your own LLM-puppeteer apps in a way
that can accommodate the variety of hardware and runtime parameters developers
are asked to support.

Installation
------------

Install Ozwald and/or add ozwald to your project's Python dependencies:

```bash
pip install ozwald
```

Ozwald can used as an API by another project (like an orchestrator
service) or it can be invoked directly by end users.

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

realms:
  default:
    service-definitions:
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

2) **Set the mandatory environment variables**

The `OZWALD_SYSTEM_KEY` environment variable is required for authentication
between the CLI and the Provisioner API.

The `OZWALD_PROVISIONER` environment variable is required for the CLI to know
which provisioner to use (usually the name of the host or "localhost").

The `OZWALD_FOOTPRINT_DATA` environment variable is required for the CLI to
know where to store footprint data.

```bash
export OZWALD_SYSTEM_KEY="your-long-random-token"
export OZWALD_PROVISIONER="daneel"
export OZWALD_FOOTPRINT_DATA="/tmp/footprint.yml"
```

3) **Start the provisioner**

Use the CLI to spin up the provisioner network and containers:

```bash
ozwald start_provisioner
ozwald footprint_services my_service[some_profile][some_variety]
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
- `realms`: Groups for `networks` and `service-definitions`.
- `service-definitions[]`: Descriptions of services, including hardware
  `varieties` and runtime `profiles`.

CLI usage
---------

The `ozwald` command handles local development and inspection:

- `start_provisioner`: Start local provisioner network and containers.
- `stop_provisioner`: Stop provisioner containers.
- `status`: Check provisioner health.
- `list_active_services`: See what's currently running.
- `list_configured_services`: See list of configured services.
- `show_host_resources`: Inspect CPU/RAM/GPU/VRAM.
- `update_services`: Update the desired set of services.
- `footprint_services`: Measure resource consumption of configured services.
- `get_footprint_logs`: Show footprint logs.
- `get_service_launch_logs`: Show service launch logs.
- `get_service_logs`: Show service logs.

Example:
```bash
ozwald start_provisioner
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

Roadmap
-------

The provisioner is the first building block. The compass needle is pointing
toward filling the void between LLM-consumer and LLM-enabled service:

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
