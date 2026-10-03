import datetime
from typing import Optional, List, Any
from sqlalchemy import String, Integer, Float, DateTime, ForeignKey, Text, JSON, Boolean
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="Retail")
    currency: Mapped[str] = mapped_column(String(10), default="INR")
    currency_symbol: Mapped[str] = mapped_column(String(5), default="₹")
    cash_drawer_target: Mapped[float] = mapped_column(Float, default=5000.0)
    opening_cash: Mapped[float] = mapped_column(Float, default=5000.0)
    current_cash_in_drawer: Mapped[float] = mapped_column(Float, default=14850.0)
    operating_hours: Mapped[str] = mapped_column(String(64), default="08:00 - 22:00")
    address: Mapped[str] = mapped_column(String(256), default="Shop 4, Block B, Indiranagar, Bengaluru")
    owner_name: Mapped[str] = mapped_column(String(128), default="Rajesh Kumar")
    owner_phone: Mapped[str] = mapped_column(String(32), default="+91 98765 43210")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)

    products = relationship("Product", back_populates="business", cascade="all, delete-orphan")
    suppliers = relationship("Supplier", back_populates="business", cascade="all, delete-orphan")
    customers = relationship("Customer", back_populates="business", cascade="all, delete-orphan")
    sales = relationship("Sale", back_populates="business", cascade="all, delete-orphan")
    purchase_orders = relationship("PurchaseOrder", back_populates="business", cascade="all, delete-orphan")

class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    contact_person: Mapped[str] = mapped_column(String(128), default="")
    phone: Mapped[str] = mapped_column(String(32), default="")
    email: Mapped[str] = mapped_column(String(128), default="")
    category: Mapped[str] = mapped_column(String(64), default="General")
    lead_time_hours: Mapped[int] = mapped_column(Integer, default=12)
    cutoff_time: Mapped[str] = mapped_column(String(10), default="20:00")
    payment_terms: Mapped[str] = mapped_column(String(64), default="Net 7 Days")
    min_order_value: Mapped[float] = mapped_column(Float, default=1000.0)

    business = relationship("Business", back_populates="suppliers")
    products = relationship("Product", back_populates="supplier")

class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), nullable=False)
    supplier_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("suppliers.id"), nullable=True)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="General")
    unit: Mapped[str] = mapped_column(String(32), default="Unit")
    unit_cost: Mapped[float] = mapped_column(Float, nullable=False)
    retail_price: Mapped[float] = mapped_column(Float, nullable=False)

    business = relationship("Business", back_populates="products")
    supplier = relationship("Supplier", back_populates="products")
    inventory = relationship("Inventory", back_populates="product", uselist=False, cascade="all, delete-orphan")

class Inventory(Base):
    __tablename__ = "inventory"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), nullable=False)
    product_id: Mapped[str] = mapped_column(String(64), ForeignKey("products.id"), nullable=False)
    current_stock: Mapped[int] = mapped_column(Integer, default=0)
    reorder_level: Mapped[int] = mapped_column(Integer, default=10)
    min_order_qty: Mapped[int] = mapped_column(Integer, default=15)
    max_stock_level: Mapped[int] = mapped_column(Integer, default=60)
    last_updated: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)

    product = relationship("Product", back_populates="inventory")

class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), default="")
    email: Mapped[str] = mapped_column(String(128), default="")
    credit_limit: Mapped[float] = mapped_column(Float, default=5000.0)
    outstanding_due: Mapped[float] = mapped_column(Float, default=0.0)
    due_since_days: Mapped[int] = mapped_column(Integer, default=0)
    last_reminder_sent_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)

    business = relationship("Business", back_populates="customers")

class Sale(Base):
    __tablename__ = "sales"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), nullable=False)
    order_number: Mapped[str] = mapped_column(String(64), nullable=False)
    customer_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("customers.id"), nullable=True)
    total_amount: Mapped[float] = mapped_column(Float, nullable=False)
    payment_method: Mapped[str] = mapped_column(String(32), default="Cash")  # Cash, UPI, Card, Store Credit
    payment_status: Mapped[str] = mapped_column(String(32), default="PAID")  # PAID, CREDIT, PARTIAL
    items_count: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)

    business = relationship("Business", back_populates="sales")
    items = relationship("SaleItem", back_populates="sale", cascade="all, delete-orphan")

class SaleItem(Base):
    __tablename__ = "sale_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sale_id: Mapped[str] = mapped_column(String(64), ForeignKey("sales.id"), nullable=False)
    product_id: Mapped[str] = mapped_column(String(64), ForeignKey("products.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    subtotal: Mapped[float] = mapped_column(Float, nullable=False)

    sale = relationship("Sale", back_populates="items")
    product = relationship("Product")

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), nullable=False)
    supplier_id: Mapped[str] = mapped_column(String(64), ForeignKey("suppliers.id"), nullable=False)
    order_number: Mapped[str] = mapped_column(String(64), nullable=False)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="PREPARED")  # DRAFT, PREPARED, APPROVED, EXECUTED, CANCELLED
    items: Mapped[Any] = mapped_column(JSON, default=list)  # list of {product_id, name, quantity, unit_cost, subtotal}
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    external_reference: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    executed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)

    business = relationship("Business", back_populates="purchase_orders")
    supplier = relationship("Supplier")

