import os
import json
import uuid
import base64
import datetime
import asyncio
import httpx
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from pydantic import BaseModel, Field

from db.database import SessionLocal, init_db
from db.models import (
    Business, Product, Inventory, Customer, Sale, PurchaseOrder, ActionRecord, AuditEvent,
    Department, HospitalTask, HospitalCall, InventoryRequest, FollowUp, HospitalDevice
)
from agent.business_operator import get_or_create_session, active_sessions
from mcp_business.server import (
    tool_get_business_profile, tool_get_sales_data, tool_get_inventory, tool_get_customer_dues,
    tool_get_operational_summary, tool_get_today_schedule, tool_get_pending_tasks, tool_get_followups,
    tool_get_department_directory, tool_create_inventory_request, tool_initiate_call
)

# Initialize database
init_db()

app = FastAPI(
    title="HospiOne Hospital Operations AI Platform API",
    description="Voice-First Hospital Operations AI Platform — Non-Clinical Healthcare Workflows",
    version="2.0.0"
)

# Static & Web Dashboard Mount
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
if os.path.exists(WEB_DIR):
    @app.get("/")
    async def serve_dashboard():
        return FileResponse(
            os.path.join(WEB_DIR, "index.html"),
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
        )

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request Models
class CreateTaskRequest(BaseModel):
    business_id: str = Field(default="biz_001")
    prompt: str = Field(..., description="Merchant command, e.g. 'Close my shop for today.'")
    language_code: Optional[str] = Field(default=None, description="Language code e.g. 'en-IN', 'hi-IN', 'kn-IN'")

class TaskInputRequest(BaseModel):
    input: str = Field(..., description="Merchant interactive choice or voice answer")
    language_code: Optional[str] = Field(default=None, description="Optional language override")

class ApprovalDecisionRequest(BaseModel):
    decision: str = Field(default="allow", description="'allow' or 'deny'")
    reason: Optional[str] = Field(default=None, description="Optional explanation for decision")
    language_code: Optional[str] = Field(default=None, description="Optional language override")

# HospiOne Hospital Operations Request Models
class CreateHospitalTaskRequest(BaseModel):
    title: str = Field(..., description="Operational task description")
    department: str = Field(default="General Operations")
    priority: str = Field(default="Standard")
    assigned_to: str = Field(default="Unassigned")
    due_hours: int = Field(default=4)
    notes: Optional[str] = None

class CreateInventoryReqRequest(BaseModel):
    item_name: str = Field(..., description="Medical supply name")
    quantity: int = Field(default=20)
    unit: str = Field(default="boxes")
    department: str = Field(default="OPD Nursing Station")
    requested_by: str = Field(default="Nurse Demo")
    priority: str = Field(default="Standard")
    rationale: Optional[str] = None

class InitiateHospitalCallRequest(BaseModel):
    department: str = Field(default="Biomedical Engineering")
    extension: Optional[str] = Field(default="214")
    phone: Optional[str] = Field(default="+91 84960 74290")
    purpose: str = Field(default="Maintenance request update")
    initiated_by: str = Field(default="Dr. Demo (Web Command)")

class UpdateTaskStatusRequest(BaseModel):
    status: str = Field(default="Completed")
    assigned_to: Optional[str] = None

class UpdateFollowupStatusRequest(BaseModel):
    contact_status: str = Field(default="Contacted")
    administrative_status: Optional[str] = None
    notes: Optional[str] = None


# Global language state & caches for Sarvam + Multilingual Failover
LAST_ACTIVE_LANGUAGE: str = "en-IN"
TRANSLATION_CACHE: Dict[str, str] = {}

def _get_live_sarvam_key() -> str:
    """Reloads .env dynamically so changing SARVAM_API_KEY in .env or UI works immediately without server restart."""
    try:
        from db.database import ENV_FILE
        load_dotenv(ENV_FILE, override=True)
    except Exception:
        pass
    return os.environ.get("SARVAM_API_KEY", "").strip()

def normalize_lang_code(lang: Optional[str], default: str = "en-IN") -> str:
    if not lang:
        return default
    l = lang.strip()
    low = l.lower()
    if low in ("auto", ""):
        return default
    if low.startswith("hi"):
        return "hi-IN"
    if low.startswith("kn") or low.startswith("kan"):
        return "kn-IN"
    if low.startswith("ta"):
        return "ta-IN"
    if low.startswith("te"):
        return "te-IN"
    if low.startswith("ml"):
        return "ml-IN"
    if low.startswith("mr"):
        return "mr-IN"
    if low.startswith("en"):
        return "en-IN"
    return l

def detect_script_language(text: str, fallback: str = "en-IN") -> str:
    """Detects Indian script (Kannada, Hindi/Devanagari, Tamil, Telugu) directly from Unicode characters."""
    if not text:
        return fallback
    for ch in text:
        cp = ord(ch)
        if 0x0C80 <= cp <= 0x0CFF:
            return "kn-IN"
        if 0x0900 <= cp <= 0x097F:
            return "hi-IN"
        if 0x0B80 <= cp <= 0x0BFF:
            return "ta-IN"
        if 0x0C00 <= cp <= 0x0C7F:
            return "te-IN"
    return fallback

async def translate_text_multilingual(text: str, target_lang: str, source_lang: str = "en-IN") -> str:
    """
    Translates text between English, Hindi (hi-IN), and Kannada (kn-IN):
    1. Primary: Sarvam AI mayura:v1 (/translate)
    2. Automatic Zero-Downtime Fallback (if Sarvam runs out of credits mid-demo):
       Google GTX Neural Translate + Gemini Flash fallback.
    """
    clean = (text or "").strip()
    if not clean:
        return clean
    target_lang = normalize_lang_code(target_lang, "en-IN")
    source_lang = normalize_lang_code(source_lang, "en-IN")
    if target_lang == source_lang:
        return clean
    if target_lang != "en-IN" and detect_script_language(clean, "en-IN") == target_lang:
        return clean

    cache_key = f"{source_lang}->{target_lang}:{clean}"
    if cache_key in TRANSLATION_CACHE:
        return TRANSLATION_CACHE[cache_key]

    sarvam_key = _get_live_sarvam_key()
    # 1. Primary: Sarvam AI Mayura v1 Translation
    if sarvam_key:
        try:
            async with httpx.AsyncClient(timeout=4.5) as client:
                resp = await client.post(
                    "https://api.sarvam.ai/translate",
                    headers={
                        "api-subscription-key": sarvam_key,
                        "Content-Type": "application/json"
                    },
                    json={
                        "input": clean[:950],
                        "source_language_code": source_lang,
                        "target_language_code": target_lang,
                        "model": "mayura:v1",
                        "enable_preprocessing": True
                    }
                )
                if resp.status_code == 200:
                    translated = (resp.json().get("translated_text") or "").strip()
                    if translated:
                        TRANSLATION_CACHE[cache_key] = translated
                        return translated
                else:
                    print(f"[Sarvam Translate] HTTP {resp.status_code} ({resp.text[:120]}) — switching to zero-downtime fallback.")
        except Exception as e:
            print(f"[Sarvam Translate] Fallback notice: {e}")

    # 2. Automatic Credit-Exhaustion Fallback: Google Neural Translate (0 credits, <150ms)
    try:
        tl_short = target_lang.split("-")[0]
        sl_short = source_lang.split("-")[0] if source_lang != "auto" else "auto"
        async with httpx.AsyncClient(timeout=3.5) as client:
            r_g = await client.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx", "sl": sl_short, "tl": tl_short, "dt": "t", "q": clean[:950]}
            )
            if r_g.status_code == 200:
                arr = r_g.json()
                if isinstance(arr, list) and arr and isinstance(arr[0], list):
                    translated = "".join(seg[0] for seg in arr[0] if seg and seg[0]).strip()
                    if translated:
                        TRANSLATION_CACHE[cache_key] = translated
                        return translated
    except Exception as e:
        print(f"[Fallback Translate] Notice: {e}")

    return clean

async def _localize_event_for_language(evt: Dict[str, Any], lang: str) -> Dict[str, Any]:
    """If the active session language is Hindi (hi-IN) or Kannada (kn-IN), localizes voice_text and adds native script display."""
    lang = normalize_lang_code(lang, "en-IN")
    if lang == "en-IN":
        return evt

    out = dict(evt)
    out["language_code"] = lang
    v_txt = (out.get("voice_text") or "").strip()
    if v_txt:
        localized_voice = await translate_text_multilingual(v_txt, lang, "en-IN")
        out["voice_text_en"] = v_txt
        out["voice_text"] = localized_voice
        lang_badge = "हिंदी (Sarvam AI)" if lang == "hi-IN" else ("ಕನ್ನಡ (Sarvam AI)" if lang == "kn-IN" else f"Sarvam AI ({lang})")
        if out.get("type") in ("model.message.delta", "model.message.completed") and out.get("content"):
            if localized_voice not in out["content"]:
                out["content"] = f"🗣️ **{lang_badge}:** {localized_voice}\n\n{out['content']}"
    return out

# Health Check
@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "service": "Business Operator TrueForge API",
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat()
    }

# Merchant Business Summary & Live Metrics
@app.get("/api/businesses/{business_id}/summary")
def get_business_summary(business_id: str = "biz_001"):
    db = SessionLocal()
    try:
        biz = db.query(Business).filter(Business.id == business_id).first()
        if not biz:
            raise HTTPException(status_code=404, detail="Business not found")

        sales = db.query(Sale).filter(Sale.business_id == business_id).all()
        total_sales = sum(s.total_amount for s in sales)
        cash_sales = sum(s.total_amount for s in sales if s.payment_method == "Cash")
        upi_sales = sum(s.total_amount for s in sales if s.payment_method == "UPI")
        card_sales = sum(s.total_amount for s in sales if s.payment_method == "Card")

        low_stock_count = db.query(Inventory).filter(
            Inventory.business_id == business_id,
            Inventory.current_stock <= Inventory.reorder_level
        ).count()

        overdue_customers = db.query(Customer).filter(
            Customer.business_id == business_id,
            Customer.outstanding_due > 0,
            Customer.due_since_days >= 7
        ).all()
        total_dues = sum(c.outstanding_due for c in overdue_customers)

        active_orders = db.query(PurchaseOrder).filter(PurchaseOrder.business_id == business_id).count()

        return {
            "business": {
                "id": biz.id,
                "name": biz.name,
                "category": biz.category,
                "currency_symbol": biz.currency_symbol,
                "opening_cash": biz.opening_cash,
                "current_cash_in_drawer": biz.current_cash_in_drawer,
                "cash_drawer_target": biz.cash_drawer_target,
                "operating_hours": biz.operating_hours,
                "owner_name": biz.owner_name
            },
            "metrics": {
                "total_sales_today": total_sales,
                "cash_sales": cash_sales,
                "upi_sales": upi_sales,
                "card_sales": card_sales,
                "low_stock_items_count": low_stock_count,
                "total_overdue_dues": total_dues,
                "overdue_customers_count": len(overdue_customers),
                "purchase_orders_count": active_orders
            }
        }
    finally:
        db.close()

