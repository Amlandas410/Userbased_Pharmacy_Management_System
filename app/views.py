import csv, datetime, decimal, io, json, secrets, re, hashlib, unicodedata
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort, current_app, make_response, jsonify
from werkzeug.exceptions import HTTPException
from .db import tenant_db, audit
main_bp=Blueprint("main",__name__)
MEDICINE_CSV_FIELDS=("name","category","manufacturer","barcode","batch_number","expiry_date","purchase_price","selling_price","quantity","reorder_level","gst_percent")

def guard(*allowed):
    if not session.get("user_id"): return redirect(url_for("auth.login"))
    if session.get("role") not in allowed: abort(403)
    return None
def money(v): return decimal.Decimal(str(v or "0")).quantize(decimal.Decimal("0.01"))

MONEY_LIMIT = decimal.Decimal("9999999999.99")
PAYMENT_METHODS = {"cash", "upi", "card", "bank_transfer", "other"}

def parse_money_field(raw, *, allow_zero=True, field_name="Amount"):
    try:
        raw_text = str(raw).strip()
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", raw_text):
            raise ValueError
        value = decimal.Decimal(raw_text)
        if (not value.is_finite() or value > MONEY_LIMIT or value < 0 or
                (not allow_zero and value == 0) or
                value != value.quantize(decimal.Decimal("0.01"))):
            raise ValueError
        return value.quantize(decimal.Decimal("0.01"))
    except (decimal.InvalidOperation, ValueError, AttributeError):
        raise ValueError(f"{field_name} must be a valid amount with at most two decimal places.")

def normalize_phone(raw):
    raw = str(raw or "")
    if not raw:
        return None
    if not re.fullmatch(r"[6-9][0-9]{9}", raw):
        raise ValueError("Phone number must be a valid 10-digit mobile number starting with 6–9.")
    return raw

def valid_contact_name(value):
    value = str(value or "").strip()
    return not value or (
        len(value) <= 160
        and any(unicodedata.category(ch).startswith("L") for ch in value)
        and all(unicodedata.category(ch).startswith(("L", "M")) or ch == " " for ch in value)
    )

def valid_person_name(value):
    value = str(value or "").strip()
    def is_letter_or_mark(ch):
        return unicodedata.category(ch).startswith(("L", "M"))
    return bool(value and len(value) <= 160 and any(unicodedata.category(ch).startswith("L") for ch in value)
                and all(is_letter_or_mark(ch) or ch.isspace() or ch in " .'-" for ch in value))

def parse_whole_number(raw, field_name, *, default=None, maximum=4294967295):
    text = str(default if raw is None or str(raw).strip()=="" else raw).strip()
    if not re.fullmatch(r"[0-9]+", text):
        raise ValueError(f"{field_name} must be a non-negative whole number.")
    value = int(text)
    if value > maximum:
        raise ValueError(f"{field_name} exceeds the maximum allowed value.")
    return value

def parse_medicine_form(form):
    name=str(form.get("name", "")).strip()
    category=str(form.get("category", "")).strip()
    manufacturer=str(form.get("manufacturer", "")).strip() or None
    barcode=str(form.get("barcode", "")).strip() or None
    batch=str(form.get("batch_number", "")).strip()
    errors=[]
    if not name: errors.append("Medicine name is required.")
    elif len(name)>180 or any(unicodedata.category(ch).startswith("C") for ch in name): errors.append("Medicine name must be 1–180 characters without control characters.")
    if not category: errors.append("Category is required.")
    elif len(category)>100 or any(unicodedata.category(ch).startswith("C") for ch in category): errors.append("Category must not exceed 100 characters.")
    if manufacturer and (len(manufacturer)>160 or any(unicodedata.category(ch).startswith("C") for ch in manufacturer)): errors.append("Manufacturer must not exceed 160 characters or contain control characters.")
    if barcode and not re.fullmatch(r"[A-Za-z0-9-]{3,100}", barcode): errors.append("Barcode must contain 3–100 letters, digits or hyphens.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", batch): errors.append("Batch number is required and may contain letters, digits, dots, underscores, slashes or hyphens.")
    expiry_text=str(form.get("expiry_date", "")).strip()
    expiry=None
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", expiry_text):
        errors.append("Expiry date must use YYYY-MM-DD.")
    else:
        try: expiry=datetime.date.fromisoformat(expiry_text)
        except ValueError: errors.append("Expiry date is not a valid calendar date.")
        if expiry and expiry<=datetime.date.today(): errors.append("Expiry date must be after today.")
    purchase=selling=gst=None
    try: purchase=parse_money_field(form.get("purchase_price", ""),allow_zero=False,field_name="Purchase price")
    except ValueError as exc: errors.append(str(exc))
    try: selling=parse_money_field(form.get("selling_price", ""),allow_zero=False,field_name="Selling price")
    except ValueError as exc: errors.append(str(exc))
    if purchase is not None and selling is not None and selling<purchase: errors.append("Selling price must be at least purchase price.")
    try: quantity=parse_whole_number(form.get("quantity", "0"),"Quantity",default="0")
    except ValueError as exc: quantity=0; errors.append(str(exc))
    try: reorder_level=parse_whole_number(form.get("reorder_level", "10"),"Reorder level",default="10")
    except ValueError as exc: reorder_level=10; errors.append(str(exc))
    try: gst=parse_money_field(form.get("gst_percent", "0"),field_name="GST")
    except ValueError as exc: gst=None; errors.append(str(exc))
    if gst is not None and gst>100: errors.append("GST must be between 0 and 100 percent.")
    if errors: raise ValueError(" ".join(errors))
    return {"name":name,"category":category,"manufacturer":manufacturer,"barcode":barcode,"batch_number":batch,
        "expiry_date":expiry,"purchase_price":purchase,"selling_price":selling,"quantity":quantity,
        "reorder_level":reorder_level,"gst_percent":gst}

