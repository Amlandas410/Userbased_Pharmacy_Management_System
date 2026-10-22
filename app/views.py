import csv, datetime, decimal, io, json, secrets
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort, current_app, make_response
from .db import tenant_db, audit
main_bp=Blueprint("main",__name__)
MEDICINE_CSV_FIELDS=("name","category","manufacturer","barcode","batch_number","expiry_date","purchase_price","selling_price","quantity","reorder_level","gst_percent")

def guard(*allowed):
    if not session.get("user_id"): return redirect(url_for("auth.login"))
    if session.get("role") not in allowed: abort(403)
    return None
def money(v): return decimal.Decimal(str(v or "0")).quantize(decimal.Decimal("0.01"))

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
            if not name or not batch or not values["expiry_date"] or not values["purchase_price"] or not values["selling_price"]:
                raise ValueError("name, batch number, expiry date, purchase price, and selling price are required")
            if len(name)>180 or (category and len(category)>100) or (manufacturer and len(manufacturer)>160) or (barcode and len(barcode)>100) or len(batch)>100:
                raise ValueError("a text value exceeds the maximum allowed length")
            expiry=datetime.date.fromisoformat(values["expiry_date"])
            if expiry<=datetime.date.today():
                raise ValueError("expiry date must be after today")

            def parse_amount(field,default,maximum):
                raw=values[field] or default
                amount=decimal.Decimal(raw)
                if not amount.is_finite() or amount<0 or amount>maximum or amount!=amount.quantize(decimal.Decimal("0.01")):
                    raise ValueError(f"{field.replace('_',' ')} must be a valid non-negative value with at most two decimal places")
                return amount.quantize(decimal.Decimal("0.01"))

            purchase=parse_amount("purchase_price","0",decimal.Decimal("9999999999.99"))
            selling=parse_amount("selling_price","0",decimal.Decimal("9999999999.99"))
            gst=parse_amount("gst_percent","0",decimal.Decimal("100"))
            if selling<purchase:
                raise ValueError("selling price must be at least purchase price")
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
    q.execute("SELECT COALESCE(SUM(total_amount),0) revenue,COALESCE(SUM(recorded_profit),0) profit,COUNT(*) bills FROM sales WHERE DATE(created_at)=CURDATE()"); today=q.fetchone()
    q.execute("SELECT COUNT(*) n FROM inventory_batches b JOIN medicines m ON m.id=b.medicine_id WHERE b.quantity<=b.reorder_level AND b.quantity>0 AND m.is_active=1"); low=q.fetchone()["n"]
    q.execute("SELECT COUNT(*) n FROM inventory_batches b JOIN medicines m ON m.id=b.medicine_id WHERE b.expiry_date<CURDATE() AND b.quantity>0 AND m.is_active=1"); expired=q.fetchone()["n"]
    q.execute("SELECT id,bill_number,customer_name,total_amount,recorded_profit,created_at FROM sales ORDER BY id DESC LIMIT 8"); recent=q.fetchall(); q.close()
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
            name=request.form.get("name","").strip(); category=request.form.get("category","").strip()
            manufacturer=request.form.get("manufacturer","").strip() or None; barcode=request.form.get("barcode","").strip() or None
            batch=request.form.get("batch_number","").strip(); expiry=datetime.date.fromisoformat(request.form.get("expiry_date",""))
            purchase=money(request.form.get("purchase_price")); selling=money(request.form.get("selling_price"))
            qty=int(request.form.get("quantity","0")); reorder=int(request.form.get("reorder_level","10")); gst=money(request.form.get("gst_percent","0"))
            if not name or not batch or expiry<=datetime.date.today() or purchase<0 or selling<purchase or qty<0 or reorder<0 or gst<0 or gst>100: raise ValueError()
        except Exception:
            flash("Check required fields. Expiry must be after today, selling price must be at least purchase price, and numeric values must be valid.","error")
            return render_template("medicine_form.html",medicine={})
        c=tenant_db(); q=c.cursor(dictionary=True)
        try:
            catid=None
            if category:
                q.execute("INSERT INTO categories(name) VALUES(%s) ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)",(category,)); catid=q.lastrowid
            q.execute("INSERT INTO medicines(name,category_id,manufacturer,barcode) VALUES(%s,%s,%s,%s)",(name,catid,manufacturer,barcode)); mid=q.lastrowid
            q.execute("INSERT INTO inventory_batches(medicine_id,batch_number,expiry_date,purchase_price,selling_price,quantity,reorder_level,gst_percent) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",(mid,batch,expiry,purchase,selling,qty,reorder,gst))
            c.commit(); audit("medicine.created","medicine",mid,{"name":name,"batch":batch}); flash("Medicine batch saved.","success"); return redirect(url_for("main.medicines"))
        except Exception:
            c.rollback(); flash("Could not save. Check for duplicate barcode or batch.","error")
        finally:q.close()
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
            name=form.get("name","").strip()
            category=form.get("category","").strip()
            manufacturer=form.get("manufacturer","").strip() or None
            barcode=form.get("barcode","").strip() or None
            batch=form.get("batch_number","").strip()
            expiry=datetime.date.fromisoformat(form.get("expiry_date",""))
            purchase_raw=form.get("purchase_price","").strip()
            selling_raw=form.get("selling_price","").strip()
            if not purchase_raw or not selling_raw:
                raise ValueError()
            purchase=money(purchase_raw)
            selling=money(selling_raw)
            quantity=int(form.get("quantity",""))
            reorder_level=int(form.get("reorder_level","10"))
            gst=money(form.get("gst_percent","0"))
            if (not name or len(name)>180 or len(category)>100 or
                (manufacturer and len(manufacturer)>160) or (barcode and len(barcode)>100) or
                not batch or len(batch)>100 or expiry<=datetime.date.today() or
                purchase<0 or purchase>decimal.Decimal("9999999999.99") or
                selling<purchase or selling>decimal.Decimal("9999999999.99") or
                not 0<=quantity<=4294967295 or not 0<=reorder_level<=4294967295 or
                gst<0 or gst>100):
                raise ValueError()
        except (ValueError, decimal.InvalidOperation):
            flash("Check required fields. Expiry must be after today, selling price must be at least purchase price, and numeric values must be valid.","error")
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
@main_bp.route("/billing",methods=["GET","POST"])
def billing():
    x=guard("owner","admin","pharmacist","billing")
    if x:return x
    if request.method=="POST":
        c=None; q=None
        try:
            customer=request.form.get("customer_name","Walk-in customer").strip()[:160] or "Walk-in customer"
            phone=request.form.get("customer_phone","").strip()[:32]; discount=money(request.form.get("discount","0"))
            raw=json.loads(request.form.get("items_json","[]"))
            if not isinstance(raw,list) or not raw or len(raw)>100 or discount<0: raise ValueError("Add items and enter a valid discount.")
            items={}
            for item in raw:
                bid=int(item["batch_id"]); qty=int(item["quantity"])
                if qty<1 or qty>10000: raise ValueError("Invalid quantity.")
                items[bid]=items.get(bid,0)+qty
            c=tenant_db(); q=c.cursor(dictionary=True); c.start_transaction()
            ids=sorted(items); marks=",".join(["%s"]*len(ids))
            q.execute("SELECT b.*,m.name,m.prescription_required FROM inventory_batches b JOIN medicines m ON m.id=b.medicine_id WHERE b.id IN ("+marks+") ORDER BY b.id FOR UPDATE",tuple(ids))
            batches={b["id"]:b for b in q.fetchall()}
            if len(batches)!=len(items): raise ValueError("A selected batch was not found.")
            subtotal=decimal.Decimal("0.00"); tax=decimal.Decimal("0.00"); profit=decimal.Decimal("0.00"); lines=[]
            for bid,qty in items.items():
                b=batches[bid]
                if b["quantity"]<qty: raise ValueError("Insufficient stock for "+b["name"])
                if b["expiry_date"]<=datetime.date.today(): raise ValueError("Expired medicine cannot be sold: "+b["name"])
                if b["prescription_required"]: raise ValueError("This medicine requires a pharmacist-approved prescription workflow; it is blocked in this starter.")
                unit=money(b["selling_price"]); cost=money(b["purchase_price"]); base=unit*qty
                t=(base*money(b["gst_percent"])/decimal.Decimal("100")).quantize(decimal.Decimal("0.01"))
                subtotal+=base; tax+=t; profit+=(unit-cost)*qty
                lines.append((bid,qty,unit,cost,t,base+t,b["name"],b["batch_number"]))
            if discount>subtotal+tax: raise ValueError("Discount cannot exceed bill total.")
            total=subtotal+tax-discount
            custid=None
            if phone:
                q.execute("SELECT id FROM customers WHERE phone=%s LIMIT 1",(phone,)); found=q.fetchone()
                if found:custid=found["id"]
                else:q.execute("INSERT INTO customers(full_name,phone) VALUES(%s,%s)",(customer,phone)); custid=q.lastrowid
            bill="PH-"+datetime.datetime.now().strftime("%Y%m%d%H%M%S")+"-"+secrets.token_hex(3).upper()
            q.execute("INSERT INTO sales(bill_number,customer_id,customer_name,subtotal,discount_amount,tax_amount,total_amount,recorded_profit,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",(bill,custid,customer,subtotal,discount,tax,total,profit-discount,session["user_id"]))
            sid=q.lastrowid
            for bid,qty,unit,cost,t,line_total,mname,batch in lines:
                q.execute("UPDATE inventory_batches SET quantity=quantity-%s WHERE id=%s AND quantity>=%s",(qty,bid,qty))
                if q.rowcount!=1:raise ValueError("Stock changed while billing. Please retry.")
                q.execute("INSERT INTO sale_items(sale_id,batch_id,medicine_name,batch_number,quantity,unit_price,unit_cost,tax_amount,line_total,line_profit) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",(sid,bid,mname,batch,qty,unit,cost,t,line_total,(unit-cost)*qty))
            q.execute("INSERT INTO audit_logs(user_id,event_type,entity_type,entity_id,detail_json,ip_address) VALUES(%s,'sale.created','sale',%s,%s,%s)",(session["user_id"],str(sid),json.dumps({"bill_number":bill,"lines":len(lines)}),request.remote_addr))
            c.commit(); return redirect(url_for("main.bill_detail",sale_id=sid))
        except Exception as e:
            if c:
                try:c.rollback()
                except Exception:pass
            flash(str(e) if isinstance(e,ValueError) else "Could not complete bill. The transaction was rolled back.","error")
        finally:
            if q:
                try:q.close()
                except Exception:pass
    c=tenant_db(); q=c.cursor(dictionary=True)
    q.execute("SELECT b.id batch_id,m.name,b.batch_number,b.expiry_date,b.selling_price,b.gst_percent,b.quantity FROM inventory_batches b JOIN medicines m ON m.id=b.medicine_id WHERE b.quantity>0 AND b.expiry_date>CURDATE() AND m.is_active=1 ORDER BY m.name LIMIT 1000")
    batches=q.fetchall(); q.close(); return render_template("billing.html",batches=batches)