# =====================================================================
# HOSPI-ONE HOSPITAL OPERATIONS API ROUTES
# =====================================================================

@app.get("/api/hospital/summary")
def get_hospital_summary():
    """Returns HospiOne operational command center metrics, KPI cards, and departments."""
    db = SessionLocal()
    try:
        tasks_count = db.query(HospitalTask).filter(HospitalTask.status.in_(["Pending", "Awaiting Approval", "In Progress"])).count()
        calls_count = db.query(HospitalCall).count()
        inv_reqs_count = db.query(InventoryRequest).filter(InventoryRequest.status.in_(["Pending Approval", "Approved"])).count()
        followups_count = db.query(FollowUp).count()
        online_devices = db.query(HospitalDevice).filter(HospitalDevice.status == "ONLINE").count()
        total_devices = db.query(HospitalDevice).count()
        low_stock_count = db.query(Inventory).filter(Inventory.current_stock <= Inventory.reorder_level).count()

        depts = db.query(Department).all()
        departments_list = [
            {
                "id": d.id,
                "name": d.name,
                "extension": d.extension,
                "phone": d.phone,
                "head": d.head,
                "location": d.location,
                "status": d.status,
                "pending_requests": d.pending_requests_count,
                "category": d.category
            }
            for d in depts
        ]

        return {
            "hospital": {
                "id": "hosp_001",
                "name": "HospiOne Hospital Operations Center",
                "category": "Multi-Specialty Healthcare Campus",
                "operating_hours": "24/7 Operations",
                "address": "HospiOne Health Campus, 100ft Road, Indiranagar, Bengaluru",
                "director": "Dr. Demo / Medical Director",
                "phone": "+91 80 4123 4500"
            },
            "metrics": {
                "pending_tasks": tasks_count,
                "calls_today": calls_count,
                "inventory_requests": inv_reqs_count,
                "followups_due": followups_count,
                "devices_online_str": f"{online_devices} / {total_devices}",
                "devices_online": online_devices,
                "devices_total": total_devices,
                "low_stock_count": low_stock_count
            },
            "departments": departments_list
        }
    finally:
        db.close()

@app.get("/api/hospital/tasks")
def list_hospital_tasks(status: Optional[str] = None, priority: Optional[str] = None):
    """Retrieve hospital operational tasks with optional status and priority filtering."""
    db = SessionLocal()
    try:
        q = db.query(HospitalTask)
        if status and status.lower() != "all":
            q = q.filter(HospitalTask.status.ilike(f"%{status}%"))
        if priority and priority.lower() != "all":
            q = q.filter(HospitalTask.priority.ilike(f"%{priority}%"))
        tasks = q.order_by(HospitalTask.created_at.desc()).all()
        return {
            "count": len(tasks),
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "department": t.department,
                    "created_by": t.created_by,
                    "assigned_to": t.assigned_to,
                    "status": t.status,
                    "priority": t.priority,
                    "due_at": t.due_at.isoformat() if t.due_at else None,
                    "created_at": t.created_at.isoformat(),
                    "notes": t.notes
                }
                for t in tasks
            ]
        }
    finally:
        db.close()

@app.post("/api/hospital/tasks")
def create_hospital_task(req: CreateHospitalTaskRequest):
    """Create a new operational task."""
    db = SessionLocal()
    try:
        now = datetime.datetime.now(datetime.UTC)
        tid = f"TASK-{int(now.timestamp()) % 100000}"
        t = HospitalTask(
            id=tid,
            title=req.title,
            department=req.department,
            priority=req.priority,
            assigned_to=req.assigned_to,
            status="Pending",
            due_at=now + datetime.timedelta(hours=req.due_hours),
            created_at=now,
            notes=req.notes
        )
        db.add(t)
        audit = AuditEvent(
            id=f"audit_{uuid.uuid4().hex[:8]}",
            business_id="biz_001",
            task_id=tid,
            source="STAFF",
            event_type="TASK_CREATED",
            summary=f"Created task {tid}: {req.title} ({req.department})",
            details={"task_id": tid, "title": req.title, "department": req.department, "priority": req.priority}
        )
        db.add(audit)
        db.commit()
        return {"status": "SUCCESS", "task_id": tid, "message": f"Task {tid} created successfully."}
    finally:
        db.close()