def parse_medicine_csv(content):
    reader=csv.DictReader(io.StringIO(content,newline=""),strict=True)
    headers=[(header or "").strip().lower() for header in (reader.fieldnames or [])]
    if len(headers)!=len(set(headers)) or set(headers)!=set(MEDICINE_CSV_FIELDS):
        return [],["CSV must contain these columns exactly once: "+", ".join(MEDICINE_CSV_FIELDS)]
    medicines=[]; errors=[]; barcode_identities={}
    row_number=1
    while True:
        try:
            row=next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            errors.append(f"Could not read CSV: {exc}.")
            break
        row_number+=1
        if row_number>1001:
            errors.append("CSV cannot contain more than 1000 medicine rows.")
            break
        values={key:(row.get(original) or "").strip() for key,original in zip(headers,reader.fieldnames)}
        if None in row:
            errors.append(f"Row {row_number}: too many columns.")
            continue
        if not any(values.values()):
            continue
        try:
            name=values["name"]; category=values["category"] or None
            manufacturer=values["manufacturer"] or None; barcode=values["barcode"] or None
            batch=values["batch_number"]
            if not name or not category or not batch or not values["expiry_date"] or not values["purchase_price"] or not values["selling_price"]:
                raise ValueError("name, category, batch number, expiry date, purchase price, and selling price are required")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", batch):
                raise ValueError("batch number contains unsupported characters")
            if barcode and not re.fullmatch(r"[A-Za-z0-9-]{3,100}", barcode):
                raise ValueError("barcode must contain 3–100 letters, digits or hyphens")
            if (len(name)>180 or (category and len(category)>100) or (manufacturer and len(manufacturer)>160) or
                (barcode and len(barcode)>100) or len(batch)>100 or
                any(unicodedata.category(ch).startswith("C") for value in (name,category,manufacturer or "") for ch in value)):
                raise ValueError("a text value exceeds the maximum allowed length")
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", values["expiry_date"]):
                raise ValueError("expiry date must use YYYY-MM-DD")
            expiry=datetime.date.fromisoformat(values["expiry_date"])
            if expiry<=datetime.date.today():
                raise ValueError("expiry date must be after today")

            def parse_amount(field,default,maximum):
                raw=values[field] or default
                if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", raw):
                    raise ValueError(f"{field.replace('_',' ')} must be a valid number with at most two decimal places")
                amount=decimal.Decimal(raw)
                if not amount.is_finite() or amount<0 or amount>maximum or amount!=amount.quantize(decimal.Decimal("0.01")):
                    raise ValueError(f"{field.replace('_',' ')} must be a valid non-negative value with at most two decimal places")
                if field in ("purchase_price", "selling_price") and amount == 0:
                    raise ValueError(f"{field.replace('_',' ')} must be greater than zero")
                return amount.quantize(decimal.Decimal("0.01"))

            purchase=parse_amount("purchase_price","0",decimal.Decimal("9999999999.99"))
            selling=parse_amount("selling_price","0",decimal.Decimal("9999999999.99"))
            gst=parse_amount("gst_percent","0",decimal.Decimal("100"))
            if selling<purchase:
                raise ValueError("selling price must be at least purchase price")
            if values["quantity"] and not re.fullmatch(r"[0-9]+", values["quantity"]):
                raise ValueError("quantity must be a non-negative whole number")
            if values["reorder_level"] and not re.fullmatch(r"[0-9]+", values["reorder_level"]):
                raise ValueError("reorder level must be a non-negative whole number")
            quantity=int(values["quantity"] or "0")
            reorder_level=int(values["reorder_level"] or "10")
            if not 0<=quantity<=4294967295 or not 0<=reorder_level<=4294967295:
                raise ValueError("quantity and reorder level must be non-negative whole numbers")
            identity=(name.casefold(),(category or "").casefold(),(manufacturer or "").casefold(),barcode or "")
            if barcode and barcode in barcode_identities and barcode_identities[barcode]!=identity:
                raise ValueError("barcode is assigned to different medicines in this CSV")
            if barcode: barcode_identities[barcode]=identity
            medicines.append({"identity":identity,"name":name,"category":category,"manufacturer":manufacturer,"barcode":barcode,
                "batch_number":batch,"expiry_date":expiry,"purchase_price":purchase,"selling_price":selling,
                "quantity":quantity,"reorder_level":reorder_level,"gst_percent":gst})
        except (ValueError,decimal.InvalidOperation) as exc:
            errors.append(f"Row {row_number}: {exc or 'invalid value'}.")
    if errors:
        return [],errors
    if not medicines:
        return [],["CSV contains no medicine rows."]
    return medicines,[]
@main_bp.route("/")
def index(): return redirect(url_for("main.dashboard" if session.get("user_id") else "auth.login"))
@main_bp.route("/dashboard")
def dashboard():
    x=guard("owner","admin","pharmacist","billing")
    if x:return x
    c=tenant_db(); q=c.cursor(dictionary=True)
    q.execute("SELECT COUNT(DISTINCT b.medicine_id) medicine_count,COALESCE(SUM(b.quantity),0) total_stock,COALESCE(SUM(b.quantity*b.purchase_price),0) inventory_cost,COALESCE(SUM(b.quantity*b.selling_price),0) inventory_retail FROM inventory_batches b JOIN medicines m ON m.id=b.medicine_id WHERE b.quantity>0 AND m.is_active=1"); stock=q.fetchone()
    q.execute("SELECT COALESCE(SUM(total_amount),0) revenue,COALESCE(SUM(amount_paid),0) collected,COALESCE(SUM(total_amount-amount_paid),0) outstanding,COALESCE(SUM(recorded_profit),0) profit,COUNT(*) bills FROM sales WHERE DATE(created_at)=CURDATE()"); today=q.fetchone()
    q.execute("SELECT COUNT(*) n FROM inventory_batches b JOIN medicines m ON m.id=b.medicine_id WHERE b.quantity<=b.reorder_level AND b.quantity>0 AND m.is_active=1"); low=q.fetchone()["n"]
    q.execute("SELECT COUNT(*) n FROM inventory_batches b JOIN medicines m ON m.id=b.medicine_id WHERE b.expiry_date<CURDATE() AND b.quantity>0 AND m.is_active=1"); expired=q.fetchone()["n"]
    q.execute("SELECT id,bill_number,customer_name,total_amount,amount_paid,payment_status,recorded_profit,created_at FROM sales ORDER BY id DESC LIMIT 8"); recent=q.fetchall(); q.close()
    return render_template("dashboard.html",stock=stock,today=today,low=low,expired=expired,recent=recent)