@main_bp.route("/bills/<int:sale_id>")
def bill_detail(sale_id):
    x=guard("owner","admin","pharmacist","billing")
    if x:return x
    c=tenant_db(); q=c.cursor(dictionary=True); q.execute("SELECT * FROM sales WHERE id=%s",(sale_id,)); sale=q.fetchone()
    if not sale:abort(404)
    q.execute("SELECT * FROM sale_items WHERE sale_id=%s",(sale_id,)); items=q.fetchall(); q.close()
    return render_template("bill.html",sale=sale,items=items)
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
    c=tenant_db(); q=c.cursor(dictionary=True); q.execute("SELECT id,bill_number,customer_name,subtotal,tax_amount,discount_amount,total_amount,recorded_profit,created_at FROM sales ORDER BY id DESC LIMIT 500"); rows=q.fetchall(); q.close()
    return render_template("sales.html",rows=rows)
@main_bp.route("/suppliers",methods=["GET","POST"])
def suppliers():
    x=guard("owner","admin","pharmacist")
    if x:return x
    c=tenant_db()
    if request.method=="POST":
        name=request.form.get("name","").strip()
        if name:
            q=c.cursor(); q.execute("INSERT INTO suppliers(name,contact_name,email,phone,address) VALUES(%s,%s,%s,%s,%s)",(name,request.form.get("contact_name","").strip() or None,request.form.get("email","").strip() or None,request.form.get("phone","").strip() or None,request.form.get("address","").strip() or None)); c.commit(); q.close(); flash("Supplier saved.","success")
        else:flash("Supplier name is required.","error")
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
        if not name or not email or len(password)<12 or role not in ("admin","pharmacist","billing"):flash("Valid details and a 12-character password are required.","error")
        else:
            from werkzeug.security import generate_password_hash
            q=c.cursor()
            try:q.execute("INSERT INTO users(full_name,email,password_hash,role) VALUES(%s,%s,%s,%s)",(name,email,generate_password_hash(password),role)); c.commit(); flash("Staff account created.","success")
            except Exception:c.rollback(); flash("Could not create staff account; email may already exist.","error")
            q.close()
    q=c.cursor(dictionary=True); q.execute("SELECT id,full_name,email,role,is_active,created_at FROM users ORDER BY id"); rows=q.fetchall(); q.close(); return render_template("staff.html",rows=rows)
@main_bp.route("/health")
def health():return {"status":"ok"},200
