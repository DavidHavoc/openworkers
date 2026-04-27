# Payload Examples

## 1. Example Session Payload (Output)
```json
{
  "session_id": "c1f1ea20-8b1b-4f40-a3bc-9fd902b9ab01",
  "task_id": "d04e578c-0fc9-4eb5-bfa3-eb1e0416b677",
  "route_strategy": "head_workers",
  "outputs": [
    {
      "tier": "worker",
      "status": "success",
      "output": "[WORKER DRY_RUN] Processed: Analyze latest framework...",
      "dry_run": true
    },
    {
      "tier": "head",
      "status": "success",
      "output": "[HEAD DRY_RUN] Processed: Analyze latest framework...",
      "dry_run": true
    }
  ],
  "memory_brief": "MEMORY_ROUTING_BRIEF\n- Similar past tasks: 0..."
}
```

## 2. Example Stored Memory Episode
```json
{
    "episode_id": "ab331201-...",
    "timestamp": "2026-04-26T12:00:00Z",
    "task_summary": "Analyze latest framework",
    "task_type": "general",
    "privacy_tier": "sanitized",
    "route": {
        "head_direct": false,
        "used_middle_tier": false,
        "used_worker_swarm": true,
        "spawn_count": 2
    },
    "models": {
        "head": "configurable_head",
        "workers": ["configurable_worker"]
    },
    "metrics": {
        "latency_ms": 32,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": 0.01
    },
    "quality": {
        "score": 0.9,
        "accepted": true,
        "confidence": 0.8
    }
}
```