@main_bp.route("/medicines")
def medicines():
    x=guard("owner","admin","pharmacist","billing")
    if x:return x
    term=request.args.get("q","").strip()
    status=request.args.get("status","")
    status_filters={
        "low_stock":" AND b.quantity>0 AND b.quantity<=b.reorder_level",
        "expired":" AND b.quantity>0 AND b.expiry_date<CURDATE()",
    }
    filter_clause=status_filters.get(status,"")
    if status not in status_filters:
        status=""
    c=tenant_db(); q=c.cursor(dictionary=True)
    q.execute("SELECT m.id,m.name,m.manufacturer,m.barcode,c.name category,b.id batch_id,b.batch_number,b.expiry_date,b.purchase_price,b.selling_price,b.quantity,b.reorder_level FROM medicines m LEFT JOIN categories c ON c.id=m.category_id LEFT JOIN inventory_batches b ON b.medicine_id=m.id WHERE m.is_active=1"+filter_clause+" AND (%s='' OR m.name LIKE %s OR m.manufacturer LIKE %s OR m.barcode LIKE %s OR b.batch_number LIKE %s) ORDER BY m.name,m.id,b.expiry_date LIMIT 500",
        (term,"%"+term+"%","%"+term+"%","%"+term+"%","%"+term+"%"))
    rows=q.fetchall(); q.close()
    return render_template("medicines.html",rows=rows,q=term,status=status,today=datetime.date.today().isoformat())
@main_bp.route("/medicines/add",methods=["GET","POST"])
def add_medicine():
    x=guard("owner","admin","pharmacist")
    if x:return x
    if request.method=="POST":
        try:
            data=parse_medicine_form(request.form)
            name=data["name"]; category=data["category"]; manufacturer=data["manufacturer"]; barcode=data["barcode"]
            batch=data["batch_number"]; expiry=data["expiry_date"]; purchase=data["purchase_price"]; selling=data["selling_price"]
            qty=data["quantity"]; reorder=data["reorder_level"]; gst=data["gst_percent"]
        except ValueError as exc:
            flash(str(exc),"error")
            return render_template("medicine_form.html",medicine=request.form)
        c=tenant_db(); q=c.cursor(dictionary=True)
        try:
            catid=None
            if category:
                q.execute("INSERT INTO categories(name) VALUES(%s) ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)",(category,)); catid=q.lastrowid
            q.execute("INSERT INTO medicines(name,category_id,manufacturer,barcode) VALUES(%s,%s,%s,%s)",(name,catid,manufacturer,barcode)); mid=q.lastrowid
            q.execute("INSERT INTO inventory_batches(medicine_id,batch_number,expiry_date,purchase_price,selling_price,quantity,reorder_level,gst_percent) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",(mid,batch,expiry,purchase,selling,qty,reorder,gst))
            c.commit()
        except Exception:
            c.rollback(); current_app.logger.exception("Medicine batch creation failed")
            flash("Could not save. Check for a duplicate barcode or batch.","error")
            return render_template("medicine_form.html",medicine=request.form)
        finally:q.close()
        try:
            audit("medicine.created","medicine",mid,{"name":name,"batch":batch})
        except Exception:
            current_app.logger.exception("Could not record medicine creation audit event")
            flash("Medicine was saved, but its audit record could not be saved. Contact an administrator.","error")
            return redirect(url_for("main.medicines"))
        flash("Medicine batch saved.","success")
        return redirect(url_for("main.medicines"))
    return render_template("medicine_form.html",medicine={})

@main_bp.route("/medicines/restock",methods=["GET","POST"])
def restock_medicine():
    x=guard("owner","admin","pharmacist")
    if x:return x
    c=tenant_db()
    if request.method=="POST":
        try:
            batch_id=int(request.form.get("batch_id",""))
            quantity=int(request.form.get("quantity",""))
            if batch_id<1 or not 1<=quantity<=4294967295:
                raise ValueError()
        except ValueError:
            flash("Choose a medicine batch and enter a quantity greater than zero.","error")
            return redirect(url_for("main.restock_medicine"))
        q=c.cursor(dictionary=True)
        try:
            q.execute("SELECT m.id medicine_id,m.name,b.batch_number FROM inventory_batches b JOIN medicines m ON m.id=b.medicine_id WHERE b.id=%s AND m.is_active=1 AND b.expiry_date>CURDATE()",(batch_id,))
            batch=q.fetchone()
            if not batch:
                c.rollback()
                flash("Choose an active, non-expired medicine batch to restock.","error")
                return redirect(url_for("main.restock_medicine"))
            q.execute("UPDATE inventory_batches b JOIN medicines m ON m.id=b.medicine_id SET b.quantity=b.quantity+%s WHERE b.id=%s AND m.is_active=1 AND b.expiry_date>CURDATE() AND b.quantity<=4294967295-%s",
                (quantity,batch_id,quantity))
            if q.rowcount!=1:
                c.rollback()
                flash("Could not restock this batch. It may be expired, inactive, or at the maximum stock quantity.","error")
                return redirect(url_for("main.restock_medicine"))
            c.commit()
        except Exception:
            c.rollback()
            current_app.logger.exception("Medicine restock failed")
            flash("Could not restock this batch. Please try again.","error")
            return redirect(url_for("main.restock_medicine"))
        finally:
            q.close()
        try:
            audit("medicine.restocked","medicine",batch["medicine_id"],{"name":batch["name"],"batch":batch["batch_number"],"quantity_added":quantity})
        except Exception:
            current_app.logger.exception("Could not record medicine restock audit event")
            flash("Stock was updated, but its audit record could not be saved. Contact an administrator.","error")
            return redirect(url_for("main.medicines"))
        flash(f"Added {quantity} unit(s) to {batch['name']} (batch {batch['batch_number']}).","success")
        return redirect(url_for("main.medicines"))
    q=c.cursor(dictionary=True)
    q.execute("SELECT b.id batch_id,m.name medicine_name,b.batch_number,b.expiry_date,b.quantity FROM inventory_batches b JOIN medicines m ON m.id=b.medicine_id WHERE m.is_active=1 AND b.expiry_date>CURDATE() ORDER BY m.name,b.expiry_date,b.batch_number")
    batches=q.fetchall(); q.close()
    return render_template("medicine_restock.html",batches=batches)