@app.post("/api/hospital/tasks/{task_id}/status")
def update_task_status(task_id: str, req: UpdateTaskStatusRequest):
    """Update task status (e.g. Completed, In Progress)."""
    db = SessionLocal()
    try:
        t = db.query(HospitalTask).filter(HospitalTask.id == task_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Task not found")
        t.status = req.status
        if req.assigned_to:
            t.assigned_to = req.assigned_to
        if req.status == "Completed":
            t.completed_at = datetime.datetime.now(datetime.UTC)
        db.commit()
        return {"status": "SUCCESS", "task_id": task_id, "new_status": t.status}
    finally:
        db.close()

@app.get("/api/hospital/calls")
def list_hospital_calls():
    """Retrieve hospital calls log."""
    db = SessionLocal()
    try:
        calls = db.query(HospitalCall).order_by(HospitalCall.time.desc()).all()
        return {
            "count": len(calls),
            "calls": [
                {
                    "id": c.id,
                    "department": c.department,
                    "extension": c.extension,
                    "recipient_phone": c.recipient_phone,
                    "recipient_name": c.recipient_name,
                    "purpose": c.purpose,
                    "initiated_by": c.initiated_by,
                    "time": c.time.isoformat(),
                    "duration_seconds": c.duration_seconds,
                    "status": c.status,
                    "calle_call_id": c.calle_call_id,
                    "is_simulated": c.is_simulated,
                    "notes": c.notes
                }
                for c in calls
            ]
        }
    finally:
        db.close()

@app.post("/api/hospital/calls/initiate")
def initiate_hospital_call(req: InitiateHospitalCallRequest):
    """Initiate a department call via CALL-E."""
    res = tool_initiate_call(
        department=req.department,
        purpose=req.purpose,
        initiated_by=req.initiated_by,
        recipient_phone=req.phone
    )
    return res

@app.get("/api/hospital/inventory")
def list_hospital_inventory():
    """Retrieve hospital inventory items with current stock, safety buffer, and reorder status."""
    db = SessionLocal()
    try:
        prods = db.query(Product).all()
        items = []
        for p in prods:
            inv = db.query(Inventory).filter(Inventory.product_id == p.id).first()
            current_stock = inv.current_stock if inv else 0
            reorder_level = inv.reorder_level if inv else 10
            status = "Healthy" if current_stock > reorder_level else ("Reorder Soon" if current_stock > 10 else "Low Stock")
            items.append({
                "product_id": p.id,
                "sku": p.sku,
                "name": p.name,
                "category": p.category,
                "unit": p.unit,
                "current_stock": current_stock,
                "reorder_level": reorder_level,
                "min_order_qty": inv.min_order_qty if inv else 15,
                "status": status
            })
        return {"count": len(items), "items": items}
    finally:
        db.close()

@app.get("/api/hospital/inventory/requests")
def list_inventory_requests():
    """Retrieve hospital inventory requests queue."""
    db = SessionLocal()
    try:
        reqs = db.query(InventoryRequest).order_by(InventoryRequest.created_at.desc()).all()
        return {
            "count": len(reqs),
            "requests": [
                {
                    "id": r.id,
                    "item_name": r.item_name,
                    "sku": r.sku,
                    "quantity": r.quantity,
                    "unit": r.unit,
                    "department": r.department,
                    "requested_by": r.requested_by,
                    "status": r.status,
                    "priority": r.priority,
                    "rationale": r.rationale,
                    "created_at": r.created_at.isoformat(),
                    "approved_by": r.approved_by,
                    "approved_at": r.approved_at.isoformat() if r.approved_at else None
                }
                for r in reqs
            ]
        }
    finally:
        db.close()

@app.post("/api/hospital/inventory/requests")
def create_inventory_request_api(req: CreateInventoryReqRequest):
    """Create a new hospital inventory request."""
    return tool_create_inventory_request(
        item_name=req.item_name,
        quantity=req.quantity,
        unit=req.unit,
        department=req.department,
        requested_by=req.requested_by,
        priority=req.priority,
        rationale=req.rationale or ""
    )

@app.post("/api/hospital/inventory/requests/{request_id}/approve")
def approve_inventory_request(request_id: str):
    """Approve a pending inventory request."""
    db = SessionLocal()
    try:
        r = db.query(InventoryRequest).filter(InventoryRequest.id == request_id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Request not found")
        r.status = "Approved"
        r.approved_by = "Dr. Demo / Admin"
        r.approved_at = datetime.datetime.now(datetime.UTC)
        audit = AuditEvent(
            id=f"audit_{uuid.uuid4().hex[:8]}",
            business_id="biz_001",
            task_id=request_id,
            source="HUMAN",
            event_type="INVENTORY_REQUEST_APPROVED",
            summary=f"Human Approved inventory request {request_id} ({r.item_name}, {r.quantity} {r.unit})",
            details={"request_id": request_id, "approved_by": r.approved_by}
        )
        db.add(audit)
        db.commit()
        return {"status": "SUCCESS", "request_id": request_id, "new_status": "Approved"}
    finally:
        db.close()

@app.post("/api/hospital/inventory/requests/{request_id}/reject")
def reject_inventory_request(request_id: str):
    """Reject an inventory request."""
    db = SessionLocal()
    try:
        r = db.query(InventoryRequest).filter(InventoryRequest.id == request_id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Request not found")
        r.status = "Rejected"
        db.commit()
        return {"status": "SUCCESS", "request_id": request_id, "new_status": "Rejected"}
    finally:
        db.close()

@app.get("/api/hospital/followups")
def list_hospital_followups():
    """Retrieve administrative follow-up coordination records (non-clinical)."""
    return tool_get_followups()

@app.post("/api/hospital/followups/{followup_id}/contact")
def update_followup_status(followup_id: str, req: UpdateFollowupStatusRequest):
    """Update administrative contact status."""
    db = SessionLocal()
    try:
        f = db.query(FollowUp).filter(FollowUp.id == followup_id).first()
        if not f:
            raise HTTPException(status_code=404, detail="Follow-up record not found")
        f.contact_status = req.contact_status
        if req.administrative_status:
            f.administrative_status = req.administrative_status
        if req.notes:
            f.notes = req.notes
        db.commit()
        return {"status": "SUCCESS", "followup_id": followup_id, "contact_status": f.contact_status}
    finally:
        db.close()

@app.get("/api/hospital/devices")
def list_hospital_devices():
    """Retrieve physical AI device fleet statuses."""
    db = SessionLocal()
    try:
        devs = db.query(HospitalDevice).all()
        return {
            "count": len(devs),
            "devices": [
                {
                    "id": d.id,
                    "name": d.name,
                    "location": d.location,
                    "hardware_type": d.hardware_type,
                    "status": d.status,
                    "mic_status": d.mic_status,
                    "speaker_status": d.speaker_status,
                    "display_status": d.display_status,
                    "network_status": d.network_status,
                    "last_heartbeat": d.last_heartbeat.isoformat(),
                    "current_user": d.current_user,
                    "last_command": d.last_command,
                    "software_version": d.software_version,
                    "firmware_version": d.firmware_version
                }
                for d in devs
            ]
        }
    finally:
        db.close()

@app.get("/api/hospital/departments")
def list_hospital_departments():
    """Retrieve hospital departments directory."""
    return tool_get_department_directory()

def _mirror_to_trueforge_bg(prompt: str):
    """Non-blocking background mirror that creates a live TrueForge session + turn on localhost:8790."""
    import threading
    def _worker():
        try:
            import requests as _req
            p_low = (prompt or "").lower()
            if any(w in p_low for w in ("stock", "inventory", "reorder", "supplier", "order", "po")):
                ag = "kirana-procurement-caller"
            elif any(w in p_low for w in ("overdue", "khata", "due", "reminder", "payment", "campaign")):
                ag = "khata-recovery-campaign-agent"
            else:
                ag = "operator-os-chief-of-staff"
            r_s = _req.post("http://localhost:8790/api/v1/sessions", json={"agent": {"name": ag}}, timeout=3)
            if r_s.status_code in (200, 201):
                sid = r_s.json()["data"]["id"]
                title = f"Live OperatorOS: {(prompt or 'Voice Command')[:32]}"
                _req.patch(f"http://localhost:8790/api/v1/sessions/{sid}", json={"title": title}, timeout=3)
                _req.post(
                    f"http://localhost:8790/api/v1/sessions/{sid}/turns",
                    json={"input": [{"type": "user.message", "content": prompt or "Check store status"}], "stream": False},
                    timeout=5
                )
        except Exception:
            pass
    threading.Thread(target=_worker, daemon=True).start()


# Start an Operator Task
@app.post("/api/tasks")
async def create_task(req: CreateTaskRequest):
    global LAST_ACTIVE_LANGUAGE
    task_id = f"task_{uuid.uuid4().hex[:8]}"
    session = get_or_create_session(task_id, req.business_id)

    script_lang = detect_script_language(req.prompt, "")
    chosen_lang = normalize_lang_code(req.language_code or script_lang or LAST_ACTIVE_LANGUAGE, "en-IN")
    session.language_code = chosen_lang
    LAST_ACTIVE_LANGUAGE = chosen_lang

    _mirror_to_trueforge_bg(req.prompt)
    return {
        "task_id": task_id,
        "business_id": req.business_id,
        "prompt": req.prompt,
        "language_code": chosen_lang,
        "status": "INITIALIZED",
        "stream_url": f"/api/tasks/{task_id}/events"
    }

# SSE Stream for TrueForge Agent Turns & Pauses
@app.get("/api/tasks/{task_id}/events")
async def stream_task_events(task_id: str, prompt: Optional[str] = "Close my shop for today.", lang: Optional[str] = None):
    global LAST_ACTIVE_LANGUAGE
    session = get_or_create_session(task_id)
    if lang:
        session.language_code = normalize_lang_code(lang, getattr(session, "language_code", LAST_ACTIVE_LANGUAGE))
    elif not getattr(session, "language_code", None):
        session.language_code = detect_script_language(prompt or "", LAST_ACTIVE_LANGUAGE)

    active_lang = getattr(session, "language_code", "en-IN")

    # If the user typed in Hindi or Kannada script, translate to English for deterministic workflow routing
    english_prompt = prompt or "Close my shop for today."
    if detect_script_language(english_prompt, "en-IN") != "en-IN":
        english_prompt = await translate_text_multilingual(english_prompt, "en-IN", source_lang=active_lang)

    async def event_generator():
        if session.status == "IDLE":
            async for evt in session.execute_task_stream(english_prompt):
                loc_evt = await _localize_event_for_language(evt, active_lang)
                if loc_evt.get("voice_text"):
                    asyncio.create_task(prewarm_tts(loc_evt["voice_text"], active_lang))
                yield f"data: {json.dumps(loc_evt)}\n\n"
        else:
            for evt in session.events:
                loc_evt = await _localize_event_for_language(evt, active_lang)
                if loc_evt.get("voice_text"):
                    asyncio.create_task(prewarm_tts(loc_evt["voice_text"], active_lang))
                yield f"data: {json.dumps(loc_evt)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

# Provide User Input for Interactive Questions (TrueForge Input Continuation)
@app.post("/api/tasks/{task_id}/input")
async def provide_task_input(task_id: str, req: TaskInputRequest):
    if task_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Task session not found")

    session = active_sessions[task_id]
    if req.language_code:
        session.language_code = normalize_lang_code(req.language_code, getattr(session, "language_code", LAST_ACTIVE_LANGUAGE))
    active_lang = getattr(session, "language_code", LAST_ACTIVE_LANGUAGE)

    for _ in range(12):
        if session.status == "WAITING_FOR_USER_INPUT":
            break
        await asyncio.sleep(0.25)

    if session.status != "WAITING_FOR_USER_INPUT":
        session.status = "WAITING_FOR_USER_INPUT"

    english_input = req.input
    if detect_script_language(english_input, "en-IN") != "en-IN":
        english_input = await translate_text_multilingual(english_input, "en-IN", source_lang=active_lang)

    async def input_generator():
        async for evt in session.continue_task_stream(english_input):
            loc_evt = await _localize_event_for_language(evt, active_lang)
            if loc_evt.get("voice_text"):
                asyncio.create_task(prewarm_tts(loc_evt["voice_text"], active_lang))
            yield f"data: {json.dumps(loc_evt)}\n\n"

    return StreamingResponse(
        input_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
    )

# Approve Pending Action (TrueForge Turn Resume)
@app.post("/api/tasks/{task_id}/approve")
async def approve_task_action(task_id: str, req: ApprovalDecisionRequest):
    if task_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Task session not found")
    
    session = active_sessions[task_id]
    if req.language_code:
        session.language_code = normalize_lang_code(req.language_code, getattr(session, "language_code", LAST_ACTIVE_LANGUAGE))
    active_lang = getattr(session, "language_code", LAST_ACTIVE_LANGUAGE)

    for _ in range(12):
        if session.status == "WAITING_FOR_APPROVAL":
            break
        await asyncio.sleep(0.25)

    if session.status != "WAITING_FOR_APPROVAL":
        session.status = "WAITING_FOR_APPROVAL"

    async def resume_generator():
        async for evt in session.resume_after_approval(decision=req.decision, reason=req.reason):
            loc_evt = await _localize_event_for_language(evt, active_lang)
            if loc_evt.get("voice_text"):
                asyncio.create_task(prewarm_tts(loc_evt["voice_text"], active_lang))
            yield f"data: {json.dumps(loc_evt)}\n\n"

    return StreamingResponse(
        resume_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        }
    )

# Reject Pending Action
@app.post("/api/tasks/{task_id}/reject")
async def reject_task_action(task_id: str, req: ApprovalDecisionRequest):
    req.decision = "deny"
    return await approve_task_action(task_id, req)

# Get Audit Trail
@app.get("/api/audit/{business_id}")
def get_audit_trail(business_id: str = "biz_001"):
    db = SessionLocal()
    try:
        events = db.query(AuditEvent).filter(AuditEvent.business_id == business_id).order_by(AuditEvent.timestamp.desc()).limit(50).all()
        return {
            "business_id": business_id,
            "events": [
                {
                    "id": e.id,
                    "task_id": e.task_id,
                    "source": e.source,
                    "event_type": e.event_type,
                    "summary": e.summary,
                    "details": e.details,
                    "timestamp": e.timestamp.isoformat()
                }
                for e in events
            ]
        }
    finally:
        db.close()

def ensure_windows_mic_unmuted() -> dict:
    """
    Uses Windows CoreAudio COM API (IMMDeviceEnumerator / IAudioEndpointVolume)
    to ensure the default Windows microphone capture endpoints are unmuted and at 100% gain.
    """
    if os.name != "nt":
        return {"status": "non-windows"}
    try:
        import ctypes
        from ctypes import wintypes, POINTER, byref, c_float, c_int, c_void_p

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", wintypes.BYTE * 8),
            ]
            def __init__(self, l, w1, w2, b1, b2, b3, b4, b5, b6, b7, b8):
                self.Data1, self.Data2, self.Data3 = l, w1, w2
                self.Data4[:] = (b1, b2, b3, b4, b5, b6, b7, b8)

        CLSID_MMDeviceEnumerator = GUID(0xBCDE0395, 0xE52F, 0x467C, 0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E)
        IID_IMMDeviceEnumerator = GUID(0xA95664D2, 0x9614, 0x4F35, 0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6)
        IID_IAudioEndpointVolume = GUID(0x5CDF2C82, 0x841E, 0x4546, 0x97, 0x22, 0x0C, 0xF7, 0x40, 0x78, 0x22, 0x9A)
        EMPTY_GUID = GUID(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)
        enum_ptr = c_void_p()
        hr = ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, 23, byref(IID_IMMDeviceEnumerator), byref(enum_ptr))
        was_muted_any = False
        if hr == 0 and enum_ptr:
            vt = ctypes.cast(enum_ptr, POINTER(POINTER(c_void_p))).contents
            GetDefaultAudioEndpoint = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, c_int, c_int, POINTER(c_void_p))(vt[4])
            for role in (0, 1, 2):
                dev_ptr = c_void_p()
                if GetDefaultAudioEndpoint(enum_ptr, 1, role, byref(dev_ptr)) == 0 and dev_ptr:
                    dev_vt = ctypes.cast(dev_ptr, POINTER(POINTER(c_void_p))).contents
                    Activate = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, POINTER(GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p))(dev_vt[3])
                    vol_ptr = c_void_p()
                    if Activate(dev_ptr, byref(IID_IAudioEndpointVolume), 23, None, byref(vol_ptr)) == 0 and vol_ptr:
                        vol_vt = ctypes.cast(vol_ptr, POINTER(POINTER(c_void_p))).contents
                        SetMasterVolumeLevelScalar = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, c_float, POINTER(GUID))(vol_vt[7])
                        SetMute = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, wintypes.BOOL, POINTER(GUID))(vol_vt[14])
                        GetMute = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, POINTER(wintypes.BOOL))(vol_vt[15])
                        muted = wintypes.BOOL()
                        GetMute(vol_ptr, byref(muted))
                        if muted.value:
                            was_muted_any = True
                        SetMute(vol_ptr, 0, byref(EMPTY_GUID))
                        SetMasterVolumeLevelScalar(vol_ptr, c_float(1.0), byref(EMPTY_GUID))
        if was_muted_any:
            print("[Voice Mic] Windows OS Microphone was MUTED — automatically unmuted & set to 100% gain!")
        return {"status": "ok", "was_muted": was_muted_any}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.post("/api/voice/ensure-mic")
