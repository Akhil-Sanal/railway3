from flask import Flask
from flask import render_template
from flask import request
from flask import redirect
from flask import session
from flask_sqlalchemy import SQLAlchemy
from flask import jsonify
from datetime import datetime

app = Flask(__name__)
app.secret_key = "railway_secret"

app.config.from_object("config.Config")

db = SQLAlchemy(app)

@app.route("/")
def home():
    return "Railway DPMS Running"

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        result = db.session.execute(
            db.text("""
                SELECT *
                FROM users
                WHERE username = :username
            """),
            {"username": username}
        )

        user = result.fetchone()

        if user and password == user.password_hash:
            role = user.role.strip()

            session["user_id"] = user.id
            session["role"] = role
            session["department_id"] = user.department_id
            session["username"] = user.username

            if role == "LEVEL1":
                return redirect("/department")
            elif role == "LEVEL2":
                return redirect("/hod")
            elif role == "LEVEL3":
                return redirect("/nodal")
            elif role == "LEVEL4":
                return redirect("/adrm")
            elif role == "LEVEL5":
                return redirect("/drm")
            elif role == "ADMIN":
                return redirect("/admin")

        return "Invalid Login"

    return render_template("login.html")

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect("/login")

    return f"""
    <h1>Railway DPMS Dashboard</h1>
    User ID : {session['user_id']} <br>
    Role : {session['role']} <br>
    Department : {session['department_id']}
    """

@app.route("/drm")
def drm():
    if "user_id" not in session:
        return redirect("/login")

    if session["role"] != "LEVEL5":
        return "Access Denied"

    result = db.session.execute(
        db.text("""
            SELECT
                md.id,
                md.performance_month,
                md.cumulative_performance,
                md.status,
                md.remarks,
                k.kpi_name
            FROM monthly_data md
            JOIN kpis k
            ON md.kpi_id = k.id
            WHERE md.status = 'FORWARDED_TO_DRM'
            ORDER BY md.created_at DESC
        """)
    )

    rows = result.fetchall()

    return render_template(
        "drm.html",
        rows=rows
    )

@app.route("/freeze/<int:id>")
def freeze(id):
    if "user_id" not in session:
        return redirect("/login")

    if session["role"] != "LEVEL5":
        return "Access Denied"

    db.session.execute(
        db.text("""
            UPDATE monthly_data
            SET status='FROZEN'
            WHERE id=:id
        """),
        {"id": id}
    )

    db.session.commit()
    return redirect("/drm")

@app.route("/testdb")
def testdb():
    try:
        db.session.execute(db.text("SELECT 1"))
        return "Database Connected Successfully"
    except Exception as e:
        return str(e)

@app.route("/debuguser")
def debuguser():
    result = db.session.execute(db.text("SELECT * FROM users"))
    rows = result.fetchall()
    return str(rows)

@app.route("/debug/returned")
def debug_returned():
    if "user_id" not in session:
        return jsonify({"error": "Login required"}), 401
    
    result = db.session.execute(
        db.text("""
            SELECT 
                md.id,
                md.kpi_id,
                md.performance_month,
                md.cumulative_performance,
                md.status,
                md.remarks,
                md.month,
                md.year,
                md.entered_by,
                k.kpi_name
            FROM monthly_data md
            JOIN kpis k ON md.kpi_id = k.id
            WHERE md.entered_by = :user_id
            AND md.status = 'RETURNED'
            ORDER BY md.created_at DESC
        """),
        {"user_id": session["user_id"]}
    )
    
    rows = result.fetchall()
    
    return jsonify({
        "count": len(rows),
        "rows": [dict(row._mapping) for row in rows]
    })

@app.route("/remove_return/<int:id>", methods=["POST"])
def remove_return(id):
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Login required"}), 401
    
    # Verify that this return entry belongs to the logged-in user
    result = db.session.execute(
        db.text("""
            SELECT id FROM monthly_data 
            WHERE id = :id AND entered_by = :user_id AND status = 'RETURNED'
        """),
        {"id": id, "user_id": session["user_id"]}
    )
    
    if not result.fetchone():
        return jsonify({"success": False, "message": "Unauthorized or entry not found"}), 403
    
    # Delete the returned entry
    db.session.execute(
        db.text("DELETE FROM monthly_data WHERE id = :id"),
        {"id": id}
    )
    db.session.commit()
    
    return jsonify({"success": True, "message": "Returned KPI removed successfully"})

