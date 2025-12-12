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
- Operability first: profiling, resource checks, and safe failure modes.
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
- Host profiling to validate resource availability before provisioning.
- GPU support: AMD and NVIDIA; CPU-only also an option.
- Shared network for provisioned services.

Acceptance criteria
- `ozwald update_services` provisions a set of configured services, refusing to
  start if insufficient resources are available or if target resources do not
  exist.
- `ozwald profile <service>[variety][profile] | ALL` profiles services.
- All functionality available via API or CLI.
- Minimal quickstart documentation and examples are published.

Quality gates
- Unit and integration tests cover provisioning flows and failure paths.
- Basic logging and structured error messages are present.
- Tested on at least one AMD GPU host, one NVIDIA GPU host, and one CPU host.

Milestone 0.x: Provisioner enhancements
--------------------------------------
Scope
- Secret management.
- Encrypted filesystems.
- Pre- and post- hooks via webhooks for provisioning and de-provisioning.

Acceptance criteria
- Containers can be run with credentials that can be used to access secrets.
- Containers can use secrets to decrypt encrypted volumes.
- Webhooks can be configured per-service and per-event.

Quality gates
- Threat model documented for secrets and encryption features.
- Integration tests exercise webhooks and failure handling.

Milestone 1: Multi-host orchestrator for vLLM
---------------------------------------------
Scope
- Central service that coordinates multiple provisioners running on separate
  hosts.
- Service registry, placement strategy, and health management.
- Declarative desired-state model and drift detection.

Acceptance criteria
- Operators can declare desired services across hosts; the orchestrator
  converges the fleet to the desired state and reports status.
- Placement accounts for GPU type, memory, and availability.
- Health checks and auto-recovery for crashed or unhealthy services.
- API and CLI expose listing, scaling, and rolling update actions.

Quality gates
- Simulated multi-host testbed and scenario tests (drain, fail, reschedule).
- Pluggable scheduler interface with at least two strategies implemented.
- Audit logs for actions and state changes.

Milestone 2: General-purpose orchestrator
-----------------------------------------
Scope
- Extend beyond vLLM to general container workloads.
- Policy engine for constraints, quotas, and access controls.
- Namespaces/projects with role-based permissions.

Acceptance criteria
- Non-LLM services can be declared, placed, and managed with the same API.
- Policies can restrict resource usage and control who can modify what.
- Backups and disaster recovery procedures for orchestrator state.

Quality gates
- Conformance tests for policy evaluation and enforcement.
- Backup and restore drills documented and automated.

Milestone 3: Gateway
--------------------
Scope
- Single entry point per host or per cluster for accessing provisioned and
  orchestrated services.
- Request-level features: routing, authentication, and output filtering.
- Optional evaluation hooks to enforce data access policies before responses
  leave the system.

Acceptance criteria
- Unified API endpoint proxies requests to services with auth and rate limits.
- Output filtering and redaction rules can be configured and audited.
- Integration with orchestrator health for adaptive routing.

Quality gates
- Security review for the proxy layer and dependency hardening.
- Latency and throughput benchmarks under representative workloads.

Milestone 4: Framework experience for AI systems
-----------------------------------------------
Scope
- A batteries-included developer workflow: project scaffolding, environments,
  evaluation harnesses, agent patterns, and reproducible pipelines.
- Opinionated defaults with escape hatches; composable primitives.

Acceptance criteria
- `ozwald new` scaffolds a project with tests, dev scripts, and examples.
- Built-in evaluation suites and dataset adapters enable continuous
  improvement.
- Agent templates ship with test scaffolds and guidance for safe iteration.

Quality gates
- Tutorial apps maintained and kept in sync with releases.
- DX metrics tracked: time-to-first-success, docs task success rates.

Cross-cutting tracks
--------------------
Security
- Secrets management backends: env file, keyring, and cloud KMS adapters.
- Supply chain: signed images, SBOM generation, and image provenance checks.
- Least-privilege defaults for containers and host capabilities.

Observability
- Structured logging with correlation IDs across CLI, API, and services.
- Metrics for provisioning times, failures, retries, and resource usage.
- Tracing across orchestrator, gateway, and managed services where possible.

Performance and reliability
- Cold start time SLOs per service type; regression alerts.
- Placement efficiency metrics: GPU memory waste and binpacking efficacy.
- Chaos experiments for container crashes and host network splits.

Developer experience
- Clear error messages with actionable remedies and links to docs.
- Config schema validation with precise diagnostics and IDE support.
- Sandbox mode for local iteration with minimal host requirements.

Documentation and examples
- Quickstarts for AMD, NVIDIA, and CPU-only setups.
- End-to-end examples: single-host provisioner; multi-host orchestrator; API
  gateway with auth and filtering.
- Security and compliance guides for regulated environments.

Versioning and compatibility
- Semantic versioning with migration notes and guarded deprecations.
- Compatibility matrix for host OS, container runtimes, and GPU drivers.

Testing strategy
- Unit tests for config parsing, profiling, and command execution.
- Integration tests for provisioning flows and orchestrator control loops.
- Scenario suites for failover, scaling, and policy enforcement.

Success indicators
- Time from config to first running service under 5 minutes.
- Clear rejection with rationale when resources are insufficient.
- Stable multi-host orchestration under common failure scenarios.

Near-term priorities checklist
------------------------------
- [ ] Finalize v0 CLI/API for provision/de-provision with profiles/varieties.
- [ ] Robust AMD/NVIDIA detection and CPU fallback.
- [ ] Resource profiling with actionable messages and exit codes.
- [ ] Initial docs and quickstart examples.
- [ ] Encrypted volumes and secrets injection.
- [ ] Webhooks for pre/post provision/deprovision.

Notes
-----
This roadmap is a living document. It will evolve with user feedback and
real-world usage. Each milestone should ship independently valuable features
while laying the groundwork for the subsequent phase.