@main_bp.route("/medicines/batches/<int:batch_id>/edit",methods=["GET","POST"])
def edit_medicine(batch_id):
    x=guard("owner")
    if x:return x
    c=tenant_db(); q=c.cursor(dictionary=True)
    q.execute("SELECT m.id medicine_id,m.name,m.category_id,c.name category,m.manufacturer,m.barcode,b.id batch_id,b.batch_number,b.expiry_date,b.purchase_price,b.selling_price,b.quantity,b.reorder_level,b.gst_percent FROM medicines m JOIN inventory_batches b ON b.medicine_id=m.id LEFT JOIN categories c ON c.id=m.category_id WHERE b.id=%s AND m.is_active=1",(batch_id,))
    medicine=q.fetchone()
    if not medicine:
        q.close()
        abort(404)
    if request.method=="POST":
        form=request.form
        try:
            data=parse_medicine_form(form)
            name=data["name"]; category=data["category"]; manufacturer=data["manufacturer"]; barcode=data["barcode"]
            batch=data["batch_number"]; expiry=data["expiry_date"]; purchase=data["purchase_price"]; selling=data["selling_price"]
            quantity=data["quantity"]; reorder_level=data["reorder_level"]; gst=data["gst_percent"]
        except ValueError as exc:
            flash(str(exc),"error")
            q.close()
            return render_template("medicine_form.html",medicine=form,editing=True)
        try:
            category_id=None
            if category:
                q.execute("INSERT INTO categories(name) VALUES(%s) ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)",(category,))
                category_id=q.lastrowid
            q.execute("UPDATE medicines SET name=%s,category_id=%s,manufacturer=%s,barcode=%s WHERE id=%s",
                (name,category_id,manufacturer,barcode,medicine["medicine_id"]))
            q.execute("UPDATE inventory_batches SET batch_number=%s,expiry_date=%s,purchase_price=%s,selling_price=%s,quantity=%s,reorder_level=%s,gst_percent=%s WHERE id=%s AND medicine_id=%s",
                (batch,expiry,purchase,selling,quantity,reorder_level,gst,batch_id,medicine["medicine_id"]))
            c.commit()
        except Exception:
            c.rollback()
            current_app.logger.exception("Medicine batch update failed")
            flash("Could not update medicine. Check for a duplicate barcode or batch.","error")
            return render_template("medicine_form.html",medicine=form,editing=True)
        finally:
            q.close()
        try:
            audit("medicine.updated","medicine",medicine["medicine_id"],{"name":name,"batch":batch,"batch_id":batch_id})
        except Exception:
            current_app.logger.exception("Could not record medicine update audit event")
            flash("Medicine was updated, but its audit record could not be saved. Contact an administrator.","error")
            return redirect(url_for("main.medicines"))
        flash("Medicine batch updated.","success")
        return redirect(url_for("main.medicines"))
    q.close()
    return render_template("medicine_form.html",medicine=medicine,editing=True)

@main_bp.route("/medicines/<int:medicine_id>/delete",methods=["POST"])
def delete_medicine(medicine_id):
    x=guard("owner")
    if x:return x
    c=tenant_db(); q=c.cursor(dictionary=True)
    try:
        q.execute("UPDATE medicines SET is_active=0 WHERE id=%s AND is_active=1",(medicine_id,))
        deleted=q.rowcount==1
        if deleted:
            c.commit()
        else:
            c.rollback()
    except Exception:
        c.rollback()
        current_app.logger.exception("Medicine archive failed")
        flash("Could not delete medicine. Please try again.","error")
        return redirect(url_for("main.medicines"))
    finally:
        q.close()
    if not deleted:
        abort(404)
    try:
        audit("medicine.deleted","medicine",medicine_id,{})
    except Exception:
        current_app.logger.exception("Could not record medicine deletion audit event")
        flash("Medicine was removed from active inventory, but its audit record could not be saved. Contact an administrator.","error")
        return redirect(url_for("main.medicines"))
    flash("Medicine deleted from active inventory. Historical records were preserved.","success")
    return redirect(url_for("main.medicines"))

@main_bp.route("/medicines/template.csv")
def medicine_csv_template():
    x=guard("owner","admin","pharmacist")
    if x:return x
    response=make_response(",".join(MEDICINE_CSV_FIELDS)+"\r\n")
    response.headers["Content-Type"]="text/csv; charset=utf-8"
    response.headers["Content-Disposition"]='attachment; filename="medicine_import_template.csv"'
    return response