class ActionRecord(Base):
    __tablename__ = "actions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)  # PURCHASE_ORDER, PAYMENT_REMINDER, CAMPAIGN_BROADCAST
    status: Mapped[str] = mapped_column(String(32), default="PLANNED")
    # Lifecycle: PLANNED -> PREPARED -> WAITING_FOR_APPROVAL -> APPROVED / REJECTED -> EXECUTING -> COMPLETED / FAILED -> VERIFIED
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    data_used: Mapped[Any] = mapped_column(JSON, default=dict)
    payload: Mapped[Any] = mapped_column(JSON, default=dict)
    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    approval_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approved_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    verified_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    verification_details: Mapped[Any] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[str] = mapped_column(String(64), ForeignKey("businesses.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(64), default="AGENT")  # AGENT, TOOL, SANDBOX, HUMAN, N8N, VOICE
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(String(256), nullable=False)
    details: Mapped[Any] = mapped_column(JSON, default=dict)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)

# =====================================================================
# HOSPI-ONE HOSPITAL OPERATIONS MODELS
# =====================================================================

class Department(Base):
    __tablename__ = "hospital_departments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    extension: Mapped[str] = mapped_column(String(32), default="")
    phone: Mapped[str] = mapped_column(String(32), default="")
    head: Mapped[str] = mapped_column(String(128), default="")
    location: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(64), default="Normal")
    pending_requests_count: Mapped[int] = mapped_column(Integer, default=0)
    category: Mapped[str] = mapped_column(String(64), default="Operational")

class HospitalTask(Base):
    __tablename__ = "hospital_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    department: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), default="Staff")
    assigned_to: Mapped[str] = mapped_column(String(128), default="Unassigned")
    status: Mapped[str] = mapped_column(String(32), default="Pending")
    # Statuses: Pending, Awaiting Approval, In Progress, Completed, Cancelled, Failed
    priority: Mapped[str] = mapped_column(String(32), default="Standard")
    # Priorities: Critical, High, Standard, Low
    category: Mapped[str] = mapped_column(String(64), default="Operational")
    due_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

class HospitalCall(Base):
    __tablename__ = "hospital_calls"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    department: Mapped[str] = mapped_column(String(128), nullable=False)
    extension: Mapped[str] = mapped_column(String(32), default="")
    recipient_phone: Mapped[str] = mapped_column(String(32), default="")
    recipient_name: Mapped[str] = mapped_column(String(128), default="")
    purpose: Mapped[str] = mapped_column(String(256), nullable=False)
    initiated_by: Mapped[str] = mapped_column(String(128), default="Voice Command")
    time: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="Completed")
    # Statuses: Completed, In Progress, No Answer, Cancelled, Demo
    calle_call_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

class InventoryRequest(Base):
    __tablename__ = "hospital_inventory_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    item_name: Mapped[str] = mapped_column(String(128), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), default="")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit: Mapped[str] = mapped_column(String(32), default="boxes")
    department: Mapped[str] = mapped_column(String(128), default="General Ward")
    requested_by: Mapped[str] = mapped_column(String(128), default="Nurse Demo")
    status: Mapped[str] = mapped_column(String(32), default="Pending Approval")
    # Statuses: Pending Approval, Approved, In Transit, Fulfilled, Rejected
    priority: Mapped[str] = mapped_column(String(32), default="Standard")
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    approved_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)

class FollowUp(Base):
    __tablename__ = "hospital_followups"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    patient_id: Mapped[str] = mapped_column(String(64), nullable=False)  # Synthetic ID e.g. PAT-00124
    department: Mapped[str] = mapped_column(String(128), default="Outpatient Coordination")
    last_contact: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    next_scheduled: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    contact_status: Mapped[str] = mapped_column(String(32), default="Scheduled")
    # Statuses: Scheduled, Pending Contact, Contacted, Rescheduled, Unable to Reach, Completed
    assigned_staff: Mapped[str] = mapped_column(String(128), default="Coordination Nurse")
    administrative_status: Mapped[str] = mapped_column(String(128), default="Appointment Reminder Due")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

class HospitalDevice(Base):
    __tablename__ = "hospital_devices"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    location: Mapped[str] = mapped_column(String(128), nullable=False)
    hardware_type: Mapped[str] = mapped_column(String(128), default="ESP32 SPI TFT / Prototype")
    status: Mapped[str] = mapped_column(String(32), default="ONLINE")
    # Statuses: ONLINE, OFFLINE, LISTENING, PROCESSING, AWAITING_APPROVAL, EXECUTING
    mic_status: Mapped[str] = mapped_column(String(32), default="Ready")
    speaker_status: Mapped[str] = mapped_column(String(32), default="Ready")
    display_status: Mapped[str] = mapped_column(String(64), default="Ready (320x240 TFT)")
    network_status: Mapped[str] = mapped_column(String(64), default="Connected (Wi-Fi 5GHz)")
    last_heartbeat: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    current_user: Mapped[str] = mapped_column(String(128), default="Reception Staff")
    last_command: Mapped[str] = mapped_column(String(256), default="Ready for voice commands")
    software_version: Mapped[str] = mapped_column(String(32), default="v2.4.0-hospi")
    firmware_version: Mapped[str] = mapped_column(String(32), default="esp32-v1.8.2")

