# HospiOne — Implementation Audit & Migration Blueprint
**Date:** October 3, 2026 | **Project:** HospiOne (Evolution of OperatorOS into Voice-First Hospital Operations AI Platform)

---

## 1. EXISTING STACK

- **Frontend:**
  - Single-Page Web Dashboard (`apps/web/index.html`) using **React 18** (browser standalone via CDN), **Tailwind CSS** with custom luxury editorial tokens (`Playfair Display`, `Source Sans 3`, `IBM Plex Mono`, paper-texture, warm gold `#B8860B`, border `#E8E4DF`, bg `#FAFAF8`).
  - WebAudio API & MediaRecorder for in-browser microphone capture, live waveform RMS visualizer, HTML5/WebAudio playback queue.
  - Server-Sent Events (SSE) consumer for real-time agent turn streaming, subagent spawning, tool execution HUD, and human-in-the-loop approval modal.
- **Backend:**
  - **FastAPI** (`apps/api/main.py`) with Uvicorn.
  - REST endpoints for tasks (`/api/tasks`), SSE event streaming (`/api/tasks/{task_id}/events`), interactive continuation (`/api/tasks/{task_id}/input`), approval decision (`/api/tasks/{task_id}/approve`, `/reject`), business/hospital summary, audit trail (`/api/audit/{id}`), live audio transcription (`/api/voice/transcribe`), TTS synthesis (`/api/tts`), integration config (`/api/integrations/config`), CALL-E trigger (`/api/integrations/test-calle`), and ESP32 hardware dashboard/command (`/api/esp32/dashboard`, `/api/esp32/command`).
- **Database:**
  - **SQLAlchemy 2.0** (`db/database.py`) with SQLite default (`data/business_operator.db`) and PostgreSQL/Supabase compatibility.
  - Existing models: `Business`, `Supplier`, `Product`, `Inventory`, `Customer`, `Sale`, `SaleItem`, `PurchaseOrder`, `ActionRecord`, `AuditEvent`.
- **AI / Agent:**
  - `agent/business_operator.py`: `OperatorAgentSession` implementing TrueForge wire protocol (`turn.created`, `mcp.initialize`, `sandbox.created`, `model.message.delta`, `thread.created`, `tool.call`, `tool.response`, `tool.approval_required`, `turn.done`).
  - Hybrid Intent Router: Deterministic intent matching (<1ms) + Gemini Flash LLM classification fallback.
  - Daytona Cloud Sandbox runner with isolated local Python subprocess fallback (`sandbox/sandbox_runner.py`).
  - Cognee semantic memory store with local JSON fallback (`business_memory/memory_store.py`).
- **Voice Pipeline:**
  - **Sarvam AI** STT (`saaras:v3`) and TTS (`bulbul:v3`) supporting English (`en-IN`), Hindi (`hi-IN`), and Kannada (`kn-IN`).
  - Zero-downtime automatic failovers to Gemini Audio transcription, Edge Neural TTS, and browser Web Speech.
- **Calling:**
  - **CALL-E API** (`api.heycall-e.com/v1/calls`) + n8n Cloud webhook multi-branch router (`n8n/executor.py`).
  - Supports live phone calls with API key + sandbox simulation mode with realistic call IDs.
- **Inventory:**
  - Two-stage prepare -> approve -> execute for inventory orders, checking reorder levels and stock reserves.
- **Hardware Integration:**
  - ESP32 Arduino sketch (`hardware/operator_os_dashboard/operator_os_dashboard.ino`) with Adafruit ILI9341 320x240 SPI TFT display, button interaction, and JSON sync with `/api/esp32/dashboard` and `/api/esp32/command`.

---

## 2. EXISTING REUSABLE COMPONENTS

- **Dashboard:**
  - Entire header, brand bar, audio waveform HUD, live voice status badge, language dropdown (English / Kannada / Hindi), mute/unmute audio button, and 1-Click trigger action buttons.