@app.route("/department", methods=["GET", "POST"])
def department():
    if "user_id" not in session:
        return redirect("/login")

    user_department = session["department_id"]
    selected_month = request.values.get("month", "JUNE")
    selected_year = request.values.get("year", "2026")
    
    try:
        selected_year = int(selected_year)
    except ValueError:
        selected_year = 2026

    # Main query for KPIs
    result = db.session.execute(
        db.text("""
            SELECT
                k.*,
                d.dept_name,
                md.performance_month,
                md.cumulative_performance,
                md.status,
                md.remarks,
                prev.performance_month AS previous_year_value,
                prev.cumulative_performance AS previous_year_cumulative
            FROM kpis k
            JOIN departments d
            ON k.department_id = d.id
            LEFT JOIN monthly_data md
            ON md.kpi_id = k.id
            AND md.entered_by = :user_id
            AND md.month = :month
            AND md.year = :year
            LEFT JOIN monthly_data prev
            ON prev.kpi_id = k.id
            AND prev.entered_by = :user_id
            AND prev.month = :month
            AND prev.year = :previous_year
            ORDER BY
                k.display_order,
                k.id
        """),
        {
            "user_id": session["user_id"],
            "month": selected_month,
            "year": selected_year,
            "previous_year": selected_year - 1
        }
    )

    kpis = result.fetchall()

    # Query for returned KPIs - NO month/year filter to show ALL returned applications
    returned_result = db.session.execute(
        db.text("""
            SELECT
                md.id,
                md.kpi_id,
                md.performance_month,
                md.cumulative_performance,
                md.status,
                md.remarks,
                md.month,
                md.year,
                k.kpi_name
            FROM monthly_data md
            JOIN kpis k ON md.kpi_id = k.id
            WHERE md.entered_by = :user_id
            AND md.status = 'RETURNED'
            ORDER BY md.created_at DESC
        """),
        {
            "user_id": session["user_id"]
        }
    )

    returned_kpis = returned_result.fetchall()

    if request.method == "POST":
        action = request.form.get("action")
        status = "DRAFT"

        if action == "submit":
            status = "SUBMITTED"

        user_department = session["department_id"]

        for kpi in kpis:
            if kpi.department_id != user_department:
                continue

            month_value = request.form.get(f"month_{kpi.id}")
            cumulative_value = request.form.get(f"cum_{kpi.id}")
            prev_cum_value = request.form.get(f"prev_cum_{kpi.id}")
            prev_year_value = request.form.get(f"prev_{kpi.id}")

            if month_value == "":
                month_value = None
            if cumulative_value == "":
                cumulative_value = None
            if prev_cum_value == "":
                prev_cum_value = None
            if prev_year_value == "":
                prev_year_value = None

            if month_value is not None or cumulative_value is not None or prev_year_value is not None or prev_cum_value is not None:
                existing = db.session.execute(
                    db.text("""
                        SELECT id, status
                        FROM monthly_data
                        WHERE kpi_id = :kpi_id
                        AND entered_by = :entered_by
                        AND month = :month
                        AND year = :year
                    """),
                    {
                        "kpi_id": kpi.id,
                        "entered_by": session["user_id"],
                        "month": selected_month,
                        "year": selected_year
                    }
                ).fetchone()

                if existing:
                    # If it was returned and we're submitting, clear the remarks and set to submitted
                    new_status = status
                    db.session.execute(
                        db.text("""
                            UPDATE monthly_data
                            SET
                                performance_month = :month_value,
                                cumulative_performance = :cumulative_value,
                                previous_year_value = :prev_year_value,
                                cumulative_performance_of_prev_year = :prev_cum_value,
                                status = :status,
                                remarks = NULL
                            WHERE id = :id
                        """),
                        {
                            "id": existing.id,
                            "month_value": float(month_value) if month_value else None,
                            "cumulative_value": float(cumulative_value) if cumulative_value else None,
                            "prev_year_value": float(prev_year_value) if prev_year_value else None,
                            "prev_cum_value": float(prev_cum_value) if prev_cum_value else None,
                            "status": new_status
                        }
                    )
                else:
                    db.session.execute(
                        db.text("""
                            INSERT INTO monthly_data
                            (
                                kpi_id,
                                month,
                                year,
                                performance_month,
                                previous_year_value,
                                cumulative_performance,
                                cumulative_performance_of_prev_year,
                                entered_by,
                                status
                            )
                            VALUES
                            (
                                :kpi_id,
                                :month,
                                :year,
                                :month_value,
                                :prev_year_value,
                                :cumulative_value,
                                :prev_cum_value,
                                :entered_by,
                                :status
                            )
                        """),
                        {
                            "kpi_id": kpi.id,
                            "month": selected_month,
                            "year": selected_year,
                            "month_value": float(month_value) if month_value else None,
                            "prev_year_value": float(prev_year_value) if prev_year_value else None,
                            "cumulative_value": float(cumulative_value) if cumulative_value else None,
                            "prev_cum_value": float(prev_cum_value) if prev_cum_value else None,
                            "entered_by": session["user_id"],
                            "status": status
                        }
                    )

        db.session.commit()

        # Refresh returned_kpis after submission
        returned_result = db.session.execute(
            db.text("""
                SELECT
                    md.id,
                    md.kpi_id,
                    md.performance_month,
                    md.cumulative_performance,
                    md.status,
                    md.remarks,
                    md.month,
                    md.year,
                    k.kpi_name
                FROM monthly_data md
                JOIN kpis k ON md.kpi_id = k.id
                WHERE md.entered_by = :user_id
                AND md.status = 'RETURNED'
                ORDER BY md.created_at DESC
            """),
            {"user_id": session["user_id"]}
        )
        returned_kpis = returned_result.fetchall()

        message = "Draft Saved Successfully" if status == "DRAFT" else "Submitted Successfully"
        
        return render_template(
            "department_form.html",
            kpis=kpis,
            returned_kpis=returned_kpis,
            user_department=session["department_id"],
            message=message,
            selected_month=selected_month,
            selected_year=selected_year
        )

    # GET request
    return render_template(
        "department_form.html",
        kpis=kpis,
        returned_kpis=returned_kpis,
        user_department=session["department_id"],
        selected_month=selected_month,
        selected_year=selected_year
    )

