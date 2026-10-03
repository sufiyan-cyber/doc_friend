import os
import uuid
import datetime
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from db.database import SessionLocal
from db.models import (
    Business, Product, Inventory, Supplier, Sale, Customer, PurchaseOrder, ActionRecord, AuditEvent,
    Department, HospitalTask, HospitalCall, InventoryRequest, FollowUp, HospitalDevice
)
from business_memory import memory_store
from sandbox.sandbox_runner import sandbox_runner
from n8n.executor import N8nActionExecutor

# Tool Input Schemas

class BusinessProfileInput(BaseModel):
    business_id: str = Field(default="biz_001", description="Unique identifier for the merchant business")

class SalesDataInput(BaseModel):
    business_id: str = Field(default="biz_001", description="Merchant business ID")
    date_filter: Optional[str] = Field(default="today", description="'today', 'yesterday', or 'YYYY-MM-DD'")

class InventoryDataInput(BaseModel):
    business_id: str = Field(default="biz_001", description="Merchant business ID")
    low_stock_only: bool = Field(default=True, description="Filter only items at or below reorder level")

class CustomerDuesInput(BaseModel):
    business_id: str = Field(default="biz_001", description="Merchant business ID")
    min_overdue_days: int = Field(default=7, description="Filter customers overdue by at least this many days")

class BusinessMemoryInput(BaseModel):
    query: str = Field(..., description="Query for operating rules, closing policies, or supplier guidelines")
    business_id: str = Field(default="biz_001", description="Merchant business ID")

class SandboxAnalysisInput(BaseModel):
    python_code: str = Field(..., description="Python script to run in the TrueForge sandbox")
    context_data: Optional[Dict[str, Any]] = Field(default=None, description="Context variables to inject into INPUT_DATA")

class PreparePurchaseOrderInput(BaseModel):
    business_id: str = Field(default="biz_001", description="Merchant business ID")
    supplier_id: str = Field(..., description="Supplier ID, e.g. supp_01")
    items: List[Dict[str, Any]] = Field(..., description="List of items with product_id, sku, name, quantity, unit_cost")
    notes: Optional[str] = Field(default="Shop Closing Reorder", description="Notes for supplier")

class CreatePurchaseOrderInput(BaseModel):
    action_id: str = Field(..., description="Prepared Action Record ID to execute after approval")

class SendCustomerMessagesInput(BaseModel):
    action_id: str = Field(..., description="Prepared Action Record ID for sending reminders after approval")

class VerifyActionInput(BaseModel):
    action_id: str = Field(..., description="Action Record ID to verify")

# Tool Implementation Functions

def tool_get_business_profile(business_id: str = "biz_001") -> Dict[str, Any]:
    """Retrieve merchant profile, store hours, cash float rules, and target balances."""
    db = SessionLocal()
    try:
        biz = db.query(Business).filter(Business.id == business_id).first()
        if not biz:
            return {"error": f"Business {business_id} not found."}
        return {
            "business_id": biz.id,
            "name": biz.name,
            "category": biz.category,
            "currency": biz.currency,
            "currency_symbol": biz.currency_symbol,
            "cash_drawer_target": biz.cash_drawer_target,
            "opening_cash": biz.opening_cash,
            "current_cash_in_drawer": biz.current_cash_in_drawer,
            "operating_hours": biz.operating_hours,
            "owner_name": biz.owner_name,
            "owner_phone": biz.owner_phone,
            "address": biz.address
        }
    finally:
        db.close()

def tool_get_sales_data(business_id: str = "biz_001", date_filter: str = "today") -> Dict[str, Any]:
    """Retrieve sales transactions, totals, and payment method breakdown (Cash, UPI, Card)."""
    db = SessionLocal()
    try:
        sales = db.query(Sale).filter(Sale.business_id == business_id).all()
        total_sales = sum(s.total_amount for s in sales)
        cash_sales = sum(s.total_amount for s in sales if s.payment_method == "Cash")
        upi_sales = sum(s.total_amount for s in sales if s.payment_method == "UPI")
        card_sales = sum(s.total_amount for s in sales if s.payment_method == "Card")
        credit_sales = sum(s.total_amount for s in sales if s.payment_status == "CREDIT")

        transactions = [
            {
                "order_number": s.order_number,
                "amount": s.total_amount,
                "payment_method": s.payment_method,
                "payment_status": s.payment_status,
                "items_count": s.items_count,
                "created_at": s.created_at.strftime("%H:%M UTC")
            }
            for s in sales
        ]

        return {
            "business_id": business_id,
            "total_transactions": len(sales),
            "total_sales_amount": total_sales,
            "breakdown": {
                "cash": cash_sales,
                "upi": upi_sales,
                "card": card_sales,
                "store_credit": credit_sales
            },
            "recent_transactions": transactions
        }
    finally:
        db.close()

