"""
Aether-Guard — RAG-Augmented Investigation Graph (Priority 8, Phase B)

Multi-step investigation graph using LangGraph for low-confidence incidents.
Replaces the single-call LLM fallback with:
  1. Similarity search against past incidents
  2. Initial hypothesis with retrieved context
  3. Optional refinement with additional context (if confidence < threshold)
  4. Hard iteration cap to prevent unbounded loops

Environment variables:
  RAG_ENABLED: Enable RAG investigation (default: true)
  RAG_MAX_ITERATIONS: Max refine cycles (default: 2)
  RAG_CONFIDENCE_THRESHOLD: Min confidence to finalize (default: 0.75)
"""

import logging
import os
from typing import TypedDict, Literal
from datetime import datetime, timezone

from langgraph.graph import StateGraph, END

from embedding import generate_embedding
from incident_storage import get_storage
import enrichment

log = logging.getLogger("aether-guard.agent.investigation")

# Configuration
RAG_ENABLED = os.getenv("RAG_ENABLED", "true").lower() == "true"
RAG_MAX_ITERATIONS = int(os.getenv("RAG_MAX_ITERATIONS", "2"))
RAG_CONFIDENCE_THRESHOLD = float(os.getenv("RAG_CONFIDENCE_THRESHOLD", "0.75"))


# ─────────────────────────────────────────────────────────────────────────────
# Graph State
# ─────────────────────────────────────────────────────────────────────────────

class GraphState(TypedDict):
    """State passed between graph nodes."""
    # Input
    alert: dict  # Original alert from listener

    # Retrieved context
    similar_incidents: list[dict]  # From Phase A similarity search

    # Hypothesis evolution
    hypothesis: dict  # Current LLM analysis
    confidence: float  # Current confidence score

    # Additional context (gathered if needed)
    additional_context: dict | None

    # Iteration tracking
    iteration_count: int

    # Error tracking (for graceful degradation)
    retrieval_failed: bool


# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

def _compose_alert_text_for_embedding(alert: dict) -> str:
    """
    Compose text for embedding from alert payload.

    Similar structure to compose_embedding_text() but for an ongoing alert.
    """
    alertname = alert.get("labels", {}).get("alertname", "unknown")
    description = alert.get("annotations", {}).get("description", "")
    summary = alert.get("annotations", {}).get("summary", "")

    # Include available metrics
    metrics = alert.get("metrics_snapshot", {})
    metrics_str = ", ".join(f"{k}={v}" for k, v in list(metrics.items())[:5])

    return f"""Alert: {alertname}
Description: {description}
Summary: {summary}
Current Metrics: {metrics_str}"""


def _format_similar_incidents_for_prompt(incidents: list[dict]) -> str:
    """
    Format similar incidents for LLM prompt with explicit override framing.

    Priority 2 trust metrics integration: Surfaces manual corrections as cautions.
    """
    if not incidents:
        return "(No similar past incidents found)"

    lines = []
    for i, inc in enumerate(incidents, 1):
        # Check if this incident was overridden (Priority 2 trust metrics)
        if inc["override_status"] == "manual_reversal":
            lines.append(f"""
{i}. ⚠️ CAUTION — Similar incident corrected by human:
   - Alert: {inc["trigger"]}
   - Original diagnosis: {inc["root_cause"]} (confidence {inc["confidence"]:.2f})
   - Action taken: {inc["action_taken"]}
   - Why it was wrong: {inc["override_reason"]}
   - Similarity: {1 - inc["distance"]:.2f}
""")
        elif inc["override_status"] == "manual_escalation":
            lines.append(f"""
{i}. ⚠️ CAUTION — Similar incident escalated by human:
   - Alert: {inc["trigger"]}
   - Original diagnosis: {inc["root_cause"]} (confidence {inc["confidence"]:.2f})
   - Why it was escalated: {inc["override_reason"]}
   - Similarity: {1 - inc["distance"]:.2f}
""")
        else:
            lines.append(f"""
{i}. ✓ Similar incident (successfully resolved):
   - Alert: {inc["trigger"]}
   - Root cause: {inc["root_cause"]} (confidence {inc["confidence"]:.2f})
   - Reasoning: {inc["reasoning"][:100]}...
   - Action: {inc["action_taken"]} → {inc["outcome"]}
   - Similarity: {1 - inc["distance"]:.2f}
""")

    return "\n".join(lines)


