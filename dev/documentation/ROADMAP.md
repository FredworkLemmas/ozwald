Project: Ozwald
Author: Fred McDavid

Overview
--------
Ozwald aims to make it easy for small and medium businesses to build and
operate AI-enabled systems. The project starts as a reliable provisioner for
containerized services (with a strong focus on vLLM) and grows into an
orchestrator and, ultimately, a developer-friendly framework similar in
spirit to Rails or Django, but for AI systems composed of containerized
services.

Guiding principles
------------------
- Developer focus: simple defaults, clear errors, sensible abstractions.
- Pragmatic UX: CLIs and APIs that match how people really work day to day.
- Operability first: footprinting, resource checks, and safe failure modes.
- Open building blocks: no lock-in, reuse standard container tooling.
- Security by design: secrets, isolation, and least privilege from the start.
- Observability: logs, metrics, and traces embedded into workflows.

Release phases
--------------
1) Provisioner for vLLM services (current focus)
2) Orchestrator for vLLM services (multi-host, multi-node)
3) General-purpose orchestrator (beyond vLLM)
4) Framework for AI systems (Rails-like developer experience)

Milestone 0: Provisioner GA (v0)
--------------------------------
Scope
- CLI and Python API to provision and de-provision containerized services.
- Service configuration by variety and profile.
- Host footprinting to validate resource availability before provisioning.
- GPU support: AMD and NVIDIA; CPU-only also an option.
- Shared network for provisioned services.

Acceptance criteria
- functionality: footprint, provision, de-provision, GPU support for AMD/NVidia.
- All functionality available via API or CLI.
- Minimal quickstart documentation and examples are published.

Milestone 0.x: Provisioner enhancements
--------------------------------------
Scope
- Secret management.
- Encrypted filesystems.
- Service groups and private networks that allow for multiple service groups to
  be run by the same provisioner at the same time, but in isolation from each
  other.
- Pre- and post- hooks via webhooks for provisioning and de-provisioning.

Milestone 1: Multi-host orchestrator
---------------------------------------------
Scope
- Central service that coordinates multiple provisioners running on separate
  hosts.
- "Modes" of operation: groups of service,variety,profile instances with queues
- API Reporter: provide callers with current provisioner service info

Milestone 2: API Gateway
-----------------------------------------
Scope
- API Gateway

Milestone 3: Scaled services
---------------------------------------------
Scope
- API Gateway is also a load balancer for scaled services.

Milestone 3.x: Auto-scaled services
---------------------------------------------
Scope
- API Gateway updated to allow for auto-scaled services.
- Scale within pre-allocated resources.
- Auto-provision and de-provision cloud services.

Milestone 4: Framework experience for AI systems
------------------------------------------------------
Scope
- A batteries-included developer workflow: project scaffolding, environments,
  evaluation harnesses, agent patterns, and reproducible pipelines.
- Opinionated framework with drop-in scalability; composable primitives.

Notes
- `ozwald new` scaffolds a project with tests, dev scripts, and examples.
- Built-in evaluation suites and dataset adapters enable continuous
  improvement.
- Agent templates ship with test scaffolds and guidance for safe iteration.
- Tutorial apps maintained and kept in sync with releases.
- DX metrics tracked: time-to-first-success, docs task success rates.

Cross-cutting tracks
--------------------
Security
- Secrets management: infisical with account/host/provisioner keys.
- Encrypted filesystems: Encfs

Observability
- logging
- metrics

Performance and reliability
- TBD

Developer experience
- TBD

Documentation and examples
- Quickstarts for AMD, NVIDIA, and CPU-only setups.
- End-to-end examples: single-host provisioner; multi-host orchestrator; API
  gateway with auth and filtering.
- Security and compliance guides for regulated environments.

Versioning
- Git-powered filesystem usage and deploys

Testing strategy
- First-class integration tests for all components.
- Opinionated evaluation harnesses for common use cases.

Success indicators
- M0: CLI and API are stable and usable. Primitives are working, tested and documented.
- Mn: TBD

Notes
-----
Roadmap should evolve.
