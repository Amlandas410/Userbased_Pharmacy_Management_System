import os, re, json
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from werkzeug.security import check_password_hash
from .db import platform_db, provision_tenant, tenant_db_name
from .extensions import limiter
auth_bp=Blueprint("auth",__name__)
EMAIL_PATTERN = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")

@auth_bp.route("/register",methods=["GET","POST"])
@limiter.limit("5 per hour")
def register():
    if os.getenv("REGISTRATION_ENABLED","true").lower()!="true":
        flash("Registrations are currently disabled.","error"); return redirect(url_for("auth.login"))
    if request.method=="POST":
        name=request.form.get("pharmacy_name","").strip()
        owner=request.form.get("owner_name","").strip()
        email=request.form.get("email","").strip().lower()
        phone=request.form.get("phone","").strip()
        license_no=request.form.get("license_number","").strip() or None
        gstin=request.form.get("gstin","").strip() or None
        address=request.form.get("address","").strip()
        password=request.form.get("password",""); confirm=request.form.get("confirm_password","")
        if not all([name,owner,email,phone,address,password]) or not EMAIL_PATTERN.fullmatch(email):
            flash("Complete all required fields with a valid email.","error"); return render_template("register.html")
        if len(password)<12 or password!=confirm:
            flash("Passwords must match and contain at least 12 characters.","error"); return render_template("register.html")
        c=platform_db(); cur=c.cursor(dictionary=True)
        cur.execute("SELECT id FROM pharmacies WHERE owner_email=%s",(email,))
        if cur.fetchone():
            cur.close(); c.close(); flash("An account with this email already exists.","error"); return render_template("register.html")
        try:
            cur2=c.cursor()
            cur2.execute("INSERT INTO pharmacies(pharmacy_name,owner_email,phone,license_number,gstin,address,database_name,status) VALUES(%s,%s,%s,%s,%s,%s,%s,'provisioning')",
                         (name,email,phone,license_no,gstin,address,"ph_pending_"+("0"*16)))
            pid=cur2.lastrowid
            dbname="ph_"+format(pid,"016x")
            cur2.execute("UPDATE pharmacies SET database_name=%s WHERE id=%s",(dbname,pid))
            cur2.execute("INSERT INTO provisioning_attempts(pharmacy_id,status) VALUES(%s,'started')",(pid,))
            c.commit(); cur2.close()
        except Exception:
            c.rollback(); cur.close(); c.close(); current_app.logger.exception("Could not start registration")
            flash("Registration could not be started. Try again.","error"); return render_template("register.html")
        cur.close(); c.close()
        try:
            provision_tenant(dbname,owner,email,password)
            c=platform_db(); cur=c.cursor()
            cur.execute("UPDATE pharmacies SET status='active' WHERE id=%s",(pid,))
            cur.execute("UPDATE provisioning_attempts SET status='complete' WHERE pharmacy_id=%s ORDER BY id DESC LIMIT 1",(pid,))
            cur.execute("INSERT INTO platform_audit_logs(pharmacy_id,event_type,actor,detail_json) VALUES(%s,'pharmacy.registered',%s,%s)",
                        (pid,email,json.dumps({"pharmacy_name":name})))
            c.commit(); cur.close(); c.close()
            flash("Pharmacy registered. Sign in to continue.","success"); return redirect(url_for("auth.login"))
        except Exception:
            current_app.logger.exception("Tenant provisioning failed")
            c=platform_db(); cur=c.cursor()
            cur.execute("UPDATE pharmacies SET status='failed' WHERE id=%s",(pid,))
            cur.execute("UPDATE provisioning_attempts SET status='failed',error_code='PROVISIONING_FAILED' WHERE pharmacy_id=%s ORDER BY id DESC LIMIT 1",(pid,))
            c.commit(); cur.close(); c.close()
            flash("Registration saved, but database setup failed. Ask the platform administrator to retry.","error")
    return render_template("register.html")

@auth_bp.route("/login",methods=["GET","POST"])
@limiter.limit("10 per minute")
def login():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower(); password=request.form.get("password","")
        c=platform_db(); cur=c.cursor(dictionary=True)
        cur.execute("SELECT id,pharmacy_name,database_name,status FROM pharmacies WHERE owner_email=%s",(email,))
        t=cur.fetchone(); cur.close(); c.close()
        # Staff sign-in is resolved against each tenant by email only in a production-ready
        # design with a tenant slug. This MVP accepts the pharmacy owner's registered email.
        if t and t["status"]=="active":
            try:
                c=tenant_db_name(t["database_name"]); cur=c.cursor(dictionary=True)
                cur.execute("SELECT id,full_name,email,password_hash,role,is_active FROM users WHERE email=%s",(email,))
                u=cur.fetchone(); cur.close(); c.close()
                if u and u["is_active"] and check_password_hash(u["password_hash"],password):
                    session.clear(); session.permanent=True
                    session.update(pharmacy_id=t["id"],pharmacy_name=t["pharmacy_name"],user_id=u["id"],user_name=u["full_name"],role=u["role"])
                    return redirect(url_for("main.dashboard"))
            except Exception: current_app.logger.exception("Login failed")
        flash("Invalid email or password, or account unavailable.","error")
    return render_template("login.html")

@auth_bp.route("/logout",methods=["POST"])
def logout():
    session.clear(); flash("Signed out successfully.","success"); return redirect(url_for("auth.login"))
