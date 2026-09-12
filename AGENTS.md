# Gquant working agreement

## Scope and entry

Gquant is a daily-bar research and manual decision-support system. It does not connect to brokers or place orders. Follow the active task's authorization and acceptance criteria; read applicable nested instructions before edits.

Read `PROJECT_STATE.md` for recovery, then the relevant sections of `README.md`, `docs/architecture.md`, `docs/validation.md` and the affected code. Resolve branch, SHA, PR, checks and active runs from GitHub. Resume matching work rather than replacing it. Keep mutable task details in the PR, not permanent instructions.

## Engineering

Use the smallest sufficient implementation and existing dependencies. Keep dependencies directed from application orchestration toward strategy, risk, execution, portfolio and data boundaries. Domain code must not depend on CLI, network or artifact publication. Do not duplicate economic logic in research runners.

Skills provide methods, not additional authorization or approval gates. Continue authorized work without repeated start confirmations. Verify affected behavior first, then expand on a stable candidate. Run architecture, input, publication, packaging and economic checks appropriate to the change; an intermediate test pass or checkpoint is not completion.

Treat a failed check, rejected candidate or invalidated hypothesis as feedback, not task completion. Diagnose it and continue with an evidence-supported alternative or a bounded check that distinguishes plausible causes, within the authorized scope and any task budget. If attempts add no information, reassess other authorized paths rather than repeat them. Finish when acceptance is met; if no safe authorized action remains, preserve progress and report the specific blocker or evidence gap without requiring proof that the goal is impossible. Do not weaken acceptance criteria, suppress failed evidence, or bypass safety, authorization or frozen contracts.

## Economic and data boundaries

Preserve close-time decisions and next-tradable-session execution, T+1 sellability, independent preset protection, order-own budgets, actual position slots, and cumulative participation budgets. Preserve account and risk state across the requested continuous interval.

Architecture, CI and documentation work must not change economic behavior without explicit scope. Never weaken frozen thresholds, windows, principals, inputs, metrics or evidence identities to pass a check. Keep diagnostic failures separate from accepted results; a diagnostic subset is not formal acceptance. Do not rewrite immutable evidence as results of a different source or configuration.

Validate inputs before use and publish complete outputs transactionally. Failed requests must not replace the last successful output. Network data is not a frozen research snapshot until validated and explicitly selected.

## Git and delivery

Use a feature branch for implementation unless a direct main update is explicitly authorized. Preserve repository protections and required review/checks. Commit coherent changes, push authorized work and verify the remote SHA. Do not reset, clean, rebase, force-push, discard unrelated work, change permissions or credentials, or perform live trading.

Review the complete task diff, resolve material findings, and verify the final source identity. Reuse prior evidence only when covered code, configuration, data and environment remain equivalent; never present an old check as a new HEAD's check. Report completion, verification, economic failures and remaining limitations separately.

## Documentation

User guides describe the implemented system, commands, parameters and limitations. Keep them free of release narratives and redundant implementation copies. Raw research evidence retains its provenance in the evidence directory and Git history. Explain units, percentages and technical terms when first introduced.