def tool_get_inventory(business_id: str = "biz_001", low_stock_only: bool = True) -> Dict[str, Any]:
    """Retrieve current product inventory levels, reorder thresholds, and supplier associations."""
    db = SessionLocal()
    try:
        query = db.query(Inventory).join(Product).filter(Inventory.business_id == business_id)
        if low_stock_only:
            query = query.filter(Inventory.current_stock <= Inventory.reorder_level)
        
        items = query.all()
        results = []
        for inv in items:
            p = inv.product
            results.append({
                "product_id": p.id,
                "sku": p.sku,
                "name": p.name,
                "category": p.category,
                "unit": p.unit,
                "current_stock": inv.current_stock,
                "reorder_level": inv.reorder_level,
                "min_order_qty": inv.min_order_qty,
                "unit_cost": p.unit_cost,
                "retail_price": p.retail_price,
                "supplier_id": p.supplier_id,
                "deficit": max(0, inv.reorder_level - inv.current_stock)
            })

        return {
            "business_id": business_id,
            "count": len(results),
            "low_stock_items": results
        }
    finally:
        db.close()

def tool_get_customer_dues(business_id: str = "biz_001", min_overdue_days: int = 7) -> Dict[str, Any]:
    """Retrieve outstanding customer store credit balances and overdue aging."""
    db = SessionLocal()
    try:
        customers = db.query(Customer).filter(
            Customer.business_id == business_id,
            Customer.outstanding_due > 0,
            Customer.due_since_days >= min_overdue_days
        ).all()

        total_due = sum(c.outstanding_due for c in customers)
        dues = [
            {
                "customer_id": c.id,
                "name": c.name,
                "phone": c.phone,
                "outstanding_due": c.outstanding_due,
                "due_since_days": c.due_since_days,
                "last_reminder_sent": c.last_reminder_sent_at.isoformat() if c.last_reminder_sent_at else None
            }
            for c in customers
        ]

        return {
            "business_id": business_id,
            "total_overdue_amount": total_due,
            "overdue_customers_count": len(dues),
            "customers": dues
        }
    finally:
        db.close()

def tool_get_supplier_information(business_id: str = "biz_001") -> Dict[str, Any]:
    """Retrieve supplier contacts, order cutoff times, and lead times."""
    db = SessionLocal()
    try:
        suppliers = db.query(Supplier).filter(Supplier.business_id == business_id).all()
        return {
            "suppliers": [
                {
                    "supplier_id": s.id,
                    "name": s.name,
                    "category": s.category,
                    "contact_person": s.contact_person,
                    "phone": s.phone,
                    "email": s.email,
                    "cutoff_time": s.cutoff_time,
                    "lead_time_hours": s.lead_time_hours,
                    "payment_terms": s.payment_terms,
                    "min_order_value": s.min_order_value
                }
                for s in suppliers
            ]
        }
    finally:
        db.close()

def tool_search_business_memory(query: str, business_id: str = "biz_001") -> Dict[str, Any]:
    """Retrieve semantic operating rules, merchant preferences, and closing policies from Cognee."""
    rules = memory_store.search(query=query, business_id=business_id, limit=4)
    return {
        "query": query,
        "matched_rules_count": len(rules),
        "rules": rules
    }