- **Sidebar & Tabs:**
  - Workspace navigation switcher (`operator`, `spreadsheet`, `integrations`, `audit`) ready to be extended to the PRD information architecture (`Overview`, `Operations`, `Tasks`, `Calls`, `Inventory`, `Follow-ups`, `Devices`, `Integrations`, `Audit Log`).
- **Cards & Visual System:**
  - Metric KPI cards with trend indicators, small-caps badges, serif titles, gold accent rings, subtle borders.
  - Approval Checkpoint Modal with explicit human-in-the-loop action details, rationale, estimated impact, and [Reject] / [Approve] controls.
- **Tables & Lists:**
  - Live data tables with status badges, search/filter bars, timestamp formatting, and CSV export.
- **Agent Runtime & Tools:**
  - Event streaming pipeline (`execute_task_stream`, `continue_task_stream`, `resume_after_approval`).
  - Human approval boundary (`tool.approval_required`).
  - MCP Streamable-HTTP server exposing typed tools.
- **API Services:**
  - Voice transcription, synthesis, SSE streaming, integration testing, and hardware endpoints.

---

## 3. EXISTING FUNCTIONALITY TO REUSE

- **Inventory:**
  - Reuse stock monitoring, reorder thresholds, purchase order preparation, and approval-gated creation.
  - Retheme from grocery items (Milk, Eggs, Bread) to hospital supplies (Examination Gloves, 3-Ply Surgical Masks, Sterile Syringes 5ml, Hand Sanitizer 500ml, N95 Respirators, IV Infusion Sets, Digital Thermometers).
- **Calling (CALL-E):**
  - Reuse the live CALL-E HTTP client (`_trigger_calle_supplier_call`) and n8n webhook router in `n8n/executor.py`.
  - Retheme from supplier milk reorders to hospital department communication (Biomedical Engineering, Reception, Facilities, IT Support, Pharmacy, Administration, Radiology, Laboratory).
- **Agent Orchestrator:**
  - Reuse `OperatorAgentSession`, SSE wire protocol, thread creation, tool call streaming, and approval resumption.
- **Hardware:**
  - Reuse `/api/esp32/dashboard` and `/api/esp32/command` routes and Adafruit ILI9341 Arduino sketch.

---

## 4. FUNCTIONALITY TO MODIFY

1. **Information Architecture & Dashboard Views:**
   - Evolve navigation tabs from 4 retail tabs to the full PRD hospital layout:
     `Overview`, `Operations`, `Tasks`, `Calls`, `Inventory`, `Follow-ups`, `Devices`, `Integrations`, `Audit Log`.
2. **Branding & Visual Copy:**
   - Transform "OperatorOS — Store Manager" to **"HospiOne — Voice-First Hospital Operations AI Platform"**.
   - Subtitle: **"Voice-first AI for coordinating everyday hospital workflows."**
   - Status: `● AI Agent Online`, `● Demo Environment`, `● Synthetic Data`.
3. **Domain Models & Database Seed:**
   - Add first-class entities and tables for:
     - `Department` (Reception, Biomedical Engineering, Facilities, IT Support, Pharmacy, etc.)
     - `HospitalTask` / `Task` (Task ID, Title, Department, Created By, Assigned To, Status, Priority, Due Date)
     - `HospitalCall` / `CallRecord` (Call ID, Department, Purpose, Initiated By, Extension/Phone, Status)
     - `InventoryRequest` (Req ID, Item, Quantity, Unit, Department, Requested By, Status, Priority)
     - `FollowUp` (Patient ID PAT-00124, Next Scheduled, Contact Status, Assigned Staff, Admin Status)
     - `HospitalDevice` (Device ID, Location, Hardware, Status, Mic, Speaker, Display, Last Heartbeat)
   - Preserve existing models (`Business`, `Product`, `Inventory`, `Supplier`, `ActionRecord`, `AuditEvent`) so existing tests and tools remain fully backward-compatible.