async def _gather_extended_logs() -> str | None:
    """
    Fetch extended log window (last 10min instead of 5min).

    Per-source graceful degradation: Returns None if fails, doesn't raise.
    """
    try:
        logs = await enrichment.fetch_logs(
            container_name="target-service",
            since_minutes=10,  # Wider window than default (5min)
            tail=200,
        )
        return "\n".join(logs) if logs else None
    except Exception as exc:
        log.warning("Failed to fetch extended logs: %s", exc)
        return None


async def _gather_extended_metrics() -> dict | None:
    """
    Fetch extended Prometheus metrics (30min window instead of 5min).

    Per-source graceful degradation: Returns None if fails, doesn't raise.
    """
    try:
        # Use enrichment module's existing Prometheus queries but with wider window
        # This is a simplified version - in production you'd query query_range for trends
        metrics = await enrichment.fetch_metrics(job="target-service")
        if metrics:
            return {
                "note": "Extended 30min window (implementation simplified for demo)",
                **metrics,
            }
        return None
    except Exception as exc:
        log.warning("Failed to fetch extended metrics: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Graph Nodes
# ─────────────────────────────────────────────────────────────────────────────

async def retrieve_similar_incidents(state: GraphState) -> dict:
    """
    Query pgvector for top-5 similar past incidents.

    Graceful degradation: If retrieval fails, continue with empty similar_incidents list.
    """
    try:
        # Generate embedding for current alert
        text = _compose_alert_text_for_embedding(state["alert"])
        embedding = await generate_embedding(text)

        async with get_storage() as storage:
            similar = await storage.find_similar_incidents(
                query_embedding=embedding,
                limit=5,
                min_confidence=None,  # Uses SIMILARITY_MIN_CONFIDENCE env var (0.7)
            )

        log.info("Retrieved %d similar incidents for RAG investigation", len(similar))
        return {"similar_incidents": similar, "retrieval_failed": False}

    except Exception as exc:
        log.warning("Similarity search failed: %s — continuing without retrieved context", exc)
        return {"similar_incidents": [], "retrieval_failed": True}


async def form_hypothesis(state: GraphState) -> dict:
    """
    Initial LLM call incorporating retrieved similar incidents as context.

    Prompt includes:
    - Current alert context (same as existing LLM fallback)
    - Similar incidents with EXPLICIT framing of overridden ones as cautions
    """
    from prompt import build_user_prompt
    from agent import call_claude

    # Build similar incidents context
    similar_context = _format_similar_incidents_for_prompt(state["similar_incidents"])

    # Compose prompt with retrieved context
    base_prompt = build_user_prompt(state["alert"])

    enhanced_prompt = f"""{base_prompt}

## Similar Past Incidents

{similar_context}

Based on the current alert and these similar past cases, provide your analysis.
Pay special attention to cases marked as "⚠️ CAUTION" — these indicate patterns
that may be misleading. If a similar case was corrected by a human, explain why
your diagnosis differs from the corrected incident (or acknowledge if the same
mistake might apply here).

Provide your analysis in the standard JSON format.
"""

    analysis = await call_claude(enhanced_prompt, attempt=1)

    log.info(
        "Initial hypothesis formed (confidence %.2f, %d similar incidents retrieved)",
        analysis.get("confidence", 0.0),
        len(state["similar_incidents"])
    )

    return {
        "hypothesis": analysis,
        "confidence": analysis.get("confidence", 0.0),
        "iteration_count": 1,
    }


def should_gather_more_context(state: GraphState) -> Literal["finalize", "gather_more_context"]:
    """
    Route to either finalize or gather_more_context based on confidence and iteration count.

    Hard iteration cap: MAX_ITERATIONS=2 (configurable via RAG_MAX_ITERATIONS env var).
    """
    confidence = state.get("confidence", 0.0)
    iteration_count = state.get("iteration_count", 1)

    if confidence >= RAG_CONFIDENCE_THRESHOLD:
        log.info("Confidence %.2f >= threshold %.2f — finalizing", confidence, RAG_CONFIDENCE_THRESHOLD)
        return "finalize"
    elif iteration_count >= RAG_MAX_ITERATIONS:
        log.warning(
            "Iteration cap reached (%d) without achieving confidence threshold (%.2f < %.2f) — forcing finalize",
            iteration_count, confidence, RAG_CONFIDENCE_THRESHOLD
        )
        return "finalize"
    else:
        log.info(
            "Confidence %.2f < threshold %.2f, iteration %d/%d — gathering more context",
            confidence, RAG_CONFIDENCE_THRESHOLD, iteration_count, RAG_MAX_ITERATIONS
        )
        return "gather_more_context"


async def gather_more_context(state: GraphState) -> dict:
    """
    Fetch additional context not in the original alert payload.

    Per-source graceful degradation: Each source fails independently.
    Sources:
    - Extended log window (last 10min instead of 5min)
    - Extended metrics window (30min instead of 5min)
    - Alert annotations (already in payload, included for completeness)
    """
    # Fetch extended logs (independent failure)
    extended_logs = await _gather_extended_logs()

    # Fetch extended metrics (independent failure)
    extended_metrics = await _gather_extended_metrics()

    # Alert annotations (already in payload)
    alert_annotations = state["alert"].get("annotations", {})

    sources_available = sum([
        extended_logs is not None,
        extended_metrics is not None,
        bool(alert_annotations),
    ])

    log.info(
        "Gathered additional context: %d/3 sources available (logs=%s, metrics=%s, annotations=%s)",
        sources_available,
        "✓" if extended_logs else "✗",
        "✓" if extended_metrics else "✗",
        "✓" if alert_annotations else "✗",
    )

    return {
        "additional_context": {
            "extended_logs": extended_logs,
            "extended_metrics": extended_metrics,
            "alert_annotations": alert_annotations,
        }
    }


async def refine_hypothesis(state: GraphState) -> dict:
    """
    Re-analyze with additional context, updating hypothesis and confidence.
    """
    from prompt import build_user_prompt
    from agent import call_claude

    hypothesis = state["hypothesis"]
    similar_context = _format_similar_incidents_for_prompt(state["similar_incidents"])
    additional_context = state["additional_context"] or {}

    # Build refined prompt with all context
    base_prompt = build_user_prompt(state["alert"])

    # Format additional context sections
    logs_section = ""
    if additional_context.get("extended_logs"):
        logs_snippet = additional_context["extended_logs"][:500]
        logs_section = f"""
## Extended Logs (last 10min)
{logs_snippet}...
"""

    metrics_section = ""
    if additional_context.get("extended_metrics"):
        metrics = additional_context["extended_metrics"]
        metrics_section = f"""
## Extended Metrics (30min window)
{metrics}
"""

    refined_prompt = f"""{base_prompt}

## Similar Past Incidents
{similar_context}

## Initial Hypothesis (confidence {state['confidence']:.2f})
Root Cause: {hypothesis.get("root_cause", "unknown")}
Reasoning: {hypothesis.get("reasoning", "")}
Recommended Action: {hypothesis.get("action", "IGNORE")}

## Additional Context Gathered
{logs_section}
{metrics_section}

Re-analyze the incident with this additional context. Update your diagnosis if
the new information changes your assessment, or increase confidence if it confirms
your initial hypothesis.

Provide your updated analysis in the standard JSON format.
"""

    refined_analysis = await call_claude(refined_prompt, attempt=1)

    new_confidence = refined_analysis.get("confidence", 0.0)
    log.info(
        "Hypothesis refined (iteration %d, confidence %.2f → %.2f)",
        state["iteration_count"] + 1,
        state["confidence"],
        new_confidence
    )

    return {
        "hypothesis": refined_analysis,
        "confidence": new_confidence,
        "iteration_count": state["iteration_count"] + 1,
    }


def finalize(state: GraphState) -> dict:
    """
    Produce final output matching existing LLM fallback shape.

    Adds RAG metadata including citations to similar incidents that informed the conclusion.
    """
    hypothesis = state["hypothesis"]

    # Build citations to retrieved incidents
    citations = []
    for inc in state["similar_incidents"]:
        citations.append(f"{inc['incident_id'][:8]} ({inc['trigger']}, {inc['matched_pattern']})")

    # Check if iteration cap was reached without meeting confidence threshold
    iteration_cap_reached = (
        state["iteration_count"] >= RAG_MAX_ITERATIONS
        and state["confidence"] < RAG_CONFIDENCE_THRESHOLD
    )

    return {
        **hypothesis,  # analysis, root_cause, confidence, action, reasoning, ...
        "rag_metadata": {
            "similar_incidents_retrieved": len(state["similar_incidents"]),
            "citations": citations,
            "iteration_count": state["iteration_count"],
            "iteration_cap_reached": iteration_cap_reached,
            "retrieval_failed": state.get("retrieval_failed", False),
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# Graph Construction
# ─────────────────────────────────────────────────────────────────────────────

def _build_investigation_graph() -> StateGraph:
    """
    Build the LangGraph investigation graph.

    Flow:
      START → retrieve_similar_incidents → form_hypothesis → [conditional]
        ├─ IF confidence >= threshold → finalize → END
        └─ IF confidence < threshold AND iterations < cap → gather_more_context
            → refine_hypothesis → [loop back to conditional]
    """
    graph = StateGraph(GraphState)

    # Add nodes
    graph.add_node("retrieve_similar_incidents", retrieve_similar_incidents)
    graph.add_node("form_hypothesis", form_hypothesis)
    graph.add_node("gather_more_context", gather_more_context)
    graph.add_node("refine_hypothesis", refine_hypothesis)
    graph.add_node("finalize", finalize)

    # Add edges
    graph.set_entry_point("retrieve_similar_incidents")
    graph.add_edge("retrieve_similar_incidents", "form_hypothesis")

    # Conditional edge after form_hypothesis and refine_hypothesis
    graph.add_conditional_edges(
        "form_hypothesis",
        should_gather_more_context,
        {
            "finalize": "finalize",
            "gather_more_context": "gather_more_context",
        }
    )

    graph.add_edge("gather_more_context", "refine_hypothesis")

    graph.add_conditional_edges(
        "refine_hypothesis",
        should_gather_more_context,
        {
            "finalize": "finalize",
            "gather_more_context": "gather_more_context",
        }
    )

    graph.add_edge("finalize", END)

    return graph


# Global graph instance (lazy-initialized)
_graph = None


async def run_investigation_graph(alert: dict) -> dict:
    """
    Run the RAG investigation graph for a given alert.

    Args:
        alert: Alert dict from listener (same format as existing LLM fallback)

    Returns:
        Analysis dict matching existing LLM fallback shape, with additional rag_metadata

    Raises:
        Exception: If graph execution fails (caller should catch and fall back to single-call LLM)
    """
    global _graph

    if _graph is None:
        _graph = _build_investigation_graph().compile()
        log.info("RAG investigation graph compiled")

    # Initialize state
    initial_state: GraphState = {
        "alert": alert,
        "similar_incidents": [],
        "hypothesis": {},
        "confidence": 0.0,
        "additional_context": None,
        "iteration_count": 0,
        "retrieval_failed": False,
    }

    # Run graph
    log.info("Starting RAG investigation for alert %s", alert.get("id", "unknown"))
    start_time = datetime.now(timezone.utc)

    result = await _graph.ainvoke(initial_state)

    elapsed_ms = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

    log.info(
        "RAG investigation complete (elapsed=%dms, iterations=%d, confidence=%.2f)",
        elapsed_ms,
        result.get("iteration_count", 0),
        result.get("confidence", 0.0)
    )

    # Extract final hypothesis from result
    final_output = result.copy()

    # Add timing metadata
    if "rag_metadata" in final_output:
        final_output["rag_metadata"]["elapsed_ms"] = elapsed_ms

    return final_output
