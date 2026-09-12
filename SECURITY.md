# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

We take security seriously. If you discover a security vulnerability in Agent Runtime, please report it responsibly.

### How to Report

**Do not open a public GitHub issue for security vulnerabilities.**

Instead, use the repository's [private vulnerability reporting](https://github.com/SciMigo/agent-runtime/security/advisories/new).

Include the following information:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fixes (optional)

We will acknowledge the report, assess its impact, and coordinate disclosure
through the private advisory.

### Scope

The following are in scope for security reports:

- Authentication and authorization bypasses
- Remote code execution vulnerabilities
- Token leakage or theft vectors
- Cross-origin security issues
- Privilege escalation

The following are **out of scope**:

- Denial of service (the runtime is designed for local use)
- Issues requiring physical access to the machine
- Issues in dependencies (please report to the upstream project)
- Social engineering attacks

### Recognition

We appreciate security researchers who help keep Agent Runtime secure. With your permission, we will acknowledge your contribution in our release notes.

## Security Best Practices

When using Agent Runtime:

1. **Only pair trusted origins** - The runtime executes arbitrary code on your behalf
2. **Review pairing requests carefully** - Verify the origin before approving
3. **Revoke unused pairings** - Use `agent-runtime pairing list` and revoke old entries
4. **Keep the runtime updated** - Install security updates promptly
5. **Use separate labs for sensitive work** - Isolate different projects

See [docs/security.md](docs/security.md) for the full security model.
