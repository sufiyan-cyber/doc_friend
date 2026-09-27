# Business Operator — EmberGround AI Hackathon 2026 Hackathon PRD

**Working name:** Business Operator / OperatorOS  
**Primary runtime:** TrueForge  
**Stack:** TrueForge + n8n + Cognee + Sarvam + PostgreSQL  
**MVP interface:** Web app using laptop/phone mic + speaker  
**Hardware:** Deferred until software E2E works  
**Hero workflow:** `Grow My Weekend Sales`

## 1. Product thesis

Business Operator is a voice-first, goal-driven AI agent for small businesses. It does not stop at answering questions. A merchant can ask, delegate, or assign an ongoing monitoring responsibility. The agent can observe business data, retrieve business memory, plan work, execute tools, run analysis in a sandbox, stop before consequential actions, get human approval, execute, verify, and report.

The differentiator is **controlled agentic execution across fragmented business systems**, not sales analytics alone.

## 2. Modes

- **ASK:** “How much did I sell this month?” → grounded read/query.
- **DELEGATE:** “Increase weekend sales. Handle the campaign.” → full agent workflow.
- **MONITOR:** “Tell me if anything important changes.” → scheduled checks and useful alerts.

## 3. MVP scope

### Must have
- TrueForge is the central agent runtime.
- Business MCP tools connected to real PostgreSQL/n8n systems.
- TrueForge sandbox execution for generated analysis.
- TrueForge approval checkpoint before external mutation.
- Cognee business memory.
- n8n integration/action workflows.
- Web dashboard with trace + approval card.
- Sarvam STT/TTS adapter, but text remains a working fallback.
- One completed hero workflow: `Grow My Weekend Sales`.

### Explicitly out of scope for the first pass
- ESP32/audio electronics.
- Wake word / continuous audio streaming.
- Production accounting/POS/payment integrations.
- Large multi-agent swarm.
- Autonomous unrestricted actions.

## 4. Architecture

```text
USER
  │
  │ text or browser voice
  ▼
WEB APP
  │
  ├── Sarvam STT
  ▼
TRUEFORGE AGENT RUNTIME
  ├── model provider
  ├── MCP tools
  ├── sandbox
  ├── approvals
  ├── sessions/events
  └── task execution
  │
  ├── Cognee (memory)
  ├── Business MCP → n8n → PostgreSQL / external systems
  └── Research tools / n8n
  │
  ▼
Approval checkpoint
  │
  ├── approve → n8n mutation → verification
  └── reject → no external side effect
```

Current upstream TrueForge documentation describes it as a runtime for model calls, MCP tools, sandboxing, approvals, context/session state, and UI/API/SDK surfaces. Use the installed version as the source of truth for exact APIs/config.

## 5. Component responsibilities

| Component | Owns |
|---|---|
| TrueForge | Agent loop, model calls, MCP, sandbox, approvals, sessions, trace |
| n8n | Deterministic integrations, HTTP/API calls, schedules, notifications |
| Cognee | Business memory, policies, preferences, historical operating knowledge |
| PostgreSQL | Transactional business data |
| Sarvam | STT/TTS only |
| Web app | UX, trace visualization, approval presentation |
| Sandbox | Isolated code/analysis execution |

## 6. Agent model

Top-level agent: **Business Operator**.

Optional specialist skills/subagents:

- Business Analyst — sales/inventory/customer analysis.
- Growth Planner — campaign strategy.
- Campaign Builder — content + action preparation.
- Research Monitor — current context/news.
- Inventory Planner — reorder analysis.
- Finance/Collections — dues/reminders.

Do not create independent agents unless delegation is genuinely useful.

## 7. Per-business customization

Every business is a workspace with:

- `business_profile`
- `memory_namespace`
- `data_source_ids`
- `enabled_skills`
- `tool_permissions`
- `approval_policy`
- `notification_preferences`
- optional `future_device_id`

The future ESP32 device maps to one business workspace. Hardware does not change agent architecture.

## 8. Hero workflow: Grow My Weekend Sales

User: **“I want more sales this weekend. Figure out what we should do and prepare the campaign.”**

1. Understand goal, dates, constraints, success metric.
2. Read sales, inventory, customer/campaign data.
3. Retrieve relevant business rules from Cognee.
4. Research relevant current context if needed.
5. Run generated analysis in TrueForge sandbox.
6. Create a concrete campaign plan.
7. Prepare campaign/messages in n8n-connected systems.
8. **STOP at TrueForge approval checkpoint.**
9. Merchant reviews and approves/rejects.
10. If approved, n8n executes the external action.
11. Agent reads back the resulting state.
12. Agent reports outcome and what to monitor next.

## 9. Tool contracts

Expose via MCP with strict schemas.

| Tool | Type | Approval |
|---|---|---|
| `get_business_profile` | read | no |
| `get_sales_data` | read | no |
| `get_inventory` | read | no |
| `get_customer_segments` | read | no |
| `search_business_memory` | read | no |
| `research_market_context` | read | no |
| `run_analysis_in_sandbox` | compute | no |
| `create_campaign_draft` | prepare | no |
| `prepare_customer_messages` | prepare | no |
| `create_purchase_order` | external mutation | **yes** |
| `publish_campaign` | external mutation | **yes** |
| `send_customer_messages` | external mutation | **yes** |
| `get_action_status` | verify | no |

## 10. n8n workflows

- `business_get_sales`
- `business_get_inventory`
- `business_research_context`
- `campaign_create_draft`
- `campaign_publish`
- `customer_message_send`
- `monitor_business` (Phase 2)

n8n decides nothing. The TrueForge agent decides when to invoke a workflow.

## 11. Cognee memory