async def api_ensure_mic():
    return ensure_windows_mic_unmuted()

# Sarvam / Gemini Voice Adapter Endpoints (Speech-to-Text & Text-to-Speech)
@app.post("/api/voice/transcribe")
async def voice_transcribe(request: Request):
    global LAST_ACTIVE_LANGUAGE
    ensure_windows_mic_unmuted()
    """
    Multilingual Speech-to-Text (English en-IN, Hindi hi-IN, Kannada kn-IN):
    1. Primary: Sarvam AI saaras:v3 (speech-to-text-translate) — auto-detects Indian language & translates command to English for workflow routing while preserving language_code so the agent replies in the same language.
    2. Automatic Zero-Downtime Failover (if Sarvam runs out of credits mid-demo):
       - Gemini Multimodal Audio STT (detects en-IN / hi-IN / kn-IN + translates to English intent)
       - Google Acoustic SpeechRecognition (en-IN, hi-IN, kn-IN)
    """
    import io
    import math
    import shutil
    import struct
    import subprocess
    import wave
    import speech_recognition as sr

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    preferred_lang_raw = (body.get("preferred_language") or body.get("language_code") or "auto").strip()
    is_auto_lang = preferred_lang_raw.lower() in ("auto", "")
    preferred_lang = normalize_lang_code(preferred_lang_raw, LAST_ACTIVE_LANGUAGE)

    browser_hint = (body.get("browser_transcript") or "").strip()
    if browser_hint and len(browser_hint) >= 2:
        script_lang = detect_script_language(browser_hint, preferred_lang if not is_auto_lang else "en-IN")
        LAST_ACTIVE_LANGUAGE = script_lang
        english_tx = browser_hint
        if script_lang != "en-IN":
            english_tx = await translate_text_multilingual(browser_hint, "en-IN", source_lang=script_lang)
        print(f"[Voice STT] (Browser Hint) Transcribed: '{english_tx}' ({script_lang})")
        return {
            "transcript": english_tx,
            "native_transcript": browser_hint,
            "language_code": script_lang,
            "provider": "WebSpeech Live Stream"
        }

    audio_b64 = body.get("audio_base64", "")
    mime_type = body.get("mime_type", "audio/webm")
    sarvam_key = _get_live_sarvam_key()
    llm_key = os.environ.get("LLM_API_KEY", "").strip()

    if not audio_b64:
        return {"transcript": "", "language_code": preferred_lang, "provider": "Empty Audio"}

    raw_bytes = base64.b64decode(audio_b64)
    if len(raw_bytes) < 400:
        return {"transcript": "", "language_code": preferred_lang, "provider": "Too Short"}

    # Convert WebM/Opus/WAV to clean, volume-normalized 16kHz mono 16-bit PCM WAV using ffmpeg
    wav_bytes = raw_bytes
    raw_wav_bytes = raw_bytes
    ffmpeg_bin = shutil.which("ffmpeg") or (r"C:\ffmpeg\bin\ffmpeg.exe" if os.path.exists(r"C:\ffmpeg\bin\ffmpeg.exe") else None)
    if ffmpeg_bin:
        try:
            proc_norm = await asyncio.to_thread(
                subprocess.run,
                [
                    ffmpeg_bin, "-y", "-i", "pipe:0",
                    "-af", "highpass=f=80,lowpass=f=7500,dynaudnorm=f=150:g=15",
                    "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"
                ],
                input=raw_bytes,
                capture_output=True,
                timeout=6
            )
            if proc_norm.returncode == 0 and len(proc_norm.stdout) > 500:
                wav_bytes = proc_norm.stdout

            proc_raw = await asyncio.to_thread(
                subprocess.run,
                [ffmpeg_bin, "-y", "-i", "pipe:0", "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"],
                input=raw_bytes,
                capture_output=True,
                timeout=5
            )
            if proc_raw.returncode == 0 and len(proc_raw.stdout) > 500:
                raw_wav_bytes = proc_raw.stdout
                if wav_bytes == raw_bytes:
                    wav_bytes = raw_wav_bytes
        except Exception as e:
            print(f"[Voice STT] FFmpeg conversion notice: {e}")

    def _fix_piped_wav(data: bytes) -> bytes:
        """FFmpeg writing WAV to pipe:1 sets Subchunk2Size=0x7FFFFFFF; rewrite header with exact PCM byte length."""
        try:
            with wave.open(io.BytesIO(data), "rb") as rf:
                params = rf.getparams()
                pcm = rf.readframes(rf.getnframes())
            out = io.BytesIO()
            with wave.open(out, "wb") as wf:
                wf.setnchannels(params.nchannels or 1)
                wf.setsampwidth(params.sampwidth or 2)
                wf.setframerate(params.framerate or 16000)
                wf.writeframes(pcm)
            return out.getvalue()
        except Exception:
            return data

    if wav_bytes != raw_bytes:
        wav_bytes = _fix_piped_wav(wav_bytes)
    if raw_wav_bytes != raw_bytes:
        raw_wav_bytes = _fix_piped_wav(raw_wav_bytes)

    # Inspect WAV duration & RMS energy so we know if microphone captured actual audio
    duration_sec = 0.0
    rms_energy = 500.0
    try:
        with wave.open(io.BytesIO(raw_wav_bytes), "rb") as wf:
            rate = wf.getframerate() or 16000
            pcm_frames = wf.readframes(wf.getnframes())
            duration_sec = (len(pcm_frames) / 2.0) / float(rate)
            if len(pcm_frames) >= 4:
                count = len(pcm_frames) // 2
                shorts = struct.unpack(f"<{count}h", pcm_frames[:count * 2])
                sum_sq = sum(s * s for s in shorts)
                rms_energy = math.sqrt(sum_sq / max(count, 1))
    except Exception:
        pass

    print(f"[Voice STT] Audio stats: raw={len(raw_bytes)}B, wav={len(wav_bytes)}B, duration={duration_sec:.2f}s, rms={rms_energy:.1f}")

    if rms_energy < 15 and duration_sec > 0:
        print("[Voice STT] Pure digital silence detected on microphone.")
        return {"transcript": "", "language_code": preferred_lang, "provider": "Silence"}

    # 1. PRIMARY: Sarvam AI saaras:v3 Speech-to-Text-Translate (Understands Hindi, Kannada, English, etc.)
    if sarvam_key:
        stt_model = os.environ.get("SARVAM_STT_MODEL", "saaras:v3").strip()
        if stt_model in ("saaras:v1", "saaras:v2", ""):
            stt_model = "saaras:v3"
        try:
            files = {"file": ("speech.wav", wav_bytes, "audio/wav")}
            data = {"model": stt_model}
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    "https://api.sarvam.ai/speech-to-text-translate",
                    headers={"api-subscription-key": sarvam_key},
                    data=data,
                    files=files
                )
                if resp.status_code == 200:
                    res_json = resp.json()
                    sarvam_txt = (res_json.get("transcript") or "").strip()
                    detected_raw = res_json.get("language_code") or "en-IN"
                    lang_prob = float(res_json.get("language_probability") or 1.0)
                    detected_lang = normalize_lang_code(detected_raw, "en-IN")
                    if not is_auto_lang:
                        final_lang = preferred_lang
                    else:
                        # If probability is low on short English clips, keep en-IN unless clearly Indian language
                        final_lang = detected_lang if lang_prob >= 0.55 else "en-IN"

                    if sarvam_txt:
                        LAST_ACTIVE_LANGUAGE = final_lang
                        print(f"[Voice STT] (Sarvam {stt_model}) Transcribed: '{sarvam_txt}' | Lang: {final_lang} (detected={detected_lang}, prob={lang_prob:.2f})")
                        return {
                            "transcript": sarvam_txt,
                            "language_code": final_lang,
                            "detected_language_code": detected_lang,
                            "provider": f"Sarvam AI ({stt_model} • {final_lang})"
                        }
                else:
                    print(f"[Voice STT] Sarvam HTTP {resp.status_code} ({resp.text[:140]}) — auto-switching to zero-downtime fallback STT!")
        except Exception as e:
            print(f"[Voice STT] Sarvam fallback notice: {e}")

    # 2. ZERO-DOWNTIME FALLBACK A: Gemini Multimodal Audio STT (Detects English, Hindi, Kannada + translates to English)
    if llm_key:
        wav_b64 = base64.b64encode(wav_bytes).decode("utf-8")
        hallucinations = ["quick brown fox", "lazy dog", "beef stew", "recipe for", "subtitles by", "thank you for watching"]
        stt_prompt = (
            "Listen carefully to this WAV audio recording from an Indian retail store owner speaking to their AI store manager (OperatorOS).\n"
            "They may speak in English (en-IN), Hindi (hi-IN), or Kannada (kn-IN).\n"
            "Common commands include: asking for store status or sales today, starting a weekend campaign or promotion, "
            "sending payment reminders or checking customer dues (Priya, Vikram), closing the shop for today / calling the supplier, "
            "or saying approve / yes / haan / haudu or deny / no / nahi / beda.\n"
            "Return valid JSON only: {\n"
            "  \"is_speech\": boolean,\n"
            "  \"language_code\": \"en-IN\" | \"hi-IN\" | \"kn-IN\",\n"
            "  \"transcript\": \"clear English translation of what was spoken so the command router can execute it\"\n"
            "}.\n"
            "CRITICAL: Never invent or hallucinate placeholder sentences like 'The quick brown fox'. If there is no intelligible speech, return {\"is_speech\": false, \"language_code\": \"en-IN\", \"transcript\": \"\"}."
        )
        for model_name in ["gemini-3-flash-preview", "gemini-2.5-flash", "gemini-flash-lite-latest"]:
            try:
                gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={llm_key}"
                payload = {
                    "contents": [
                        {
                            "parts": [
                                {"text": stt_prompt},
                                {"inline_data": {"mime_type": "audio/wav", "data": wav_b64}}
                            ]
                        }
                    ],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": 0.0
                    }
                }
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.post(gemini_url, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                        for p in parts:
                            if p.get("text") and not p.get("thought"):
                                raw_txt = p["text"].strip()
                                try:
                                    parsed = json.loads(raw_txt)
                                    is_speech = parsed.get("is_speech", False)
                                    text = (parsed.get("transcript") or "").strip()
                                    det_lang = normalize_lang_code(parsed.get("language_code"), "en-IN")
                                except Exception:
                                    is_speech = True
                                    text = raw_txt
                                    det_lang = "en-IN"
                                if not is_speech or not text or any(h in text.lower() for h in hallucinations):
                                    continue
                                final_lang = preferred_lang if not is_auto_lang else det_lang
                                LAST_ACTIVE_LANGUAGE = final_lang
                                print(f"[Voice STT] (Gemini Fallback {model_name}) Transcribed: '{text}' ({final_lang})")
                                return {
                                    "transcript": text,
                                    "language_code": final_lang,
                                    "provider": f"Gemini Multimodal STT ({final_lang})"
                                }
            except Exception as e:
                print(f"[Voice STT] {model_name} fallback notice: {e}")

    # 3. ZERO-DOWNTIME FALLBACK B: Dedicated Acoustic SpeechRecognition (en-IN, hi-IN, kn-IN)
    def _run_google_stt_multilingual(wav_data: bytes, raw_fallback: bytes, pref_lang: str, auto_mode: bool):
        rec = sr.Recognizer()
        rec.dynamic_energy_threshold = False
        langs_to_try = ["en-IN", "hi-IN", "kn-IN"] if auto_mode else [pref_lang, "en-IN"]
        for candidate_wav in (wav_data, raw_fallback):
            for lang in langs_to_try:
                try:
                    with sr.AudioFile(io.BytesIO(candidate_wav)) as source:
                        audio_obj = rec.record(source)
                    txt = rec.recognize_google(audio_obj, language=lang)
                    if txt and txt.strip():
                        return txt.strip(), lang
                except sr.UnknownValueError:
                    continue
                except Exception:
                    break
        return "", "en-IN"

    try:
        stt_text, stt_lang = await asyncio.to_thread(_run_google_stt_multilingual, wav_bytes, raw_wav_bytes, preferred_lang, is_auto_lang)
        if stt_text:
            final_lang = preferred_lang if not is_auto_lang else normalize_lang_code(detect_script_language(stt_text, stt_lang))
            LAST_ACTIVE_LANGUAGE = final_lang
            english_tx = stt_text
            if final_lang != "en-IN":
                english_tx = await translate_text_multilingual(stt_text, "en-IN", source_lang=final_lang)
            print(f"[Voice STT] (Google Acoustic Fallback) Transcribed: '{english_tx}' (raw='{stt_text}', lang={final_lang})")
            return {
                "transcript": english_tx,
                "native_transcript": stt_text,
                "language_code": final_lang,
                "provider": f"Google Acoustic STT ({final_lang})"
            }
    except Exception as e:
        print(f"[Voice STT] Google STT fallback notice: {e}")

    return {
        "transcript": "",
        "language_code": preferred_lang,
        "provider": "Browser WebSpeech"
    }

@app.post("/api/voice/synthesize")
async def voice_synthesize(payload: Dict[str, Any]):
    """
    Sarvam TTS Adapter (bulbul:v3):
    Synthesizes English (en-IN), Hindi (hi-IN), or Kannada (kn-IN) speech via Sarvam AI,
    with automatic fallback to Edge Neural TTS if Sarvam credits are exhausted.
    """
    text = (payload.get("text") or "").strip()
    lang = normalize_lang_code(payload.get("language_code") or payload.get("lang"), detect_script_language(text, LAST_ACTIVE_LANGUAGE))
    if not text:
        return {"status": "empty", "text": ""}

    audio_bytes, media_type = await synthesize_tts_bytes(text, lang)
    if audio_bytes:
        return {
            "status": "ok",
            "audio_base64": base64.b64encode(audio_bytes).decode("utf-8"),
            "media_type": media_type,
            "language_code": lang,
            "provider": "Sarvam Bulbul v3 TTS" if media_type == "audio/wav" else f"Edge Neural TTS ({lang})"
        }

    return {
        "status": "browser_tts",
        "text": text,
        "language_code": lang,
        "provider": "Browser SpeechSynthesis"
    }

# Multilingual Audio TTS Cache (Sarvam Bulbul v3 Primary + Edge Neural TTS Credit-Exhaustion Failover)
TTS_AUDIO_CACHE: Dict[str, Any] = {}
TTS_LOCKS: Dict[str, asyncio.Lock] = {}

EDGE_NEURAL_VOICES = {
    "en-IN": "en-IN-NeerjaNeural",
    "hi-IN": "hi-IN-SwaraNeural",
    "kn-IN": "kn-IN-SapnaNeural",
    "ta-IN": "ta-IN-PallaviNeural",
    "te-IN": "te-IN-ShrutiNeural",
    "ml-IN": "ml-IN-SobhanaNeural",
    "mr-IN": "mr-IN-AarohiNeural"
}

async def synthesize_tts_bytes(text: str, lang: str = "auto", prefer_edge_for_prewarm: bool = False):
    import edge_tts
    import io

    clean_text = (text or "").strip()[:600]
    if not clean_text:
        return b"", "audio/mpeg"

    script_lang = detect_script_language(clean_text, "")
    if script_lang:
        target_lang = script_lang
    elif lang and lang.lower() != "auto":
        target_lang = normalize_lang_code(lang, "en-IN")
    else:
        target_lang = normalize_lang_code(LAST_ACTIVE_LANGUAGE, "en-IN")

    cache_key = f"{target_lang}:{clean_text}"
    if cache_key in TTS_AUDIO_CACHE:
        cached = TTS_AUDIO_CACHE[cache_key]
        return cached if isinstance(cached, tuple) else (cached, "audio/mpeg")

    lock = TTS_LOCKS.setdefault(cache_key, asyncio.Lock())
    async with lock:
        if cache_key in TTS_AUDIO_CACHE:
            cached = TTS_AUDIO_CACHE[cache_key]
            return cached if isinstance(cached, tuple) else (cached, "audio/mpeg")

        sarvam_key = _get_live_sarvam_key()
        # 1. Primary: Sarvam AI Bulbul v3 TTS (when key is configured and not silent startup prewarm)
        if sarvam_key and not prefer_edge_for_prewarm:
            tts_model = os.environ.get("SARVAM_TTS_MODEL", "bulbul:v3").strip()
            if tts_model in ("bulbul:v1", "bulbul:v2", ""):
                tts_model = "bulbul:v3"
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.post(
                        "https://api.sarvam.ai/text-to-speech",
                        headers={
                            "api-subscription-key": sarvam_key,
                            "Content-Type": "application/json"
                        },
                        json={
                            "inputs": [clean_text[:500]],
                            "target_language_code": target_lang,
                            "speaker": "priya",
                            "model": tts_model,
                            "pace": 1.05,
                            "enable_preprocessing": True
                        }
                    )
                    if resp.status_code == 200:
                        audios = resp.json().get("audios", [])
                        if audios and audios[0]:
                            wav_data = base64.b64decode(audios[0])
                            if len(wav_data) > 200:
                                res_tuple = (wav_data, "audio/wav")
                                TTS_AUDIO_CACHE[cache_key] = res_tuple
                                return res_tuple
                    else:
                        print(f"[Sarvam TTS] HTTP {resp.status_code} ({resp.text[:120]}) — switching to Edge Neural ({target_lang}) fallback!")
            except Exception as e:
                print(f"[Sarvam TTS] Fallback notice ({target_lang}): {e}")

        # 2. Automatic Credit-Exhaustion Fallback: Microsoft Edge Neural TTS (Supports en-IN, hi-IN, kn-IN with 0 credits)
        voice_name = EDGE_NEURAL_VOICES.get(target_lang, "en-IN-NeerjaNeural")
        communicate = edge_tts.Communicate(clean_text, voice_name, rate="+8%", pitch="+0Hz")
        audio_buffer = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_buffer.write(chunk["data"])

        data = audio_buffer.getvalue()
        if data:
            res_tuple = (data, "audio/mpeg")
            TTS_AUDIO_CACHE[cache_key] = res_tuple
            return res_tuple
        return b"", "audio/mpeg"

async def prewarm_tts(text: str, lang: str = "en-IN", prefer_edge: bool = False):
    if not text:
        return
    try:
        await synthesize_tts_bytes(text, lang=lang, prefer_edge_for_prewarm=prefer_edge)
    except Exception as e:
        print(f"[TTS Prewarm] Notice: {e}")

@app.on_event("startup")
async def startup_prewarm_tts():
    common_lines = [
        "Namaste Rajesh-ji! Operator O S live voice mode is active. How can I help with your store today?",
        "Checking your live sales and store systems right now, Rajesh-ji.",
        "Today's total sales are 9850 rupees across 6 transactions, and your cash drawer holds 14850 rupees. We also have 4 dairy and bakery items running low before the 8 PM cutoff. Would you like me to close the shop for today, or run a weekend campaign, Rajesh-ji?",
        "Right away, Rajesh-ji. I am dispatching background agents to check today's sales, cash drawer, inventory levels, and store closing policies.",
        "I found 4 depleted dairy and bakery items. Now I am running cash reconciliation and reorder calculations in the TrueForge Python sandbox.",
        "Sir, today's sales are 9,850 rupees and your cash drawer is reconciled with zero variance. Please deposit 9,850 rupees into the safe and leave 5,000 rupees float. I have also prepared a purchase order for 4 depleted dairy and bakery items from MilkyWay Fresh Foods totaling 4500 rupees. Should I approve or deny this order?",
        "Certainly, Rajesh-ji! I am dispatching our inventory and Cognee memory agents to analyze your store profile, surplus stock, and customer base.",
        "On it, Rajesh-ji. I am dispatching the ledger agent to check overdue customer credit accounts past 7 days.",
        "Approval confirmed, Rajesh-ji! Dispatching via n8n Cloud now.",
        "Understood, sir. I have cancelled the action. No changes were made."
    ]
    for line in common_lines:
        asyncio.create_task(prewarm_tts(line, lang="en-IN", prefer_edge=True))

@app.get("/api/tts")
async def text_to_speech_audio(text: str = "Hello", lang: str = "auto"):
    """
    Generates high-quality speech audio in English (en-IN), Hindi (hi-IN), or Kannada (kn-IN):
    - Primary: Sarvam AI Bulbul v3 TTS (audio/wav)
    - Zero-Downtime Failover: Microsoft Edge Neural TTS (en-IN-NeerjaNeural / hi-IN-SwaraNeural / kn-IN-SapnaNeural)
    """
    from fastapi.responses import Response

    clean_text = text.strip()[:600]
    if not clean_text:
        return JSONResponse({"error": "empty text"}, status_code=400)

    try:
        res = await synthesize_tts_bytes(clean_text, lang=lang)
        audio_bytes, media_type = res if isinstance(res, tuple) else (res, "audio/mpeg")
        if not audio_bytes:
            return JSONResponse({"error": "empty audio"}, status_code=500)
        return Response(
            content=audio_bytes,
            media_type=media_type,
            headers={
                "Cache-Control": "public, max-age=3600",
                "Content-Length": str(len(audio_bytes)),
            }
        )
    except Exception as e:
        print(f"[TTS] Error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


# ==============================================================================
# REAL DATABASE POS MUTATION, SPREADSHEET LEDGER & LIVE INTEGRATIONS (CALL-E / TELEGRAM / N8N)
# ==============================================================================

class RecordSaleRequest(BaseModel):
    business_id: str = Field(default="biz_001")
    amount: float = Field(default=1250.0)
    payment_method: str = Field(default="Cash")
    items_count: int = Field(default=3)
    notes: Optional[str] = Field(default="Live POS Walk-in Customer Sale")


@app.post("/api/sales/record")
def record_live_pos_sale(req: RecordSaleRequest):
    """
    Inserts a real Sale transaction into the SQLite/PostgreSQL database, updates
    Business.current_cash_in_drawer, appends to the CSV Spreadsheet ledger, and logs an AuditEvent.
    """
    from n8n.executor import append_to_spreadsheet

    db = SessionLocal()
    try:
        biz = db.query(Business).filter(Business.id == req.business_id).first()
        if not biz:
            raise HTTPException(status_code=404, detail="Business not found")

        order_no = f"ORD-{datetime.datetime.now().strftime('%H%M%S')}-{uuid.uuid4().hex[:3].upper()}"
        sale = Sale(
            id=f"sale_{uuid.uuid4().hex[:8]}",
            business_id=req.business_id,
            order_number=order_no,
            total_amount=float(req.amount),
            payment_method=req.payment_method,
            payment_status="PAID",
            items_count=req.items_count,
            notes=req.notes,
            created_at=datetime.datetime.now(datetime.UTC)
        )
        db.add(sale)

        if req.payment_method.lower() == "cash":
            biz.current_cash_in_drawer = float(biz.current_cash_in_drawer or 0.0) + float(req.amount)

        audit = AuditEvent(
            id=f"audit_{uuid.uuid4().hex[:8]}",
            business_id=req.business_id,
            task_id="live_pos",
            source="POS_DATABASE",
            event_type="LIVE_SALE_RECORDED",
            summary=f"Recorded live POS sale {order_no} for ₹{req.amount:,.2f} ({req.payment_method}) in database.",
            details={
                "order_number": order_no,
                "amount": req.amount,
                "payment_method": req.payment_method,
                "new_cash_in_drawer": biz.current_cash_in_drawer
            }
        )
        db.add(audit)
        db.commit()

        append_to_spreadsheet({
            "Timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "Action_ID": sale.id,
            "Branch_Executed": "LIVE_POS_DATABASE_SALE",
            "Reference_No": order_no,
            "Counterparty": "Walk-in Store Customer",
            "Phone": "-",
            "Amount_INR": req.amount,
            "Details": f"{req.payment_method} Sale ({req.items_count} items) - {req.notes}",
            "External_Status": "COMMITTED_TO_DB_AND_SHEET"
        })

        all_sales = db.query(Sale).filter(Sale.business_id == req.business_id).all()
        new_total = sum(s.total_amount for s in all_sales)

        return {
            "status": "RECORDED_IN_DB",
            "order_number": order_no,
            "amount_added": req.amount,
            "payment_method": req.payment_method,
            "new_total_sales_today": new_total,
            "new_transactions_count": len(all_sales),
            "new_cash_in_drawer": biz.current_cash_in_drawer
        }
    finally:
        db.close()


@app.get("/api/spreadsheet")
def get_spreadsheet_rows():
    """Returns all rows from the persistent CSV Spreadsheet Ledger."""
    import csv
    from n8n.executor import SPREADSHEET_PATH, append_to_spreadsheet

    if not os.path.exists(SPREADSHEET_PATH):
        # Seed initial baseline row so spreadsheet is immediately ready
        append_to_spreadsheet({
            "Timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "Action_ID": "act_init_01",
            "Branch_Executed": "SYSTEM_INITIALIZED",
            "Reference_No": "LEDGER-INIT",
            "Counterparty": "Green Valley Organic Grocers",
            "Phone": "+919876543210",
            "Amount_INR": 9850.0,
            "Details": "Opening daily POS & n8n spreadsheet ledger",
            "External_Status": "READY"
        })

    rows = []
    try:
        with open(SPREADSHEET_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append(r)
    except Exception as e:
        return {"rows": [], "error": str(e)}

    return {
        "spreadsheet_path": SPREADSHEET_PATH,
        "count": len(rows),
        "rows": list(reversed(rows))
    }


@app.get("/api/spreadsheet/download")
def download_spreadsheet_csv():
    from fastapi.responses import FileResponse
    from n8n.executor import SPREADSHEET_PATH
    if not os.path.exists(SPREADSHEET_PATH):
        get_spreadsheet_rows()
    return FileResponse(
        SPREADSHEET_PATH,
        media_type="text/csv",
        filename="operator_ledger_spreadsheet.csv"
    )


@app.get("/api/n8n/workflow-json")
def download_n8n_workflow_json():
    from fastapi.responses import FileResponse
    from db.database import ROOT_DIR
    wf_path = os.path.join(ROOT_DIR, "n8n", "workflows", "operator_os_n8n_cloud_import.json")
    return FileResponse(
        wf_path,
        media_type="application/json",
        filename="operator_os_n8n_multi_branch_workflow.json"
    )


@app.get("/api/integrations/config")
def get_integrations_config():
    from n8n.executor import _refresh_env, _extract_google_sheet_id
    _refresh_env()
    calle_key = os.environ.get("CALLE_API_KEY", "").strip()
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    sarvam_key = _get_live_sarvam_key()
    n8n_url = os.environ.get("N8N_BASE_URL", "").strip()
    return {
        "n8n_base_url": n8n_url,
        "n8n_webhook_url": n8n_url,
        "sarvam_api_key_set": bool(sarvam_key),
        "sarvam_configured": bool(sarvam_key),
        "sarvam_api_key_masked": (sarvam_key[:8] + "...") if sarvam_key else "",
        "calle_api_key_set": bool(calle_key),
        "calle_configured": bool(calle_key),
        "calle_api_key_preview": (calle_key[:8] + "...") if calle_key else "",
        "calle_api_key_masked": (calle_key[:8] + "...") if calle_key else "",
        "supplier_phone_number": os.environ.get("SUPPLIER_PHONE_NUMBER", "+919876543210").strip(),
        "telegram_bot_token_set": bool(tg_token),
        "telegram_configured": bool(tg_token),
        "telegram_bot_token_masked": (tg_token[:9] + "...") if tg_token else "",
        "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
        "google_sheet_id": _extract_google_sheet_id() or os.environ.get("GOOGLE_SHEET_ID", "").strip(),
        "spreadsheet_webhook_url": os.environ.get("SPREADSHEET_WEBHOOK_URL", "").strip()
    }


@app.post("/api/integrations/config")
async def update_integrations_config(request: Request):
    """Updates runtime environment variables and persists them to .env."""
    from db.database import ENV_FILE
    body = await request.json()
    key_map = {
        "n8n_base_url": "N8N_BASE_URL",
        "sarvam_api_key": "SARVAM_API_KEY",
        "calle_api_key": "CALLE_API_KEY",
        "supplier_phone_number": "SUPPLIER_PHONE_NUMBER",
        "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
        "telegram_chat_id": "TELEGRAM_CHAT_ID",
        "google_sheet_id": "GOOGLE_SHEET_ID",
        "spreadsheet_webhook_url": "SPREADSHEET_WEBHOOK_URL"
    }
    updated = []
    for req_k, env_k in key_map.items():
        if req_k in body and body[req_k] is not None:
            val = str(body[req_k]).strip()
            if req_k in ("calle_api_key", "telegram_bot_token", "sarvam_api_key") and not val:
                continue
            os.environ[env_k] = val
            updated.append(env_k)
    if "SARVAM_API_KEY" in updated:
        TTS_AUDIO_CACHE.clear()
        TRANSLATION_CACHE.clear()

    # Persist to .env file
    if os.path.exists(ENV_FILE) and updated:
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            existing_keys = set()
            new_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    k = stripped.split("=", 1)[0].strip()
                    if k in updated:
                        new_lines.append(f"{k}={os.environ.get(k, '')}\n")
                        existing_keys.add(k)
                        continue
                new_lines.append(line)
            for k in updated:
                if k not in existing_keys:
                    new_lines.append(f"{k}={os.environ.get(k, '')}\n")
            with open(ENV_FILE, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
        except Exception as e:
            print(f"[Config Save] Notice: {e}")

    return {"status": "saved", "updated": updated, "config": get_integrations_config()}


@app.post("/api/integrations/test-calle")
async def test_calle_supplier_call(request: Request):
    """Places an immediate test outbound supplier voice call via CALL-E (heycall-e.com)."""
    from n8n.executor import N8nActionExecutor
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    if body.get("calle_api_key"):
        os.environ["CALLE_API_KEY"] = str(body["calle_api_key"]).strip()
    if body.get("supplier_phone_number"):
        os.environ["SUPPLIER_PHONE_NUMBER"] = str(body["supplier_phone_number"]).strip()

    phone = os.environ.get("SUPPLIER_PHONE_NUMBER", "+919876543210").strip()
    res = N8nActionExecutor._trigger_calle_supplier_call(
        order_number=f"PO-TEST-{uuid.uuid4().hex[:4].upper()}",
        supplier_name="MilkyWay Fresh Foods Co.",
        supplier_phone=phone,
        items=[
            {"name": "Organic A2 Farm Milk (1L)", "quantity": 25},
            {"name": "Free-Range Brown Eggs (6pc)", "quantity": 20},
            {"name": "Artisanal Sourdough Bread", "quantity": 10}
        ],
        total_amount=4500.0,
        action_id=f"act_test_{uuid.uuid4().hex[:6]}",
        business_id="biz_001"
    )
    return res


@app.post("/api/integrations/test-telegram")
async def test_telegram_message(request: Request):
    """Sends an immediate test payment reminder / store notification to Telegram."""
    from n8n.executor import N8nActionExecutor
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    if body.get("telegram_bot_token"):
        os.environ["TELEGRAM_BOT_TOKEN"] = str(body["telegram_bot_token"]).strip()
    if body.get("telegram_chat_id"):
        os.environ["TELEGRAM_CHAT_ID"] = str(body["telegram_chat_id"]).strip()

    msg = (
        "💳 *OperatorOS — Live Telegram Integration Verified!*\n\n"
        "• *Store:* Green Valley Organic Grocers\n"
        "• *Branch 2 (Payment Reminders):* Priya Sharma (₹1,850) & Vikram Rao (₹2,400)\n"
        "• *Branch 1 (Supplier PO):* Connected to CALL-E & Spreadsheet\n"
        "• *Status:* Ready for voice-activated dispatches!"
    )
    return N8nActionExecutor._send_telegram_notification(msg)


@app.get("/mcp")
def mcp_info():
    """MCP Server discovery endpoint for TrueForge (http://localhost:8790)."""
    return {
        "name": "OperatorOS Business MCP Server",
        "version": "1.0.0",
        "protocol": "mcp/json-rpc-2.0",
        "endpoint": "http://localhost:8000/mcp",
        "stdio_command": "python -m mcp_business.server",
        "tools_count": 13
    }


@app.post("/mcp")
async def mcp_jsonrpc_endpoint(request: Request):
    """
    Full JSON-RPC 2.0 Model Context Protocol (MCP) Server endpoint so TrueForge (http://localhost:8790)
    can connect directly to http://localhost:8000/mcp and list/invoke all 13 OperatorOS business tools.
    """
    from mcp_business.server import (
        tool_get_business_profile,
        tool_get_sales_data,
        tool_get_inventory,
        tool_get_customer_dues,
        tool_get_supplier_information,
        tool_search_business_memory,
        tool_run_analysis_in_sandbox,
        tool_prepare_purchase_order,
        tool_create_purchase_order,
        tool_prepare_campaign,
        tool_broadcast_campaign,
        tool_prepare_customer_dues_reminder,
        tool_send_customer_messages,
        tool_get_action_status
    )

    body = await request.json()
    rpc_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params") or {}

    tools_manifest = [
        {
            "name": "get_business_profile",
            "description": "Retrieve merchant profile, store hours, cash float rules, and live drawer balance from Supabase DB.",
            "inputSchema": {"type": "object", "properties": {"business_id": {"type": "string", "default": "biz_001"}}}
        },
        {
            "name": "get_sales_data",
            "description": "Retrieve live POS sales transactions, totals, and Cash/UPI/Card split from Supabase DB.",
            "inputSchema": {"type": "object", "properties": {"business_id": {"type": "string", "default": "biz_001"}, "date_filter": {"type": "string", "default": "today"}}}
        },
        {
            "name": "get_inventory",
            "description": "Retrieve live product inventory and low-stock items below reorder level from Supabase DB.",
            "inputSchema": {"type": "object", "properties": {"business_id": {"type": "string", "default": "biz_001"}, "low_stock_only": {"type": "boolean", "default": True}}}
        },
        {
            "name": "get_customer_dues",
            "description": "Retrieve customers with overdue store credit balances from Supabase DB.",
            "inputSchema": {"type": "object", "properties": {"business_id": {"type": "string", "default": "biz_001"}, "min_overdue_days": {"type": "integer", "default": 7}}}
        },
        {
            "name": "get_supplier_information",
            "description": "Retrieve supplier contacts, phone numbers, and order cutoff times from Supabase DB.",
            "inputSchema": {"type": "object", "properties": {"business_id": {"type": "string", "default": "biz_001"}}}
        },
        {
            "name": "search_business_memory",
            "description": "Query Cognee semantic business memory for store operating policies and supplier rules.",
            "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "business_id": {"type": "string", "default": "biz_001"}}, "required": ["query"]}
        },
        {
            "name": "run_analysis_in_sandbox",
            "description": "Execute financial reconciliation, reorder math, or campaign ROI scripts in TrueForge/Daytona Sandbox.",
            "inputSchema": {"type": "object", "properties": {"python_code": {"type": "string"}, "context_data": {"type": "object"}}, "required": ["python_code"]}
        },
        {
            "name": "prepare_purchase_order",
            "description": "Stage a supplier purchase order in Supabase DB awaiting human approval.",
            "inputSchema": {"type": "object", "properties": {"business_id": {"type": "string", "default": "biz_001"}, "supplier_id": {"type": "string"}, "items": {"type": "array"}}, "required": ["supplier_id", "items"]}
        },
        {
            "name": "create_purchase_order",
            "description": "Execute an approved purchase order via n8n Cloud, CALL-E supplier voice call, Google Sheets, and Telegram.",
            "inputSchema": {"type": "object", "properties": {"action_id": {"type": "string"}}, "required": ["action_id"]}
        },
        {
            "name": "prepare_campaign",
            "description": "Stage a promotional customer campaign in Supabase DB awaiting human approval.",
            "inputSchema": {"type": "object", "properties": {"business_id": {"type": "string", "default": "biz_001"}, "title": {"type": "string"}, "channel": {"type": "string"}, "discount_pct": {"type": "integer"}, "target_segment": {"type": "string"}, "copy_text": {"type": "string"}, "estimated_revenue": {"type": "number"}}}
        },
        {
            "name": "broadcast_campaign",
            "description": "Broadcast an approved campaign via n8n Cloud, Telegram, and Google Sheets.",
            "inputSchema": {"type": "object", "properties": {"action_id": {"type": "string"}}, "required": ["action_id"]}
        },
        {
            "name": "prepare_customer_dues_reminder",
            "description": "Stage overdue customer credit payment reminders in Supabase DB awaiting human approval.",
            "inputSchema": {"type": "object", "properties": {"business_id": {"type": "string", "default": "biz_001"}, "customer_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["customer_ids"]}
        },
        {
            "name": "send_customer_messages",
            "description": "Dispatch approved overdue payment reminders via n8n Cloud, Telegram Bot, and Google Sheets.",
            "inputSchema": {"type": "object", "properties": {"action_id": {"type": "string"}}, "required": ["action_id"]}
        },
        {
            "name": "get_action_status",
            "description": "Verify action execution across Supabase DB, n8n Cloud, and Spreadsheet ledger.",
            "inputSchema": {"type": "object", "properties": {"action_id": {"type": "string"}}, "required": ["action_id"]}
        }
    ]

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "OperatorOS Business MCP Server", "version": "1.0.0"}
            }
        }
    if method == "notifications/initialized":
        return {"jsonrpc": "2.0", "id": rpc_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {"tools": tools_manifest}
        }
    if method == "tools/call":
        t_name = params.get("name", "")
        t_args = params.get("arguments") or {}
        dispatch_map = {
            "get_operational_summary": lambda a: tool_get_operational_summary(),
            "get_today_schedule": lambda a: tool_get_today_schedule(),
            "get_pending_tasks": lambda a: tool_get_pending_tasks(a.get("department")),
            "get_followups": lambda a: tool_get_followups(a.get("status")),
            "get_department_directory": lambda a: tool_get_department_directory(),
            "create_inventory_request": lambda a: tool_create_inventory_request(
                a.get("item_name", "Examination Gloves"), int(a.get("quantity", 20)),
                a.get("unit", "boxes"), a.get("department", "General Ward"),
                a.get("requested_by", "Staff"), a.get("priority", "Standard")
            ),
            "initiate_call": lambda a: tool_initiate_call(
                a.get("department", "Biomedical Engineering"),
                a.get("purpose", "Maintenance request update"),
                a.get("initiated_by", "Voice Command")
            ),
            "get_business_profile": lambda a: tool_get_business_profile(a.get("business_id", "biz_001")),
            "get_sales_data": lambda a: tool_get_sales_data(a.get("business_id", "biz_001"), a.get("date_filter", "today")),
            "get_inventory": lambda a: tool_get_inventory(a.get("business_id", "biz_001"), a.get("low_stock_only", False)),
            "get_customer_dues": lambda a: tool_get_customer_dues(a.get("business_id", "biz_001"), a.get("min_overdue_days", 7)),
            "get_supplier_information": lambda a: tool_get_supplier_information(a.get("business_id", "biz_001")),
            "search_business_memory": lambda a: tool_search_business_memory(a.get("query", ""), a.get("business_id", "biz_001")),
            "run_analysis_in_sandbox": lambda a: tool_run_analysis_in_sandbox(a.get("python_code", ""), a.get("context_data")),
            "prepare_purchase_order": lambda a: tool_prepare_purchase_order(a.get("business_id", "biz_001"), a.get("supplier_id", "supp_01"), a.get("items", [])),
            "create_purchase_order": lambda a: tool_create_purchase_order(a.get("action_id", "")),
            "prepare_campaign": lambda a: tool_prepare_campaign(
                a.get("business_id", "biz_001"),
                a.get("title", "Weekend Organic Harvest"),
                a.get("channel", "WhatsApp Broadcast"),
                a.get("discount_pct", 15),
                a.get("target_segment", "142 Loyal Customers"),
                a.get("copy_text", "15% OFF this weekend!"),
                a.get("estimated_revenue", 22397.5)
            ),
            "broadcast_campaign": lambda a: tool_broadcast_campaign(a.get("action_id", "")),
            "prepare_customer_dues_reminder": lambda a: tool_prepare_customer_dues_reminder(a.get("business_id", "biz_001"), a.get("customer_ids", ["cust_01", "cust_02"])),
            "send_customer_messages": lambda a: tool_send_customer_messages(a.get("action_id", "")),
            "get_action_status": lambda a: tool_get_action_status(a.get("action_id", ""))
        }
        fn = dispatch_map.get(t_name)
        if not fn:
            return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": -32601, "message": f"Tool {t_name} not found"}}
        out = fn(t_args)
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(out, indent=2)}],
                "isError": False
            }
        }

    return {"jsonrpc": "2.0", "id": rpc_id, "result": {"status": "ok"}}


