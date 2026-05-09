# Contributing to Cube Sandbox

Thank you for your interest in contributing to Cube Sandbox! This document provides guidelines and information to help you get started.

## Ways to Contribute

- **Report bugs** — Open a [GitHub Issue](https://github.com/tencentcloud/CubeSandbox/issues) with steps to reproduce.
- **Request features** — Describe your use case and proposed solution in an Issue.
- **Improve documentation** — Fix typos, clarify explanations, or add examples.
- **Submit code** — Fix bugs, implement features, or improve performance.

## Getting Started

### Prerequisites

- Linux with KVM support (x86_64)
- Docker
- Go 1.21+
- Rust 1.75+ (with `x86_64-unknown-linux-musl` target)
- protoc (Protocol Buffers compiler)

### Build Environment

Cube Sandbox provides a Docker-based builder image for a consistent build environment:

```bash
# Build the builder image
make builder-image

# Start an interactive shell inside the builder
make builder-shell

# Build all Go components (CubeMaster, Cubelet, network-agent)
make all

# Build individual components
make cubemaster
make cubelet
make agent
make shim
```

See the [Makefile](./Makefile) for the full list of build targets.

### Project Structure

| Directory | Language | Description |
|---|---|---|
| `CubeAPI/` | Rust | E2B-compatible REST API gateway |
| `CubeMaster/` | Go | Orchestration scheduler and cluster management |
| `Cubelet/` | Go | Per-node sandbox lifecycle agent |
| `CubeProxy/` | Go | Reverse proxy for sandbox request routing |
| `CubeShim/` | Rust | containerd shim bridging to KVM MicroVM |
| `agent/` | Rust | In-guest daemon running inside each sandbox |
| `hypervisor/` | Rust | KVM-based MicroVM manager (Cloud Hypervisor fork) |
| `mvs/` / `CubeNet/` | Go | CubeVS eBPF-based network isolation |
| `network-agent/` | Go | Network management service |
| `deploy/` | Shell | Deployment scripts and guest image tooling |
| `examples/` | Python | SDK examples and end-to-end scenarios |
| `docs/` | Markdown | VitePress documentation site (EN + ZH) |

## Submitting a Pull Request

1. **Fork** the repository and create a feature branch from `main`.
2. **Make your changes** — keep commits focused and atomic.
3. **Test** — make sure existing tests and linters still pass.
4. **Add tests** — add focused test coverage when behavior changes.
5. **Document** — update relevant docs if your change affects user-facing behavior.
6. **Open the PR** — describe the motivation and what the change does. Link related Issues.

### Commit Messages

Write clear commit messages that explain *why* the change was made:

```
component: short summary of the change

Longer description explaining the motivation, trade-offs, or context.
Closes #123
```

Prefix the summary with the component name (e.g., `cubeapi:`, `cubelet:`, `docs:`, `shim:`).

### Code Style

- **Go** — follow standard `gofmt` formatting and project conventions.
- **Rust** — follow `rustfmt` and `clippy` recommendations.
- **Documentation** — use clear, concise language. Both English and Chinese docs should be kept in sync.

## Reporting Security Issues

If you discover a security vulnerability, please report it responsibly via [GitHub Security Advisories](https://github.com/tencentcloud/CubeSandbox/security/advisories) rather than opening a public Issue.

## License

By contributing to Cube Sandbox, you agree that your contributions will be licensed under the [Apache License 2.0](./LICENSE).


## AI-Generated Code Policy

AI agents MUST NOT add Signed-off-by tags. Only humans can legally certify the Developer Certificate of Origin (DCO). The human submitter is responsible for:

- Reviewing all AI-generated code
- Ensuring compliance with licensing requirements
- Adding their own Signed-off-by tag to certify the DCO
- Taking full responsibility for the contribution