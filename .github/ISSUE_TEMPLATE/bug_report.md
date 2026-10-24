---
name: Bug report
about: Report a reproducible behavior, checker, runner or workbench issue
title: "[Bug] "
---

<!-- Suspected vulnerability? Read SECURITY.md first. Never attach credentials,
private HDL, license material or unsanitized logs to this public issue. -->

## Problem

What happened, and what behavior did you expect?

## Reproduction

1. Revision / release:
2. Target family and installed fixture:
3. Contract ID/version and non-sensitive source/checker/contract hashes:
4. Exact typed stimulus / seed and resource budget:
5. Steps or command to reproduce:

## Environment

- OS / container image:
- Python / Node versions, if relevant:
- Simulator / compiler / cocotb / Vivado versions actually used:
- Execution status and design verdict (report separately):

## Evidence

Was this actual tool execution, mocked/scripted execution, a skipped test, or an observation without execution? Include expected versus observed values, the failure signature and a minimal sanitized excerpt where relevant. State whether exact replay reproduced it. Do not substitute a build-success message for a functional result.

## Scope and limitations

Which requirements were checked? What remains unknown?

- [ ] I removed secrets and private source/paths from this report and attachments.
- [ ] I identified the actual execution basis and did not label mocks as live results.
