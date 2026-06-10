from flask import Flask
from flask import render_template
from flask import request
from flask import redirect
from flask import session
from flask import jsonify
from flask_sqlalchemy import SQLAlchemy

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
                k.kpi_name
            FROM monthly_data md

            JOIN kpis k
            ON md.kpi_id = k.id

            WHERE md.status = 'FORWARDED_TO_DRM'
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
        {
            "id": id
        }
    )

    db.session.commit()

    return redirect("/drm")
@app.route("/testdb")
def testdb():

    try:
        db.session.execute(
            db.text("SELECT 1")
        )

        return "Database Connected Successfully"

    except Exception as e:

        return str(e)
@app.route("/debuguser")
def debuguser():

    result = db.session.execute(
        db.text("SELECT * FROM users")
    )

    rows = result.fetchall()

    return str(rows)
@app.route("/department", methods=["GET", "POST"])
def department():

   
    if "user_id" not in session:
        return redirect("/login")
    

    user_department = session["department_id"]
    selected_month = request.form.get("month", "JUNE")
    selected_year = request.form.get("year", "2026")

    result = db.session.execute(
        db.text("""
           SELECT
    k.*,
    d.dept_name,
    md.performance_month,
    md.cumulative_performance,
    md.status,

    prev.performance_month AS previous_year_value

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
AND prev.month = :month
AND prev.year = :previous_year

ORDER BY
    k.display_order,
    k.id
        """),
    {
    "user_id": session["user_id"],
    "month": selected_month,
    "year": int(selected_year),
    "previous_year": int(selected_year) - 1
}
    )

    kpis = result.fetchall()
    

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

            if month_value == "":
                month_value = None

            if cumulative_value == "":
                cumulative_value = None

            if month_value is not None or cumulative_value is not None:

                existing = db.session.execute(
                    db.text("""
                        SELECT id
                        FROM monthly_data
                        WHERE kpi_id = :kpi_id
                        AND entered_by = :entered_by
                        AND month = 'JUNE'
                        AND year = 2026
                    """),
                    {
                        "kpi_id": kpi.id,
                        "entered_by": session["user_id"]
                    }
                ).fetchone()

                if existing:

                    db.session.execute(
                        db.text("""
                            UPDATE monthly_data
                            SET
                                performance_month = :month_value,
                                cumulative_performance = :cumulative_value,
                                status = :status
                            WHERE id = :id
                        """),
                        {
                            "id": existing.id,
                            "month_value": float(month_value) if month_value else None,
                            "cumulative_value": float(cumulative_value) if cumulative_value else None,
                            "status": status
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
                                cumulative_performance,
                                entered_by,
                                status
                            )
                            VALUES
                            (
                                :kpi_id,
                                'JUNE',
                                2026,
                                :month_value,
                                :cumulative_value,
                                :entered_by,
                                :status
                            )
                        """),
                        {
                            "kpi_id": kpi.id,
                            "month_value": float(month_value) if month_value else None,
                            "cumulative_value": float(cumulative_value) if cumulative_value else None,
                            "entered_by": session["user_id"],
                            "status": status
                        }
                    )

        db.session.commit()

        if status == "DRAFT":
            return render_template(
                "department_form.html",
                kpis=kpis,
                user_department=session["department_id"],
                message="Draft Saved Successfully"
            )

        return redirect("/submitted")

    return render_template(
        "department_form.html",
        kpis=kpis,
        user_department=session["department_id"]
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

    result = db.session.execute(
        db.text("""
            SELECT
                md.id,
                md.performance_month,
                md.cumulative_performance,
                md.status,
                md.month,
                md.year,

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
            "year": int(selected_year)
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
@app.route("/nodal")
def nodal():

    if "user_id" not in session :
        return redirect("/login")

    if session["role"] != "LEVEL3":
        return "Access Denied"

    result = db.session.execute(
        db.text("""
            SELECT
                md.id,
                md.performance_month,
                md.cumulative_performance,
                md.status,
                k.kpi_name
            FROM monthly_data md

            JOIN kpis k
            ON md.kpi_id = k.id

            WHERE md.status = 'APPROVED'
        """)
    )

    rows = result.fetchall()

    return render_template(
        "nodal.html",
        rows=rows
    )
@app.route("/adrm")
def adrm():

    if "user_id" not in session:
        return redirect("/login")

    if session["role"] != "LEVEL4":
        return "Access Denied"

    result = db.session.execute(
        db.text("""
            SELECT
                md.id,
                md.performance_month,
                md.cumulative_performance,
                md.status,
                k.kpi_name
            FROM monthly_data md
            JOIN kpis k
            ON md.kpi_id = k.id

            WHERE md.status = 'FORWARDED_TO_ADRM'
        """)
    )

    rows = result.fetchall()

    return render_template(
        "adrm.html",
        rows=rows
    )
@app.route("/forward_to_drm/<int:id>")
def forward_to_drm(id):

    db.session.execute(
        db.text("""
            UPDATE monthly_data
            SET status='FORWARDED_TO_DRM'
            WHERE id=:id
        """),
        {
            "id": id
        }
    )

    db.session.commit()

    return redirect("/adrm")

@app.route("/forward_to_adrm/<int:id>")
def forward_to_adrm(id):

    db.session.execute(
        db.text("""
            UPDATE monthly_data
            SET status='FORWARDED_TO_ADRM'
            WHERE id=:id
        """),
        {
            "id": id
        }
    )

    db.session.commit()

    return redirect("/nodal")

@app.route("/admin/kpis")
def manage_kpis():

    if session["role"] != "ADMIN":
        return "Access Denied"

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

    if session["role"] != "ADMIN":
        return "Access Denied"

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

ṣ