@main_bp.route("/medicines/import",methods=["POST"])
def import_medicines():
    x=guard("owner","admin","pharmacist")
    if x:return x
    uploaded=request.files.get("file")
    if not uploaded or not uploaded.filename:
        flash("Choose a CSV file to import.","error")
        return redirect(url_for("main.medicines"))
    try:
        content=uploaded.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("The CSV file must use UTF-8 encoding.","error")
        return redirect(url_for("main.medicines"))
    medicines,errors=parse_medicine_csv(content)
    if errors:
        flash("CSV import was not applied. "+"; ".join(errors[:5]),"error")
        return redirect(url_for("main.medicines"))

    c=tenant_db(); q=c.cursor()
    try:
        c.start_transaction()
        medicine_ids={}
        for medicine in medicines:
            medicine_id=medicine_ids.get(medicine["identity"])
            if medicine_id is None:
                category_id=None
                if medicine["category"]:
                    q.execute("INSERT INTO categories(name) VALUES(%s) ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)",(medicine["category"],))
                    category_id=q.lastrowid
                q.execute("INSERT INTO medicines(name,category_id,manufacturer,barcode) VALUES(%s,%s,%s,%s)",
                    (medicine["name"],category_id,medicine["manufacturer"],medicine["barcode"]))
                medicine_id=q.lastrowid
                medicine_ids[medicine["identity"]]=medicine_id
            q.execute("INSERT INTO inventory_batches(medicine_id,batch_number,expiry_date,purchase_price,selling_price,quantity,reorder_level,gst_percent) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (medicine_id,medicine["batch_number"],medicine["expiry_date"],medicine["purchase_price"],medicine["selling_price"],medicine["quantity"],medicine["reorder_level"],medicine["gst_percent"]))
        c.commit()
    except Exception:
        c.rollback()
        current_app.logger.exception("Medicine CSV import failed")
        flash("CSV import failed; no rows were imported. Check for duplicate barcodes or medicine batches.","error")
        return redirect(url_for("main.medicines"))
    finally:
        q.close()
    try:
        audit("medicine.csv_imported","medicine",None,{"count":len(medicines)})
    except Exception:
        current_app.logger.exception("Could not record medicine CSV import audit event")
    flash(f"Imported {len(medicines)} medicine batch(es).","success")
    return redirect(url_for("main.medicines"))
@main_bp.route("/api/customers/lookup")
def customer_lookup():
    x=guard("owner","admin","pharmacist","billing")
    if x:return x
    try:
        phone=normalize_phone(request.args.get("phone", ""))
    except ValueError as exc:
        return jsonify({"error":str(exc)}),400
    if not phone:
        return jsonify({"found":False,"previous_bills":[],"has_unpaid":False})
    c=tenant_db(); q=c.cursor(dictionary=True)
    try:
        q.execute("SELECT id,full_name FROM customers WHERE REGEXP_REPLACE(COALESCE(phone,''),'[^0-9]','')=%s ORDER BY id DESC",(phone,))
        customers=q.fetchall()
        if not customers:
            return jsonify({"found":False,"previous_bills":[],"has_unpaid":False})
        ids=[row["id"] for row in customers]
        marks=",".join(["%s"]*len(ids))
        q.execute("SELECT id,bill_number,total_amount,amount_paid,payment_status,created_at FROM sales WHERE customer_id IN ("+marks+") ORDER BY id DESC LIMIT 12",tuple(ids))
        bills=q.fetchall()
        # Always surface every outstanding invoice, even if it is older than the 12-item history window.
        q.execute("SELECT id,bill_number,total_amount,amount_paid,payment_status,created_at FROM sales WHERE customer_id IN ("+marks+") AND total_amount>amount_paid ORDER BY id DESC",tuple(ids))
        outstanding=q.fetchall()
        seen={row["id"] for row in bills}
        bills.extend(row for row in outstanding if row["id"] not in seen)
        bills.sort(key=lambda row:row["id"],reverse=True)
        previous=[]; has_unpaid=False
        for bill in bills:
            due=money(bill["total_amount"])-money(bill["amount_paid"])
            status=bill["payment_status"]
            if due>0:
                has_unpaid=True
                status="partial" if money(bill["amount_paid"])>0 else "unpaid"
            previous.append({"id":bill["id"],"bill_number":bill["bill_number"],
                "total":str(money(bill["total_amount"])),"paid":str(money(bill["amount_paid"])),
                "due":str(due),"payment_status":status,"created_at":str(bill["created_at"])})
        return jsonify({"found":True,"customer_name":customers[0]["full_name"],
            "previous_bills":previous,"has_unpaid":has_unpaid})
    finally:
        q.close()

@main_bp.route("/billing",methods=["GET","POST"])
def billing():
    x=guard("owner","admin","pharmacist","billing")
    if x:return x
    form_data={"customer_name":"Walk-in customer","customer_phone":"","discount":"0"}
    if request.method=="POST":
        form_data=request.form
        c=None; q=None; lock_name=None; lock_acquired=False
        try:
            customer=request.form.get("customer_name","Walk-in customer").strip()
            customer=customer or "Walk-in customer"
            if not valid_person_name(customer) and customer.casefold()!="walk-in customer":
                raise ValueError("Enter a valid customer name without digits or unsupported symbols.")
            phone=normalize_phone(request.form.get("customer_phone",""))
            if customer.casefold()!="walk-in customer" and not phone:
                raise ValueError("Enter the customer's phone number so previous bills and unpaid dues can be checked.")
            discount=parse_money_field(request.form.get("discount","0"),field_name="Discount")
            raw=json.loads(request.form.get("items_json","[]"))
            if not isinstance(raw,list) or not raw or len(raw)>100:
                raise ValueError("Add at least one medicine before creating the bill.")
            items={}
            for item in raw:
                if not isinstance(item,dict): raise ValueError("Invalid bill item.")
                bid=int(item.get("batch_id",0)); qty=int(item.get("quantity",0))
                if bid<1 or qty<1 or qty>10000: raise ValueError("Invalid medicine or quantity.")
                items[bid]=items.get(bid,0)+qty
                if items[bid]>10000: raise ValueError("A medicine quantity cannot exceed 10,000 units.")

            c=tenant_db(); q=c.cursor(dictionary=True); c.start_transaction()
            custid=None
            if phone:
                lock_name="mlcust_"+hashlib.sha256(phone.encode("ascii")).hexdigest()[:56]
                q.execute("SELECT GET_LOCK(%s,5) acquired",(lock_name,))
                lock_acquired=(q.fetchone() or {}).get("acquired")==1
                if not lock_acquired:
                    raise ValueError("Customer account is busy with another checkout. Please retry.")
                q.execute("SELECT id,full_name FROM customers WHERE REGEXP_REPLACE(COALESCE(phone,''),'[^0-9]','')=%s ORDER BY id DESC FOR UPDATE",(phone,))
                existing=q.fetchall()
                if existing:
                    customer_ids=[row["id"] for row in existing]
                    marks=",".join(["%s"]*len(customer_ids))
                    q.execute("SELECT id,bill_number,total_amount,amount_paid,payment_status FROM sales WHERE customer_id IN ("+marks+") AND total_amount>amount_paid ORDER BY id FOR UPDATE",tuple(customer_ids))
                    unpaid=q.fetchall()
                    if unpaid:
                        c.rollback()
                        flash(f"Settle previous bill {unpaid[0]['bill_number']} before creating another bill.","error")
                        return redirect(url_for("main.bill_detail",sale_id=unpaid[0]["id"]))
                    custid=existing[0]["id"]
                else:
                    q.execute("INSERT INTO customers(full_name,phone) VALUES(%s,%s)",(customer,phone))
                    custid=q.lastrowid

            ids=sorted(items); marks=",".join(["%s"]*len(ids))
            q.execute("SELECT b.*,m.name,m.prescription_required FROM inventory_batches b JOIN medicines m ON m.id=b.medicine_id WHERE b.id IN ("+marks+") AND m.is_active=1 ORDER BY b.id FOR UPDATE",tuple(ids))
            batches={b["id"]:b for b in q.fetchall()}
            if len(batches)!=len(items): raise ValueError("A selected medicine batch was not found or is no longer active.")
            subtotal=decimal.Decimal("0.00"); tax=decimal.Decimal("0.00"); profit=decimal.Decimal("0.00"); lines=[]
            for bid,qty in items.items():
                b=batches[bid]
                if b["quantity"]<qty: raise ValueError("Insufficient stock for "+b["name"])
                if b["expiry_date"]<=datetime.date.today(): raise ValueError("Expired medicine cannot be sold: "+b["name"])
                if b["prescription_required"]: raise ValueError("This medicine requires a pharmacist-approved prescription workflow; it is blocked until that workflow is implemented.")
                unit=money(b["selling_price"]); cost=money(b["purchase_price"]); base=unit*qty
                t=(base*money(b["gst_percent"])/decimal.Decimal("100")).quantize(decimal.Decimal("0.01"))
                subtotal+=base; tax+=t; profit+=(unit-cost)*qty
                lines.append((bid,qty,unit,cost,t,base+t,b["name"],b["batch_number"]))
            if discount>subtotal+tax: raise ValueError("Discount cannot exceed bill total.")
            total=subtotal+tax-discount
            bill="PH-"+datetime.datetime.now().strftime("%Y%m%d%H%M%S")+"-"+secrets.token_hex(3).upper()
            q.execute("INSERT INTO sales(bill_number,customer_id,customer_name,subtotal,discount_amount,tax_amount,total_amount,amount_paid,payment_status,recorded_profit,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,0,'unpaid',%s,%s)",
                (bill,custid,customer,subtotal,discount,tax,total,profit-discount,session["user_id"]))
            sid=q.lastrowid
            for bid,qty,unit,cost,t,line_total,mname,batch in lines:
                q.execute("UPDATE inventory_batches SET quantity=quantity-%s WHERE id=%s AND quantity>=%s",(qty,bid,qty))
                if q.rowcount!=1: raise ValueError("Stock changed while billing. Please retry.")
                q.execute("INSERT INTO sale_items(sale_id,batch_id,medicine_name,batch_number,quantity,unit_price,unit_cost,tax_amount,line_total,line_profit) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (sid,bid,mname,batch,qty,unit,cost,t,line_total,(unit-cost)*qty))
            q.execute("INSERT INTO audit_logs(user_id,event_type,entity_type,entity_id,detail_json,ip_address) VALUES(%s,'sale.created','sale',%s,%s,%s)",
                (session["user_id"],str(sid),json.dumps({"bill_number":bill,"lines":len(lines),"payment_status":"unpaid"}),request.remote_addr))
            c.commit()
            return redirect(url_for("main.bill_detail",sale_id=sid))
        except Exception as e:
            if c:
                try:c.rollback()
                except Exception:pass
            current_app.logger.info("Billing request rejected or failed: %s",e if isinstance(e,ValueError) else type(e).__name__)
            flash(str(e) if isinstance(e,ValueError) else "Could not complete bill. The transaction was rolled back.","error")
        finally:
            if q:
                if lock_acquired and lock_name:
                    try:
                        q.execute("SELECT RELEASE_LOCK(%s)",(lock_name,)); q.fetchone()
                    except Exception: current_app.logger.exception("Could not release customer checkout lock")
                try:q.close()
                except Exception:pass
    c=tenant_db(); q=c.cursor(dictionary=True)
    q.execute("SELECT b.id batch_id,m.name,b.batch_number,b.expiry_date,b.selling_price,b.gst_percent,b.quantity FROM inventory_batches b JOIN medicines m ON m.id=b.medicine_id WHERE b.quantity>0 AND b.expiry_date>CURDATE() AND m.is_active=1 ORDER BY m.name LIMIT 1000")
    batches=q.fetchall(); q.close()
    return render_template("billing.html",batches=batches,form_data=form_data)

@main_bp.route("/bills/<int:sale_id>/payments",methods=["POST"])
def record_payment(sale_id):
    x=guard("owner","admin","pharmacist","billing")
    if x:return x
    c=tenant_db(); q=c.cursor(dictionary=True)
    try:
        amount=parse_money_field(request.form.get("payment_amount",""),allow_zero=False,field_name="Payment amount")
        method=request.form.get("payment_method","").strip().lower()
        if method not in PAYMENT_METHODS: raise ValueError("Choose a valid payment method.")
        token=request.form.get("payment_token","").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}",token): raise ValueError("Invalid payment submission token. Refresh the bill and try again.")
        reference=request.form.get("reference_note","").strip()
        if len(reference)>120: raise ValueError("Payment reference cannot exceed 120 characters.")
        c.start_transaction()
        q.execute("SELECT id,bill_number,total_amount,amount_paid,payment_status FROM sales WHERE id=%s FOR UPDATE",(sale_id,))
        sale=q.fetchone()
        if not sale: abort(404)
        due=money(sale["total_amount"])-money(sale["amount_paid"])
        if due<=0 or sale["payment_status"]=="paid": raise ValueError("This bill is already fully paid.")
        if amount>due: raise ValueError(f"Payment cannot exceed the outstanding balance of ₹{due:.2f}.")
        q.execute("INSERT INTO bill_payments(sale_id,payment_token,amount,payment_method,reference_note,received_by) VALUES(%s,%s,%s,%s,%s,%s)",
            (sale_id,token,amount,method,reference or None,session["user_id"]))
        new_paid=money(sale["amount_paid"])+amount
        new_status="paid" if new_paid>=money(sale["total_amount"]) else "partial"
        q.execute("UPDATE sales SET amount_paid=%s,payment_status=%s,payment_method=%s,paid_at=%s WHERE id=%s",
            (new_paid,new_status,method,datetime.datetime.now() if new_status=="paid" else None,sale_id))
        q.execute("INSERT INTO audit_logs(user_id,event_type,entity_type,entity_id,detail_json,ip_address) VALUES(%s,'sale.payment_received','sale',%s,%s,%s)",
            (session["user_id"],str(sale_id),json.dumps({"bill_number":sale["bill_number"],"amount":str(amount),"method":method,"status":new_status}),request.remote_addr))
        c.commit()
        flash("Payment recorded. Bill is now "+new_status+".","success")
    except ValueError as exc:
        c.rollback(); flash(str(exc),"error")
    except HTTPException:
        c.rollback(); raise
    except Exception:
        c.rollback(); current_app.logger.exception("Payment recording failed")
        flash("Payment could not be recorded. No payment changes were committed.","error")
    finally:
        q.close()
    return redirect(url_for("main.bill_detail",sale_id=sale_id))

@main_bp.route("/bills/<int:sale_id>")
def bill_detail(sale_id):
    x=guard("owner","admin","pharmacist","billing")
    if x:return x
    c=tenant_db(); q=c.cursor(dictionary=True); q.execute("SELECT * FROM sales WHERE id=%s",(sale_id,)); sale=q.fetchone()
    if not sale:
        q.close(); abort(404)
    q.execute("SELECT * FROM sale_items WHERE sale_id=%s ORDER BY id",(sale_id,)); items=q.fetchall()
    q.execute("SELECT amount,payment_method,reference_note,created_at FROM bill_payments WHERE sale_id=%s ORDER BY id DESC",(sale_id,)); payments=q.fetchall(); q.close()
    due=money(sale["total_amount"])-money(sale["amount_paid"])
    return render_template("bill.html",sale=sale,items=items,payments=payments,due=due,payment_token=secrets.token_urlsafe(24))
@main_bp.route("/bills/<int:sale_id>/json")
def bill_json(sale_id):
    x=guard("owner","admin","pharmacist","billing")
    if x:return x
    c=tenant_db(); q=c.cursor(dictionary=True); q.execute("SELECT * FROM sales WHERE id=%s",(sale_id,)); sale=q.fetchone()
    if not sale:abort(404)
    q.execute("SELECT medicine_name,batch_number,quantity,unit_price,unit_cost,tax_amount,line_total FROM sale_items WHERE sale_id=%s",(sale_id,)); items=q.fetchall(); q.close()
    for row in [sale]+items:
        for k,v in list(row.items()):
            if isinstance(v,(decimal.Decimal,datetime.date,datetime.datetime)):row[k]=str(v)
    r=make_response(json.dumps({"bill":sale,"items":items},indent=2)); r.headers["Content-Type"]="application/json"; r.headers["Content-Disposition"]=f'attachment; filename="{sale["bill_number"]}.json'; return r
@main_bp.route("/sales")
def sales():
    x=guard("owner","admin","pharmacist","billing")
    if x:return x
    search_phone=request.args.get("phone","")
    search_error=None
    phone=None
    if search_phone:
        try:
            phone=normalize_phone(search_phone)
        except ValueError as exc:
            search_error=str(exc)
    rows=[]
    if not search_error:
        c=tenant_db(); q=c.cursor(dictionary=True)
        query="SELECT s.id,s.bill_number,s.customer_name,c.phone customer_phone,s.subtotal,s.tax_amount,s.discount_amount,s.total_amount,s.amount_paid,s.payment_status,s.recorded_profit,s.created_at FROM sales s LEFT JOIN customers c ON c.id=s.customer_id"
        params=()
        if phone:
            query+=" WHERE REGEXP_REPLACE(COALESCE(c.phone,''),'[^0-9]','')=%s"
            params=(phone,)
        q.execute(query+" ORDER BY s.id DESC LIMIT 500",params)
        rows=q.fetchall(); q.close()
    return render_template("sales.html",rows=rows,search_phone=search_phone,search_error=search_error)
@main_bp.route("/suppliers",methods=["GET","POST"])
def suppliers():
    x=guard("owner","admin","pharmacist")
    if x:return x
    c=tenant_db()
    if request.method=="POST":
        name=request.form.get("name","").strip()
        contact_name=request.form.get("contact_name","").strip()
        email=request.form.get("email","").strip()
        phone=request.form.get("phone","")
        errors=[]
        if not name: errors.append("Supplier name must be provided.")
        if contact_name and not valid_contact_name(contact_name): errors.append("Contact person must contain only letters and spaces.")
        if email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email): errors.append("Enter a valid supplier email.")
        if phone:
            try: normalize_phone(phone)
            except ValueError: errors.append("Enter a valid supplier phone number with exactly 10 digits.")
        if errors:
            for error in errors: flash(error,"error")
        else:
            q=c.cursor()
            q.execute("INSERT INTO suppliers(name,contact_name,email,phone,address) VALUES(%s,%s,%s,%s,%s)",
                (name,contact_name or None,email or None,phone or None,request.form.get("address","").strip() or None))
            c.commit(); q.close(); flash("Supplier saved.","success")
    q=c.cursor(dictionary=True); q.execute("SELECT * FROM suppliers ORDER BY name"); rows=q.fetchall(); q.close(); return render_template("suppliers.html",rows=rows)
