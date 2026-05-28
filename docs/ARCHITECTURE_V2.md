# Aether-Guard V2 Architecture

## Production-Grade AI-Assisted Incident Response

**Status:** In Development (V2 Refactor)
**Last Updated:** April 29, 2026

---

## Executive Summary

Aether-Guard is **not** an "autonomous AI agent." It is an **AI-assisted incident response system** with verified safety guarantees, measurable accuracy, and human oversight.

**What it does:**
1. Detects SLO violations using Google's multi-burn-rate methodology
2. Performs hybrid root cause analysis (rules + heuristics + LLM)
3. Recommends remediation actions with confidence scoring
4. Executes low-risk actions automatically, high-risk actions with approval
5. Verifies outcomes and auto-rolls back failed remediations
6. Measures accuracy against ground truth and tracks MTTR improvements

**What it does NOT do:**
- Replace human SREs (it assists them)
- Make high-risk decisions without approval (ROLLBACK requires human confirmation)
- Trust LLM outputs blindly (rule-based fallbacks + policy gates)
- Operate without evaluation (every decision is measured and tracked)

---

## Design Principles

### 1. Safety First
- **Defense in depth:** Policy engine → Approval router → Verification engine → Auto-rollback
- **Blast radius control:** Canary rollouts, max pods affected limits
- **Human partnership:** High-risk actions require Slack approval (5min timeout)

### 2. Hybrid Intelligence
- **Rules for known patterns** (60% of incidents): OOM kills, restart loops, memory leaks
  - Latency: <50ms
  - Reliability: Works offline (no API dependency)
  - Confidence: 0.85-0.95 (empirically validated)

- **LLM for ambiguous cases** (40% of incidents): Complex multi-service failures
  - Latency: 2-5s
  - Reliability: Falls back to IGNORE on API failure
  - Confidence: 0.60-0.90 (prompt-dependent, not calibrated)

### 3. Measurable Impact
- **Evaluation framework:** Golden dataset of 100+ historical incidents
- **Incident replay:** Test prompt changes before deploying
- **Accuracy tracking:** Action accuracy, root cause accuracy, MTTR improvement
- **A/B testing:** Compare different models, prompts, confidence thresholds

### 4. Explainability
- **Chain-of-thought prompting:** LLM cites specific log lines and metrics
- **Audit logs:** Every decision logged with full context (signals → reasoning → action → outcome)
- **Post-mortem generation:** Blameless incident reports in Google SRE format

---

## Architecture Overview

```
                          ┌─────────────────────┐
                          │   SIGNAL LAYER      │
                          │  Prometheus + Logs  │
                          └──────────┬──────────┘
                                     │
                                     ▼
                          ┌─────────────────────┐
                          │  DETECTION ENGINE   │
                          │  Multi-burn-rate    │
                          │  SLO alerts         │
                          └──────────┬──────────┘
                                     │
                                     ▼
                    ╔════════════════════════════════╗
                    ║    RCA ENGINE (HYBRID)         ║
                    ║                                ║
                    ║  ┌──────────────────────────┐  ║
                    ║  │ 1. Rule-Based Triage    │  ║
                    ║  │    • Pattern matching    │  ║
                    ║  │    • 60% of incidents    │  ║
                    ║  │    • Confidence: 0.85-0.95│ ║
                    ║  └──────────┬───────────────┘  ║
                    ║             │                  ║
                    ║             ▼                  ║
                    ║  ┌──────────────────────────┐  ║
                    ║  │ 2. LLM Analysis (Claude) │  ║
                    ║  │    • Runs if rules < 0.85│  ║
                    ║  │    • Chain-of-thought    │  ║
                    ║  │    • Cites evidence      │  ║
                    ║  └──────────┬───────────────┘  ║
                    ║             │                  ║
                    ║             ▼                  ║
                    ║  ┌──────────────────────────┐  ║
                    ║  │ 3. Historical Context    │  ║
                    ║  │    • Similar incidents   │  ║
                    ║  │    • Past outcomes       │  ║
                    ║  └──────────────────────────┘  ║
                    ╚════════════════╤═══════════════╝
                                     │
                                     ▼
                          ┌─────────────────────┐
                          │  DECISION ENGINE    │
                          │  Policy matching    │
                          │  Risk assessment    │
                          └──────────┬──────────┘
                                     │
                                     ▼
                    ╔════════════════════════════════╗
                    ║      SAFETY LAYER              ║
                    ║                                ║
                    ║  ┌──────────────────────────┐  ║
                    ║  │ Policy Gate              │  ║
                    ║  │ • Action allowlist       │  ║
                    ║  │ • Time-of-day rules      │  ║
                    ║  │ • Rate limits            │  ║
                    ║  └──────────┬───────────────┘  ║
                    ║             │                  ║
                    ║             ▼                  ║
                    ║  ┌──────────────────────────┐  ║
                    ║  │ Approval Router          │  ║
                    ║  │ • Low risk → auto        │  ║
                    ║  │ • High risk → Slack      │  ║
                    ║  └──────────────────────────┘  ║
                    ╚════════════════╤═══════════════╝
                                     │
                                     ▼
                          ┌─────────────────────┐
                          │  EXECUTION ENGINE   │
                          │  Kubernetes API     │
                          │  Canary rollouts    │
                          └──────────┬──────────┘
                                     │
                                     ▼
                          ┌─────────────────────┐
                          │ VERIFICATION ENGINE │
                          │ • Metrics improved? │
                          │ • Auto-rollback?    │
                          └──────────┬──────────┘
                                     │
                                     ▼
                          ┌─────────────────────┐
                          │  EVALUATION ENGINE  │
                          │  Accuracy tracking  │
                          │  MTTR benchmarks    │
                          └─────────────────────┘
```

