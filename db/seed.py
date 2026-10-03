import datetime
from db.database import init_db, SessionLocal
from db.models import (
    Business, Supplier, Product, Inventory, Customer, Sale, SaleItem, AuditEvent,
    Department, HospitalTask, HospitalCall, InventoryRequest, FollowUp, HospitalDevice
)

def seed_database():
    init_db()
    db = SessionLocal()
    try:
        now = datetime.datetime.utcnow()

        # -------------------------------------------------------------
        # 1. Hospital Business / Facility Profile (HospiOne)
        # -------------------------------------------------------------
        existing_biz = db.query(Business).filter(Business.id == "biz_001").first()
        if not existing_biz:
            biz = Business(
                id="biz_001",
                name="HospiOne Hospital Operations Center",
                category="Multi-Specialty Hospital Campus",
                currency="INR",
                currency_symbol="₹",
                cash_drawer_target=5000.0,
                opening_cash=5000.0,
                current_cash_in_drawer=14850.0,
                operating_hours="24/7 Operations",
                address="HospiOne Health Hub, 100ft Road, Indiranagar, Bengaluru",
                owner_name="Dr. Demo / Medical Director",
                owner_phone="+91 80 4123 4500"
            )
            db.add(biz)
            db.flush()
        else:
            existing_biz.name = "HospiOne Hospital Operations Center"
            existing_biz.category = "Multi-Specialty Hospital Campus"
            existing_biz.operating_hours = "24/7 Operations"
            existing_biz.owner_name = "Dr. Demo / Medical Director"
            existing_biz.owner_phone = "+91 80 4123 4500"
            existing_biz.address = "HospiOne Health Hub, 100ft Road, Indiranagar, Bengaluru"
            db.flush()

        # -------------------------------------------------------------
        # 2. Medical Vendors / Suppliers
        # -------------------------------------------------------------
        supps = [
            ("supp_01", "MedEquip Diagnostics & Consumables Co.", "Anil Sharma", "+91 84960 74290", "orders@medequip.in", "Medical Consumables & PPE", 6, "20:00", 1500.0),
            ("supp_02", "Apex Surgical & Healthcare Supplies", "Deepak Verma", "+91 98100 22334", "sales@apexsurgical.com", "Surgical & Infusion Systems", 24, "17:00", 3000.0),
        ]
        for sid, sname, cname, sphone, semail, scat, lead, cutoff, min_val in supps:
            existing_s = db.query(Supplier).filter(Supplier.id == sid).first()
            if not existing_s:
                s = Supplier(
                    id=sid,
                    business_id="biz_001",
                    name=sname,
                    contact_person=cname,
                    phone=sphone,
                    email=semail,
                    category=scat,
                    lead_time_hours=lead,
                    cutoff_time=cutoff,
                    payment_terms="Net 15 Days",
                    min_order_value=min_val
                )
                db.add(s)
            else:
                existing_s.name = sname
                existing_s.category = scat
                existing_s.phone = sphone
        db.flush()

        # -------------------------------------------------------------
        # 3. Medical Products & Hospital Inventory Catalog
        # -------------------------------------------------------------
        products_data = [
            ("prod_01", "supp_01", "MED-GLV-01", "Examination Gloves (Nitrile Powder-Free)", "Personal Protective Equipment", "boxes", 420.0, 550.0, 420, 50, 20),
            ("prod_02", "supp_01", "MED-MSK-02", "3-Ply Surgical Masks (Fluid Resistant)", "Personal Protective Equipment", "boxes", 180.0, 250.0, 280, 40, 20),
            ("prod_03", "supp_01", "MED-SYR-03", "Sterile Disposable Syringes 5ml with Needle", "General Medical Consumables", "boxes", 320.0, 450.0, 15, 30, 25),
            ("prod_04", "supp_01", "MED-SAN-04", "Hand Sanitizer 500ml (Hospital Grade 75% IPA)", "Hygiene & Sanitation", "bottles", 140.0, 195.0, 42, 50, 30),
            ("prod_05", "supp_02", "MED-N95-05", "N95 Particulate Respirator Masks", "Personal Protective Equipment", "boxes", 650.0, 850.0, 85, 25, 15),
            ("prod_06", "supp_02", "MED-IVS-06", "IV Infusion Administration Sets (Vented)", "Infusion & Vascular Access", "packs", 280.0, 390.0, 18, 25, 20),
            ("prod_07", "supp_02", "MED-THM-07", "Digital Thermometer Probe Covers", "Diagnostic Accessories", "packs", 150.0, 220.0, 65, 20, 10),
        ]
        for pid, sid, sku, name, cat, unit, cost, price, stock, reorder, moq in products_data:
            existing_p = db.query(Product).filter(Product.id == pid).first()
            if not existing_p:
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
            else:
                existing_p.sku = sku
                existing_p.name = name
                existing_p.category = cat
                existing_p.unit = unit
                existing_p.unit_cost = cost
                existing_p.retail_price = price
        db.flush()

        for pid, sid, sku, name, cat, unit, cost, price, stock, reorder, moq in products_data:
            existing_inv = db.query(Inventory).filter(Inventory.product_id == pid).first()
            if not existing_inv:
                inv = Inventory(
                    id=f"inv_{pid}",
                    business_id="biz_001",
                    product_id=pid,
                    current_stock=stock,
                    reorder_level=reorder,
                    min_order_qty=moq,
                    max_stock_level=600
                )
                db.add(inv)
            else:
                existing_inv.current_stock = stock
                existing_inv.reorder_level = reorder
                existing_inv.min_order_qty = moq
        db.flush()

        # -------------------------------------------------------------
        # 4. Hospital Departments Directory
        # -------------------------------------------------------------
        departments_data = [
            ("dept_reception", "Reception & Patient Admissions", "101", "+91 80 4123 4501", "Mrs. Kavita R.", "Main Atrium Ground Floor", "Normal", 1, "Administrative"),
            ("dept_biomed", "Biomedical Engineering", "214", "+91 80 4123 4514", "Er. Ramesh K.", "Engineering Annex B-12", "3 pending", 3, "Technical & Maintenance"),
            ("dept_inventory", "Central Hospital Stores & Inventory", "305", "+91 80 4123 4535", "Mr. Suresh Nair", "Basement Logistics Hub", "5 pending", 5, "Supply Chain"),
            ("dept_facilities", "Facilities & Housekeeping", "118", "+91 80 4123 4518", "Mr. Anand P.", "Service Block C", "2 pending", 2, "Operations & Facilities"),
            ("dept_it", "IT & Systems Support", "404", "+91 80 4123 4544", "Ms. Shalini Murthy", "Admin Tower 4th Floor", "4 pending", 4, "IT & Infrastructure"),
            ("dept_admin", "Hospital Administration & Medical Records", "501", "+91 80 4123 4550", "Dr. Vikram Seth", "Admin Tower 5th Floor", "Normal", 0, "Executive Administration"),
            ("dept_lab", "Clinical Pathology & Diagnostic Lab", "612", "+91 80 4123 4562", "Dr. Ananya Roy", "Diagnostic Wing 1st Floor", "Normal", 1, "Diagnostic Support"),
            ("dept_radiology", "Radiology & Imaging Services", "708", "+91 80 4123 4578", "Dr. Sandeep Rao", "Imaging Center Ground Floor", "Normal", 0, "Imaging Services")
        ]
        for did, dname, ext, phone, head, loc, status, pending, cat in departments_data:
            existing_d = db.query(Department).filter(Department.id == did).first()
            if not existing_d:
                d = Department(
                    id=did,
                    name=dname,
                    extension=ext,
                    phone=phone,
                    head=head,
                    location=loc,
                    status=status,
                    pending_requests_count=pending,
                    category=cat
                )
                db.add(d)
            else:
                existing_d.name = dname
                existing_d.extension = ext
                existing_d.phone = phone
                existing_d.head = head
                existing_d.location = loc
                existing_d.status = status
                existing_d.pending_requests_count = pending
        db.flush()

        # -------------------------------------------------------------
        # 5. Hospital Tasks (12 Pending / Active Operational Tasks)
        # -------------------------------------------------------------
        tasks_data = [
            ("TASK-1024", "Check pending biomedical maintenance request for ICU Ventilator #4", "Biomedical Engineering", "Dr. Demo", "Er. Ramesh K. (BioTech)", "Pending", "Critical", now + datetime.timedelta(hours=2)),
            ("TASK-1025", "Create examination glove inventory request for OPD Ward", "Central Hospital Stores", "Nurse Demo", "Suresh Nair (Stores)", "Awaiting Approval", "Standard", now + datetime.timedelta(hours=3)),
            ("TASK-1026", "Confirm OPD reception equipment availability and check-in kiosks", "Reception & Patient Admissions", "Dr. Demo", "Kavita R. (Reception)", "In Progress", "Standard", now + datetime.timedelta(hours=1)),
            ("TASK-1027", "Air conditioning maintenance in OPD Consultation Room 302", "Facilities & Housekeeping", "Dr. Demo", "Anand P. (Facilities)", "Pending", "High", now + datetime.timedelta(hours=4)),
            ("TASK-1028", "Verify electronic health record network connectivity in Ward B", "IT & Systems Support", "Nurse Staff", "Shalini Murthy (IT)", "Completed", "Standard", now - datetime.timedelta(hours=2)),
            ("TASK-1029", "Sanitation and sterilization cycle audit for Minor OT 2", "Facilities & Housekeeping", "Dr. Raman", "Facilities Team", "Completed", "Standard", now - datetime.timedelta(hours=4)),
            ("TASK-1030", "Prepare weekend emergency buffer inventory report", "Central Hospital Stores", "Hospital Administrator", "Suresh Nair (Stores)", "In Progress", "Low", now + datetime.timedelta(hours=8)),
            ("TASK-1031", "Calibrate non-invasive blood pressure monitors in Triage", "Biomedical Engineering", "Nurse Demo", "Er. Ramesh K. (BioTech)", "Pending", "Standard", now + datetime.timedelta(hours=5)),
            ("TASK-1032", "Replace UPS backup battery in Radiology Server Rack", "IT & Systems Support", "IT Helpdesk", "IT Hardware Team", "Pending", "High", now + datetime.timedelta(hours=3)),
            ("TASK-1033", "Post-discharge administrative transport coordination for PAT-00124", "Reception & Patient Admissions", "Staff", "Nurse Priya M.", "Pending", "Standard", now + datetime.timedelta(hours=2)),
            ("TASK-1034", "Audit fire safety equipment in Wing 3 corridors", "Facilities & Housekeeping", "Administrator", "Safety Officer", "Completed", "Low", now - datetime.timedelta(hours=1)),
            ("TASK-1035", "Review supplier delivery SLA for Sterile Syringes batch", "Central Hospital Stores", "Stores Head", "Procurement Desk", "Pending", "Standard", now + datetime.timedelta(hours=6)),
        ]
        for tid, title, dept, cby, assto, status, prio, due in tasks_data:
            existing_t = db.query(HospitalTask).filter(HospitalTask.id == tid).first()
            if not existing_t:
                t = HospitalTask(
                    id=tid,
                    title=title,
                    department=dept,
                    created_by=cby,
                    assigned_to=assto,
                    status=status,
                    priority=prio,
                    due_at=due,
                    created_at=now - datetime.timedelta(hours=5),
                    completed_at=now - datetime.timedelta(hours=1) if status == "Completed" else None
                )
                db.add(t)
            else:
                existing_t.title = title
                existing_t.status = status
                existing_t.priority = prio
                existing_t.assigned_to = assto
        db.flush()

        # -------------------------------------------------------------
        # 6. Hospital Calls (8 Calls Today matching Section 15 KPI)
        # -------------------------------------------------------------
        calls_data = [
            ("CALL-8841", "Biomedical Engineering", "214", "+91 84960 74290", "Er. Ramesh K.", "Maintenance request update for Room 302", "Dr. Demo (Voice)", 64, "Completed", "calle_demo_01", False),
            ("CALL-8842", "Central Hospital Stores", "305", "+91 80 4123 4535", "Suresh Nair", "Emergency stock verification for Examination Gloves", "Nurse Demo (Voice)", 82, "Completed", "calle_demo_02", False),
            ("CALL-8843", "Facilities & Housekeeping", "118", "+91 80 4123 4518", "Anand P.", "Air conditioning filter repair in Consultation Room 302", "Dr. Demo (Voice)", 45, "Completed", "calle_demo_03", False),
            ("CALL-8844", "IT & Systems Support", "404", "+91 80 4123 4544", "Shalini Murthy", "Badge scanner authentication timeout in OPD East", "Reception Desk", 55, "Completed", "calle_demo_04", False),
            ("CALL-8845", "Reception & Admissions", "101", "+91 80 4123 4501", "Kavita R.", "Patient arrival coordination and wheelchair staging", "Administrator", 38, "Completed", "calle_demo_05", False),
            ("CALL-8846", "Clinical Pathology Lab", "612", "+91 80 4123 4562", "Dr. Ananya Roy", "Batch turnaround time inquiry for routine morning panels", "OPD Nurse", 50, "Completed", "calle_demo_06", False),
            ("CALL-8847", "Radiology & Imaging", "708", "+91 80 4123 4578", "Dr. Sandeep Rao", "Equipment maintenance window confirmation", "Operations Lead", 72, "Completed", "calle_demo_07", False),
            ("CALL-8848", "Biomedical Engineering", "214", "+91 80 4123 4514", "Er. Ramesh K.", "Defibrillator preventive maintenance schedule check", "ICU Station #02 (Voice)", 40, "Completed", "calle_demo_08", False),
        ]
        for cid, dept, ext, phone, rname, purp, init_by, dur, status, calle_id, is_sim in calls_data:
            existing_c = db.query(HospitalCall).filter(HospitalCall.id == cid).first()
            if not existing_c:
                c = HospitalCall(
                    id=cid,
                    department=dept,
                    extension=ext,
                    recipient_phone=phone,
                    recipient_name=rname,
                    purpose=purp,
                    initiated_by=init_by,
                    time=now - datetime.timedelta(minutes=dur * 3),
                    duration_seconds=dur,
                    status=status,
                    calle_call_id=calle_id,
                    is_simulated=is_sim
                )
                db.add(c)
            else:
                existing_c.status = status
                existing_c.purpose = purp
        db.flush()

        # -------------------------------------------------------------
        # 7. Open Hospital Inventory Requests (5 Requests matching Section 15 KPI)
        # -------------------------------------------------------------
        inv_reqs = [
            ("REQ-1048", "Examination Gloves (Nitrile)", "MED-GLV-01", 20, "boxes", "OPD Nursing Station", "Nurse Demo", "Pending Approval", "Standard", "Regular shift restock for examination cubicles 1-6."),
            ("REQ-1049", "Sterile Disposable Syringes 5ml", "MED-SYR-03", 25, "boxes", "Inpatient Ward 3", "Nurse Mary S.", "Pending Approval", "Urgent", "Stock depleted below 15-box safety buffer threshold."),
            ("REQ-1050", "Hand Sanitizer 500ml", "MED-SAN-04", 30, "bottles", "Visitor Entrance & Triage", "Facilities Lead", "Approved", "Standard", "Weekly sanitization dispenser replenishment."),
            ("REQ-1051", "IV Infusion Administration Sets", "MED-IVS-06", 20, "packs", "Emergency Day Care", "Duty Sister", "Pending Approval", "Urgent", "Emergency reserve replenishment before weekend."),
            ("REQ-1052", "3-Ply Surgical Masks", "MED-MSK-02", 15, "boxes", "OPD Reception Counter", "Reception Staff", "Pending Approval", "Standard", "Front desk visitor distribution buffer."),
        ]
        for rid, item, sku, qty, unit, dept, req_by, status, prio, rat in inv_reqs:
            existing_r = db.query(InventoryRequest).filter(InventoryRequest.id == rid).first()
            if not existing_r:
                r = InventoryRequest(
                    id=rid,
                    item_name=item,
                    sku=sku,
                    quantity=qty,
                    unit=unit,
                    department=dept,
                    requested_by=req_by,
                    status=status,
                    priority=prio,
                    rationale=rat,
                    created_at=now - datetime.timedelta(hours=2)
                )
                db.add(r)
            else:
                existing_r.status = status
                existing_r.quantity = qty
        db.flush()

        # -------------------------------------------------------------
        # 8. Administrative Follow-ups (17 Due Today matching Section 15 KPI)
        # Strictly administrative / continuity of care — NO clinical triage
        # -------------------------------------------------------------
        followups_data = [
            ("FOL-101", "PAT-00124", "Cardiology OPD", "Pending Contact", "Nurse Priya M.", "Appointment Reminder Due", "Routine appointment schedule reminder for tomorrow morning."),
            ("FOL-102", "PAT-00125", "Orthopedics OPD", "Scheduled", "Staff Ramesh", "Insurance Documentation Due", "Remind patient to submit physical TPA insurance pre-auth form."),
            ("FOL-103", "PAT-00126", "General Medicine", "Contacted", "Nurse Priya M.", "Follow-up Confirmed", "Confirmed administrative appointment check-in for 11:00 AM."),
            ("FOL-104", "PAT-00127", "Dermatology Clinic", "Rescheduled", "Front Desk Staff", "Rescheduled to Thursday", "Patient requested slot change; administrative slot updated."),
            ("FOL-105", "PAT-00128", "Ophthalmology OPD", "Scheduled", "Nurse Mary S.", "Appointment Reminder Due", "Send automated appointment time confirmation."),
            ("FOL-106", "PAT-00129", "ENT Consultation", "Pending Contact", "Staff Ramesh", "Billing Clearance Verification", "Verify pre-discharge billing clearance paperwork."),
            ("FOL-107", "PAT-00130", "Pediatrics OPD", "Scheduled", "Nurse Priya M.", "Vaccination Slot Confirmation", "Routine administrative slot booking reminder."),
            ("FOL-108", "PAT-00131", "Neurology Review", "Pending Contact", "Staff Ramesh", "Digital Scan Slip Retrieval", "Coordinate collection of hard-copy imaging film."),
            ("FOL-109", "PAT-00132", "Nephrology Day Care", "Scheduled", "Nurse Mary S.", "Transport Coordination", "Confirm hospital shuttle pickup time with patient family."),
            ("FOL-110", "PAT-00133", "Pulmonology Clinic", "Pending Contact", "Front Desk Staff", "Appointment Reminder Due", "Routine follow-up confirmation call."),
            ("FOL-111", "PAT-00134", "Gastroenterology", "Scheduled", "Nurse Priya M.", "Dietary Fasting Instructions Ack", "Confirm receipt of non-clinical pre-procedure fasting pamphlet."),
            ("FOL-112", "PAT-00135", "Endocrinology Review", "Pending Contact", "Staff Ramesh", "Administrative Check-in Due", "Verify current phone contact and emergency contact on file."),
            ("FOL-113", "PAT-00136", "Rheumatology Clinic", "Scheduled", "Nurse Mary S.", "Appointment Reminder Due", "Routine OPD visit slot reminder."),
            ("FOL-114", "PAT-00137", "Physiotherapy Center", "Scheduled", "Front Desk Staff", "Session 4 Coordination", "Administrative attendance slot confirmation."),
            ("FOL-115", "PAT-00138", "General Surgery OPD", "Pending Contact", "Nurse Priya M.", "Wound Dressing Slip Reminder", "Remind patient to bring dressing clinic visit voucher."),
            ("FOL-116", "PAT-00139", "Oncology Supportive", "Scheduled", "Staff Ramesh", "Care Coordinator Meeting", "Confirm administrative care coordination meeting slot."),
            ("FOL-117", "PAT-00140", "Urology Outpatient", "Pending Contact", "Nurse Mary S.", "Appointment Reminder Due", "Send SMS reminder for tomorrow afternoon appointment.")
        ]
        for fid, pid, dept, cstatus, astaff, astatus, notes in followups_data:
            existing_f = db.query(FollowUp).filter(FollowUp.id == fid).first()
            if not existing_f:
                f = FollowUp(
                    id=fid,
                    patient_id=pid,
                    department=dept,
                    last_contact=now - datetime.timedelta(days=2),
                    next_scheduled=now + datetime.timedelta(hours=4),
                    contact_status=cstatus,
                    assigned_staff=astaff,
                    administrative_status=astatus,
                    notes=notes
                )
                db.add(f)
            else:
                existing_f.contact_status = cstatus
                existing_f.administrative_status = astatus
        db.flush()

        # -------------------------------------------------------------
        # 9. Hospital Physical AI Devices (3 Online / 1 Offline = 3/4)
        # -------------------------------------------------------------
        devices_data = [
            ("HOSPI-01", "HOSPI Device #01", "OPD Reception Counter", "ESP32 SPI TFT / Prototype", "ONLINE", "Ready", "Ready", "Ready (320x240 TFT)", "Connected (Wi-Fi 5GHz)", "Reception Staff / Dr. Demo", "Show today's appointment follow-ups"),
            ("HOSPI-02", "HOSPI Device #02", "ICU Nursing Station East", "ESP32 Audio Kit / Enclosure", "ONLINE", "Ready", "Ready", "Ready (Color TFT)", "Connected (Wi-Fi 5GHz)", "Nurse Demo", "Create inventory request for 20 boxes of examination gloves"),
            ("HOSPI-03", "HOSPI Device #03", "Biomedical Engineering Workshop", "Raspberry Pi 4 / Mic Array", "ONLINE", "Ready", "Ready", "Ready (7-inch Touch)", "Connected (Ethernet)", "Er. Ramesh K.", "Check pending maintenance tickets"),
            ("HOSPI-04", "HOSPI Device #04", "Emergency Triage Desk", "ESP32 SPI TFT Prototype", "OFFLINE", "Standby", "Standby", "Sleeping", "Reconnecting", "Triage Team", "Maintenance diagnostic run"),
        ]
        for did, dname, loc, hw, stat, mic, spk, disp, net, user, cmd in devices_data:
            existing_dev = db.query(HospitalDevice).filter(HospitalDevice.id == did).first()
            if not existing_dev:
                dev = HospitalDevice(
                    id=did,
                    name=dname,
                    location=loc,
                    hardware_type=hw,
                    status=stat,
                    mic_status=mic,
                    speaker_status=spk,
                    display_status=disp,
                    network_status=net,
                    last_heartbeat=now if stat == "ONLINE" else now - datetime.timedelta(minutes=15),
                    current_user=user,
                    last_command=cmd
                )
                db.add(dev)
            else:
                existing_dev.status = stat
                existing_dev.last_command = cmd
                existing_dev.last_heartbeat = now if stat == "ONLINE" else now - datetime.timedelta(minutes=15)
        db.flush()

        # -------------------------------------------------------------
        # 10. Audit initial event for HospiOne
        # -------------------------------------------------------------
        audit = AuditEvent(
            id=f"audit_hospi_init_{now.strftime('%H%M%S')}",
            business_id="biz_001",
            task_id="hospi_init",
            source="SYSTEM",
            event_type="HOSPITAL_OPERATIONS_INITIALIZED",
            summary="HospiOne Hospital Operations Center provisioned with 8 departments, 12 tasks, 5 inventory requests, 17 follow-ups, and 4 physical AI devices.",
            details={"departments": 8, "tasks": 12, "inventory_requests": 5, "follow_ups": 17, "devices_online": "3/4"}
        )
        db.add(audit)

        db.commit()
        print("Hospital Operations Database successfully seeded with HospiOne dataset.")
    except Exception as e:
        db.rollback()
        print(f"Error seeding hospital database: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    seed_database()