# ==============================================================================
# ESP32 SPI TFT HOSPITAL OPERATIONS DASHBOARD ENDPOINTS
# ==============================================================================

class ESP32CommandRequest(BaseModel):
    business_id: str = Field(default="biz_001")
    command: str = Field(..., description="Hospital staff command or 'approve'/'deny'")
    task_id: Optional[str] = Field(default=None)


@app.get("/api/esp32/dashboard")
def get_esp32_dashboard(business_id: str = "biz_001"):
    """Compact flat JSON payload tailored for the ESP32 320x240 SPI TFT Hospital Operations Dashboard."""
    db = SessionLocal()
    try:
        tasks_count = db.query(HospitalTask).filter(HospitalTask.status.in_(["Pending", "Awaiting Approval", "In Progress"])).count()
        calls_count = db.query(HospitalCall).count()
        inv_reqs_count = db.query(InventoryRequest).filter(InventoryRequest.status.in_(["Pending Approval", "Approved"])).count()
        followups_count = db.query(FollowUp).count()
        devices_online = db.query(HospitalDevice).filter(HospitalDevice.status == "ONLINE").count()
        devices_total = db.query(HospitalDevice).count()

        latest_session = None
        for sess in reversed(list(active_sessions.values())):
            if sess.business_id == business_id:
                latest_session = sess
                break

        agent_status = latest_session.status if latest_session else "ONLINE"
        agent_msg = "HospiOne systems nominal. Ready for hospital voice commands."
        last_cmd = "Command Center Ready"
        waiting_approval = False
        active_task_id = latest_session.session_id if latest_session else ""

        if latest_session:
            waiting_approval = (latest_session.status == "WAITING_FOR_APPROVAL")
            for evt in reversed(latest_session.events):
                if evt.get("voice_text"):
                    agent_msg = evt["voice_text"]
                    break
                elif evt.get("summary"):
                    agent_msg = evt["summary"]
                    break

        return {
            "hospital_name": "HospiOne Ops",
            "store_name": "HospiOne Ops",  # compatibility with ESP32 TFT header
            "owner": "Dr. Demo",
            "pending_tasks": tasks_count,
            "calls_today": calls_count,
            "inventory_requests": inv_reqs_count,
            "followups_due": followups_count,
            "devices_online": f"{devices_online}/{devices_total}",
            # compatibility keys for older esp32 firmware:
            "sales_today": tasks_count,
            "cash_sales": calls_count,
            "upi_sales": inv_reqs_count,
            "cash_drawer": followups_count,
            "low_stock": inv_reqs_count,
            "overdue_dues": tasks_count,
            "overdue_count": followups_count,
            "agent_status": agent_status,
            "waiting_approval": 1 if waiting_approval else 0,
            "task_id": active_task_id,
            "last_cmd": last_cmd[:36],
            "agent_msg": agent_msg[:180]
        }
    finally:
        db.close()


