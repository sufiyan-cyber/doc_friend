import uuid
import datetime
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from db.database import SessionLocal
from db.models import Business, Product, Inventory, Supplier, Sale, Customer, PurchaseOrder, ActionRecord, AuditEvent
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
# Official MCP Server Registration (FastMCP — Stdio & Streamable HTTP for TrueForge)
# Connect in TrueForge (http://localhost:8790):
#   Command: python -m mcp_business.server   (Cwd: A:\emberground)
#   Or HTTP: http://127.0.0.1:8000/mcp
# ==============================================================================
try:
    from mcp.server.fastmcp import FastMCP
    mcp_server = FastMCP("OperatorOS Business MCP")

    mcp_server.tool(name="get_business_profile", description="Retrieve merchant profile, cash float rules, and live drawer balance from Supabase DB.")(tool_get_business_profile)
    mcp_server.tool(name="get_sales_data", description="Retrieve live POS sales transactions, totals, and Cash/UPI/Card split from Supabase DB.")(tool_get_sales_data)
    mcp_server.tool(name="get_inventory", description="Retrieve live product inventory and low-stock alerts below reorder level from Supabase DB.")(tool_get_inventory)
    mcp_server.tool(name="get_customer_dues", description="Retrieve customers with overdue store credit balances from Supabase DB.")(tool_get_customer_dues)
    mcp_server.tool(name="get_supplier_information", description="Retrieve supplier contacts, phone numbers, and order cutoff times from Supabase DB.")(tool_get_supplier_information)
    mcp_server.tool(name="search_business_memory", description="Query Cognee semantic business memory for store operating policies and supplier rules.")(tool_search_business_memory)
    mcp_server.tool(name="run_analysis_in_sandbox", description="Execute financial reconciliation, reorder math, or campaign ROI scripts in TrueForge/Daytona Sandbox.")(tool_run_analysis_in_sandbox)
    mcp_server.tool(name="prepare_purchase_order", description="Stage a supplier purchase order in Supabase DB awaiting human approval.")(tool_prepare_purchase_order)
    mcp_server.tool(name="create_purchase_order", description="Execute an approved purchase order via n8n Cloud, CALL-E supplier voice call, Google Sheets, and Telegram.")(tool_create_purchase_order)
    mcp_server.tool(name="prepare_campaign", description="Stage a promotional customer campaign awaiting human approval.")(tool_prepare_campaign)
    mcp_server.tool(name="broadcast_campaign", description="Broadcast an approved campaign via n8n Cloud, Telegram, and Google Sheets.")(tool_broadcast_campaign)
    mcp_server.tool(name="prepare_customer_dues_reminder", description="Stage overdue customer credit payment reminders awaiting human approval.")(tool_prepare_customer_dues_reminder)
    mcp_server.tool(name="send_customer_messages", description="Dispatch approved overdue payment reminders via n8n Cloud, Telegram Bot, and Google Sheets.")(tool_send_customer_messages)
    mcp_server.tool(name="get_action_status", description="Verify action execution across Supabase DB, n8n Cloud, and Spreadsheet ledger.")(tool_get_action_status)
except Exception as _mcp_err:
    mcp_server = None

if __name__ == "__main__":
    if mcp_server:
        mcp_server.run()