---

## Component Details

### RCA Engine (Hybrid)

**Rule-Based Triage** (`rules.py`)
- Pattern matching for known incident types
- Examples:
  - OOM kill detected → `RESTART` (confidence 0.95)
  - Restart loop detected → `ROLLBACK` (confidence 0.92)
  - Memory leak (alert + metrics + logs) → `RESTART` (confidence 0.88)
- **Fallback:** If no rule matches (confidence <0.85), escalate to LLM

**LLM Analysis** (`agent.py`)
- Claude Sonnet 4.5 with chain-of-thought prompting
- **Input:** Alert metadata + Prometheus metrics + container logs + rule hypothesis (if any)
- **Output:** JSON with `{root_cause, action, confidence, reasoning, evidence_citations}`
- **Safety:** 3 retry attempts, JSON schema validation, confidence threshold gate

**Historical Context** (`replay.py`)
- Vector DB search for similar past incidents
- Few-shot examples added to LLM prompt
- **Example:** "Last time MemorySaturation fired with similar logs, RESTART worked"

---

### Safety Layer

**Policy Engine** (`policy.py`)
- Deterministic rules for allowed/forbidden actions
- **Example policies:**
  - ❌ `MemoryLeak + SCALE` → FORBIDDEN (scaling makes leaks worse)
  - ✅ `CPUSaturation + SCALE` → ALLOWED (if traffic is high)
  - ✅ `BadDeployment + ROLLBACK` → ALLOWED but requires CRITICAL approval
- Time-aware: Stricter rules outside business hours (9 AM - 6 PM)
- Blast radius limits: Low risk = 1 pod, High risk = 5 pods

**Approval Router**
- **Low risk** (e.g., SCALE with confidence 0.90): Auto-approve
- **Medium risk** (e.g., RESTART with confidence 0.80): Slack notification, proceed after 60s
- **High/Critical risk** (e.g., ROLLBACK): Slack approval required (5min timeout)

---

### Verification Engine (`verification.py`)

**Post-Remediation Checks:**
1. Wait 2 minutes for metrics to stabilize
2. Re-query Prometheus: error rate, latency, throughput
3. Compare before/after:
   - Did error rate drop ≥50%?
   - Did latency improve ≥30%?
   - Are we back within SLO?
4. If NO improvement → auto-rollback
5. Log outcome for evaluation

**Example:**
```python
async with VerificationEngine() as verifier:
    before = await verifier.capture_snapshot()
    execute_restart(container)
    result = await verifier.verify(before, alert_type="SLOErrorBudgetBurn")
    if result.should_rollback:
        execute_rollback(container)
        log.error("Remediation failed, rolled back: %s", result.reason)
```

---

### Evaluation Engine (`replay.py`)

**Golden Dataset:**
- 100+ historical incidents with human-validated ground truth
- Stored in JSONL: `{incident_id, alert, metrics, logs, ground_truth_action, outcome}`

**Incident Replay:**
```bash
# Replay all incidents and measure accuracy
python -m agent.replay --replay-all

# Output:
# Action Accuracy:      87.5%
# Root Cause Accuracy:  82.3%
# Mean Confidence:      0.84
```

**A/B Testing:**
```python
# Test prompt change
results_baseline = await replayer.replay_all()
# ... modify prompt ...
results_variant = await replayer.replay_all()
print(f"Baseline: {compute_accuracy(results_baseline):.1%}")
print(f"Variant:  {compute_accuracy(results_variant):.1%}")
```

---

## Key Metrics

| Metric | Target | Current (V1) | V2 Goal |
|--------|--------|--------------|---------|
| **Action Accuracy** | >85% | Unknown (no eval) | 90% |
| **Root Cause Accuracy** | >80% | Unknown | 85% |
| **False Positive Rate** | <5% | Unknown | 3% |
| **False Remediation Rate** | <2% | Unknown | 1% |
| **MTTR (vs manual)** | -50% | Baseline | -60% |
| **Rule Coverage** | >60% | 0% (100% LLM) | 70% |
| **LLM Dependency** | <40% | 100% | 30% |

---

## Deployment Modes

