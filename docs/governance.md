# Governance & compliance

Bubo is built for teams that have to *answer for* their AI usage — regulated
industries, security-conscious orgs, anyone whose reviewers need an audit trail.
The posture is **self-hosted and BYO-LLM**, so every control below runs on *your*
infrastructure and the data never leaves it.

!!! note "Honest boundary, up front"
    Bubo produces **auditable data, advisory flags, and a consumable risk
    signal**. It does **not** own CI or branch protection, so it cannot *block* a
    merge — your existing pipeline acts on what Bubo surfaces. Every governance
    feature here is advisory by design.

## Self-hosted by default

- Code, diffs, prompts, review data, and the SQLite audit store all live where you
  run Bubo.
- **BYO-LLM**: your model, your key, your data boundary. No third-party review SaaS
  ever sees your code.

## AI-code provenance (opt-in)

Tag every reviewed change with a **confidence-banded risk signal**, never a binary
verdict. Bubo reads the change's commit trailers (it already checks the change out)
and records:

- a **band** — `unknown` / `likely_ai` / `collaborative`;
- a **source** — `trailer` (deterministic) today; LLM `detection` is deliberately
  deferred, because cross-domain detection is fragile and Bubo leads with what's
  *declared*;
- the matched declaration lines and any **sensitive-path** hits.

Two honesty rules are built in: it's a **band, never a verdict**, and `unknown` is
the default — *absence of a declaration is not proof of human authorship*
(declared ≠ detected). Persisted **write-once** for audit integrity. Off by
default; enable `[governance].capture_provenance` — see the
[configuration reference](configuration.md).

## Rigor modulation + policy gates (opt-in)

- **Rigor modulation** injects a heightened-scrutiny directive (prioritize the
  security lens) into the review prompt when a change *escalates* — its band is in
  `escalate_bands` and it touches a sensitive path.
- **Policy gates** (`policy_mode` = `off` / `report-only` / `soft`) record a
  **write-once, advisory** governance *decision* (`flag` / `clear`) per change in a
  dedicated table — queryable, never merge-blocking.

Both are off by default — see the [configuration reference](configuration.md).

## Precision controls

- **Dispute-driven suppression** turns your team's accept/reject history into a
  per-repo precision lever: Bubo stops re-posting finding-classes you keep
  rejecting. See
  [dispute-driven suppression](configuration.md#dispute-driven-suppression).
- **Verify before posting** runs independent "is this real?" checks so weak
  findings never reach the author.

## The auditable report

`bubo report` (and the `get_governance_report` MCP tool) assemble a single,
**read-only**, on-prem report from the data Bubo already stores: a provenance
breakdown, the accept-vs-dispute rate, a noise trend, a bug-catch ROI proxy,
review latency, per-category dispute rates, no-findings acknowledgements, policy
decisions, and a per-change **audit trail** — as JSON or CSV. It never mutates
state, so it's safe to run from a monitoring cron. See
[the governance report](operate.md#governance-report).

## Observability

Bubo emits OpenTelemetry metrics (`llm_review.*`) for runs, findings, tokens,
cost, provenance, and governance decisions — so dashboards and alerting live
outside the poller. See [metrics & telemetry](telemetry.md).

## Compliance at a glance

| Need | How Bubo meets it |
|---|---|
| 🔒 **Data residency** | Self-hosted, BYO-LLM — nothing leaves your infrastructure. |
| 🧬 **AI-usage visibility** | Banded provenance per change, persisted write-once. |
| ⚖️ **Risk-proportionate review** | Rigor modulation on escalated changes. |
| 📝 **Auditable decisions** | Write-once policy decisions + per-change audit trail. |
| 📤 **Reporting / export** | `bubo report` JSON/CSV, strictly read-only. |
| 📊 **Monitoring** | OpenTelemetry metrics for every signal. |