@main_bp.route("/reports")
def reports():
    x=guard("owner","admin")
    if x:return x
    c=tenant_db(); q=c.cursor(dictionary=True)
    q.execute("SELECT DATE(created_at) day,COUNT(*) bills,SUM(total_amount) revenue,SUM(recorded_profit) profit FROM sales WHERE created_at>=DATE_SUB(CURDATE(),INTERVAL 30 DAY) GROUP BY DATE(created_at) ORDER BY day DESC"); days=q.fetchall()
    q.execute("SELECT COALESCE(SUM(b.quantity*b.purchase_price),0) cost_value,COALESCE(SUM(b.quantity*b.selling_price),0) retail_value FROM inventory_batches b JOIN medicines m ON m.id=b.medicine_id WHERE b.quantity>0 AND m.is_active=1"); inventory=q.fetchone(); q.close()
    return render_template("reports.html",days=days,inventory=inventory)

@main_bp.route("/reports/export.csv")
def export_report_csv():
    x=guard("owner","admin")
    if x:return x
    c=tenant_db(); q=c.cursor()
    q.execute("SELECT DATE(created_at),COUNT(*),SUM(total_amount),SUM(recorded_profit) FROM sales WHERE created_at>=DATE_SUB(CURDATE(),INTERVAL 30 DAY) GROUP BY DATE(created_at) ORDER BY DATE(created_at) DESC")
    days=q.fetchall()
    q.execute("SELECT COALESCE(SUM(b.quantity*b.purchase_price),0),COALESCE(SUM(b.quantity*b.selling_price),0) FROM inventory_batches b JOIN medicines m ON m.id=b.medicine_id WHERE b.quantity>0 AND m.is_active=1")
    inventory=q.fetchone()
    output=io.StringIO(newline="")
    writer=csv.writer(output)
    writer.writerow(("Report type","Date","Bills","Revenue (INR)","Profit estimate (INR)","Inventory cost value (INR)","Inventory retail value (INR)"))
    writer.writerow(("Inventory valuation","","","","",inventory[0],inventory[1]))
    writer.writerows(("Daily sales",*day,"","") for day in days)
    q.close()
    response=make_response("\ufeff"+output.getvalue())
    response.headers["Content-Type"]="text/csv; charset=utf-8"
    response.headers["Content-Disposition"]='attachment; filename="pharmacy_report.csv"'
    return response