@app.post("/api/esp32/command")
async def execute_esp32_command(req: ESP32CommandRequest):
    """Executes a staff command, interactive answer, or approval decision from the ESP32 dashboard."""
    cmd_clean = req.command.strip()
    cmd_lower = cmd_clean.lower()

    # Locate latest session if any
    target_session = None
    if req.task_id and req.task_id in active_sessions:
        target_session = active_sessions[req.task_id]
    else:
        for sess in reversed(list(active_sessions.values())):
            if sess.business_id == req.business_id:
                target_session = sess
                break

    # 1. Handle Approve / Deny if a session is waiting for approval
    if target_session and target_session.status == "WAITING_FOR_APPROVAL" and cmd_lower in ("approve", "yes", "allow", "a", "deny", "reject", "no", "d"):
        decision = "allow" if cmd_lower in ("approve", "yes", "allow", "a") else "deny"
        async for _ in target_session.resume_after_approval(decision=decision):
            pass
    # 2. Handle interactive question continuation
    elif target_session and target_session.status == "WAITING_FOR_USER_INPUT":
        async for _ in target_session.continue_task_stream(cmd_clean):
            pass
    # 3. Handle quick hospital actions from hardware buttons
    elif cmd_lower in ("glove", "+glove", "request glove", "gloves"):
        tool_create_inventory_request(
            item_name="Examination Gloves (Nitrile Powder-Free)",
            quantity=20,
            unit="boxes",
            department="OPD Reception Counter",
            requested_by="ESP32 Device Staff",
            priority="Standard"
        )
    elif cmd_lower in ("call", "call biomed", "call engineering"):
        tool_initiate_call(
            department="Biomedical Engineering",
            purpose="Urgent maintenance inquiry from ESP32 Reception Device",
            initiated_by="ESP32 Device #01"
        )
    # 4. Otherwise start a new HospiOne agent task
    else:
        task_id = f"esp32_{uuid.uuid4().hex[:6]}"
        sess = get_or_create_session(task_id, req.business_id)
        async for _ in sess.execute_task_stream(cmd_clean):
            pass

    state = get_esp32_dashboard(req.business_id)
    state["last_cmd"] = cmd_clean[:36]
    return state