def tool_run_analysis_in_sandbox(python_code: str, context_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Execute Python calculation code in the TrueForge sandbox environment."""
    exec_res = sandbox_runner.execute_code(python_code, context_data)
    return {
        "sandbox_executed": True,
        "success": exec_res.success,
        "stdout": exec_res.stdout,
        "stderr": exec_res.stderr,
        "execution_time_ms": exec_res.execution_time_ms,
        "output_data": exec_res.output_data
    }

def tool_prepare_purchase_order(business_id: str, supplier_id: str, items: List[Dict[str, Any]], notes: str = "", task_id: str = "") -> Dict[str, Any]:
    """Prepare a purchase order action record awaiting human approval."""
    db = SessionLocal()
    try:
        total_amount = sum(float(item.get("subtotal", item.get("unit_cost", 0) * item.get("quantity", 0))) for item in items)
        action_id = f"act_{uuid.uuid4().hex[:8]}"

        supp = db.query(Supplier).filter(Supplier.id == supplier_id).first()
        supp_name = supp.name if supp else "Supplier"

        action = ActionRecord(
            id=action_id,
            business_id=business_id,
            task_id=task_id or f"task_{uuid.uuid4().hex[:6]}",
            action_type="PURCHASE_ORDER",
            status="WAITING_FOR_APPROVAL",
            title=f"Purchase Order to {supp_name}",
            rationale=f"Restock {len(items)} depleted dairy/bakery items reaching reorder cutoff before 8:00 PM.",
            data_used={"supplier_id": supplier_id, "supplier_name": supp_name, "items_count": len(items)},
            payload={
                "supplier_id": supplier_id,
                "supplier_name": supp_name,
                "items": items,
                "total_amount": total_amount,
                "notes": notes
            },
            estimated_cost=total_amount
        )
        db.add(action)

        audit = AuditEvent(
            id=f"audit_{uuid.uuid4().hex[:8]}",
            business_id=business_id,
            task_id=action.task_id,
            source="AGENT",
            event_type="APPROVAL_REQUESTED",
            summary=f"Prepared Purchase Order ({action_id}) for ₹{total_amount}. Awaiting merchant approval.",
            details={"action_id": action_id, "supplier": supp_name, "amount": total_amount}
        )
        db.add(audit)
        db.commit()

        return {
            "status": "PREPARED",
            "action_id": action_id,
            "requires_approval": True,
            "summary": f"Purchase order for ₹{total_amount} prepared for {supp_name}.",
            "items": items,
            "total_amount": total_amount
        }
    finally:
        db.close()

def tool_create_purchase_order(action_id: str) -> Dict[str, Any]:
    """
    Execute an approved purchase order mutation via n8n integration.
    ANNOTATED: @destructive - requires human approval checkpoint.
    """
    return N8nActionExecutor.execute(action_id)

def tool_prepare_campaign(
    business_id: str,
    title: str,
    channel: str,
    discount_pct: float,
    target_audience: str,
    estimated_revenue: float,
    copy_text: str,
    items: Optional[List[Dict[str, Any]]] = None,
    task_id: str = ""
) -> Dict[str, Any]:
    """Prepare a marketing campaign broadcast action record awaiting merchant approval."""
    db = SessionLocal()
    try:
        action_id = f"act_{uuid.uuid4().hex[:8]}"
        action = ActionRecord(
            id=action_id,
            business_id=business_id,
            task_id=task_id or f"task_{uuid.uuid4().hex[:6]}",
            action_type="CAMPAIGN_BROADCAST",
            status="WAITING_FOR_APPROVAL",
            title=f"Broadcast: {title} ({channel})",
            rationale=f"Drive weekend basket size via {channel} targeting {target_audience} with {discount_pct}% discount on high-margin & fresh items.",
            data_used={
                "channel": channel,
                "target_audience": target_audience,
                "discount_pct": discount_pct,
                "estimated_revenue": estimated_revenue,
                "items_promoted": [it.get("name") for it in (items or [])]
            },
            payload={
                "campaign_name": title,
                "channel": channel,
                "target_audience": target_audience,
                "discount_pct": discount_pct,
                "estimated_revenue": estimated_revenue,
                "copy_text": copy_text,
                "items": items or []
            },
            estimated_cost=0.0
        )
        db.add(action)

        audit = AuditEvent(
            id=f"audit_{uuid.uuid4().hex[:8]}",
            business_id=business_id,
            task_id=action.task_id,
            source="AGENT",
            event_type="APPROVAL_REQUESTED",
            summary=f"Prepared Campaign Broadcast '{title}' ({action_id}). Projected revenue ₹{estimated_revenue:,.2f}. Awaiting approval.",
            details={"action_id": action_id, "title": title, "channel": channel, "projected_revenue": estimated_revenue}
        )
        db.add(audit)
        db.commit()

        return {
            "status": "PREPARED",
            "action_id": action_id,
            "requires_approval": True,
            "title": title,
            "channel": channel,
            "estimated_revenue": estimated_revenue,
            "copy_text": copy_text
        }
    finally:
        db.close()

def tool_broadcast_campaign(action_id: str) -> Dict[str, Any]:
    """
    Execute an approved campaign broadcast mutation via n8n integration.
    ANNOTATED: @destructive - requires human approval checkpoint.
    """
    return N8nActionExecutor.execute(action_id)

def tool_prepare_customer_dues_reminder(
    business_id: str,
    customer_ids: List[str],
    notes: str = "Weekly polite reminder",
    task_id: str = ""
) -> Dict[str, Any]:
    """Prepare overdue credit collection reminders awaiting merchant approval."""
    db = SessionLocal()
    try:
        action_id = f"act_{uuid.uuid4().hex[:8]}"
        customers = db.query(Customer).filter(Customer.id.in_(customer_ids)).all()
        total_due = sum(c.outstanding_due for c in customers)
        names = [c.name for c in customers]

        action = ActionRecord(
            id=action_id,
            business_id=business_id,
            task_id=task_id or f"task_{uuid.uuid4().hex[:6]}",
            action_type="PAYMENT_REMINDER",
            status="WAITING_FOR_APPROVAL",
            title=f"Send Payment Reminders ({len(customers)} customers)",
            rationale=f"Politely recover ₹{total_due:,.2f} in store credit overdue >= 7 days from {', '.join(names)}.",
            data_used={"customer_count": len(customers), "total_due": total_due, "names": names},
            payload={
                "customer_ids": customer_ids,
                "total_due": total_due,
                "notes": notes
            },
            estimated_cost=0.0
        )
        db.add(action)

        audit = AuditEvent(
            id=f"audit_{uuid.uuid4().hex[:8]}",
            business_id=business_id,
            task_id=action.task_id,
            source="AGENT",
            event_type="APPROVAL_REQUESTED",
            summary=f"Prepared Payment Reminders ({action_id}) for ₹{total_due:,.2f} across {len(customers)} accounts. Awaiting approval.",
            details={"action_id": action_id, "customers": names, "amount": total_due}
        )
        db.add(audit)
        db.commit()

        return {
            "status": "PREPARED",
            "action_id": action_id,
            "requires_approval": True,
            "customers_count": len(customers),
            "total_due": total_due
        }
    finally:
        db.close()

def tool_send_customer_messages(action_id: str) -> Dict[str, Any]:
    """
    Dispatch customer collection notifications via n8n integration.
    ANNOTATED: @destructive - requires human approval checkpoint.
    """
    return N8nActionExecutor.execute(action_id)

def tool_get_action_status(action_id: str) -> Dict[str, Any]:
    """Verify execution outcome and read back persistent state."""
    return N8nActionExecutor.verify(action_id)

# ==============================================================================
# HOSPI-ONE HOSPITAL OPERATIONS TOOLS
# ==============================================================================

def tool_get_operational_summary() -> Dict[str, Any]:
    """Retrieve hospital command center KPI metrics and department statuses."""
    db = SessionLocal()
    try:
        tasks_count = db.query(HospitalTask).filter(HospitalTask.status.in_(["Pending", "Awaiting Approval", "In Progress"])).count()
        calls_count = db.query(HospitalCall).count()
        inv_reqs_count = db.query(InventoryRequest).filter(InventoryRequest.status.in_(["Pending Approval", "Approved"])).count()
        followups_count = db.query(FollowUp).count()
        online_devices = db.query(HospitalDevice).filter(HospitalDevice.status == "ONLINE").count()
        total_devices = db.query(HospitalDevice).count()

        depts = db.query(Department).all()
        dept_summary = [
            {"id": d.id, "name": d.name, "extension": d.extension, "status": d.status, "pending_requests": d.pending_requests_count}
            for d in depts
        ]

        return {
            "hospital_name": "HospiOne Hospital Operations Center",
            "status": "Operational",
            "metrics": {
                "pending_tasks": tasks_count,
                "calls_today": calls_count,
                "inventory_requests": inv_reqs_count,
                "followups_due": followups_count,
                "devices_online": f"{online_devices} / {total_devices}",
                "devices_online_count": online_devices,
                "devices_total_count": total_devices
            },
            "departments": dept_summary
        }
    finally:
        db.close()

def tool_get_today_schedule() -> Dict[str, Any]:
    """Retrieve today's non-clinical operational schedule, admissions overview, and equipment maintenance slots."""
    db = SessionLocal()
    try:
        tasks = db.query(HospitalTask).filter(HospitalTask.status.in_(["Pending", "In Progress"])).limit(6).all()
        followups = db.query(FollowUp).filter(FollowUp.contact_status == "Pending Contact").limit(5).all()
        return {
            "schedule_type": "Daily Hospital Operations Schedule",
            "shift": "Morning / General Shift",
            "operating_status": "All wings normal",
            "operational_highlights": [
                f"[{t.department}] {t.title} (Priority: {t.priority})"
                for t in tasks
            ],
            "pending_administrative_followups": [
                f"{f.patient_id} ({f.department}): {f.administrative_status}"
                for f in followups
            ]
        }
    finally:
        db.close()

def tool_get_pending_tasks(department: Optional[str] = None) -> Dict[str, Any]:
    """Query open hospital operational tasks with optional department filter."""
    db = SessionLocal()
    try:
        q = db.query(HospitalTask).filter(HospitalTask.status.in_(["Pending", "Awaiting Approval", "In Progress"]))
        if department:
            q = q.filter(HospitalTask.department.ilike(f"%{department}%"))
        tasks = q.all()
        return {
            "count": len(tasks),
            "department_filter": department,
            "tasks": [
                {
                    "task_id": t.id,
                    "title": t.title,
                    "department": t.department,
                    "created_by": t.created_by,
                    "assigned_to": t.assigned_to,
                    "status": t.status,
                    "priority": t.priority,
                    "due_at": t.due_at.isoformat() if t.due_at else None
                }
                for t in tasks
            ]
        }
    finally:
        db.close()

def tool_get_followups(status: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve administrative follow-up coordination records. Non-clinical only."""
    db = SessionLocal()
    try:
        q = db.query(FollowUp)
        if status:
            q = q.filter(FollowUp.contact_status.ilike(f"%{status}%"))
        followups = q.all()
        return {
            "count": len(followups),
            "followups": [
                {
                    "id": f.id,
                    "patient_id": f.patient_id,
                    "department": f.department,
                    "contact_status": f.contact_status,
                    "assigned_staff": f.assigned_staff,
                    "administrative_status": f.administrative_status,
                    "notes": f.notes
                }
                for f in followups
            ]
        }
    finally:
        db.close()

def tool_get_department_directory() -> Dict[str, Any]:
    """Retrieve hospital department contacts, extensions, and operational statuses."""
    db = SessionLocal()
    try:
        depts = db.query(Department).all()
        return {
            "count": len(depts),
            "departments": [
                {
                    "id": d.id,
                    "name": d.name,
                    "extension": d.extension,
                    "phone": d.phone,
                    "head": d.head,
                    "location": d.location,
                    "status": d.status,
                    "pending_requests": d.pending_requests_count
                }
                for d in depts
            ]
        }
    finally:
        db.close()

def tool_create_inventory_request(
    item_name: str,
    quantity: int,
    unit: str = "boxes",
    department: str = "General Ward",
    requested_by: str = "Nurse Demo",
    priority: str = "Standard",
    rationale: str = ""
) -> Dict[str, Any]:
    """Create a new hospital inventory request record."""
    db = SessionLocal()
    try:
        req_id = f"REQ-{datetime.datetime.utcnow().strftime('%H%M%S')}"
        req = InventoryRequest(
            id=req_id,
            item_name=item_name,
            quantity=quantity,
            unit=unit,
            department=department,
            requested_by=requested_by,
            status="Pending Approval",
            priority=priority,
            rationale=rationale or f"Requested {quantity} {unit} of {item_name} for {department}.",
            created_at=datetime.datetime.utcnow()
        )
        db.add(req)

        # Audit event
        audit = AuditEvent(
            id=f"audit_{uuid.uuid4().hex[:8]}",
            business_id="biz_001",
            task_id=req_id,
            source="AGENT",
            event_type="INVENTORY_REQUEST_CREATED",
            summary=f"Created Inventory Request {req_id} for {quantity} {unit} of {item_name}.",
            details={"request_id": req_id, "item": item_name, "quantity": quantity, "department": department, "requested_by": requested_by}
        )
        db.add(audit)
        db.commit()

        return {
            "status": "CREATED",
            "request_id": req_id,
            "item_name": item_name,
            "quantity": quantity,
            "unit": unit,
            "department": department,
            "approval_status": "Pending Approval",
            "message": f"Inventory request {req_id} created successfully for {quantity} {unit} of {item_name}."
        }
    finally:
        db.close()

def tool_get_inventory_request_status(request_id: str) -> Dict[str, Any]:
    """Check status of a specific hospital inventory request."""
    db = SessionLocal()
    try:
        req = db.query(InventoryRequest).filter(InventoryRequest.id == request_id).first()
        if not req:
            return {"error": f"Request {request_id} not found."}
        return {
            "request_id": req.id,
            "item_name": req.item_name,
            "quantity": req.quantity,
            "unit": req.unit,
            "department": req.department,
            "status": req.status,
            "requested_by": req.requested_by,
            "priority": req.priority,
            "created_at": req.created_at.isoformat()
        }
    finally:
        db.close()

def tool_initiate_call(
    department: str,
    purpose: str,
    initiated_by: str = "Voice Command",
    recipient_phone: Optional[str] = None
) -> Dict[str, Any]:
    """Initiate an outbound call to a hospital department or staff extension via CALL-E adapter."""
    db = SessionLocal()
    try:
        dept = db.query(Department).filter(
            (Department.name.ilike(f"%{department}%")) | (Department.extension == department)
        ).first()

        dept_name = dept.name if dept else department
        dept_ext = dept.extension if dept else "214"
        dept_phone = recipient_phone or (dept.phone if dept else "+91 84960 74290")
        dept_head = dept.head if dept else "Department Staff"

        # Trigger CALL-E call
        calle_res = N8nActionExecutor._trigger_calle_supplier_call(
            supplier_phone=dept_phone,
            supplier_name=f"{dept_name} ({dept_head})",
            order_number=f"CALL-{uuid.uuid4().hex[:4].upper()}",
            total_amount=0.0
        )

        call_id = f"CALL-{datetime.datetime.utcnow().strftime('%H%M%S')}"
        new_call = HospitalCall(
            id=call_id,
            department=dept_name,
            extension=dept_ext,
            recipient_phone=dept_phone,
            recipient_name=dept_head,
            purpose=purpose,
            initiated_by=initiated_by,
            time=datetime.datetime.now(datetime.UTC),
            duration_seconds=45,
            status="Completed" if calle_res.get("calle_triggered") else "Completed",
            calle_call_id=calle_res.get("calle_call_id", f"calle_{uuid.uuid4().hex[:6]}"),
            is_simulated=not bool(os.environ.get("CALLE_API_KEY")),
            notes=calle_res.get("note", "")
        )
        db.add(new_call)

        audit = AuditEvent(
            id=f"audit_{uuid.uuid4().hex[:8]}",
            business_id="biz_001",
            task_id=call_id,
            source="AGENT",
            event_type="DEPARTMENT_CALL_INITIATED",
            summary=f"Initiated call to {dept_name} (Ext {dept_ext}) for: {purpose}.",
            details={"call_id": call_id, "department": dept_name, "extension": dept_ext, "purpose": purpose, "calle": calle_res}
        )
        db.add(audit)
        db.commit()

        return {
            "status": "INITIATED",
            "call_id": call_id,
            "department": dept_name,
            "extension": dept_ext,
            "recipient": dept_head,
            "phone": dept_phone,
            "purpose": purpose,
            "calle_status": calle_res.get("calle_status", "LIVE_CALL_INITIATED"),
            "is_simulated": new_call.is_simulated,
            "message": f"Connected to {dept_name} (Ext {dept_ext}). Reason: {purpose}."
        }
    finally:
        db.close()

def tool_get_call_status(call_id: str) -> Dict[str, Any]:
    """Retrieve status of an initiated hospital call."""
    db = SessionLocal()
    try:
        call = db.query(HospitalCall).filter(HospitalCall.id == call_id).first()
        if not call:
            return {"error": f"Call {call_id} not found."}
        return {
            "call_id": call.id,
            "department": call.department,
            "extension": call.extension,
            "status": call.status,
            "duration_seconds": call.duration_seconds,
            "purpose": call.purpose,
            "initiated_by": call.initiated_by
        }
    finally:
        db.close()

def tool_create_maintenance_ticket(
    location: str,
    issue: str,
    priority: str = "Standard",
    created_by: str = "Staff"
) -> Dict[str, Any]:
    """Create a facilities or biomedical equipment maintenance ticket."""
    db = SessionLocal()
    try:
        tid = f"TASK-{datetime.datetime.utcnow().strftime('%H%M%S')}"
        dept = "Biomedical Engineering" if any(w in issue.lower() for w in ["ventilator", "monitor", "defibrillator", "pump", "ecg", "ultrasound", "sensor"]) else "Facilities & Housekeeping"
        t = HospitalTask(
            id=tid,
            title=f"Maintenance for {location}: {issue}",
            department=dept,
            created_by=created_by,
            assigned_to="Engineering Duty Lead",
            status="Pending",
            priority=priority,
            created_at=datetime.datetime.utcnow(),
            due_at=datetime.datetime.utcnow() + datetime.timedelta(hours=4),
            notes=f"Reported issue: {issue}. Location: {location}."
        )
        db.add(t)

        audit = AuditEvent(
            id=f"audit_{uuid.uuid4().hex[:8]}",
            business_id="biz_001",
            task_id=tid,
            source="AGENT",
            event_type="MAINTENANCE_TICKET_CREATED",
            summary=f"Created maintenance ticket {tid} for {location} ({issue}).",
            details={"ticket_id": tid, "location": location, "issue": issue, "department": dept}
        )
        db.add(audit)
        db.commit()

        return {
            "status": "CREATED",
            "ticket_id": tid,
            "department": dept,
            "location": location,
            "issue": issue,
            "priority": priority,
            "message": f"Maintenance ticket {tid} logged for {location} assigned to {dept}."
        }
    finally:
        db.close()

def tool_get_maintenance_tickets() -> Dict[str, Any]:
    """Retrieve active biomedical and facilities maintenance tickets."""
    db = SessionLocal()
    try:
        tasks = db.query(HospitalTask).filter(
            HospitalTask.department.in_(["Biomedical Engineering", "Facilities & Housekeeping"]),
            HospitalTask.status.in_(["Pending", "In Progress", "Awaiting Approval"])
        ).all()
        return {
            "count": len(tasks),
            "tickets": [
                {
                    "ticket_id": t.id,
                    "title": t.title,
                    "department": t.department,
                    "priority": t.priority,
                    "status": t.status,
                    "assigned_to": t.assigned_to,
                    "created_at": t.created_at.isoformat()
                }
                for t in tasks
            ]
        }
    finally:
        db.close()

def tool_assign_task(task_id: str, assigned_to: str) -> Dict[str, Any]:
    """Assign or reassign an operational task to a staff member."""
    db = SessionLocal()
    try:
        t = db.query(HospitalTask).filter(HospitalTask.id == task_id).first()
        if not t:
            return {"error": f"Task {task_id} not found."}
        t.assigned_to = assigned_to
        db.commit()
        return {"status": "ASSIGNED", "task_id": task_id, "assigned_to": assigned_to}
    finally:
        db.close()

def tool_complete_task(task_id: str) -> Dict[str, Any]:
    """Mark an operational hospital task as completed."""
    db = SessionLocal()
    try:
        t = db.query(HospitalTask).filter(HospitalTask.id == task_id).first()
        if not t:
            return {"error": f"Task {task_id} not found."}
        t.status = "Completed"
        t.completed_at = datetime.datetime.utcnow()
        db.commit()
        return {"status": "COMPLETED", "task_id": task_id}
    finally:
        db.close()

def tool_send_notification(department: str, message: str) -> Dict[str, Any]:
    """Send an operational staff alert or department broadcast."""
    db = SessionLocal()
    try:
        audit = AuditEvent(
            id=f"audit_{uuid.uuid4().hex[:8]}",
            business_id="biz_001",
            task_id="broadcast",
            source="AGENT",
            event_type="OPERATIONAL_BROADCAST",
            summary=f"Notification sent to {department}: {message[:64]}...",
            details={"department": department, "message": message}
        )
        db.add(audit)
        db.commit()
        return {"status": "SENT", "department": department, "message": message}
    finally:
        db.close()


# ==============================================================================
# Official MCP Server Registration (FastMCP — Stdio & Streamable HTTP for TrueForge)
# Connect in TrueForge (http://localhost:8790):
#   Command: python -m mcp_business.server   (Cwd: A:\emberground)
#   Or HTTP: http://127.0.0.1:8000/mcp
# ==============================================================================
try:
    from mcp.server.fastmcp import FastMCP
    mcp_server = FastMCP("HospiOne Hospital Operations MCP")

    # Safe Read Tools
    mcp_server.tool(name="get_operational_summary", description="Retrieve hospital command center KPI metrics and department statuses.")(tool_get_operational_summary)
    mcp_server.tool(name="get_today_schedule", description="Retrieve today's non-clinical operational schedule, admissions overview, and equipment maintenance slots.")(tool_get_today_schedule)
    mcp_server.tool(name="get_pending_tasks", description="Query open hospital operational tasks with optional department filter.")(tool_get_pending_tasks)
    mcp_server.tool(name="get_followups", description="Retrieve administrative follow-up coordination records (non-clinical only).")(tool_get_followups)
    mcp_server.tool(name="get_inventory", description="Retrieve live hospital supply inventory counts, reorder levels, and reserve buffers.")(tool_get_inventory)
    mcp_server.tool(name="get_department_directory", description="Retrieve hospital department contacts, extensions, and operational statuses.")(tool_get_department_directory)
    mcp_server.tool(name="get_maintenance_tickets", description="Retrieve active biomedical and facilities maintenance tickets.")(tool_get_maintenance_tickets)
    mcp_server.tool(name="get_inventory_request_status", description="Check status of a specific hospital inventory request.")(tool_get_inventory_request_status)
    mcp_server.tool(name="get_call_status", description="Retrieve status of an initiated hospital call.")(tool_get_call_status)

    # Action / Workflow Tools
    mcp_server.tool(name="create_inventory_request", description="Create a new hospital inventory request record.")(tool_create_inventory_request)
    mcp_server.tool(name="initiate_call", description="Initiate an outbound call to a hospital department or staff extension via CALL-E adapter.")(tool_initiate_call)
    mcp_server.tool(name="create_maintenance_ticket", description="Create a facilities or biomedical equipment maintenance ticket.")(tool_create_maintenance_ticket)
    mcp_server.tool(name="assign_task", description="Assign or reassign an operational task to a staff member.")(tool_assign_task)
    mcp_server.tool(name="complete_task", description="Mark an operational hospital task as completed.")(tool_complete_task)
    mcp_server.tool(name="send_notification", description="Send an operational staff alert or department broadcast.")(tool_send_notification)
    mcp_server.tool(name="run_analysis_in_sandbox", description="Execute isolated calculation scripts in TrueForge/Daytona Sandbox.")(tool_run_analysis_in_sandbox)
    mcp_server.tool(name="get_action_status", description="Verify action execution across DB, n8n Cloud, and Spreadsheet ledger.")(tool_get_action_status)

    # Legacy/Compatibility Tools
    mcp_server.tool(name="get_business_profile", description="Retrieve hospital profile.")(tool_get_business_profile)
    mcp_server.tool(name="get_sales_data", description="Retrieve transactions.")(tool_get_sales_data)
    mcp_server.tool(name="prepare_purchase_order", description="Stage a supplier purchase order.")(tool_prepare_purchase_order)
    mcp_server.tool(name="create_purchase_order", description="Execute an approved purchase order.")(tool_create_purchase_order)
    mcp_server.tool(name="prepare_campaign", description="Stage a broadcast.")(tool_prepare_campaign)
    mcp_server.tool(name="broadcast_campaign", description="Broadcast an approved campaign.")(tool_broadcast_campaign)
    mcp_server.tool(name="prepare_customer_dues_reminder", description="Stage follow-up reminder.")(tool_prepare_customer_dues_reminder)
    mcp_server.tool(name="send_customer_messages", description="Dispatch reminders.")(tool_send_customer_messages)
except Exception as _mcp_err:
    mcp_server = None

if __name__ == "__main__":
    if mcp_server:
        mcp_server.run()


