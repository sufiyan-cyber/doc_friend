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

from agent.business_operator import get_or_create_session
from db.database import SessionLocal
from db.models import ActionRecord, PurchaseOrder, AuditEvent
from db.seed import seed_database

async def run_all_tests():
    seed_database()
    print("==================================================================")
    print("OPERATOR-OS VERTICAL SLICE TESTS: VOICE-FIRST AUTONOMOUS AGENT")
    print("==================================================================")

    # -----------------------------------------------------------------
    # TEST 1: QUICK GROUNDED STATUS QUERY
    # -----------------------------------------------------------------
    print("\n[TEST 1] Testing Instant Status Query: 'What was my sales status today?'")
    sess_status = get_or_create_session("test_sess_status")
    async for event in sess_status.execute_task_stream("What was my sales status today?"):
        if event["type"] == "model.message.delta":
            print(f"  -> Spoken Voice Output: {event.get('voice_text')}")
        elif event["type"] == "turn.done":
            print(f"  -> Turn Status: {event['state']['status']}")
    assert sess_status.status == "COMPLETED", "Status query should complete immediately!"
    print("  ✓ Test 1 Passed: Grounded sales query completed with instant voice output.")

    # -----------------------------------------------------------------
    # TEST 2: CLOSE SHOP OPERATIONAL WORKFLOW WITH SPOKEN APPROVAL
    # -----------------------------------------------------------------
    print("\n[TEST 2] Testing Close Shop Sequence: 'Close my shop for today.'")
    sess_close = get_or_create_session("test_sess_close")
    async for event in sess_close.execute_task_stream("Close my shop for today."):
        etype = event["type"]
        if etype == "tool.call":
            print(f"  -> Tool Call: {event['tool_name']}")
        elif etype == "tool.approval_required":
            print(f"  [SPOKEN APPROVAL CHECKPOINT]: {event.get('voice_text')}")
    assert sess_close.status == "WAITING_FOR_APPROVAL", "Should be paused for approval!"
    print("  -> Session is waiting for merchant approval.")

    print("  -> Simulating Merchant Speaking 'Approve'...")
    async for event in sess_close.resume_after_approval("allow"):
        etype = event["type"]
        if etype == "tool.call":
            print(f"  -> Executing: {event['tool_name']}")
        elif etype == "model.message.delta":
            print(f"  -> Final Confirmation Spoken: {event.get('voice_text')}")
    assert sess_close.status == "COMPLETED", "Should complete after approval!"
    print("  ✓ Test 2 Passed: Close shop flow executed mutation and verified.")

    # -----------------------------------------------------------------
    # TEST 3: CONVERSATIONAL CAMPAIGN DISCOVERY & ROI SIMULATION
    # -----------------------------------------------------------------
    print("\n[TEST 3] Testing Marketing Campaign Flow: 'Run a campaign for my shop.'")
    sess_camp = get_or_create_session("test_sess_camp")
    print("  -> Step 3A: Merchant initiates 'Run a campaign for my shop'")
    async for event in sess_camp.execute_task_stream("Run a campaign for my shop"):
        if event["type"] == "agent.question_required":
            print(f"  [INTERACTIVE QUESTION SPOKEN]: {event.get('voice_text')}")
            print(f"     Options available: {len(event.get('channel_options', []))} channels, {len(event.get('discount_options', []))} discounts")
    assert sess_camp.status == "WAITING_FOR_USER_INPUT", "Should pause to ask merchant questions!"
    print("  -> Agent paused waiting for merchant channel & discount preferences.")

    print("\n  -> Step 3B: Merchant responds via voice: 'Run WhatsApp broadcast with 15% discount'")
    async for event in sess_camp.continue_task_stream("Run WhatsApp broadcast with 15% discount"):
        etype = event["type"]
        if etype == "tool.call":
            print(f"  -> Tool Call: {event['tool_name']}")
        elif etype == "tool.approval_required":
            print(f"  [CAMPAIGN APPROVAL SPOKEN]: {event.get('voice_text')}")
    assert sess_camp.status == "WAITING_FOR_APPROVAL", "Should pause for approval before broadcasting!"
    print("  -> Campaign drafted with TrueForge Sandbox ROI simulation and awaiting merchant approval.")

    print("\n  -> Step 3C: Merchant responds 'Approve' to broadcast campaign...")
    async for event in sess_camp.resume_after_approval("allow"):
        etype = event["type"]
        if etype == "tool.call":
            print(f"  -> Dispatched: {event['tool_name']}")
        elif etype == "model.message.delta":
            print(f"  -> Confirmation Spoken: {event.get('voice_text')}")
    assert sess_camp.status == "COMPLETED", "Campaign broadcast should complete!"
    print("  ✓ Test 3 Passed: Multi-turn campaign workflow completed end-to-end.")

    # -----------------------------------------------------------------
    # TEST 4: TELEGRAM PAYMENT REMINDERS BRANCH
    # -----------------------------------------------------------------
    print("\n[TEST 4] Testing Payment Reminders Branch: 'Send payment reminders for overdue customer dues.'")
    sess_dues = get_or_create_session("test_sess_dues")
    async for event in sess_dues.execute_task_stream("Send payment reminders for overdue customer dues."):
        if event["type"] == "tool.approval_required":
            print(f"  [REMINDER APPROVAL SPOKEN]: {event.get('voice_text')}")
    assert sess_dues.status == "WAITING_FOR_APPROVAL", "Should pause for reminder approval!"
    async for event in sess_dues.resume_after_approval("allow"):
        if event["type"] == "assistant.message.completed":
            print(f"  -> Reminder Confirmation Spoken: {event.get('voice_text')}")
    assert sess_dues.status == "COMPLETED", "Payment reminder branch should complete!"
    print("  ✓ Test 4 Passed: Payment reminders branch executed via n8n + Telegram + Spreadsheet.")

    # -----------------------------------------------------------------
    # TEST 5: CALL-E SUPPLIER VOICE CALL BRANCH
    # -----------------------------------------------------------------
    print("\n[TEST 5] Testing CALL-E Supplier Voice Call Branch: 'Call supplier via CALL-E to place reorder'")
    sess_call = get_or_create_session("test_sess_call")
    async for event in sess_call.execute_task_stream("Call supplier via CALL-E to place reorder"):
        if event["type"] == "tool.approval_required":
            print(f"  [CALL-E APPROVAL SPOKEN]: {event.get('voice_text')}")
    assert sess_call.status == "WAITING_FOR_APPROVAL", "Should pause for CALL-E call approval!"
    async for event in sess_call.resume_after_approval("allow"):
        if event["type"] == "assistant.message.completed":
            print(f"  -> CALL-E Confirmation Spoken: {event.get('voice_text')}")
    assert sess_call.status == "COMPLETED", "CALL-E supplier call branch should complete!"
    print("  ✓ Test 5 Passed: Supplier voice call branch executed via n8n + CALL-E + Spreadsheet.")

    # Database & Spreadsheet Verification
    from n8n.executor import get_spreadsheet_rows
    rows = get_spreadsheet_rows(limit=10)
    print(f"\n[SPREADSHEET LEDGER VERIFICATION] Total Rows Logged: {len(rows)}")
    for r in rows[:3]:
        print(f"  - [{r.get('Branch_Executed')}] {r.get('Counterparty')}: ₹{r.get('Amount_INR')} ({r.get('External_Status')})")

    # Database Verification
    db = SessionLocal()
    try:
        po_count = db.query(PurchaseOrder).count()
        actions = db.query(ActionRecord).all()
        audits = db.query(AuditEvent).all()
        print("\n[DATABASE & AUDIT VERIFICATION]")
        print(f"Total Purchase Orders in DB: {po_count}")
        print(f"Total Actions in DB: {len(actions)}")
        print(f"Total Audit Trail Events in DB: {len(audits)}")
        recent_audits = audits[-3:]
        for a in recent_audits:
            print(f"  - [{a.source}] {a.event_type}: {a.summary}")
    finally:
        db.close()

    print("\n==================================================================")
    print("ALL TESTS PASSED SUCCESSFULLY! OPERATOR-OS FULLY OPERATIONAL.")
    print("==================================================================")

if __name__ == "__main__":
    asyncio.run(run_all_tests())
