# Documentation

Documentation of Dzdia Newsroom Engine, organized by what you want to do. Start with the [architecture overview](architecture/overview.md) if you are new to the project.

## Understand the system

- [Architecture overview](architecture/overview.md): context, containers, request lifecycle, code organization, invariants, target architecture.
- [Job queue](architecture/job-queue.md): delivery guarantees, leases, heartbeat, fencing tokens, retries, shutdown.
- [Data model](architecture/data-model.md): tables, indexes, job payloads, retention, planned tables.
- [Security](architecture/security.md): assets, trust boundaries, threats and mitigations, known gaps.

## Know why it is built this way

- [Architecture decision records](adr/README.md): one file per decision, with the alternatives that were rejected.
- [Design review, 2026-10-28](reviews/2026-10-28-design-review.md): findings, their status and the remediation plan.

## Run and operate it

- [Configuration](operations/configuration.md): every environment variable, and which service receives which secret.
- [Deployment](operations/deployment.md): local stack; production VM installation, deployment, rollback and backups.
- [Runbook](operations/runbook.md): logs, queue inspection, incidents, secret rotation.

## Develop it

- [Getting started](development/getting-started.md): setup, running the processes, repository layout.
- [Testing](development/testing.md): test layout, integration tests, CI, principles.
- [Conventions](development/conventions.md): standards, error taxonomy, how to add a job kind, a migration, a variable or an ADR.

## Evaluate it

- [Baseline v1](evals/baseline-v1.md): how the legacy bot's hallucinations are measured, and the current results.

## Plan

- [Roadmap](roadmap.md): weekly plan and next steps.
- [Status](STATUS.md): detailed working notes for the current session (in French).
