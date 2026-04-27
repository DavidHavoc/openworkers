from fastapi import FastAPI

app = FastAPI(
    title="OpenWorkers API",
    description="MVP for a research-focused hierarchical multi-agent system",
    version="0.1.0",
)

@app.get("/health")
async def health_check():
    """Basic health check endpoint."""
    return {"status": "ok", "tier": "api-gateway"}

@app.post("/tasks/")
async def submit_task(query: str):
    """Stub for task submission."""
    # This would initiate the router and orchestrator
    return {"status": "accepted", "task_id": "stub_id_123", "query": query}
