# OpenWorkers Architecture & Integrations

## Core Hierarchy Flow

```mermaid
flowchart TD
    User([User]) --> API[API Gateway / Session Manager]
    API --> PreRouter[Cheap Pre-Router / Query Classifier]
    
    PreRouter --> Head[Opus Head <br> Policy + Judge + Planner]
    PreRouter --> Final[Final Opus Synthesis / Answer]
    
    Head --> MCP[Direct call to trusted MCP tools]
    
    Head --> MidTier[Mid-Tier Western Agents<br> - dedupe <br> - clustering <br> - contradiction check <br> - evidence ranking <br> - safe summarization]
    
    Head --> Worker[Cheap Chinese Worker Swarm<br> - search <br> - extraction <br> - tagging <br> - translation <br> - chunk summarization <br> - candidate generation]
    
    Head --> Final
    MidTier --> Final
    Worker --> Final
    
    subgraph Core System Infrastructure [Under all tiers]
        direction LR
        S1[Shared Task Graph / Blackboard]
        S2[Evidence Store + Source IDs]
        S3[Prompt / Prefix Cache]
        S4[MCP Tool Layer]
        S5[Safety / Privacy Router]
        S6[Evaluation + Learning Logs]
    end
```

### Textual Representation
```text
User
  |
  v
API Gateway / Session Manager
  |
  v
Cheap Pre-Router / Query Classifier
  |------------------------------\
  |                               \
  v                                \
Opus Head (Policy + Judge + Planner) \
  |   |    |     |                    \
  |   |    |     |                     \
  |   |    |     +--> Direct call to trusted MCP tools
  |   |    |
  |   |    +--> Mid-Tier Western Agents
  |   |            - dedupe
  |   |            - clustering
  |   |            - contradiction check
  |   |            - evidence ranking
  |   |            - safe summarization
  |   |
  |   +--> Cheap Chinese Worker Swarm
  |                - search
  |                - extraction
  |                - tagging
  |                - translation
  |                - chunk summarization
  |                - candidate generation
  |
  v
Final Opus Synthesis / Answer

Under all tiers:
- Shared Task Graph / Blackboard
- Evidence Store + Source IDs
- Prompt / Prefix Cache
- MCP Tool Layer
- Safety / Privacy Router
- Evaluation + Learning Logs
```

## Security & Tiers
- **Public**: Access to normal web search.
- **Sanitized**: Access to structural datasets safely.
- **Trusted**: Exclusive access to `KnowledgeRetrievalTool`. Trusted tasks bypass generic routing and enforce `head_direct` to avoid leaking traces to cheap workers.

## Observability
All key system jumps emit JSON lines mapping to `obs_logger`, tracking:
- Sessions, latencies, memory hit-rates, and API adapter budgets.