@main_bp.route("/staff",methods=["GET","POST"])
def staff():
    x=guard("owner","admin")
    if x:return x
    c=tenant_db()
    if request.method=="POST":
        name=request.form.get("full_name","").strip(); email=request.form.get("email","").strip().lower(); password=request.form.get("password",""); role=request.form.get("role","billing")
        if (not name or len(name)>160 or any(unicodedata.category(ch).startswith("C") for ch in name)
                or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email)
                or len(password)<12 or role not in ("admin","pharmacist","billing")):
            flash("Enter a valid name, email, role, and password of at least 12 characters.","error")
        else:
            from werkzeug.security import generate_password_hash
            q=c.cursor()
            try:
                q.execute("INSERT INTO users(full_name,email,password_hash,role) VALUES(%s,%s,%s,%s)",(name,email,generate_password_hash(password),role))
                c.commit()
                flash("Staff account created. To sign in, enter this staff email and the pharmacy owner's registered email.","success")
            except Exception:
                c.rollback()
                current_app.logger.exception("Could not create staff account")
                flash("Could not create staff account; check whether the email is already in use.","error")
            q.close()
    q=c.cursor(dictionary=True); q.execute("SELECT id,full_name,email,role,is_active,created_at FROM users ORDER BY id"); rows=q.fetchall(); q.close(); return render_template("staff.html",rows=rows,current_user_id=session.get("user_id"))