### 1. Shadow Mode (Recommended for First 2 Weeks)
- Agent analyzes incidents but does NOT execute actions
- Logs what it *would* do for comparison to human SREs
- **Use case:** Build trust, collect ground truth data

### 2. Canary Mode
- Enable auto-remediation for LOW-risk actions only
- Require approval for MEDIUM/HIGH risk
- **Use case:** Gradual rollout

### 3. Production Mode
- Full auto-remediation with policy + approval gates
- High-risk actions still require approval
- **Use case:** After 2+ weeks of successful canary

---

## Safety Guarantees

| Risk | Mitigation |
|------|------------|
| **LLM API outage** | Rule-based fallbacks handle 60% of incidents |
| **Wrong action recommended** | Policy engine blocks forbidden actions (e.g., SCALE for memory leak) |
| **Remediation makes problem worse** | Verification engine auto-rolls back if metrics don't improve |
| **Remediation storm** | Cooldown (5min) + rate limits (max 3 actions/hour) |
| **3 AM rollback to bad code** | Time-of-day gate blocks high-risk actions outside business hours |
| **Cascading failure** | Blast radius limits (max 5 pods affected) + canary rollouts |

---

## Tradeoffs & Limitations

### Known Limitations

1. **LLM Confidence Not Calibrated**
   - Confidence scores are subjective, not probabilities
   - Mitigation: Treat as ranking tool, not decision gate

2. **Context Window Limited**
   - Only last 100 log lines analyzed
   - Mitigation: Semantic log search (future work)

3. **No Multi-Service RCA**
   - Currently targets single service (target-service)
   - Mitigation: Service mesh integration (future work)

4. **Historical Context Requires Data**
   - Few-shot learning needs 50+ incidents recorded
   - Mitigation: Start with rule-based, layer in LLM as data grows

### Accepted Tradeoffs

| What We Optimized For | What We Sacrificed |
|-----------------------|-------------------|
| **Safety** | Speed (verification adds 2min latency) |
| **Explainability** | Simplicity (hybrid engine is complex) |
| **Reliability** | LLM creativity (rules are rigid) |
| **Measurability** | Autonomy (requires ground truth data) |

---

## Migration from V1

### V1 Architecture (Current)
```
Alertmanager → Listener → Agent (100% LLM) → Docker API
```

**Problems:**
- ❌ No rule-based fallbacks
- ❌ No post-remediation verification
- ❌ No evaluation framework
- ❌ Confidence threshold is only safety mechanism
- ❌ In-memory state (lost on restart)

### V2 Architecture (Proposed)
```
Alertmanager → Listener → RCA (Rules + LLM) → Decision → Safety → Execution → Verification → Evaluation
```

**Improvements:**
- ✅ Rule-based triage (60% coverage)
- ✅ Post-remediation verification + auto-rollback
- ✅ Incident replay + accuracy tracking
- ✅ Policy engine + approval router
- ✅ Persistent state (Redis/PostgreSQL)

### Migration Steps

1. **Phase 1:** Add rule engine (non-breaking, runs in parallel with LLM)
2. **Phase 2:** Add policy engine (start with permissive policies)
3. **Phase 3:** Add verification engine (shadow mode: log only)
4. **Phase 4:** Add incident recorder (collect ground truth data)
5. **Phase 5:** Deploy V2 in canary mode (30 days)
6. **Phase 6:** Full production rollout

---

## Testing Strategy

### Unit Tests (New in V2)
- `test_policy.py`: 40+ test cases for policy matrix
- `test_verification.py`: Metrics comparison logic
- `test_rules.py`: Pattern matching accuracy
- `test_replay.py`: Incident serialization

### Integration Tests
- `test_hybrid_rca.py`: Rules → LLM fallback flow
- `test_safety_layer.py`: Policy + approval + execution
- `test_end_to_end.py`: Alert → remediation → verification

### Evaluation Tests
- `test_replay_accuracy.py`: Regression tests against golden dataset
- Target: >85% accuracy on 100+ historical incidents

---

## Future Work

### Short-Term (Q2 2026)
- [ ] Slack approval bot integration
- [ ] Redis for persistent cooldown state
- [ ] Semantic log search (Elasticsearch)
- [ ] Multi-service support

### Medium-Term (Q3 2026)
- [ ] Anomaly detection (Prophet baseline)
- [ ] Multi-agent consensus (Claude + GPT-4 + Gemini)
- [ ] Feedback loop (outcome-based few-shot learning)
- [ ] Kubernetes HPA integration

### Long-Term (Q4 2026)
- [ ] Service mesh observability (Istio)
- [ ] Multi-region deployment
- [ ] Active learning (RLHF for action selection)

---

## References

- **Google SRE Book:** Multi-burn-rate alerting, error budgets, blameless postmortems
- **Anthropic Prompt Engineering:** Chain-of-thought, citation, JSON mode
- **Production ML Systems:** Evaluation frameworks, shadow deployment, canary rollout

---

**End of Architecture Document**