@app.route("/hod")
def hod():
    if "user_id" not in session:
        return redirect("/login")

    if session["role"] != "LEVEL2":
        return "Access Denied"

    department_id = session["department_id"]
    selected_month = request.args.get("month", "JUNE")
    selected_year = request.args.get("year", "2026")

    try:
        selected_year = int(selected_year)
    except ValueError:
        selected_year = 2026

    result = db.session.execute(
        db.text("""
            SELECT
                md.id,
                md.performance_month,
                md.cumulative_performance,
                md.status,
                md.month,
                md.year,
                md.remarks,
                md.created_at,
                k.kpi_name,
                k.unit,
                k.annual_target,
                k.section_name,
                d.dept_name
            FROM monthly_data md
            JOIN kpis k
            ON md.kpi_id = k.id
            JOIN departments d
            ON k.department_id = d.id
            WHERE md.status = 'SUBMITTED'
            AND k.department_id = :department_id
            AND md.month = :month
            AND md.year = :year
            ORDER BY
                k.display_order,
                k.id
        """),
        {
            "department_id": department_id,
            "month": selected_month,
            "year": selected_year
        }
    )

    rows = result.fetchall()

    return render_template(
        "hod_review.html",
        rows=rows,
        selected_month=selected_month,
        selected_year=selected_year
    )

@app.route("/approve/<int:id>")
def approve(id):
    if "user_id" not in session:
        return redirect("/login")
    
    if session["role"] != "LEVEL2":
        return "Access Denied"
    
    db.session.execute(
        db.text("""
            UPDATE monthly_data
            SET status = 'APPROVED'
            WHERE id = :id
        """),
        {"id": id}
    )
    db.session.commit()
    return redirect("/hod")