@main_bp.route("/staff/<int:user_id>/edit",methods=["POST"])
def edit_staff(user_id):
    x=guard("owner","admin")
    if x:return x
    if user_id==session.get("user_id"):
        flash("You cannot edit or deactivate your own account here.","error")
        return redirect(url_for("main.staff"))
    name=request.form.get("full_name","").strip()
    email=request.form.get("email","").strip().lower()
    role=request.form.get("role","")
    is_active=request.form.get("is_active","")
    password=request.form.get("password","")
    if (not name or len(name)>160 or any(unicodedata.category(ch).startswith("C") for ch in name)
            or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+",email)
            or role not in ("admin","pharmacist","billing") or is_active not in ("0","1")
            or (password and len(password)<12)):
        flash("Enter a valid name, email, role, and account status. A new password must be at least 12 characters.","error")
        return redirect(url_for("main.staff"))
    c=tenant_db(); q=c.cursor(dictionary=True)
    try:
        q.execute("SELECT id,role FROM users WHERE id=%s",(user_id,))
        target=q.fetchone()
        if not target or target["role"]=="owner":
            flash("Owner accounts cannot be edited here.","error")
            return redirect(url_for("main.staff"))
        if password:
            from werkzeug.security import generate_password_hash
            q.execute("UPDATE users SET full_name=%s,email=%s,role=%s,is_active=%s,password_hash=%s WHERE id=%s",
                      (name,email,role,is_active=="1",generate_password_hash(password),user_id))
        else:
            q.execute("UPDATE users SET full_name=%s,email=%s,role=%s,is_active=%s WHERE id=%s",
                      (name,email,role,is_active=="1",user_id))
        c.commit()
        flash("Staff account and password updated." if password else "Staff account updated.","success")
    except Exception:
        c.rollback()
        current_app.logger.exception("Could not update staff account")
        flash("Could not update staff account; check whether the email is already in use.","error")
    finally:
        q.close()
    return redirect(url_for("main.staff"))
@main_bp.route("/health")
def health():return {"status":"ok"},200
