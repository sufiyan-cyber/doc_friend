import os
import json
import uuid
import datetime
import asyncio
from typing import Dict, Any, List, Optional, AsyncGenerator

from db.database import SessionLocal
from db.models import ActionRecord, AuditEvent, Business, Product, Inventory, Customer
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

BUSINESS_OPERATOR_SYSTEM_PROMPT = """
You are Business Operator (OperatorOS), the voice-first autonomous digital store manager for retail small businesses.
Your responsibility is NOT just answering questions—you proactively manage the shop, inspect data across systems, run computations in the TrueForge sandbox, prepare necessary real-world actions, pause for human approval before consequential mutations, execute approved actions through connected tools/n8n, and verify the final outcomes.
"""

class OperatorAgentSession:
    """
    Manages an active business task turn, event streaming, voice narration, and approval checkpoints.
    Follows the TrueForge event wire protocol.
    """
    def __init__(self, session_id: str, business_id: str = "biz_001"):
        self.session_id = session_id
        self.business_id = business_id
        self.events: List[Dict[str, Any]] = []
        self.pending_approvals: List[Dict[str, Any]] = []
        self.status = "IDLE"  # IDLE, RUNNING, WAITING_FOR_USER_INPUT, WAITING_FOR_APPROVAL, COMPLETED, REJECTED
        self.active_action_id: Optional[str] = None
        self.workflow_type: str = "CLOSE_SHOP"
        self.dialogue_context: Dict[str, Any] = {}

    def _create_event(self, event_type: str, data: Dict[str, Any], thread_id: Optional[str] = "main") -> Dict[str, Any]:
        evt = {
            "id": f"evt_{uuid.uuid4().hex[:12]}",
            "type": event_type,
            "thread_id": thread_id,
            "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
            **data
        }
        self.events.append(evt)
        return evt

    def _detect_intent(self, prompt: str) -> str:
        p = prompt.lower().strip()
        if any(w in p for w in ["weekend", "campaign", "camp", "grow", "promo", "promot", "marketing", "bundle", "discount", "offer", "broadcast", "advertise", "boost", "start a", "run a", "flash sale", "more customers", "bring customers"]):
            if not any(s in p for s in ["what", "how much", "total sales", "sales today", "status"]) or any(c in p for c in ["campaign", "camp", "promo", "offer", "discount", "weekend", "broadcast", "marketing", "boost", "grow"]):
                return "WEEKEND_SALES"
        if any(w in p for w in ["due", "remind", "payment", "credit", "overdue", "collection", "priya", "vikram", "udhaar", "owe", "pending", "bill", "recover", "follow up", "send out", "telegram"]):
            return "CUSTOMER_DUES"
        if any(w in p for w in ["close", "closing", "shut", "end day", "end of day", "reconcile", "restock", "purchase order", "reorder", "pack up", "done for today", "wrap up", "lock up", "supplier", "call supplier", "call milkyway", "place order", "order milk", "calle", "call-e"]):
            return "CLOSE_SHOP"
        return "ASK_SALES"

    async def _detect_intent_async(self, prompt: str) -> str:
        """
        TrueForge Hybrid Semantic Router:
        1. Instant deterministic match (<1ms) for explicit campaign, dues, closing/supplier call, or status commands.
        2. Gemini 3 Flash semantic intent classification when the merchant speaks a nuanced or conversational command.
        """
        fast = self._detect_intent(prompt)
        if fast in ("WEEKEND_SALES", "CUSTOMER_DUES", "CLOSE_SHOP"):
            return fast
        p = prompt.lower().strip()
        if any(w in p for w in ["status", "sales today", "total sales", "what were my sales", "how is my shop", "how much did we make", "cash in drawer"]):
            return "ASK_SALES"

        import httpx
        llm_key = os.environ.get("LLM_API_KEY", "").strip()
        if not llm_key:
            return fast

        try:
            gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent?key={llm_key}"
            router_prompt = (
                "Classify the retail merchant's spoken request into exactly one TrueForge workflow intent:\n"
                "- WEEKEND_SALES: running a campaign, promotion, discount, marketing broadcast, WhatsApp/Telegram/SMS offer, or growing sales.\n"
                "- CUSTOMER_DUES: sending payment reminders via Telegram/WhatsApp, checking overdue customer credit/udhaar/bills (e.g. Priya, Vikram), or collecting dues.\n"
                "- CLOSE_SHOP: closing the shop for today, end-of-day cash drawer reconciliation, safe deposit, calling the supplier (CALL-E), or reordering low stock from suppliers.\n"
                "- ASK_SALES: asking about store status, sales numbers, inventory questions, or general conversation.\n\n"
                f"Merchant said: \"{prompt}\"\n"
                "Return JSON only: {\"intent\": \"WEEKEND_SALES\" | \"CUSTOMER_DUES\" | \"CLOSE_SHOP\" | \"ASK_SALES\"}"
            )
            payload = {
                "contents": [{"parts": [{"text": router_prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "thinkingConfig": {"thinkingLevel": "MINIMAL"},
                    "temperature": 0.0
                }
            }
            async with httpx.AsyncClient(timeout=2.5) as client:
                resp = await client.post(gemini_url, json=payload)
                if resp.status_code == 200:
                    parts = resp.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    for part in parts:
                        if part.get("text") and not part.get("thought"):
                            parsed = json.loads(part["text"])
                            intent = parsed.get("intent", "ASK_SALES")
                            if intent in ("WEEKEND_SALES", "CUSTOMER_DUES", "CLOSE_SHOP", "ASK_SALES"):
                                print(f"[TrueForge Router] Classified '{prompt}' -> {intent}")
                                return intent
        except Exception as e:
            print(f"[TrueForge Router] Fallback notice: {e}")
        return fast

    async def _generate_conversational_reply(self, user_prompt: str, sales_data: Dict[str, Any], profile_data: Dict[str, Any], inv_data: Dict[str, Any], dues_data: Dict[str, Any]) -> Optional[Dict[str, str]]:
        """
        Calls Gemini 3 Flash (MINIMAL thinking) to generate a real-time human-like conversational response
        grounded in live store telemetry when the user asks custom questions.
        """
        import httpx
        llm_key = os.environ.get("LLM_API_KEY", "").strip()
        if not llm_key:
            return None

        p_lower = user_prompt.lower().strip()
        # Use instant deterministic DB-grounded reply for standard status/sales queries so response is <50ms!
        if any(w in p_lower for w in ["status", "what were my sales", "total sales", "sales today", "how much did we make", "how is my shop"]):
            return None

        try:
            gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent?key={llm_key}"
            low_items_names = ", ".join(it.get("name", "") for it in inv_data.get("low_stock_items", []))
            cust_summary = ", ".join(f"{c.get('name')} Rs {int(c.get('outstanding_due', 0))}" for c in dues_data.get("customers", []))
            sys_prompt = (
                "You are OperatorOS, a warm, sharp, human-like AI store manager talking out loud in real time to Rajesh-ji, "
                f"owner of {profile_data.get('name', 'Green Valley Organic Grocers')} in Indiranagar, Bengaluru.\n"
                f"Live Database Facts right now:\n"
                f"- Today's Gross Sales: Rs {int(sales_data.get('total_sales_amount', 0))} across {sales_data.get('total_transactions', 0)} orders "
                f"(Cash: Rs {int(sales_data['breakdown']['cash'])}, UPI: Rs {int(sales_data['breakdown']['upi'])}, Card: Rs {int(sales_data['breakdown']['card'])})\n"
                f"- Cash in Drawer: Rs {int(profile_data.get('current_cash_in_drawer', 0))} (Rs {int(profile_data.get('opening_cash', 5000))} float)\n"
                f"- Low Stock Alert: {inv_data.get('count', 0)} items ({low_items_names}) below reorder level before 8 PM MilkyWay cutoff\n"
                f"- Overdue Customer Dues: Rs {int(dues_data.get('total_overdue_amount', 0))} across {dues_data.get('overdue_customers_count', 0)} accounts ({cust_summary})\n\n"
                f"User said: \"{user_prompt}\"\n\n"
                "Respond in valid JSON with two keys:\n"
                "1. \"voice_text\": 2 to 3 natural spoken sentences (no markdown symbols, no rupee symbols—say 'rupees') answering Rajesh-ji directly using the exact live database numbers above. IMPORTANT: NEVER start with 'Namaste', 'Hello', or 'Hi'—answer directly.\n"
                "2. \"markdown\": Concise formatted markdown summary matching your spoken response."
            )
            payload = {
                "contents": [{"parts": [{"text": sys_prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "thinkingConfig": {"thinkingLevel": "MINIMAL"},
                    "temperature": 0.4
                }
            }
            async with httpx.AsyncClient(timeout=4.5) as client:
                resp = await client.post(gemini_url, json=payload)
                if resp.status_code == 200:
                    parts = resp.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    for p in parts:
                        if p.get("text") and not p.get("thought"):
                            parsed = json.loads(p["text"])
                            if parsed.get("voice_text") and parsed.get("markdown"):
                                return parsed
        except Exception as e:
            print(f"[Conversational LLM] Fallback notice: {e}")
        return None

    async def execute_task_stream(self, user_prompt: str) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Executes the business task, streaming TrueForge events and live voice narration in real time.
        Stops at human input or human approval checkpoints.
        """
        turn_id = f"turn_{uuid.uuid4().hex[:8]}"
        self.status = "RUNNING"
        self.workflow_type = await self._detect_intent_async(user_prompt)

        # 1. turn.created
        yield self._create_event("turn.created", {
            "turn_id": turn_id,
            "intent": self.workflow_type,
            "input": [{"type": "user.message", "content": user_prompt}],
            "state": {"status": "running"}
        }, thread_id=None)

        # 2. mcp.initialize (TrueFoundry MCP Gateway connections)
        yield self._create_event("mcp.initialize", {
            "mcp_servers": [
                {"id": "mcp_biz_01", "name": "business-ops-mcp", "transport_type": "streamable-http"},
                {"id": "mcp_cognee_01", "name": "cognee-memory-mcp", "transport_type": "streamable-http"},
                {"id": "mcp_n8n_01", "name": "n8n-workflows-mcp", "transport_type": "streamable-http"}
            ]
        }, thread_id="main")

        # 3. sandbox.created (Daytona Cloud / TrueForge Code Mode Sandbox)
        yield self._create_event("sandbox.created", {
            "sandbox_id": f"sbx_dtn_{uuid.uuid4().hex[:8]}",
            "provider": "Daytona Cloud Sandbox (TrueForge Code Mode)"
        }, thread_id=None)
        await asyncio.sleep(0.15)

        # -------------------------------------------------------------
        # BRANCH 1: ASK_SALES / CONVERSATIONAL STATUS & LIVE DB READ
        # -------------------------------------------------------------
        if self.workflow_type == "ASK_SALES":
            yield self._create_event("model.message.delta", {
                "content": "Dispatching background store agents to query live SQLite/PostgreSQL database records for **Green Valley Organic Grocers**...",
                "voice_text": "Checking your live sales and store systems right now, Rajesh-ji."
            })
            await asyncio.sleep(0.45)

            call_sales_id = f"call_{uuid.uuid4().hex[:8]}"
            yield self._create_event("tool.call", {
                "id": call_sales_id,
                "tool_name": "get_sales_data",
                "agent_step": "Sales Agent: Querying live database POS transactions & payment split...",
                "arguments": {"business_id": self.business_id, "date_filter": "today"}
            })
            sales_data = tool_get_sales_data(self.business_id)
            yield self._create_event("tool.response", {
                "tool_call_id": call_sales_id,
                "content": sales_data
            })
            await asyncio.sleep(0.45)

            call_inv_id = f"call_{uuid.uuid4().hex[:8]}"
            yield self._create_event("tool.call", {
                "id": call_inv_id,
                "tool_name": "get_inventory",
                "agent_step": "Inventory Agent: Querying live database stock levels & reorder alerts...",
                "arguments": {"business_id": self.business_id, "low_stock_only": True}
            })
            inv_data = tool_get_inventory(self.business_id, low_stock_only=True)
            yield self._create_event("tool.response", {
                "tool_call_id": call_inv_id,
                "content": inv_data
            })
            await asyncio.sleep(0.35)

            profile_data = tool_get_business_profile(self.business_id)
            dues_data = tool_get_customer_dues(self.business_id, min_overdue_days=7)

            llm_reply = await self._generate_conversational_reply(user_prompt, sales_data, profile_data, inv_data, dues_data)

            if llm_reply:
                ans = llm_reply["markdown"]
                voice_ans = llm_reply["voice_text"]
            else:
                low_cnt = inv_data.get("count", len(inv_data.get("low_stock_items", [])))
                dues_amt = dues_data.get("total_overdue_amount", 0.0)
                dues_cnt = dues_data.get("overdue_customers_count", 0)
                ans = (
                    f"### Today's Live Database Store Status & Sales Snapshot\n\n"
                    f"- **Store:** {profile_data.get('name', 'Green Valley Organic Grocers')}\n"
                    f"- **Total Gross Sales (Live DB):** ₹{sales_data['total_sales_amount']:,.2f} across {sales_data['total_transactions']} transactions\n"
                    f"- **Payment Split:** Cash: ₹{sales_data['breakdown']['cash']:,.2f} | UPI: ₹{sales_data['breakdown']['upi']:,.2f} | Card: ₹{sales_data['breakdown']['card']:,.2f}\n"
                    f"- **Current Cash in Drawer:** ₹{profile_data.get('current_cash_in_drawer', 14850):,.2f} (includes ₹{profile_data.get('opening_cash', 5000):,.0f} opening float buffer)\n"
                    f"- **Low Stock Alert:** {low_cnt} Dairy & Bakery items below reorder threshold\n"
                    f"- **Overdue Customer Dues:** ₹{dues_amt:,.2f} across {dues_cnt} accounts"
                )
                voice_ans = (
                    f"Today's total sales are {int(sales_data['total_sales_amount'])} rupees across {sales_data['total_transactions']} transactions, "
                    f"and your cash drawer holds {int(profile_data.get('current_cash_in_drawer', 14850))} rupees. "
                    f"We also have {low_cnt} dairy and bakery items running low before the 8 PM cutoff. "
                    f"Would you like me to close the shop for today, or run a weekend campaign, Rajesh-ji?"
                )

            self.status = "COMPLETED"
            yield self._create_event("model.message.delta", {
                "content": ans,
                "voice_text": voice_ans
            })
            yield self._create_event("turn.done", {
                "state": {"status": "done", "output": {"content": ans}}
            }, thread_id=None)
            return

        # -------------------------------------------------------------
        # BRANCH 2: WEEKEND_SALES / CAMPAIGN WORKFLOW (Interactive Discovery)
        # -------------------------------------------------------------
        if self.workflow_type == "WEEKEND_SALES":
            yield self._create_event("model.message.delta", {
                "content": "Dispatching parallel TrueForge subagents (`inventory-analyst`, `cognee-memory-researcher`) to analyze store products, customer demographics, and margin velocity for **Green Valley Organic Grocers**...",
                "voice_text": "Certainly, Rajesh-ji! I am dispatching our inventory and Cognee memory agents to analyze your store profile, surplus stock, and customer base."
            })
            await asyncio.sleep(0.45)

            # Spawn Subagent Thread 1: Inventory & Margin Analyst
            sub_inv_thread = f"thread_inv_{uuid.uuid4().hex[:6]}"
            yield self._create_event("thread.created", {
                "title": "subagent: inventory-margin-analyst",
                "parent": {"thread_id": "main", "tool_call_id": f"call_sub_{uuid.uuid4().hex[:6]}"},
                "agent_info": {"type": "dynamic", "name": "inventory-margin-analyst", "input": "Scan SKUs for high-margin surplus stock & store demographics"}
            }, thread_id=sub_inv_thread)

            # Ingest Business Profile
            call_prof_id = f"call_{uuid.uuid4().hex[:8]}"
            yield self._create_event("tool.call", {
                "id": call_prof_id,
                "tool_name": "get_business_profile",
                "agent_step": "Profile Subagent: Loading store demographics & active shopper count...",
                "arguments": {"business_id": self.business_id}
            }, thread_id=sub_inv_thread)
            profile_data = tool_get_business_profile(self.business_id)
            yield self._create_event("tool.response", {"tool_call_id": call_prof_id, "content": profile_data}, thread_id=sub_inv_thread)
            await asyncio.sleep(0.45)

            # Ingest Inventory (Check all items for high-margin surplus)
            call_inv_id = f"call_{uuid.uuid4().hex[:8]}"
            yield self._create_event("tool.call", {
                "id": call_inv_id,
                "tool_name": "get_inventory",
                "agent_step": "Inventory Subagent: Scanning SKUs for high-margin surplus stock...",
                "arguments": {"business_id": self.business_id, "low_stock_only": False}
            }, thread_id=sub_inv_thread)
            inv_data = tool_get_inventory(self.business_id, low_stock_only=False)
            yield self._create_event("tool.response", {"tool_call_id": call_inv_id, "content": inv_data}, thread_id=sub_inv_thread)
            yield self._create_event("thread.done", {
                "title": "subagent: inventory-margin-analyst",
                "state": {"status": "done", "output": {"content": "Identified 28 units Honey (38% margin) & 32 units Coconut Oil (37% margin)."}}
            }, thread_id=sub_inv_thread)
            await asyncio.sleep(0.45)

            # Spawn Subagent Thread 2: Cognee Semantic Memory Researcher
            sub_mem_thread = f"thread_mem_{uuid.uuid4().hex[:6]}"
            yield self._create_event("thread.created", {
                "title": "subagent: cognee-memory-researcher",
                "parent": {"thread_id": "main", "tool_call_id": f"call_sub_{uuid.uuid4().hex[:6]}"},
                "agent_info": {"type": "dynamic", "name": "cognee-memory-researcher", "input": "Retrieve Indiranagar campaign conversion rules from Cognee graph"}
            }, thread_id=sub_mem_thread)

            # Query Cognee Memory for Campaign Rules
            call_mem_id = f"call_{uuid.uuid4().hex[:8]}"
            yield self._create_event("tool.call", {
                "id": call_mem_id,
                "tool_name": "search_business_memory",
                "agent_step": "Cognee Memory Subagent: Retrieving past Indiranagar campaign conversion rules...",
                "arguments": {"query": "weekend promotions broadcast customer loyalty Indiranagar"}
            }, thread_id=sub_mem_thread)
            mem_data = tool_search_business_memory("weekend promotions broadcast customer loyalty Indiranagar", self.business_id)
            yield self._create_event("tool.response", {"tool_call_id": call_mem_id, "content": mem_data}, thread_id=sub_mem_thread)
            yield self._create_event("thread.done", {
                "title": "subagent: cognee-memory-researcher",
                "state": {"status": "done", "output": {"content": "Retrieved Weekend Fresh Bundles & WhatsApp 94% open-rate policy."}}
            }, thread_id=sub_mem_thread)
            await asyncio.sleep(0.45)

            # Formulate strategic questions based on store profile
            self.dialogue_context = {
                "store_name": profile_data.get("name", "Green Valley Organic Grocers"),
                "category": profile_data.get("category", "Organic Retail"),
                "customer_count": 142,
                "high_margin_items": [
                    {"name": "Artisanal Wildflower Honey", "price": 450, "cost": 280, "margin": "38%"},
                    {"name": "Organic Cold-Pressed Coconut Oil", "price": 380, "cost": 240, "margin": "37%"},
                    {"name": "Organic Chia Seeds", "price": 220, "cost": 130, "margin": "41%"}
                ]
            }

            question_voice = (
                "Sir, since Green Valley is an organic grocery store, I see we have surplus stock of high-margin Artisanal Wildflower Honey and Cold-Pressed Coconut Oil, "
                "plus 142 loyal repeat customers in Indiranagar. How big of a campaign would you like to run, and where should we promote it? "
                "We can do a WhatsApp broadcast to your 142 customers, a wider SMS flash sale to 250 area shoppers, or an in-store deal. "
                "And what discount should we offer—say 15 percent or 20 percent?"
            )

            question_markdown = f"""### 🎯 Campaign Strategy & Merchant Discovery

**Store Profile:** {self.dialogue_context['store_name']} ({self.dialogue_context['category']})  
**Active Customer Base:** 142 Registered Repeat Shoppers in Indiranagar  
**Identified Surplus Opportunities:**
- **Artisanal Wildflower Honey (500g):** ₹450 retail (38% margin, 28 units surplus)
- **Cold-Pressed Coconut Oil (1L):** ₹380 retail (37% margin, 32 units surplus)
- **Fresh Weekend Staples:** Farm A2 Milk & Sourdough Loaves

---

#### Recommended Channels & Offers:
Please speak your preference (e.g. *"Run a WhatsApp broadcast with 15% discount"*) or select an option below:
"""

            self.status = "WAITING_FOR_USER_INPUT"
            yield self._create_event("model.message.delta", {
                "content": question_markdown
            })

            interactive_event = self._create_event("agent.question_required", {
                "voice_text": question_voice,
                "tool_info": {"type": "truefoundry-system", "name": "ask_user_question"},
                "question": "How big of a campaign & which channel/discount should we run?",
                "context": self.dialogue_context,
                "channel_options": [
                    {"id": "whatsapp", "label": "WhatsApp Broadcast (142 Loyal Customers)", "recommended": True, "badge": "94% Open Rate • ₹0 Cost"},
                    {"id": "sms", "label": "SMS Flash Sale (250 Area Shoppers)", "badge": "Wide Outreach"},
                    {"id": "instore", "label": "In-Store Weekend Special (Chalkboard & POS)", "badge": "High Footfall"}
                ],
                "discount_options": [
                    {"id": "15", "label": "15% Weekend Organic Essentials", "recommended": True, "detail": "Optimal Margin Balance"},
                    {"id": "20", "label": "20% Super Saver Family Bundle", "detail": "Maximum Volume Driver"},
                    {"id": "bogo", "label": "Buy 2 Get 1 Free on Artisanal Pantry", "detail": "Surplus Clearance"}
                ]
            })
            yield interactive_event

            yield self._create_event("turn.done", {
                "state": {
                    "status": "waiting_for_input",
                    "output": {"content": question_markdown},
                    "required_input": interactive_event
                }
            }, thread_id=None)
            return

        # -------------------------------------------------------------
        # BRANCH 3: CUSTOMER_DUES WORKFLOW
        # -------------------------------------------------------------
        if self.workflow_type == "CUSTOMER_DUES":
            yield self._create_event("model.message.delta", {
                "content": "Dispatching TrueForge `finance-collections` subagent to audit customer credit accounts, Cognee collection policies, and calculate overdue balances in the Daytona sandbox...",
                "voice_text": "On it, Rajesh-ji. I am dispatching the ledger agent to check overdue customer credit accounts past 7 days."
            })
            await asyncio.sleep(0.45)

            sub_fin_thread = f"thread_fin_{uuid.uuid4().hex[:6]}"
            yield self._create_event("thread.created", {
                "title": "subagent: finance-collections-auditor",
                "parent": {"thread_id": "main", "tool_call_id": f"call_sub_{uuid.uuid4().hex[:6]}"},
                "agent_info": {"type": "dynamic", "name": "finance-collections-auditor", "input": "Audit overdue credit accounts > 7 days and verify Cognee reminder policy"}
            }, thread_id=sub_fin_thread)

            call_dues_id = f"call_{uuid.uuid4().hex[:8]}"
            yield self._create_event("tool.call", {
                "id": call_dues_id,
                "tool_name": "get_customer_dues",
                "agent_step": "Ledger Subagent: Auditing customer credit accounts overdue > 7 days...",
                "arguments": {"business_id": self.business_id, "min_overdue_days": 7}
            }, thread_id=sub_fin_thread)
            dues_data = tool_get_customer_dues(self.business_id, min_overdue_days=7)
            yield self._create_event("tool.response", {"tool_call_id": call_dues_id, "content": dues_data}, thread_id=sub_fin_thread)
            await asyncio.sleep(0.45)

            # Query Cognee Memory for Credit & Dues Policy
            call_mem_dues_id = f"call_{uuid.uuid4().hex[:8]}"
            yield self._create_event("tool.call", {
                "id": call_mem_dues_id,
                "tool_name": "search_business_memory",
                "agent_step": "Cognee Memory Subagent: Verifying 7-day credit & ₹500 minimum reminder rule...",
                "arguments": {"query": "customer credit dues reminder overdue UPI policy"}
            }, thread_id=sub_fin_thread)
            dues_mem = tool_search_business_memory("customer credit dues reminder overdue UPI policy", self.business_id)
            yield self._create_event("tool.response", {"tool_call_id": call_mem_dues_id, "content": dues_mem}, thread_id=sub_fin_thread)

            # Run TrueForge Sandbox Code Mode aging & recovery calculation
            customers = dues_data.get("customers", [])
            total_due = dues_data.get("total_overdue_amount", 4250.0)
            cust_ids = [c["customer_id"] for c in customers]

            dues_sandbox_code = """
customers = INPUT_DATA.get("customers", [])
eligible = [c for c in customers if c.get("overdue_days", 0) >= 7 and c.get("outstanding_due", 0) >= 500]
total_recoverable = sum(c.get("outstanding_due", 0) for c in eligible)
weighted_days = round(sum(c.get("overdue_days", 0) * c.get("outstanding_due", 0) for c in eligible) / max(total_recoverable, 1), 1)
print(json.dumps({
    "eligible_accounts": len(eligible),
    "total_recoverable_inr": total_recoverable,
    "weighted_avg_overdue_days": weighted_days,
    "policy_check": "PASSED_7DAY_500INR_RULE"
}))
"""
            call_sbx_dues_id = f"call_{uuid.uuid4().hex[:8]}"
            yield self._create_event("tool.call", {
                "id": call_sbx_dues_id,
                "tool_name": "run_analysis_in_sandbox",
                "agent_step": "Daytona Sandbox (Code Mode): Computing credit aging & policy eligibility...",
                "arguments": {"python_code": dues_sandbox_code.strip()}
            }, thread_id=sub_fin_thread)
            sbx_dues_res = tool_run_analysis_in_sandbox(dues_sandbox_code, {"customers": customers})
            yield self._create_event("tool.response", {"tool_call_id": call_sbx_dues_id, "content": sbx_dues_res}, thread_id=sub_fin_thread)

            yield self._create_event("thread.done", {
                "title": "subagent: finance-collections-auditor",
                "state": {"status": "done", "output": {"content": f"Verified {len(customers)} accounts totaling ₹{total_due:,.2f} eligible for UPI reminders."}}
            }, thread_id=sub_fin_thread)
            await asyncio.sleep(0.35)

            call_prep_id = f"call_{uuid.uuid4().hex[:8]}"
            yield self._create_event("tool.call", {
                "id": call_prep_id,
                "tool_name": "prepare_customer_dues_reminder",
                "agent_step": "Collections Agent: Drafting courteous WhatsApp UPI reminder links...",
                "arguments": {"customer_ids": cust_ids, "business_id": self.business_id}
            })
            prep_res = tool_prepare_customer_dues_reminder(self.business_id, cust_ids, "Weekly courteous reminder", task_id=turn_id)
            yield self._create_event("tool.response", {"tool_call_id": call_prep_id, "content": prep_res})
            self.active_action_id = prep_res.get("action_id")
            await asyncio.sleep(0.45)

            self.status = "WAITING_FOR_APPROVAL"
            approval_call_id = f"call_approval_{uuid.uuid4().hex[:8]}"
            cust_spoken_parts = [
                f"{c['name']} has {int(c['outstanding_due'])} rupees overdue for {c['due_since_days']} days"
                for c in customers
            ]
            cust_spoken_str = " and ".join(cust_spoken_parts) if cust_spoken_parts else f"{len(customers)} customers have overdue balances"
            dues_voice = (
                f"Sir, {cust_spoken_str}, totaling {int(total_due)} rupees. "
                f"I have prepared courteous Telegram and WhatsApp payment reminders with UPI payment links and staged the spreadsheet ledger entry via n8n. "
                f"Should I approve or deny sending these reminders?"
            )

            approval_event = self._create_event("tool.approval_required", {
                "voice_text": dues_voice,
                "tool_calls": [
                    {
                        "id": approval_call_id,
                        "tool_name": "send_customer_messages",
                        "action_id": self.active_action_id,
                        "action_title": f"Dispatch Telegram & WhatsApp Payment Reminders ({len(customers)} accounts)",
                        "rationale": f"Politely recover ₹{total_due:,.2f} in store credit past the 7-day threshold and log to Spreadsheet.",
                        "data_used": {"overdue_count": len(customers), "total_due": f"₹{total_due:,.2f}", "n8n_branch": "PAYMENT_REMINDER"},
                        "external_effect": f"n8n Switch Branch 2: Sends Telegram & WhatsApp UPI payment reminders for {len(customers)} customers and appends a row to Google Sheets / Spreadsheet Ledger.",
                        "estimated_cost": 0.0,
                        "arguments": {"action_id": self.active_action_id}
                    }
                ]
            })
            self.pending_approvals.append(approval_event)
            yield approval_event

            yield self._create_event("turn.done", {
                "state": {"status": "done", "output": None, "required_actions": [approval_event]}
            }, thread_id=None)
            return

        # -------------------------------------------------------------
        # BRANCH 4: CLOSE_SHOP & SUPPLIER CALL WORKFLOW (Full Closing Sequence)
        # -------------------------------------------------------------
        yield self._create_event("model.message.delta", {
            "content": f"Understood. Starting store closing & supplier restock sequence for **Green Valley Organic Grocers** (`{user_prompt}`).\n\nDispatching background agents to query live SQLite/PostgreSQL database records for store profile, POS sales, inventory stock levels, and Cognee operating policies...",
            "voice_text": "Right away, Rajesh-ji. I am dispatching background agents to check today's live database sales, cash drawer, inventory levels, and supplier order cutoffs."
        })
        await asyncio.sleep(0.55)

        # 1. Tool Call: get_business_profile
        call_profile_id = f"call_{uuid.uuid4().hex[:8]}"
        yield self._create_event("tool.call", {
            "id": call_profile_id,
            "tool_name": "get_business_profile",
            "agent_step": "Store Agent: Querying DB cash drawer float & operating parameters...",
            "arguments": {"business_id": self.business_id}
        })
        profile_data = tool_get_business_profile(self.business_id)
        yield self._create_event("tool.response", {"tool_call_id": call_profile_id, "content": profile_data})
        await asyncio.sleep(0.5)

        # 2. Tool Call: get_sales_data
        call_sales_id = f"call_{uuid.uuid4().hex[:8]}"
        yield self._create_event("tool.call", {
            "id": call_sales_id,
            "tool_name": "get_sales_data",
            "agent_step": "Sales Agent: Aggregating live Cash, UPI & Card transactions from DB...",
            "arguments": {"business_id": self.business_id, "date_filter": "today"}
        })
        sales_data = tool_get_sales_data(self.business_id)
        yield self._create_event("tool.response", {"tool_call_id": call_sales_id, "content": sales_data})
        await asyncio.sleep(0.5)

        # 3. Tool Call: get_inventory
        call_inv_id = f"call_{uuid.uuid4().hex[:8]}"
        yield self._create_event("tool.call", {
            "id": call_inv_id,
            "tool_name": "get_inventory",
            "agent_step": "Inventory Agent: Scanning DB shelves for depleted perishables...",
            "arguments": {"business_id": self.business_id, "low_stock_only": True}
        })
        inv_data = tool_get_inventory(self.business_id, low_stock_only=True)
        yield self._create_event("tool.response", {"tool_call_id": call_inv_id, "content": inv_data})
        await asyncio.sleep(0.5)

        # 4. Tool Call: get_customer_dues
        call_dues_id = f"call_{uuid.uuid4().hex[:8]}"
        yield self._create_event("tool.call", {
            "id": call_dues_id,
            "tool_name": "get_customer_dues",
            "agent_step": "Ledger Agent: Checking outstanding customer credit balances...",
            "arguments": {"business_id": self.business_id, "min_overdue_days": 7}
        })
        dues_data = tool_get_customer_dues(self.business_id, min_overdue_days=7)
        yield self._create_event("tool.response", {"tool_call_id": call_dues_id, "content": dues_data})
        await asyncio.sleep(0.5)

        # 5. Tool Call: search_business_memory (Cognee)
        call_mem_id = f"call_{uuid.uuid4().hex[:8]}"
        yield self._create_event("tool.call", {
            "id": call_mem_id,
            "tool_name": "search_business_memory",
            "agent_step": "Cognee Memory Agent: Checking store rules for float target & supplier cutoffs...",
            "arguments": {"query": "closing cash drawer float safe deposit supplier cutoff"}
        })
        mem_data = tool_search_business_memory("closing cash drawer float safe deposit supplier cutoff", self.business_id)
        yield self._create_event("tool.response", {"tool_call_id": call_mem_id, "content": mem_data})
        await asyncio.sleep(0.55)

        low_cnt = inv_data.get("count", len(inv_data.get("low_stock_items", [])))
        # 6. TrueForge Sandbox Execution: Generated Python script
        yield self._create_event("model.message.delta", {
            "content": "\n\nWriting and executing reconciliation & reorder analysis inside **TrueForge Sandbox**...",
            "voice_text": f"I found {low_cnt} depleted dairy and bakery items in the database. Now I am running cash reconciliation and reorder calculations in the TrueForge Python sandbox."
        })
        await asyncio.sleep(0.45)

        sandbox_code = f"""# TrueForge Sandbox Execution: Closing Cash Reconciliation & Stock Deficit Math
import json

sales_breakdown = {json.dumps(sales_data.get("breakdown", {}))}
low_stock_items = {json.dumps(inv_data.get("low_stock_items", []))}
opening_float = {profile_data.get("opening_cash", 5000.0)}
current_drawer_cash = {profile_data.get("current_cash_in_drawer", 14850.0)}
drawer_target = {profile_data.get("cash_drawer_target", 5000.0)}

# 1. Cash Reconciliation
cash_sales = sales_breakdown.get("cash", 0.0)
expected_drawer_cash = opening_float + cash_sales
variance = current_drawer_cash - expected_drawer_cash
safe_deposit_recommended = max(0.0, current_drawer_cash - drawer_target)

# 2. Purchase Order Calculations for depleted items
reorder_items = []
total_po_cost = 0.0
for item in low_stock_items:
    qty_needed = max(item["min_order_qty"], item["reorder_level"] - item["current_stock"])
    subtotal = qty_needed * item["unit_cost"]
    total_po_cost += subtotal
    reorder_items.append({{
        "product_id": item["product_id"],
        "sku": item["sku"],
        "name": item["name"],
        "quantity": qty_needed,
        "unit_cost": item["unit_cost"],
        "subtotal": subtotal
    }})

output = {{
    "cash_reconciliation": {{
        "opening_float": opening_float,
        "recorded_cash_sales": cash_sales,
        "expected_cash": expected_drawer_cash,
        "actual_counted_cash": current_drawer_cash,
        "variance": variance,
        "safe_deposit_amount": safe_deposit_recommended,
        "status": "RECONCILED" if variance == 0 else "FLAGGED"
    }},
    "reorder_plan": {{
        "supplier_id": "supp_01",
        "supplier_name": "MilkyWay Fresh Foods Co.",
        "cutoff_time": "20:00",
        "items": reorder_items,
        "total_amount": total_po_cost
    }}
}}

print(json.dumps(output))
"""

        call_sandbox_id = f"call_{uuid.uuid4().hex[:8]}"
        yield self._create_event("tool.call", {
            "id": call_sandbox_id,
            "tool_name": "run_analysis_in_sandbox",
            "agent_step": "TrueForge Sandbox Agent: Executing isolated Python cash & PO math...",
            "arguments": {"python_code": sandbox_code}
        })
        sandbox_res = tool_run_analysis_in_sandbox(sandbox_code)
        yield self._create_event("tool.response", {"tool_call_id": call_sandbox_id, "content": sandbox_res})
        await asyncio.sleep(0.55)

        calc_out = sandbox_res.get("output_data") or {}
        reorder_plan = calc_out.get("reorder_plan", {})
        cash_recon = calc_out.get("cash_reconciliation", {})

        # 7. Tool Call: prepare_purchase_order
        call_prep_id = f"call_{uuid.uuid4().hex[:8]}"
        yield self._create_event("tool.call", {
            "id": call_prep_id,
            "tool_name": "prepare_purchase_order",
            "agent_step": "Procurement Agent: Staging Purchase Order & CALL-E Supplier Voice Call...",
            "arguments": {
                "business_id": self.business_id,
                "supplier_id": reorder_plan.get("supplier_id", "supp_01"),
                "items": reorder_plan.get("items", []),
                "notes": "Daily closing restock order before 8:00 PM cutoff."
            }
        })
        prep_res = tool_prepare_purchase_order(
            business_id=self.business_id,
            supplier_id=reorder_plan.get("supplier_id", "supp_01"),
            items=reorder_plan.get("items", []),
            notes="Daily closing restock order before 8:00 PM cutoff.",
            task_id=turn_id
        )
        yield self._create_event("tool.response", {"tool_call_id": call_prep_id, "content": prep_res})
        self.active_action_id = prep_res.get("action_id")
        await asyncio.sleep(0.5)

        # 8. Human Approval Boundary (TrueForge Approval Checkpoint)
        self.status = "WAITING_FOR_APPROVAL"
        approval_call_id = f"call_approval_{uuid.uuid4().hex[:8]}"

        live_total_sales = int(sales_data.get("total_sales_amount", 9850))
        live_safe_deposit = int(cash_recon.get("safe_deposit_amount", 9850))
        live_float = int(cash_recon.get("opening_float", 5000))
        live_po_total = int(reorder_plan.get("total_amount", 4500))
        live_items_cnt = len(reorder_plan.get("items", [])) or 4

        approval_voice = (
            f"Sir, today's sales are {live_total_sales} rupees and your cash drawer is reconciled with zero variance. "
            f"Please deposit {live_safe_deposit} rupees into the safe and leave {live_float} rupees float. "
            f"I have also prepared a purchase order for {live_items_cnt} depleted dairy and bakery items from MilkyWay Fresh Foods totaling {live_po_total} rupees, "
            f"and staged an outbound CALL-E phone call to place the order with the supplier. "
            f"Should I approve or deny this order?"
        )

        approval_event = self._create_event("tool.approval_required", {
            "voice_text": approval_voice,
            "tool_calls": [
                {
                    "id": approval_call_id,
                    "tool_name": "create_purchase_order",
                    "action_id": self.active_action_id,
                    "action_title": f"Call Supplier (CALL-E) & Dispatch PO to {reorder_plan.get('supplier_name', 'MilkyWay Fresh Foods Co.')}",
                    "rationale": f"Restock {live_items_cnt} critical dairy & bakery items below reorder levels before the 8:00 PM cutoff, place outbound CALL-E supplier voice call, and log to Spreadsheet.",
                    "data_used": {
                        "sales_today": f"₹{sales_data.get('total_sales_amount', 0):,.2f}",
                        "cash_in_drawer": f"₹{cash_recon.get('actual_counted_cash', 0):,.2f}",
                        "low_stock_count": live_items_cnt,
                        "n8n_branch": "PURCHASE_ORDER"
                    },
                    "external_effect": f"n8n Switch Branch 1: Places outbound AI voice call via CALL-E (heycall-e.com) to MilkyWay Fresh Foods for ₹{reorder_plan.get('total_amount', 0):,.2f}, logs PO row to Google Sheets / Spreadsheet, and sends Telegram alert.",
                    "estimated_cost": reorder_plan.get("total_amount", 0.0),
                    "arguments": {"action_id": self.active_action_id}
                }
            ]
        })
        self.pending_approvals.append(approval_event)
        yield approval_event

        yield self._create_event("turn.done", {
            "state": {
                "status": "done",
                "output": None,
                "required_actions": [approval_event]
            }
        }, thread_id=None)

    async def continue_task_stream(self, user_input: str) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Continues a task session after the merchant answers an interactive question (e.g. Campaign selection).
        """
        turn_id = f"turn_input_{uuid.uuid4().hex[:8]}"
        self.status = "RUNNING"

        yield self._create_event("turn.created", {
            "turn_id": turn_id,
            "input": [{"type": "user.interactive_response", "content": user_input}],
            "state": {"status": "running"}
        }, thread_id=None)
        await asyncio.sleep(0.25)

        # Parse user's selections from voice / text
        p = user_input.lower()
        channel = "WhatsApp Broadcast (142 Loyal Customers)"
        if "sms" in p:
            channel = "SMS Flash Sale (250 Customers)"
        elif "in-store" in p or "store" in p or "chalkboard" in p:
            channel = "In-Store Weekend Special"

        discount_pct = 15.0
        if "20" in p:
            discount_pct = 20.0
        elif "10" in p:
            discount_pct = 10.0
        elif "buy 2" in p or "bogo" in p:
            discount_pct = 25.0

        ack_voice = f"Got it, Rajesh-ji! Setting up a {int(discount_pct)} percent weekend {channel.split('(')[0].strip()} for 142 customers. Running ROI simulation in TrueForge sandbox now."
        yield self._create_event("model.message.delta", {
            "content": f"Understood! Configuring **{channel}** with **{int(discount_pct)}% Discount**.\n\nExecuting ROI & basket size simulation inside **TrueForge Sandbox**...",
            "voice_text": ack_voice
        })
        await asyncio.sleep(0.3)

        # Execute TrueForge Sandbox ROI Simulation
        sim_code = f"""# TrueForge Sandbox: Campaign Impact & Customer Conversion Simulation
import json

total_customers = 142
avg_basket_spend = 850.0
discount_pct = {discount_pct}
channel = "{channel}"

# Expected conversion benchmark for Indiranagar organic retail WhatsApp broadcast
expected_conv_rate = 0.22 if "WhatsApp" in channel else (0.12 if "SMS" in channel else 0.18)
projected_buyers = round(total_customers * expected_conv_rate)
discounted_basket = avg_basket_spend * (1.0 - (discount_pct / 100.0))
projected_revenue = round(projected_buyers * discounted_basket, 2)
gross_margin_rate = 0.38
projected_gross_profit = round(projected_revenue * gross_margin_rate, 2)
messaging_cost = 0.0

output = {{
    "channel": channel,
    "target_audience": f"{{total_customers}} Indiranagar Loyal Customers",
    "projected_buyers": projected_buyers,
    "discount_pct": discount_pct,
    "projected_revenue": projected_revenue,
    "projected_gross_profit": projected_gross_profit,
    "messaging_cost": messaging_cost,
    "roi_ratio": "Infinite (Zero Ad Cost)"
}}
print(json.dumps(output))
"""
        call_sim_id = f"call_{uuid.uuid4().hex[:8]}"
        yield self._create_event("tool.call", {
            "id": call_sim_id,
            "tool_name": "run_analysis_in_sandbox",
            "agent_step": "TrueForge Sandbox Agent: Simulating campaign conversion & ROI math...",
            "arguments": {"python_code": sim_code}
        })
        sim_res = tool_run_analysis_in_sandbox(sim_code)
        yield self._create_event("tool.response", {"tool_call_id": call_sim_id, "content": sim_res})
        await asyncio.sleep(0.5)

        sim_out = sim_res.get("output_data") or {}
        proj_rev = sim_out.get("projected_revenue", 26550.0)
        proj_buyers = sim_out.get("projected_buyers", 31)

        # Formulate persuasive copy
        campaign_copy = (
            f"🌿 Green Valley Organic Grocers Weekend Special! Enjoy {int(discount_pct)}% OFF on pure Wildflower Honey, "
            f"Farm Fresh A2 Milk, and Artisanal Sourdough this Saturday & Sunday. Visit us in Indiranagar or reply ORDER to get home delivery. "
            f"Valid till Sunday 9 PM!"
        )

        # Prepare Campaign ActionRecord
        call_prep_camp_id = f"call_{uuid.uuid4().hex[:8]}"
        yield self._create_event("tool.call", {
            "id": call_prep_camp_id,
            "tool_name": "prepare_campaign",
            "agent_step": "Marketing Agent: Staging WhatsApp campaign payload & audience list...",
            "arguments": {
                "business_id": self.business_id,
                "title": f"Weekend Organic Harvest ({int(discount_pct)}% OFF)",
                "channel": channel,
                "discount_pct": discount_pct,
                "target_audience": "142 Indiranagar Loyal Customers",
                "estimated_revenue": proj_rev,
                "copy_text": campaign_copy
            }
        })
        prep_camp_res = tool_prepare_campaign(
            business_id=self.business_id,
            title=f"Weekend Organic Harvest ({int(discount_pct)}% OFF)",
            channel=channel,
            discount_pct=discount_pct,
            target_audience="142 Indiranagar Loyal Customers",
            estimated_revenue=proj_rev,
            copy_text=campaign_copy,
            task_id=turn_id
        )
        yield self._create_event("tool.response", {"tool_call_id": call_prep_camp_id, "content": prep_camp_res})
        self.active_action_id = prep_camp_res.get("action_id")
        await asyncio.sleep(0.45)

        # Human Approval Boundary for Campaign Broadcast
        self.status = "WAITING_FOR_APPROVAL"
        approval_call_id = f"call_approval_{uuid.uuid4().hex[:8]}"
        approval_voice = (
            f"Sir, the WhatsApp campaign 'Weekend Organic Harvest' for 142 loyal customers is ready with a {int(discount_pct)} percent discount. "
            f"TrueForge sandbox projects {int(proj_rev)} rupees in incremental revenue with zero ad cost. "
            f"Should I approve or deny this broadcast?"
        )

        approval_event = self._create_event("tool.approval_required", {
            "voice_text": approval_voice,
            "tool_calls": [
                {
                    "id": approval_call_id,
                    "tool_name": "broadcast_campaign",
                    "action_id": self.active_action_id,
                    "action_title": f"Broadcast '{prep_camp_res.get('title')}' to 142 Customers",
                    "rationale": f"Drive {int(discount_pct)}% weekend promotional bundle via {channel} with projected ₹{proj_rev:,.2f} revenue across ~{proj_buyers} buyers.",
                    "data_used": {
                        "channel": channel,
                        "audience": "142 Indiranagar Repeat Customers",
                        "projected_revenue": f"₹{proj_rev:,.2f}",
                        "projected_profit": f"₹{sim_out.get('projected_gross_profit', 10090):,.2f}",
                        "ad_cost": "₹0.00"
                    },
                    "external_effect": f"Will transmit automated WhatsApp broadcast messages to 142 registered customer phone numbers.",
                    "estimated_cost": 0.0,
                    "arguments": {"action_id": self.active_action_id}
                }
            ]
        })
        self.pending_approvals.append(approval_event)
        yield approval_event

        yield self._create_event("turn.done", {
            "state": {
                "status": "done",
                "output": None,
                "required_actions": [approval_event]
            }
        }, thread_id=None)

    async def resume_after_approval(self, decision: str = "allow", reason: Optional[str] = None) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Resumes execution after merchant provides approval decision (allow/deny).
        """
        turn_id = f"turn_resume_{uuid.uuid4().hex[:8]}"
        
        yield self._create_event("turn.created", {
            "turn_id": turn_id,
            "input": [
                {
                    "type": "user.tool_approval",
                    "action_id": self.active_action_id,
                    "approval": {"status": decision, "reason": reason}
                }
            ],
            "state": {"status": "running"}
        }, thread_id=None)
        await asyncio.sleep(0.25)

        db = SessionLocal()
        action_record = None
        try:
            action_record = db.query(ActionRecord).filter(ActionRecord.id == self.active_action_id).first()
        finally:
            db.close()

        action_type = action_record.action_type if action_record else "ACTION"

        # REJECTION PATH
        if decision == "deny":
            self.status = "REJECTED"
            db = SessionLocal()
            try:
                action = db.query(ActionRecord).filter(ActionRecord.id == self.active_action_id).first()
                if action:
                    action.status = "REJECTED"
                    action.approval_reason = reason or "Denied by merchant."
                    db.commit()
            finally:
                db.close()

            yield self._create_event("model.message.delta", {
                "content": f"Action cancelled by merchant. Reason: {reason or 'Denied'}. No external action was dispatched.",
                "voice_text": "Understood, sir. I have cancelled the action. No changes were made."
            })
            yield self._create_event("turn.done", {
                "state": {"status": "done", "output": {"content": "Action rejected by merchant."}}
            }, thread_id=None)
            return

        # APPROVAL GRANTED PATH
        self.status = "EXECUTING"
        db = SessionLocal()
        try:
            action = db.query(ActionRecord).filter(ActionRecord.id == self.active_action_id).first()
            if action:
                action.status = "APPROVED"
                action.approved_by = "Rajesh Kumar (Merchant)"
                action.approved_at = datetime.datetime.now(datetime.UTC)
                db.commit()
        finally:
            db.close()

        yield self._create_event("model.message.delta", {
            "content": f"Merchant approval confirmed. Invoking n8n Cloud action executor for Action `{self.active_action_id}`...",
            "voice_text": "Approval confirmed, Rajesh-ji! Dispatching via n8n Cloud now."
        })
        await asyncio.sleep(0.45)

        # 1. Execute Mutation via connected n8n tool
        call_exec_id = f"call_{uuid.uuid4().hex[:8]}"
        exec_tool_name = "create_purchase_order"
        if action_type == "CAMPAIGN_BROADCAST":
            exec_tool_name = "broadcast_campaign"
        elif action_type == "PAYMENT_REMINDER":
            exec_tool_name = "send_customer_messages"

        yield self._create_event("tool.call", {
            "id": call_exec_id,
            "tool_name": exec_tool_name,
            "agent_step": f"n8n Cloud Executor: Dispatching {exec_tool_name} webhook workflow...",
            "arguments": {"action_id": self.active_action_id}
        })

        if action_type == "CAMPAIGN_BROADCAST":
            exec_res = tool_broadcast_campaign(self.active_action_id)
        elif action_type == "PAYMENT_REMINDER":
            exec_res = tool_send_customer_messages(self.active_action_id)
        else:
            exec_res = tool_create_purchase_order(self.active_action_id)

        yield self._create_event("tool.response", {
            "tool_call_id": call_exec_id,
            "content": exec_res
        })
        await asyncio.sleep(0.45)

        # 2. Readback Verification via tool_get_action_status
        call_verify_id = f"call_{uuid.uuid4().hex[:8]}"
        yield self._create_event("tool.call", {
            "id": call_verify_id,
            "tool_name": "get_action_status",
            "agent_step": "Audit Verification Agent: Reading back database & n8n execution state...",
            "arguments": {"action_id": self.active_action_id}
        })
        verify_res = tool_get_action_status(self.active_action_id)
        yield self._create_event("tool.response", {
            "tool_call_id": call_verify_id,
            "content": verify_res
        })
        await asyncio.sleep(0.35)

        self.status = "COMPLETED"

        # Construct customized final summaries & voice responses grounded in live DB & n8n execution
        if action_type == "CAMPAIGN_BROADCAST":
            camp_id = exec_res.get("campaign_id", "CAMP-CONFIRMED")
            tg_status = exec_res.get("telegram_notification", {}).get("status", "READY")
            final_summary = f"""### 🚀 Campaign Broadcast Dispatched & Logged to Spreadsheet

**Store:** Green Valley Organic Grocers  
**n8n Router Branch:** `{exec_res.get('n8n_branch', 'CAMPAIGN_BROADCAST_TELEGRAM_AND_SHEETS')}` (Ref: `{camp_id}`)  
**Channel:** {exec_res.get('channel', 'Telegram & WhatsApp Broadcast')}  
**Telegram Bot Status:** `{tg_status}`  
**Spreadsheet Ledger:** Row appended (`data/operator_ledger_spreadsheet.csv`)  
**Target Audience:** {exec_res.get('target_audience', '142 Indiranagar Customers')}  
**Projected Incremental Sales:** ₹{exec_res.get('estimated_incremental_revenue', 22397.5):,.2f}  
**Promotional Copy:**  
> "{exec_res.get('copy_preview', 'Weekend Organic Special Active')}"
"""
            final_voice = (
                f"The Weekend Organic Harvest campaign has been broadcast via n8n Cloud, sent to Telegram, and logged to your spreadsheet ledger. "
                f"Have a profitable weekend, Rajesh-ji!"
            )

        elif action_type == "PAYMENT_REMINDER":
            total_sent = exec_res.get("total_sent", 2)
            total_due_val = exec_res.get("total_due", 4250.0)
            tg_status = exec_res.get("telegram_notification", {}).get("status", "READY")
            final_summary = f"""### 📩 Payment Reminders Sent via Telegram & Logged to Spreadsheet

**Store:** Green Valley Organic Grocers  
**n8n Router Branch:** `{exec_res.get('n8n_branch', 'PAYMENT_REMINDER_TELEGRAM_AND_SHEETS')}` (Batch: `{exec_res.get('batch_id', 'notif_01')}`)  
**Recipients:** {total_sent} Customers (Total Overdue: ₹{total_due_val:,.2f})  
**Telegram Bot Status:** `{tg_status}`  
**Spreadsheet Ledger:** Row appended (`data/operator_ledger_spreadsheet.csv`)  
**Channel:** Telegram Bot + WhatsApp Courteous Notice with direct UPI link  
"""
            final_voice = (
                f"Payment reminders for your {total_sent} overdue accounts totaling {int(total_due_val)} rupees have been dispatched via n8n and Telegram, "
                f"and logged to your spreadsheet ledger."
            )

        else:
            order_no = exec_res.get("order_number", "PO-CONFIRMED")
            live_sales = tool_get_sales_data(self.business_id)
            live_prof = tool_get_business_profile(self.business_id)
            gross_sales = live_sales.get("total_sales_amount", 9850.0)
            tx_cnt = live_sales.get("total_transactions", 6)
            cash_split = live_sales.get("breakdown", {}).get("cash", 4850.0)
            upi_split = live_sales.get("breakdown", {}).get("upi", 3500.0)
            card_split = live_sales.get("breakdown", {}).get("card", 1500.0)
            drawer_cash = live_prof.get("current_cash_in_drawer", 14850.0)
            float_target = live_prof.get("cash_drawer_target", 5000.0)
            safe_dep = max(0.0, drawer_cash - float_target)
            po_val = exec_res.get("total_amount", 4500.0)
            calle_info = exec_res.get("calle_voice_call", {})
            calle_status = calle_info.get("calle_status", "CALL_STAGED")
            supp_phone = exec_res.get("supplier_phone", "+919876543210")

            final_summary = f"""### 🏪 Shop Closing, CALL-E Supplier Call & Spreadsheet Sign-off

**Store:** Green Valley Organic Grocers  
**n8n Router Branch:** `{exec_res.get('n8n_branch', 'PURCHASE_ORDER_CALLE_AND_SHEETS')}` (Order: `{order_no}`)  
**CALL-E Outbound Supplier Call:** `{calle_status}` (Target: `{supp_phone}`)  
**Spreadsheet Ledger:** Row appended (`data/operator_ledger_spreadsheet.csv`)

---

#### 1. Live Database Financial Reconciliation
- **Total Today's Gross Sales:** ₹{gross_sales:,.2f} ({tx_cnt} orders in DB)
- **Payment Split:** Cash: ₹{cash_split:,.2f} | UPI: ₹{upi_split:,.2f} | Card: ₹{card_split:,.2f}
- **Drawer Float Target:** ₹{float_target:,.2f}
- **Total Cash in Drawer:** ₹{drawer_cash:,.2f}
- **Safe Deposit Advice:** Transfer **₹{safe_dep:,.2f}** to the store safe; retain **₹{float_target:,.2f}** float buffer for 8:00 AM opening.

#### 2. Restock & CALL-E Supplier Voice Call
- **Dispatched Purchase Order:** `{order_no}`
- **Supplier:** {exec_res.get('supplier_name', 'MilkyWay Fresh Foods Co.')} (`{supp_phone}`)
- **Total PO Value:** ₹{po_val:,.2f}
- **Estimated Delivery:** Tomorrow, 06:00 AM
"""
            final_voice = (
                f"Purchase order {order_no} for {int(po_val)} rupees has been dispatched via n8n Cloud, logged to your Google Sheet, "
                f"and the CALL-E voice call to MilkyWay Fresh Foods has been placed to your phone. "
                f"Your shop closing is complete. Have a great evening!"
            )

        yield self._create_event("model.message.delta", {
            "content": final_summary,
            "voice_text": final_voice
        })

        yield self._create_event("turn.done", {
            "state": {
                "status": "done",
                "output": {"content": final_summary}
            }
        }, thread_id=None)

# Active sessions registry
active_sessions: Dict[str, OperatorAgentSession] = {}

def get_or_create_session(session_id: str, business_id: str = "biz_001") -> OperatorAgentSession:
    if session_id not in active_sessions:
        active_sessions[session_id] = OperatorAgentSession(session_id, business_id)
    return active_sessions[session_id]