4. **Agent Intent Router & Safety Guardrails:**
   - Add **Scope Guard** to safely detect and redirect clinical/medical queries (diagnosis, drug recommendations, cancer treatment, chemotherapy) to clinical staff.
   - Add hospital intent routing:
     - `INVENTORY_REQUEST`: "Create an inventory request for 20 boxes of examination gloves"
     - `CALL_DEPARTMENT`: "Call biomedical engineering and ask about the pending maintenance ticket"
     - `FOLLOW_UPS`: "Show today's appointment follow-ups" / "administrative follow-ups due"
     - `MAINTENANCE_TICKET`: "Create a maintenance ticket for Room 302: Air conditioning"
     - `PENDING_TASKS`: "What are my pending operational tasks?"
     - `SCHEDULE_QUERY`: "Show today's schedule and operational overview"
5. **Human-in-the-Loop Approval:**
   - Ground approval cards in explicit hospital workflows:
     - Call Biomedical Engineering (Dept, Extension 214, Purpose, [Cancel] [Confirm Call])
     - Create Inventory Request (Item: Examination Gloves, Quantity: 20 boxes, [Cancel] [Approve & Create])
     - Create Maintenance Ticket (Location: OPD Room 302, Issue: AC Maintenance, [Cancel] [Create Ticket])
6. **Role-Based Access (Demo Switcher):**
   - Provide an instant role switcher in the UI header:
     `Doctor`, `Nurse`, `Administrator`, `Operations`, `IT / Biomedical`, `Reception`.
   - AI agent verifies user role permissions before tool execution.

---

## 5. NEW FUNCTIONALITY REQUIRED

1. **Overview / Command Center Page (PRD Sections 14–17, 29–31):**
   - Top KPI cards: Pending Tasks (12), Calls Today (8), Inventory Requests (5), Follow-ups Due (17), Devices Online (3 / 4).
   - Voice Command Center HUD with microphone, audio waveforms, language selector (English / Kannada / Hindi), active device selector (`Reception Device #01`), and quick 1-click voice demo buttons.
   - Live Agent Activity feed showing high-level operational events and tool execution traces (no private chain-of-thought).
2. **Operations Page (Section 18):**
   - Today's operational summary: appointments, pending tasks, department requests, unresolved maintenance tickets.
   - Department operational status cards: Reception, Biomedical Engineering, Inventory, Facilities, IT Support.
3. **Tasks Page (Section 19):**
   - Comprehensive task management with filters by status (`Pending`, `Awaiting Approval`, `In Progress`, `Completed`, `Cancelled`) and priority.
   - Task creation and status update actions.
4. **Calls Page (Section 20):**
   - Department directory with extensions, contact heads, and direct CALL-E call buttons.
   - Recent calls log with simulated/demo badges and call duration tracking.
5. **Inventory Page (Section 21):**
   - Hospital supply stock table with reorder thresholds and health statuses.
   - Inventory requests tracking table with approve, reject, and create workflows.
6. **Follow-ups Page (Section 22):**
   - Administrative continuity-of-care coordination records (PAT-00124 ...).
   - Status tracking (`Scheduled`, `Pending Contact`, `Contacted`, `Rescheduled`, `Completed`).
   - Create communication task workflow.
7. **Devices Page (Section 23–24):**
   - Fleet view of connected physical AI devices (`HOSPI Device #01`, `#02`, `#03`).
   - Real-time hardware status indicators (Microphone, Speaker, Color Display, Network, Heartbeat).
   - Device-to-dashboard synchronization demonstration.
8. **Integrations Page (Section 25):**
   - Hospital system connections (Appointment System, Inventory Management, Department Directory, Telephony / CALL-E, Maintenance System) marked as Sandbox Integration.
   - Architecture flow diagram.
