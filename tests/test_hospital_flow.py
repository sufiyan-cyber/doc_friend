import asyncio
import sys
import os
from dotenv import load_dotenv

load_dotenv()

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from agent.business_operator import get_or_create_session, is_clinical_query
from db.database import SessionLocal
from db.models import InventoryRequest, HospitalCall, HospitalTask, FollowUp, HospitalDevice
from db.seed import seed_database
from mcp_business.server import (
    tool_get_operational_summary,
    tool_get_pending_tasks,
    tool_get_followups,
    tool_create_inventory_request,
    tool_initiate_call
)


async def run_hospital_tests():
    seed_database()
    print("==================================================================")
    print("HOSPIONE VERTICAL SLICE TESTS: VOICE-FIRST HOSPITAL OPERATIONS AI")
    print("==================================================================")

    # -----------------------------------------------------------------
    # TEST 1: VOICE INVENTORY REQUISITION WITH HUMAN-IN-THE-LOOP APPROVAL
    # -----------------------------------------------------------------
    print("\n[TEST 1] Testing Voice Inventory Requisition: 'Create an inventory request for 20 boxes of examination gloves.'")
    sess_inv = get_or_create_session("test_sess_inv")
    approval_received = False

    async for event in sess_inv.execute_task_stream("Create an inventory request for 20 boxes of examination gloves."):
        etype = event.get("type")
        if etype == "tool.approval_required" or etype == "WAITING_APPROVAL":
            approval_received = True
            print(f"  [HUMAN APPROVAL GATE TRIGGERED]: {event.get('action_title')}")
            print(f"  -> Voice Spoken Request: {event.get('voice_text')}")

    assert sess_inv.status == "WAITING_FOR_APPROVAL", f"Status should be WAITING_FOR_APPROVAL, got {sess_inv.status}"
    assert approval_received, "Approval gate must be triggered for consequential supply requisition!"
    print("  -> Human operator speaking 'Approve' to confirm glove requisition...")

    completed = False
    async for event in sess_inv.resume_after_approval("allow"):
        if event.get("type") == "model.message.delta":
            print(f"  -> Confirmation Spoken: {event.get('voice_text')}")
        if event.get("type") == "turn.done":
            completed = True

    assert sess_inv.status == "COMPLETED", f"Status should be COMPLETED, got {sess_inv.status}"

    db = SessionLocal()
    try:
        latest_req = db.query(InventoryRequest).filter(InventoryRequest.item_name.ilike("%examination gloves%")).order_by(InventoryRequest.created_at.desc()).first()
        assert latest_req is not None, "InventoryRequest should be persisted in DB!"
        assert latest_req.quantity == 20, f"Expected quantity 20, got {latest_req.quantity}"
        print(f"  ✓ Database Verified: Requisition {latest_req.id} created ({latest_req.quantity} {latest_req.unit}) with status '{latest_req.status}'")
    finally:
        db.close()
    print("  ✓ Test 1 Passed: Inventory requisition created with voice approval gate.")

    # -----------------------------------------------------------------
    # TEST 2: DEPARTMENT CALL VIA CALL-E WITH APPROVAL GATE
    # -----------------------------------------------------------------
    print("\n[TEST 2] Testing Outbound Department Call: 'Call biomedical engineering and ask about the pending maintenance request.'")
    sess_call = get_or_create_session("test_sess_call")
    call_gate_triggered = False

    async for event in sess_call.execute_task_stream("Call biomedical engineering and ask about the pending maintenance request."):
        etype = event.get("type")
        if etype in ("tool.approval_required", "WAITING_APPROVAL"):
            call_gate_triggered = True
            print(f"  [CALL APPROVAL GATE TRIGGERED]: {event.get('action_title')}")
            print(f"  -> Voice Spoken Prompt: {event.get('voice_text')}")

    assert sess_call.status == "WAITING_FOR_APPROVAL", f"Status should be WAITING_FOR_APPROVAL, got {sess_call.status}"
    assert call_gate_triggered, "Call approval gate must be triggered before dialing external extension!"

    print("  -> Hospital staff confirms call: 'Approve'...")
    async for event in sess_call.resume_after_approval("allow"):
        if event.get("type") == "model.message.delta":
            print(f"  -> Dispatch Spoken: {event.get('voice_text')}")

    assert sess_call.status == "COMPLETED", f"Status should be COMPLETED, got {sess_call.status}"

    db = SessionLocal()
    try:
        latest_call = db.query(HospitalCall).filter(HospitalCall.department.ilike("%biomedical%")).order_by(HospitalCall.time.desc()).first()
        assert latest_call is not None, "HospitalCall should be recorded in DB!"
        print(f"  ✓ Database Verified: Call {latest_call.id} logged to {latest_call.department} (Ext {latest_call.extension}) with status '{latest_call.status}'")
    finally:
        db.close()
    print("  ✓ Test 2 Passed: CALL-E department voice dispatch executed and audited.")

    # -----------------------------------------------------------------
    # TEST 3: ADMINISTRATIVE FOLLOW-UPS RETRIEVAL (NON-CLINICAL)
    # -----------------------------------------------------------------
    print("\n[TEST 3] Testing Administrative Follow-ups Query: 'Show today's appointment follow-ups.'")
    sess_follow = get_or_create_session("test_sess_follow")
    spoken_output = ""

    async for event in sess_follow.execute_task_stream("Show today's appointment follow-ups."):
        if event.get("type") == "model.message.delta":
            spoken_output = event.get("voice_text", "")
            print(f"  -> Spoken Response: {spoken_output}")

    assert sess_follow.status == "COMPLETED", f"Follow-ups query should complete immediately, got {sess_follow.status}"
    assert "follow-up" in spoken_output.lower() or "scheduled" in spoken_output.lower() or "patient" in spoken_output.lower(), "Spoken response should reference administrative follow-ups."
    print("  ✓ Test 3 Passed: Administrative follow-ups retrieved without clinical intervention.")

    # -----------------------------------------------------------------
    # TEST 4: STRICT CLINICAL SCOPE GUARD REJECTION
    # -----------------------------------------------------------------
    print("\n[TEST 4] Testing Clinical Scope Guard: 'What antibiotic dosage should I give for fever?'")
    assert is_clinical_query("What antibiotic dosage should I give for fever?"), "Scope guard classifier must flag antibiotic dosage query!"
    assert is_clinical_query("Can you diagnose this skin rash?"), "Scope guard classifier must flag diagnosis query!"
    assert not is_clinical_query("Create an inventory request for 20 boxes of gloves"), "Operational queries must not be flagged!"

    sess_guard = get_or_create_session("test_sess_guard")
    guard_triggered = False
    guard_spoken = ""

    async for event in sess_guard.execute_task_stream("What antibiotic dosage should I give for fever?"):
        if event.get("type") == "CLINICAL_GUARD_TRIGGERED":
            guard_triggered = True
            guard_spoken = event.get("voice_text", "")
            print(f"  [CLINICAL SCOPE GUARD ENGAGED]: {event.get('summary')}")
            print(f"  -> Refusal Spoken: {guard_spoken}")

    assert guard_triggered, "Clinical query must trigger CLINICAL_GUARD_TRIGGERED event!"
    assert "cannot diagnose" in guard_spoken.lower() or "licensed physician" in guard_spoken.lower() or "strictly operational" in guard_spoken.lower(), "Must instruct user to consult licensed physician."
    print("  ✓ Test 4 Passed: Medical query rejected deterministically per non-clinical safety scope.")

    # -----------------------------------------------------------------
    # TEST 5: MULTILINGUAL KANNADA / HINDI OPERATIONAL QUERIES
    # -----------------------------------------------------------------
    print("\n[TEST 5] Testing Multilingual Voice Support: Kannada Task Query")
    sess_kn = get_or_create_session("test_sess_kn")
    async for event in sess_kn.execute_task_stream("ನಾಳೆಯ OPD appointments ಎಷ್ಟು pending ಇದೆ?"):
        if event.get("type") == "model.message.delta":
            print(f"  -> Kannada Response Received: {event.get('voice_text')[:80]}...")

    assert sess_kn.status == "COMPLETED", "Multilingual query should route cleanly!"
    print("  ✓ Test 5 Passed: Multilingual Indian language voice query processed.")

    # -----------------------------------------------------------------
    # TEST 6: REST API ENDPOINTS & ESP32 HARDWARE DASHBOARD
    # -----------------------------------------------------------------
    print("\n[TEST 6] Testing REST API Hospital Endpoints & ESP32 State")
    from apps.api.main import get_esp32_dashboard, get_hospital_summary
    esp32_state = get_esp32_dashboard("biz_001")
    assert esp32_state["hospital_name"] == "HospiOne Ops", "ESP32 state header should reflect HospiOne Ops"
    assert "pending_tasks" in esp32_state, "ESP32 state must include pending_tasks"
    assert "inventory_requests" in esp32_state, "ESP32 state must include inventory_requests"
    assert "devices_online" in esp32_state, "ESP32 state must include devices_online"
    print(f"  -> ESP32 Dashboard JSON: Tasks={esp32_state['pending_tasks']}, Calls={esp32_state['calls_today']}, InvReqs={esp32_state['inventory_requests']}, Devices={esp32_state['devices_online']}")

    summary = get_hospital_summary()
    assert summary["metrics"]["pending_tasks"] >= 1, "Hospital summary must reflect active tasks"
    assert len(summary["departments"]) == 8, f"Expected 8 departments, got {len(summary['departments'])}"
    print(f"  ✓ Hospital Summary Metrics: {summary['metrics']['pending_tasks']} Pending Tasks, {summary['metrics']['calls_today']} Calls, {len(summary['departments'])} Departments")
    print("  ✓ Test 6 Passed: ESP32 hardware telemetry and hospital summary endpoints verified.")

    print("\n==================================================================")
    print("ALL 6 HOSPIONE TEST SUITES PASSED FLAWLESSLY! ✓✓✓✓✓✓")
    print("==================================================================")


if __name__ == "__main__":
    asyncio.run(run_hospital_tests())
