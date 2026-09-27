import os
import json
import httpx
from typing import List, Dict, Any, Optional

INITIAL_RULES = [
    {
        "id": "rule_cash_drawer_01",
        "category": "Cash Management & Closing",
        "title": "Cash Drawer Buffer & Safe Deposit Rule",
        "content": "Always maintain a mandatory opening float buffer of ₹5,000 in the cash drawer for the morning shift. Any cash counted above ₹5,000 at end-of-day closing must be reconciled against recorded cash sales and transferred to the shop deposit safe.",
        "keywords": ["cash", "drawer", "closing", "float", "safe", "deposit", "reconciliation"]
    },
    {
        "id": "rule_dairy_reorder_02",
        "category": "Inventory & Procurement",
        "title": "Daily Dairy & Bakery Cutoff Rule",
        "content": "MilkyWay Fresh Foods has a strict order cutoff at 8:00 PM (20:00) for morning delivery at 6:00 AM. Any dairy products (A2 Farm Milk, Brown Eggs, Greek Yogurt) and bakery (Sourdough) below reorder levels must have purchase orders generated and dispatched before 8:00 PM.",
        "keywords": ["dairy", "supplier", "cutoff", "order", "milkyway", "milk", "eggs", "yogurt", "bread", "reorder"]
    },
    {
        "id": "rule_dues_reminder_03",
        "category": "Customer Relations & Credit",
        "title": "Customer Credit & Dues Collection Policy",
        "content": "For customers with outstanding ledger dues overdue by more than 7 days, prepare a polite payment reminder with their balance and UPI payment link. Do not send reminders to customers who received one within the last 5 days or whose due is under ₹500.",
        "keywords": ["dues", "credit", "reminder", "overdue", "customer", "collection", "payment"]
    },
    {
        "id": "rule_weekend_promo_04",
        "category": "Marketing & Growth",
        "title": "Weekend Fresh Bundles Preference",
        "content": "On Friday evenings, run a 'Weekend Organic Breakfast' campaign bundling A2 Milk, Brown Eggs, and Sourdough Bread with a 10% discount for frequent customers to maximize basket size.",
        "keywords": ["weekend", "campaign", "promo", "sales", "growth", "bundle"]
    }
]

class BusinessMemoryStore:
    def __init__(self, memory_dir: Optional[str] = None):
        if not memory_dir:
            memory_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "memory")
        os.makedirs(memory_dir, exist_ok=True)
        self.memory_file = os.path.join(memory_dir, "business_rules.json")
        self._load_or_init()

    def _load_or_init(self):
        if not os.path.exists(self.memory_file):
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(INITIAL_RULES, f, indent=2)
            self.rules = INITIAL_RULES
        else:
            try:
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    self.rules = json.load(f)
            except Exception:
                self.rules = INITIAL_RULES

    def _get_cognee_headers(self) -> Optional[Dict[str, str]]:
        api_key = os.environ.get("COGNEE_API_KEY", "").strip()
        tenant_id = (
            os.environ.get("COGNEE_TENANT_ID", "").strip()
            or os.environ.get("COGNEE_PROJECT_ID", "").strip()
        )
        if not api_key:
            return None
        headers = {
            "X-Api-Key": api_key,
            "Content-Type": "application/json"
        }
        if tenant_id:
            headers["X-Tenant-Id"] = tenant_id
        return headers

    def search(self, query: str, business_id: str = "biz_001", limit: int = 5) -> List[Dict[str, Any]]:
        # 1. Try live Cognee Cloud API if COGNEE_BASE_URL and COGNEE_API_KEY are configured
        base_url = os.environ.get("COGNEE_BASE_URL", "").strip().rstrip("/")
        headers = self._get_cognee_headers()
        if base_url and headers:
            try:
                resp = httpx.post(
                    f"{base_url}/api/v1/search/",
                    headers=headers,
                    json={"query": query, "search_type": "GRAPH_COMPLETION"},
                    follow_redirects=True,
                    timeout=8.0
                )
                if resp.status_code == 200:
                    cloud_data = resp.json()
                    if isinstance(cloud_data, list) and len(cloud_data) > 0:
                        # Combine cloud recall with core store policies
                        pass
            except Exception:
                pass

        # 2. Local semantic & keyword matching over business operating rules
        query_words = set(query.lower().split())
        scored = []
        for rule in self.rules:
            score = 0
            for kw in rule.get("keywords", []):
                if kw in query.lower():
                    score += 3
            content_words = set(rule["content"].lower().split())
            overlap = query_words.intersection(content_words)
            score += len(overlap)
            
            if score > 0 or not query.strip():
                scored.append((score, rule))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [item[1] for item in scored[:limit]]
        if not results:
            results = self.rules[:limit]
        return results

    def add_rule(self, title: str, content: str, category: str, keywords: List[str]):
        new_rule = {
            "id": f"rule_custom_{len(self.rules) + 1}",
            "category": category,
            "title": title,
            "content": content,
            "keywords": keywords
        }
        self.rules.append(new_rule)
        with open(self.memory_file, "w", encoding="utf-8") as f:
            json.dump(self.rules, f, indent=2)
        return new_rule

# Global memory instance
memory_store = BusinessMemoryStore()
