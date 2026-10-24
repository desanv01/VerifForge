# Security policy

VerifForge currently targets local development with curated fixtures. The provided single local identity, loopback bindings and development database defaults are not a public-hosting security configuration. A public source repository does not imply a deployed public execution service.

## Report a vulnerability privately

Do not post exploit details, credentials, private HDL, license material or unsanitized evidence in a public issue or pull request.

If GitHub's **Security → Report a vulnerability** option is available on this repository, use it for a private report. If that option is unavailable, open an issue containing only a request for a private security contact; wait for a private channel before sharing details. No private-reporting endpoint, monitored email address, response-time guarantee or bug-bounty program is asserted here.

A useful private report includes:

- Affected revision and component, plus the execution environment and tool versions.
- Minimal reproduction and the trust boundary crossed.
- Expected versus observed behavior and potential impact.
- Sanitized logs or artifact identifiers, with secrets and private source removed.

## Sensitive boundaries

The planner must not change reviewed checkers, issue arbitrary commands or enlarge budgets. Runners must isolate curated execution, remove service credentials from subprocess environments, enforce cancellation and resource bounds, and prevent late results from becoming accepted findings. Artifact paths, ownership and immutable identities must be validated before replay or export.

Treat imported logs, model responses and project text as untrusted data. A hash is a consistency check, not proof of authenticity when an attacker can replace both content and recorded hashes. A successful build or passing test is limited to its stated checks.

If you accidentally disclose a credential, revoke or rotate it with the relevant provider and notify the maintainer privately. Removing it from a file alone does not invalidate an exposed credential. Avoid including the credential in the report.

There is no supported production release or long-term security maintenance schedule yet. Security fixes target the current development branch; version support will be documented when releases exist.