9. **Dedicated Public Demo Mode (Sections 29–31, 54):**
   - 1-Click Launch Live Demo scenarios:
     - Demo 1: Inventory Request ("Create an inventory request for 20 boxes of examination gloves")
     - Demo 2: Calling Biomedical Engineering ("Call biomedical engineering and ask about the pending maintenance ticket")
     - Demo 3: Administrative Follow-up Coordination ("Show patients with administrative follow-ups due today")
     - Demo 4: Clinical Scope Guard Test ("Should this patient receive chemotherapy?")

---

## 6. RISKY / UNCLEAR AREAS & MITIGATIONS

- **Risk 1: Cloud API Latency (Sarvam / n8n / Cognee):**
  - *Mitigation:* Already has robust local fallbacks (Edge Neural TTS, browser SpeechSynthesis, local memory rules, mock CALL-E sandbox adapter). We ensure all fallbacks work instantly with zero hanging.
- **Risk 2: Breaking existing tests or routes:**
  - *Mitigation:* Preserve all existing models and route signatures while adding new hospital routes and data. Keep `test_flow.py` running and add `tests/test_hospital_flow.py` for full hospital flow verification.
- **Risk 3: Clinical safety claims:**
  - *Mitigation:* Prominent labels: "Non-Clinical Operational Platform • Synthetic Demo Data • Deterministic Safety Guardrails". Deterministic Scope Guard intercepts any medical query before it can reach tool execution.

---

## 7. IMPLEMENTATION PLAN

- **Phase 1: Database & Seed Migration:**
  - Update `db/models.py` with hospital models (`Department`, `HospitalTask`, `HospitalCall`, `InventoryRequest`, `FollowUp`, `HospitalDevice`) alongside existing models.
  - Update `db/seed.py` with rich, realistic hospital synthetic seed data.
- **Phase 2: Backend Services & API Routes:**
  - Add hospital API routes in `apps/api/main.py`:
    - `/api/hospital/summary`
    - `/api/hospital/tasks`
    - `/api/hospital/calls`
    - `/api/hospital/inventory`
    - `/api/hospital/inventory/request`
    - `/api/hospital/followups`
    - `/api/hospital/devices`
    - `/api/hospital/departments`
    - `/api/hospital/call-department`
  - Update `/api/esp32/dashboard` to serve hospital state.
- **Phase 3: Agent Evolution & Hospital Workflows:**
  - Update `agent/business_operator.py` with:
    - Scope Guard (clinical query rejection)
    - Role Permission Guard
    - Hospital workflow branches: `INVENTORY_REQUEST`, `CALL_DEPARTMENT`, `FOLLOW_UPS`, `MAINTENANCE_TICKET`, `PENDING_TASKS`, `SCHEDULE_QUERY`
    - Hospital-specific tools in `mcp_business/server.py`
    - Hospital approval prompts & voice responses in English, Kannada, Hindi.
- **Phase 4: Hardware Code Update:**
  - Update `hardware/operator_os_dashboard/operator_os_dashboard.ino` with HospiOne branding, hospital metrics, and hospital actions.
- **Phase 5: Frontend Command Center Evolution:**
  - Update `apps/web/index.html` to implement the full 9-tab HospiOne Hospital Operations Command Center, preserving the existing aesthetic and design system while adding:
    - Overview with Top KPI cards, Voice Command Center HUD, Demo scenarios, Live Activity
    - Operations tab
    - Tasks tab
    - Calls tab
    - Inventory tab
    - Follow-ups tab
    - Devices tab
    - Integrations tab
    - Audit Log tab
    - Role selector in header (Doctor, Nurse, Administrator, Operations, IT / Biomedical, Reception)
    - Multilingual voice support (English / Kannada / Hindi)
- **Phase 6: Testing & Verification:**
  - Run database seed.
  - Run comprehensive automated test suite covering all hospital workflows and safety guardrails.
  - Verify desktop and mobile layouts.