@app.post("/v1/chat/completions")
async def trueforge_openai_compatible_completions(request: Request):
    """
    OpenAI-compatible Chat Completions endpoint for TrueForge (http://localhost:8790)
    so TrueFoundry model calls execute real MCP tools against Supabase PostgreSQL,
    Daytona Sandbox, Cognee Memory, n8n Cloud, CALL-E, Telegram, and Google Sheets
    even if external LLM gateway token quotas are exhausted.
    """
    import time
    body = await request.json()
    messages = body.get("messages", [])
    tools = body.get("tools", [])
    stream = body.get("stream", False)
    model_name = body.get("model", "gemini-2.5-flash")

    # Map available tool names sent by TrueForge
    available_tool_names = {}
    for t in tools:
        fn = t.get("function", {})
        t_name = fn.get("name", "")
        for base_t in (
            "get_business_profile",
            "get_sales_data",
            "get_inventory",
            "get_customer_dues",
            "get_supplier_information",
            "search_business_memory",
            "run_analysis_in_sandbox",
            "prepare_purchase_order",
            "create_purchase_order",
            "prepare_campaign",
            "broadcast_campaign",
            "prepare_customer_dues_reminder",
            "send_customer_messages",
            "get_action_status",
        ):
            if t_name == base_t or t_name.endswith(f"__{base_t}") or base_t in t_name:
                available_tool_names[base_t] = t_name

    last_msg = messages[-1] if messages else {}
    last_role = last_msg.get("role", "user")

    # Check if we already executed tools in this turn
    has_tool_results = any(m.get("role") == "tool" for m in messages)

    # Extract latest user text
    user_text = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content", "")
            if isinstance(c, list):
                user_text = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
            else:
                user_text = str(c)
            break
    u_low = user_text.lower()

    # Step 1: If tools are available and haven't been called yet, emit real MCP tool calls!
    if available_tool_names and not has_tool_results and last_role == "user":
        selected_calls = []
        if any(w in u_low for w in ("stock", "inventory", "reorder", "supplier", "order", "po", "milk", "atta", "dal")):
            if "get_inventory" in available_tool_names:
                selected_calls.append((available_tool_names["get_inventory"], {"business_id": "biz_001", "low_stock_only": True}))
            if "get_supplier_information" in available_tool_names:
                selected_calls.append((available_tool_names["get_supplier_information"], {"business_id": "biz_001"}))
            if any(w in u_low for w in ("sandbox", "margin", "analysis")):
                if "run_analysis_in_sandbox" in available_tool_names:
                    selected_calls.append((
                        available_tool_names["run_analysis_in_sandbox"],
                        {
                            "python_code": "items = [{'sku': 'Nandini Milk', 'qty': 40, 'cost': 24}, {'sku': 'Whole Wheat Atta 5kg', 'qty': 15, 'cost': 210}, {'sku': 'Toor Dal 1kg', 'qty': 20, 'cost': 145}]\ntotal = sum(i['qty']*i['cost'] for i in items)\nprint(f'Recommended PO Total: INR {total}')",
                            "context_data": {"business_id": "biz_001"}
                        }
                    ))
        elif any(w in u_low for w in ("overdue", "khata", "due", "reminder", "credit", "payment", "campaign")):
            if "get_customer_dues" in available_tool_names:
                selected_calls.append((available_tool_names["get_customer_dues"], {"business_id": "biz_001", "min_overdue_days": 7}))
            if "search_business_memory" in available_tool_names:
                selected_calls.append((available_tool_names["search_business_memory"], {"query": "credit recovery and cash float policy", "business_id": "biz_001"}))
        else:
            for k, args in (
                ("get_business_profile", {"business_id": "biz_001"}),
                ("get_sales_data", {"business_id": "biz_001", "date_filter": "today"}),
                ("get_inventory", {"business_id": "biz_001", "low_stock_only": True}),
                ("get_customer_dues", {"business_id": "biz_001", "min_overdue_days": 7}),
            ):
                if k in available_tool_names:
                    selected_calls.append((available_tool_names[k], args))

        if selected_calls:
            tool_calls_payload = [
                {
                    "index": idx,
                    "id": f"call_{uuid.uuid4().hex[:10]}",
                    "type": "function",
                    "function": {"name": t_name, "arguments": json.dumps(t_args)},
                }
                for idx, (t_name, t_args) in enumerate(selected_calls)
            ]
            if not stream:
                return {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:10]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model_name,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": None, "tool_calls": tool_calls_payload},
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 180, "completion_tokens": 65, "total_tokens": 245},
                }

            async def _stream_tool_calls():
                cid = f"chatcmpl-{uuid.uuid4().hex[:10]}"
                created = int(time.time())
                chunk1 = {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_name,
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": "", "tool_calls": tool_calls_payload}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk1)}\n\n"
                chunk2 = {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_name,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 180, "completion_tokens": 65, "total_tokens": 245},
                }
                yield f"data: {json.dumps(chunk2)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(_stream_tool_calls(), media_type="text/event-stream")

    # Step 2: Build rich executive response from live Supabase data + MCP tool outputs
    from mcp_business.server import tool_get_inventory, tool_get_customer_dues
    summary = get_business_summary("biz_001")
    biz = summary["business"]
    metrics = summary["metrics"]
    inv_data = tool_get_inventory("biz_001", True)
    dues_data = tool_get_customer_dues("biz_001", 7)
    low_stock_list = ", ".join(
        f"**{i.get('product_name', i.get('name', 'Item'))}** ({i.get('current_stock', 0)} {i.get('unit', 'units')} left vs reorder {i.get('reorder_level', 10)})"
        for i in inv_data.get("items", [])[:4]
    )
    overdue_list = ", ".join(
        f"**{c.get('name', 'Customer')}** (₹{c.get('outstanding_due', 0):,.0f} overdue {c.get('due_since_days', 7)}d)"
        for c in dues_data.get("overdue_customers", [])[:4]
    )

    reply_md = (
        f"### OperatorOS Live Execution Report — {biz['name']}\n\n"
        f"- **Today's Live POS Sales (Supabase PostgreSQL)**: **₹{metrics['total_sales_today']:,.0f}** "
        f"(Cash: ₹{metrics['cash_sales']:,.0f} | UPI: ₹{metrics['upi_sales']:,.0f} | Card: ₹{metrics['card_sales']:,.0f})\n"
        f"- **Cash Drawer Float**: **₹{biz['current_cash_in_drawer']:,.0f}** (Min safety reserve: ₹{biz.get('cash_drawer_target', 5000):,.0f})\n"
        f"- **Low-Stock Inventory Alert ({metrics['low_stock_items_count']} SKUs)**: {low_stock_list}\n"
        f"- **Overdue Khata Credit ({metrics['overdue_customers_count']} Customers — ₹{metrics['total_overdue_dues']:,.0f})**: {overdue_list}\n\n"
        f"**Connected Action Pipelines Ready**:\n"
        f"1. **n8n Cloud (`PURCHASE_ORDER`)**: Logs to Google Sheets (`PurchaseOrders`) + dials supplier via **CALL-E (`+918496074290`)**.\n"
        f"2. **n8n Cloud (`PAYMENT_REMINDER`)**: Logs to Google Sheets (`PaymentReminders`) + sends live **Telegram** alerts.\n"
        f"3. **n8n Cloud (`CAMPAIGN_BROADCAST`)**: Logs to Google Sheets (`Campaigns`) + broadcasts **Weekend Organic Harvest (15% OFF)**."
    )

    if not stream:
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:10]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_name,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": reply_md}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 340, "completion_tokens": 195, "total_tokens": 535},
        }

    async def _stream_text():
        cid = f"chatcmpl-{uuid.uuid4().hex[:10]}"
        created = int(time.time())
        chunk1 = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": reply_md}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk1)}\n\n"
        chunk2 = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 340, "completion_tokens": 195, "total_tokens": 535},
        }
        yield f"data: {json.dumps(chunk2)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_stream_text(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("apps.api.main:app", host="0.0.0.0", port=8000, reload=True)




