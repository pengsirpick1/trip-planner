# Unified LLM Call Telemetry

Every model interaction passes through `TelemetryLLMGateway`. A `llm_call_id` represents one logical call. `llm_call_attempts` records every actual provider request, including structured-output repair attempts. Token totals are summed across all attempts; a repair does not hide failed-request usage.

`LLMCallContext` carries user, run, graph/version, task and stage. Stages include `INTENT`, `ROUTER`, `PLANNER`, `REACT`, `SYNTHESIS` and `DIRECT`. ReAct scopes each task before calling the gateway, preserving tenant and task attribution under parallel execution.

The provider adapter returns an immutable attempt snapshot containing usage, latency, provider request ID and outcome per HTTP request. It does not use instance-level usage counters for attribution. `llm_calls` holds logical aggregates and `llm_call_attempts` is append-only evidence.