@app.route("/approve_bulk", methods=["POST"])
def approve_bulk():
    if "user_id" not in session:
        return jsonify({"message": "Login Required"}), 401

    if session["role"] != "LEVEL2":
        return jsonify({"message": "Access Denied"}), 403

    data = request.get_json()
    ids = data.get("ids", [])

    if not ids:
        return jsonify({"message": "No KPI Selected"}), 400

    for id in ids:
        db.session.execute(
            db.text("""
                UPDATE monthly_data
                SET status = 'APPROVED'
                WHERE id = :id
            """),
            {"id": id}
        )

    db.session.commit()
    return jsonify({
        "message": f"{len(ids)} KPI(s) Approved Successfully"
    })

@app.route("/return/<int:id>", methods=["GET", "POST"])
def return_entry(id):
    if "user_id" not in session:
        return redirect("/login")
    
    if session["role"] != "LEVEL2":
        return "Access Denied"
    
    if request.method == "POST":
        remarks = request.form.get("remarks", "")
        
        db.session.execute(
            db.text("""
                UPDATE monthly_data
                SET status = 'RETURNED',
                    remarks = :remarks
                WHERE id = :id
            """),
            {
                "id": id,
                "remarks": remarks
            }
        )
        db.session.commit()
        return redirect("/hod")
    
    # GET request - show the remark form
    result = db.session.execute(
        db.text("""
            SELECT 
                md.id,
                k.kpi_name,
                md.performance_month,
                md.cumulative_performance
            FROM monthly_data md
            JOIN kpis k ON md.kpi_id = k.id
            WHERE md.id = :id
        """),
        {"id": id}
    )
    kpi = result.fetchone()
    
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Return KPI - Add Remarks</title>
        <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.2/css/bootstrap.min.css" rel="stylesheet"/>
        <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet"/>
        <style>
            :root {{
                --rly-blue: #003366;
                --rly-gold: #d4a017;
            }}
            body {{
                background: #eef2f7;
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            }}
            .card-header {{
                background: var(--rly-blue);
                color: white;
            }}
            .btn-submit {{
                background: var(--rly-blue);
                color: white;
            }}
            .btn-submit:hover {{
                background: #004080;
            }}
            .kpi-info {{
                background: #f8f9fa;
                border-left: 4px solid var(--rly-gold);
            }}
        </style>
    </head>
    <body>
        <div class="container mt-5">
            <div class="row justify-content-center">
                <div class="col-md-8">
                    <div class="card shadow">
                        <div class="card-header">
                            <h4 class="mb-0">
                                <i class="fas fa-undo-alt me-2"></i>
                                Return KPI for Revision
                            </h4>
                        </div>
                        <div class="card-body">
                            <div class="alert alert-info kpi-info">
                                <div class="row">
                                    <div class="col-md-12">
                                        <strong><i class="fas fa-chart-line me-1"></i> KPI:</strong> {kpi.kpi_name}
                                    </div>
                                    <div class="col-md-6 mt-2">
                                        <strong><i class="fas fa-calendar-alt me-1"></i> Monthly Performance:</strong> {kpi.performance_month if kpi.performance_month else 'Not entered'}
                                    </div>
                                    <div class="col-md-6 mt-2">
                                        <strong><i class="fas fa-chart-bar me-1"></i> Cumulative Performance:</strong> {kpi.cumulative_performance if kpi.cumulative_performance else 'Not entered'}
                                    </div>
                                </div>
                            </div>
                            
                            <form method="POST">
                                <div class="mb-3">
                                    <label for="remarks" class="form-label">
                                        <i class="fas fa-comment-dots me-1"></i>
                                        Remarks / Reason for Return <span class="text-danger">*</span>
                                    </label>
                                    <textarea 
                                        class="form-control" 
                                        id="remarks" 
                                        name="remarks" 
                                        rows="5" 
                                        placeholder="Please provide detailed reason for returning this KPI. Include specific feedback and required corrections..."
                                        required
                                    ></textarea>
                                    <div class="form-text text-muted mt-2">
                                        <i class="fas fa-info-circle me-1"></i>
                                        These remarks will be visible to the department user when they view this KPI.
                                    </div>
                                </div>
                                
                                <div class="d-flex justify-content-between mt-4">
                                    <a href="/hod" class="btn btn-secondary">
                                        <i class="fas fa-times me-1"></i> Cancel
                                    </a>
                                    <button type="submit" class="btn btn-submit">
                                        <i class="fas fa-paper-plane me-1"></i> Submit Return
                                    </button>
                                </div>
                            </form>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.2/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    """

@app.route("/nodal")
def nodal():
    if "user_id" not in session:
        return redirect("/login")

    if session["role"] != "LEVEL3":
        return "Access Denied"

    selected_month = request.args.get("month", "JUNE")
    selected_year = request.args.get("year", "2026")
    
    try:
        selected_year = int(selected_year)
    except ValueError:
        selected_year = 2026

    result = db.session.execute(
        db.text("""
            SELECT
                md.id,
                md.performance_month,
                md.cumulative_performance,
                md.status,
                md.remarks,
                k.kpi_name,
                d.dept_name
            FROM monthly_data md
            JOIN kpis k
            ON md.kpi_id = k.id
            JOIN departments d
            ON k.department_id = d.id
            WHERE md.status = 'APPROVED'
            AND md.month = :month
            AND md.year = :year
            ORDER BY k.id
        """),
        {
            "month": selected_month,
            "year": selected_year
        }
    )

    rows = result.fetchall()

    return render_template(
        "nodal.html",
        rows=rows,
        selected_month=selected_month,
        selected_year=selected_year
    )

@app.route("/adrm")
def adrm():
    if "user_id" not in session:
        return redirect("/login")

    if session["role"] != "LEVEL4":
        return "Access Denied"

    selected_month = request.args.get("month", "JUNE")
    selected_year = request.args.get("year", "2026")
    
    try:
        selected_year = int(selected_year)
    except ValueError:
        selected_year = 2026

    result = db.session.execute(
        db.text("""
            SELECT
                md.id,
                md.performance_month,
                md.cumulative_performance,
                md.status,
                md.remarks,
                k.kpi_name,
                d.dept_name
            FROM monthly_data md
            JOIN kpis k
            ON md.kpi_id = k.id
            JOIN departments d
            ON k.department_id = d.id
            WHERE md.status = 'FORWARDED_TO_ADRM'
            AND md.month = :month
            AND md.year = :year
            ORDER BY k.id
        """),
        {
            "month": selected_month,
            "year": selected_year
        }
    )

    rows = result.fetchall()

    return render_template(
        "adrm.html",
        rows=rows,
        selected_month=selected_month,
        selected_year=selected_year
    )

@app.route("/forward_to_drm/<int:id>")
def forward_to_drm(id):
    if "user_id" not in session:
        return redirect("/login")
    
    if session["role"] != "LEVEL4":
        return "Access Denied"
    
    db.session.execute(
        db.text("""
            UPDATE monthly_data
            SET status='FORWARDED_TO_DRM'
            WHERE id=:id
        """),
        {"id": id}
    )
    db.session.commit()
    return redirect("/adrm")

@app.route("/forward_to_adrm/<int:id>")
def forward_to_adrm(id):
    if "user_id" not in session:
        return redirect("/login")
    
    if session["role"] != "LEVEL3":
        return "Access Denied"
    
    db.session.execute(
        db.text("""
            UPDATE monthly_data
            SET status='FORWARDED_TO_ADRM'
            WHERE id=:id
        """),
        {"id": id}
    )
    db.session.commit()
    return redirect("/nodal")

@app.route("/admin/kpis")
def manage_kpis():
    if "user_id" not in session or session["role"] != "ADMIN":
        return redirect("/login")

    result = db.session.execute(
        db.text("""
            SELECT *
            FROM kpis
            ORDER BY display_order
        """)
    )

    kpis = result.fetchall()

    return render_template(
        "manage_kpis.html",
        kpis=kpis
    )

@app.route("/admin/update_kpi/<int:id>", methods=["POST"])
def update_kpi(id):
    if "user_id" not in session or session["role"] != "ADMIN":
        return redirect("/login")

    annual_target = request.form["annual_target"]

    db.session.execute(
        db.text("""
            UPDATE kpis
            SET annual_target = :annual_target
            WHERE id = :id
        """),
        {
            "annual_target": annual_target,
            "id": id
        }
    )

    db.session.commit()
    return redirect("/admin/kpis")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

if __name__ == "__main__":
    app.run(debug=True)
