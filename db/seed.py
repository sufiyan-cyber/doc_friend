import datetime
from db.database import init_db, SessionLocal
from db.models import Business, Supplier, Product, Inventory, Customer, Sale, SaleItem, AuditEvent

def seed_database():
    init_db()
    db = SessionLocal()
    try:
        # Check if already seeded
        existing = db.query(Business).filter(Business.id == "biz_001").first()
        if existing:
            print("Database already contains biz_001. Skipping re-seed.")
            return

        print("Seeding database with realistic retail data for 'Green Valley Organic Grocers'...")

        # 1. Business
        biz = Business(
            id="biz_001",
            name="Green Valley Organic Grocers",
            category="Organic Retail & Daily Essentials",
            currency="INR",
            currency_symbol="₹",
            cash_drawer_target=5000.0,
            opening_cash=5000.0,
            current_cash_in_drawer=14850.0,  # 5,000 float + 9,850 total cash sales
            operating_hours="08:00 - 22:00",
            address="Shop 4, Block B, Indiranagar 100ft Road, Bengaluru",
            owner_name="Rajesh Kumar",
            owner_phone="+91 98765 43210"
        )
        db.add(biz)
        db.flush()

        # 2. Suppliers
        s1 = Supplier(
            id="supp_01",
            business_id="biz_001",
            name="MilkyWay Fresh Foods Co.",
            contact_person="Anil Sharma",
            phone="+91 98450 11223",
            email="orders@milkywaydairy.in",
            category="Dairy & Bakery",
            lead_time_hours=10,
            cutoff_time="20:00",
            payment_terms="Net 7 Days",
            min_order_value=1500.0
        )
        s2 = Supplier(
            id="supp_02",
            business_id="biz_001",
            name="PureOrigins Staples & Spices",
            contact_person="Deepak Verma",
            phone="+91 98100 22334",
            email="sales@pureorigins.com",
            category="Grocery & Staples",
            lead_time_hours=48,
            cutoff_time="17:00",
            payment_terms="Net 15 Days",
            min_order_value=3000.0
        )
        db.add_all([s1, s2])
        db.flush()

        # 3. Products & Inventory
        products_data = [
            ("prod_01", "supp_01", "DAIRY-MILK-01", "A2 Whole Farm Milk (1L)", "Dairy", "Pack", 52.0, 68.0, 4, 15, 25),
            ("prod_02", "supp_01", "DAIRY-EGGS-06", "Farm Fresh Brown Eggs (Pack of 6)", "Dairy & Eggs", "Pack", 65.0, 85.0, 3, 12, 20),
            ("prod_03", "supp_01", "BAKE-BREAD-01", "Organic Sourdough Loaf (400g)", "Bakery", "Loaf", 70.0, 110.0, 2, 8, 10),
            ("prod_04", "supp_01", "DAIRY-YOGURT-01", "Greek Yogurt Plain (400g)", "Dairy", "Tub", 80.0, 120.0, 5, 10, 15),
            ("prod_05", "supp_02", "GROC-OIL-01", "Cold Pressed Sunflower Oil (1L)", "Grocery", "Bottle", 180.0, 240.0, 18, 10, 12),
            ("prod_06", "supp_02", "GROC-SALT-01", "Himalayan Pink Rock Salt (1kg)", "Grocery", "Pack", 55.0, 90.0, 32, 12, 20),
        ]

        for pid, sid, sku, name, cat, unit, cost, price, stock, reorder, moq in products_data:
            p = Product(
                id=pid,
                business_id="biz_001",
                supplier_id=sid,
                sku=sku,
                name=name,
                category=cat,
                unit=unit,
                unit_cost=cost,
                retail_price=price
            )
            db.add(p)
        db.flush()

        for pid, sid, sku, name, cat, unit, cost, price, stock, reorder, moq in products_data:
            inv = Inventory(
                id=f"inv_{pid}",
                business_id="biz_001",
                product_id=pid,
                current_stock=stock,
                reorder_level=reorder,
                min_order_qty=moq,
                max_stock_level=60
            )
            db.add(inv)
        db.flush()

        # 4. Customers
        c1 = Customer(
            id="cust_01",
            business_id="biz_001",
            name="Priya Sharma",
            phone="+91 99887 76655",
            email="priya.sharma@example.com",
            credit_limit=5000.0,
            outstanding_due=1850.0,
            due_since_days=9
        )
        c2 = Customer(
            id="cust_02",
            business_id="biz_001",
            name="Vikram Rao",
            phone="+91 97766 55443",
            email="vikram.rao@example.com",
            credit_limit=6000.0,
            outstanding_due=2400.0,
            due_since_days=14,
            last_reminder_sent_at=datetime.datetime.utcnow() - datetime.timedelta(days=7)
        )
        c3 = Customer(
            id="cust_03",
            business_id="biz_001",
            name="Anita Desai",
            phone="+91 98112 23344",
            email="anita.desai@example.com",
            credit_limit=4000.0,
            outstanding_due=650.0,
            due_since_days=2
        )
        db.add_all([c1, c2, c3])
        db.flush()

        # 5. Today's Sales Transactions
        now = datetime.datetime.utcnow()
        sales_records = [
            ("INV-20260924-01", 1240.0, "Cash", "PAID", 4, now - datetime.timedelta(hours=9)),
            ("INV-20260924-02", 850.0, "UPI", "PAID", 2, now - datetime.timedelta(hours=8)),
            ("INV-20260924-03", 2150.0, "Cash", "PAID", 6, now - datetime.timedelta(hours=6)),
            ("INV-20260924-04", 1500.0, "Card", "PAID", 3, now - datetime.timedelta(hours=5)),
            ("INV-20260924-05", 1460.0, "Cash", "PAID", 4, now - datetime.timedelta(hours=4)),
            ("INV-20260924-06", 2650.0, "UPI", "PAID", 7, now - datetime.timedelta(hours=2)),
        ]
        # Total Sales = 1240 + 850 + 2150 + 1500 + 1460 + 2650 = 9850.0
        # Cash Sales = 1240 + 2150 + 1460 = 4850.0
        # UPI Sales = 850 + 2650 = 3500.0
        # Card Sales = 1500.0

        for order_no, amt, method, status, count, ts in sales_records:
            s = Sale(
                id=f"sale_{order_no}",
                business_id="biz_001",
                order_number=order_no,
                total_amount=amt,
                payment_method=method,
                payment_status=status,
                items_count=count,
                created_at=ts
            )
            db.add(s)
        db.flush()

        # 6. Audit initial event
        audit = AuditEvent(
            id="audit_init_001",
            business_id="biz_001",
            task_id="system_init",
            source="SYSTEM",
            event_type="SYSTEM_INITIALIZED",
            summary="Merchant Green Valley Organic Grocers workspace provisioned with initial catalog & balance.",
            details={"products": 6, "suppliers": 2, "customers": 3}
        )
        db.add(audit)

        db.commit()
        print("Database successfully seeded.")
    except Exception as e:
        db.rollback()
        print(f"Error seeding database: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    seed_database()