Store:
- business identity
- business policies/rules
- owner preferences
- supplier/product knowledge
- historical campaign/operator outcomes

PostgreSQL remains the transaction source of truth.

## 12. Approval model

```text
READ / ANALYZE
   ↓
PREPARE
   ↓
VERIFY
   ↓
TRUEFORGE APPROVAL CHECKPOINT
   ↓
APPROVE → EXECUTE → VERIFY
   └──────→ REJECT / CANCEL
```

All external side-effect tools require approval in MVP.

## 13. Voice

```text
Browser mic → Sarvam STT → text → TrueForge
TrueForge result → Sarvam TTS → browser speaker
```

Push-to-talk; no custom audio hardware in MVP. Text must always remain available as fallback.

## 14. API

```text
POST /api/businesses
POST /api/businesses/:id/onboarding
GET  /api/businesses/:id/summary
GET  /api/businesses/:id/tasks
POST /api/tasks
GET  /api/tasks/:id
POST /api/tasks/:id/approve
POST /api/tasks/:id/reject
POST /api/voice/transcribe
POST /api/voice/synthesize
GET  /api/tasks/:id/events
GET  /api/audit/:business_id
GET  /api/health
```

## 15. Recommended repo structure

```text
business-operator/
├── apps/web/
├── apps/api/
├── agent/{prompts,policies,schemas,task-definitions}/
├── mcp/business-tools/
├── n8n/workflows/
├── cognee/{ingestion,memory-model}/
├── db/{migrations,seed}/
├── sandbox/examples/
├── scripts/
├── docs/demo-script.md
├── .env.example
├── README.md
└── docker-compose.yml
```

## 16. Build order

1. PostgreSQL + synthetic merchant dataset.
2. n8n read/action workflows.
3. TrueForge + model provider.
4. Business MCP server.
5. Real read tool through TrueForge.
6. Sandbox execution.
7. Approval-gated mutation.
8. Hero workflow in text mode.
9. Cognee memory.
10. Web trace/approval UI.
11. Sarvam voice adapter.
12. Polish + monitoring + hardware planning.

## 17. Acceptance criteria

- **AC-01:** Sales query hits real DB/tool.
- **AC-02:** Business rule retrieved from Cognee and used.
- **AC-03:** Hero task runs generated code in TrueForge sandbox.
- **AC-04:** Real campaign draft is created.
- **AC-05:** Agent pauses at real TrueForge approval before publish/send.
- **AC-06:** Approval executes real n8n action and readback verifies it.
- **AC-07:** Reject path causes no external mutation.
- **AC-08:** Failed actions are reported truthfully.
- **AC-09:** Sarvam voice is optional; text fallback works.
- **AC-10:** Demo clearly shows TrueForge doing the work.
- **AC-11:** README works on another laptop.
- **AC-12:** No secrets committed.

## 18. Demo script

1. “I want more sales this weekend. Figure out what we should do.”
2. Show TrueForge retrieving business data and Cognee context.
3. Show sandbox analysis.
4. Show campaign plan + prepared actions.
5. Pause at approval.
6. Approve.
7. Show n8n execution.
8. Show verification/readback.
9. Ask “What should I watch next?”

## 19. Track 2 (AI Business Operator) Mapping

| Requirement | Proof |
|---|---|
| Reach something real | MCP → DB/n8n/real workflow |
| Run what it writes | TrueForge sandbox |
| Know when to stop | TrueForge approval checkpoint |
| One job, finished | Weekend sales campaign |
| Approval in demo | Visible wait + user decision |
| Public repo | README + setup |
| Only yours to connect | Synthetic data + team-owned keys |

## 20. Future hardware

After software E2E works:

- ESP32-S3
- microphone
- speaker
- push-to-talk button
- status LED
- optional RFID

Hardware becomes a physical endpoint for the same Business Operator workspace. RFID can provide product context; a physical button can serve as an approval channel.

## 21. Environment variables — fill last

```env
APP_ENV=development
APP_BASE_URL=http://localhost:3000
API_BASE_URL=http://localhost:8000

DATABASE_URL=

TRUEFORGE_BASE_URL=
TRUEFORGE_API_KEY=
TRUEFORGE_AGENT_ID=
TRUEFORGE_MODEL_PROFILE=

LLM_PROVIDER=
LLM_MODEL=
LLM_API_KEY=
LLM_BASE_URL=

SARVAM_API_KEY=
SARVAM_STT_MODEL=
SARVAM_TTS_MODEL=

COGNEE_BASE_URL=
COGNEE_API_KEY=
COGNEE_PROJECT_ID=

N8N_BASE_URL=
N8N_API_KEY=
N8N_WEBHOOK_SECRET=

BUSINESS_MCP_URL=
BUSINESS_MCP_AUTH_TOKEN=

NEWS_API_BASE_URL=
NEWS_API_KEY=

SESSION_SECRET=
WEBHOOK_SIGNING_SECRET=
```

**Important:** exact TrueForge configuration keys and SDK signatures must be taken from the current installed version. Do not guess internal variable names.

## 22. Vibe-coder rules

1. No second agent loop outside TrueForge.
2. No fake traces or fake tool results.
3. Strictly typed tool schemas.
4. Synthetic but realistic data.
5. Idempotent approval-gated mutations.
6. PostgreSQL = transaction source; Cognee = semantic memory.
7. n8n = integration/automation layer, not the reasoning engine.
8. One reliable hero flow beats broad unfinished features.
9. Audit task/tool/approval/execution/verification state; never log secrets.
10. Claim success only after verification.

## References

- TrueForge: https://github.com/truefoundry/trueforge
- Current upstream README describes model calls, MCP tools, sandboxing, approvals, context/session capabilities, API/SDK, and UI surfaces.
