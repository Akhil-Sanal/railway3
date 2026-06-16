from flask import Flask
from flask import render_template
from flask import request
from flask import redirect
from flask import session
from flask_sqlalchemy import SQLAlchemy
from flask import jsonify
from flask import send_from_directory
from flask import send_file
from datetime import datetime
import os
from werkzeug.utils import secure_filename
import uuid
import glob
import json
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from io import BytesIO

app = Flask(__name__)
app.secret_key = "railway_secret"

app.config.from_object("config.Config")

# Add connection pooling to prevent database disconnections
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 3600,
    'pool_size': 10,
    'max_overflow': 20
}

# Configuration for file uploads
UPLOAD_FOLDER = 'uploads/hod_approvals'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10MB max
ALLOWED_EXTENSIONS = {'pdf'}

# Create upload directory if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

db = SQLAlchemy(app)

# Add connection check before each request
@app.before_request
def before_request():
    """Ensure database connection is alive before each request"""
    try:
        db.session.execute(db.text("SELECT 1"))
    except Exception:
        db.session.rollback()

@app.teardown_appcontext
def shutdown_session(exception=None):
    """Close session after each request"""
    db.session.remove()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_document_path(monthly_data_id):
    """Get the document path for a given KPI ID"""
    # First check if there's a mapping file for bulk upload
    mapping_file = os.path.join(app.config['UPLOAD_FOLDER'], f"bulk_mapping_{monthly_data_id}.txt")
    if os.path.exists(mapping_file):
        with open(mapping_file, 'r') as f:
            bulk_filename = f.read().strip()
            if bulk_filename:
                # Verify the file exists
                if os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], bulk_filename)):
                    return bulk_filename
                else:
                    # File doesn't exist, remove mapping
                    os.remove(mapping_file)
    
    # Look for files starting with the KPI ID (single upload)
    pattern = os.path.join(app.config['UPLOAD_FOLDER'], f"{monthly_data_id}_*.pdf")
    files = glob.glob(pattern)
    if files:
        # Return the most recent file
        return os.path.basename(files[0])
    return None

def get_document_info(monthly_data_id):
    """Get document info for a given KPI ID"""
    doc_path = get_document_path(monthly_data_id)
    if doc_path:
        # Check if it's a bulk file
        if doc_path.startswith('bulk_'):
            # For bulk files, extract the original filename
            # Format: bulk_{timestamp}_{original_filename}.pdf
            parts = doc_path.split('_', 2)
            if len(parts) >= 3:
                original_name = parts[2].replace('.pdf', '')
                return {
                    'path': doc_path,
                    'original_name': original_name + '.pdf',
                    'is_bulk': True
                }
        else:
            # For single files, format: {kpi_id}_{timestamp}_{original_filename}.pdf
            parts = doc_path.split('_', 2)
            if len(parts) >= 3:
                original_name = parts[2].replace('.pdf', '')
                return {
                    'path': doc_path,
                    'original_name': original_name + '.pdf',
                    'is_bulk': False
                }
    return None

def get_document_remarks(monthly_data_id):
    """Get remarks for a document"""
    remarks_file = os.path.join(app.config['UPLOAD_FOLDER'], f"{monthly_data_id}_remarks.txt")
    if os.path.exists(remarks_file):
        with open(remarks_file, 'r') as f:
            return f.read()
    return ""

def copy_to_approved_table(monthly_data_id):
    """Copy a record from monthly_data to approved_data table - ONLY called when DRM approves"""
    try:
        # Fetch the record from monthly_data with all fields
        result = db.session.execute(
            db.text("""
                SELECT 
                    kpi_id,
                    month,
                    year,
                    performance_month,
                    cumulative_performance,
                    entered_by,
                    previous_year_value,
                    cumulative_performance_of_prev_year,
                    remarks
                FROM monthly_data
                WHERE id = :id
            """),
            {"id": monthly_data_id}
        )
        record = result.fetchone()
        
        if record:
            # Check if already exists in approved_data
            existing = db.session.execute(
                db.text("""
                    SELECT id FROM approved_data
                    WHERE kpi_id = :kpi_id 
                    AND entered_by = :entered_by 
                    AND month = :month 
                    AND year = :year
                """),
                {
                    "kpi_id": record.kpi_id,
                    "entered_by": record.entered_by,
                    "month": record.month,
                    "year": record.year
                }
            ).fetchone()
            
            if existing:
                # Update existing record
                db.session.execute(
                    db.text("""
                        UPDATE approved_data
                        SET 
                            performance_month = :performance_month,
                            cumulative_performance = :cumulative_performance,
                            previous_year_value = :previous_year_value,
                            cumulative_performance_of_prev_year = :cumulative_performance_of_prev_year,
                            remarks = :remarks,
                            status = 'APPROVED',
                            created_at = NOW()
                        WHERE id = :id
                    """),
                    {
                        "id": existing.id,
                        "performance_month": record.performance_month,
                        "cumulative_performance": record.cumulative_performance,
                        "previous_year_value": record.previous_year_value,
                        "cumulative_performance_of_prev_year": record.cumulative_performance_of_prev_year,
                        "remarks": record.remarks
                    }
                )
            else:
                # Insert new record
                db.session.execute(
                    db.text("""
                        INSERT INTO approved_data
                        (
                            kpi_id,
                            month,
                            year,
                            performance_month,
                            cumulative_performance,
                            entered_by,
                            previous_year_value,
                            cumulative_performance_of_prev_year,
                            remarks,
                            status,
                            created_at
                        )
                        VALUES
                        (
                            :kpi_id,
                            :month,
                            :year,
                            :performance_month,
                            :cumulative_performance,
                            :entered_by,
                            :previous_year_value,
                            :cumulative_performance_of_prev_year,
                            :remarks,
                            'APPROVED',
                            NOW()
                        )
                    """),
                    {
                        "kpi_id": record.kpi_id,
                        "month": record.month,
                        "year": record.year,
                        "performance_month": record.performance_month,
                        "cumulative_performance": record.cumulative_performance,
                        "entered_by": record.entered_by,
                        "previous_year_value": record.previous_year_value,
                        "cumulative_performance_of_prev_year": record.cumulative_performance_of_prev_year,
                        "remarks": record.remarks
                    }
                )
            db.session.commit()
            return True
    except Exception as e:
        db.session.rollback()
        print(f"Error copying to approved_data: {str(e)}")
        return False

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

    selected_month = request.args.get("month", "JUNE")
    selected_year = request.args.get("year", "2026")
    
    try:
        selected_year = int(selected_year)
    except ValueError:
        selected_year = 2026

    # Query to get all FORWARDED_TO_DRM KPIs with performance data and annual targets
    result = db.session.execute(
        db.text("""
            SELECT
                md.id,
                md.performance_month,
                md.cumulative_performance,
                md.status,
                md.remarks,
                md.created_at,
                k.kpi_name,
                k.annual_target,
                COALESCE(k.unit, '') as unit
            FROM monthly_data md
            JOIN kpis k ON md.kpi_id = k.id
            WHERE md.status = 'FORWARDED_TO_DRM'
            AND md.month = :month
            AND md.year = :year
            ORDER BY md.created_at DESC
        """),
        {
            "month": selected_month,
            "year": selected_year
        }
    )

    rows = result.fetchall()
    
    # Convert to list of dictionaries with calculated values for indicators
    rows_list = []
    for row in rows:
        # Safely convert values to float for calculation
        try:
            monthly_val = float(row.performance_month) if row.performance_month and row.performance_month != '-' else 0
        except (ValueError, TypeError):
            monthly_val = 0
            
        try:
            cumulative_val = float(row.cumulative_performance) if row.cumulative_performance and row.cumulative_performance != '-' else 0
        except (ValueError, TypeError):
            cumulative_val = 0
            
        try:
            annual_target = float(row.annual_target) if row.annual_target else 0
        except (ValueError, TypeError):
            annual_target = 0
        
        row_dict = {
            'id': row.id,
            'performance_month': row.performance_month if row.performance_month is not None else '-',
            'cumulative_performance': row.cumulative_performance if row.cumulative_performance is not None else '-',
            'status': row.status,
            'remarks': row.remarks,
            'created_at': row.created_at,
            'kpi_name': row.kpi_name,
            'annual_target': row.annual_target,
            'unit': row.unit,
            'monthly_val': monthly_val,
            'cumulative_val': cumulative_val,
            'annual_target_val': annual_target
        }
        rows_list.append(row_dict)

    return render_template(
        "drm.html",
        rows=rows_list,
        selected_month=selected_month,
        selected_year=selected_year
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
    
    try:
        # First verify that this return entry belongs to the logged-in user and has RETURNED status
        result = db.session.execute(
            db.text("""
                SELECT id, kpi_id, month, year 
                FROM monthly_data 
                WHERE id = :id 
                AND entered_by = :user_id 
                AND status = 'RETURNED'
            """),
            {"id": id, "user_id": session["user_id"]}
        )
        
        record = result.fetchone()
        
        if not record:
            return jsonify({"success": False, "message": "Record not found or you don't have permission to delete it"}), 403
        
        # Delete the returned entry
        db.session.execute(
            db.text("DELETE FROM monthly_data WHERE id = :id"),
            {"id": id}
        )
        db.session.commit()
        
        return jsonify({
            "success": True, 
            "message": "Returned KPI removed successfully. You can now enter fresh data."
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error removing returned item: {str(e)}")
        return jsonify({"success": False, "message": f"Database error: {str(e)}"}), 500

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

    # Query for KPIs with their current data
    result = db.session.execute(
        db.text("""
            SELECT
                k.*,
                d.dept_name,
                md.id as monthly_data_id,
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

    # Query for returned KPIs for the selected month/year only (with full data)
    returned_result = db.session.execute(
        db.text("""
            SELECT
                md.id,
                md.kpi_id,
                md.performance_month,
                md.cumulative_performance,
                md.previous_year_value,
                md.cumulative_performance_of_prev_year,
                md.status,
                md.remarks,
                md.month,
                md.year,
                k.kpi_name,
                k.unit,
                k.annual_target,
                k.section_name
            FROM monthly_data md
            JOIN kpis k ON md.kpi_id = k.id
            WHERE md.entered_by = :user_id
            AND md.status = 'RETURNED'
            AND md.month = :month
            AND md.year = :year
            ORDER BY md.created_at DESC
        """),
        {
            "user_id": session["user_id"],
            "month": selected_month,
            "year": selected_year
        }
    )

    returned_kpis = returned_result.fetchall()
    
    # Create a set of returned KPI IDs for easy lookup
    returned_kpi_ids = {r.kpi_id for r in returned_kpis}

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
                # Check if there's an existing record
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
                    # Determine new status based on current status and action
                    new_status = status
                    if existing.status == 'RETURNED' and status == 'DRAFT':
                        new_status = 'DRAFT'
                    elif existing.status == 'RETURNED' and status == 'SUBMITTED':
                        new_status = 'SUBMITTED'
                    
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
                    # Insert new record
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
        
        # Refresh the data after update
        # Re-query for updated returned KPIs
        returned_result = db.session.execute(
            db.text("""
                SELECT
                    md.id,
                    md.kpi_id,
                    md.performance_month,
                    md.cumulative_performance,
                    md.previous_year_value,
                    md.cumulative_performance_of_prev_year,
                    md.status,
                    md.remarks,
                    md.month,
                    md.year,
                    k.kpi_name,
                    k.unit,
                    k.annual_target,
                    k.section_name
                FROM monthly_data md
                JOIN kpis k ON md.kpi_id = k.id
                WHERE md.entered_by = :user_id
                AND md.status = 'RETURNED'
                AND md.month = :month
                AND md.year = :year
                ORDER BY md.created_at DESC
            """),
            {
                "user_id": session["user_id"],
                "month": selected_month,
                "year": selected_year
            }
        )
        returned_kpis = returned_result.fetchall()
        returned_kpi_ids = {r.kpi_id for r in returned_kpis}

        message = "Draft Saved Successfully" if status == "DRAFT" else "Submitted Successfully"
        
        return render_template(
            "department_form.html",
            kpis=kpis,
            returned_kpis=returned_kpis,
            returned_kpi_ids=returned_kpi_ids,
            user_department=session["department_id"],
            message=message,
            selected_month=selected_month,
            selected_year=selected_year
        )

    return render_template(
        "department_form.html",
        kpis=kpis,
        returned_kpis=returned_kpis,
        returned_kpi_ids=returned_kpi_ids,
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

    # Query to get all submitted KPIs with previous year data
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
                COALESCE(CAST(md.previous_year_value AS DECIMAL(10,2)), 0) as previous_year_value,
                COALESCE(CAST(md.cumulative_performance_of_prev_year AS DECIMAL(10,2)), 0) as cumulative_performance_of_prev_year,
                k.kpi_name,
                COALESCE(k.unit, '') as unit,
                k.annual_target,
                k.section_name,
                d.dept_name
            FROM monthly_data md
            JOIN kpis k ON md.kpi_id = k.id
            JOIN departments d ON k.department_id = d.id
            WHERE md.status = 'SUBMITTED'
            AND k.department_id = :department_id
            AND UPPER(md.month) = UPPER(:month)
            AND md.year = :year
            ORDER BY k.display_order, k.id
        """),
        {
            "department_id": department_id,
            "month": selected_month,
            "year": selected_year
        }
    )

    rows = result.fetchall()
    
    # Convert to list of dictionaries
    rows_list = []
    for row in rows:
        # Safely convert values to float for calculation
        try:
            monthly_val = float(row.performance_month) if row.performance_month and row.performance_month != '-' else 0
        except (ValueError, TypeError):
            monthly_val = 0
            
        try:
            cumulative_val = float(row.cumulative_performance) if row.cumulative_performance and row.cumulative_performance != '-' else 0
        except (ValueError, TypeError):
            cumulative_val = 0
        
        row_dict = {
            'id': row.id,
            'performance_month': row.performance_month if row.performance_month is not None else '-',
            'cumulative_performance': row.cumulative_performance if row.cumulative_performance is not None else '-',
            'status': row.status,
            'month': row.month,
            'year': row.year,
            'remarks': row.remarks,
            'created_at': row.created_at,
            'previous_year_value': row.previous_year_value if row.previous_year_value and row.previous_year_value != 0 else 'Not Available',
            'cumulative_performance_of_prev_year': row.cumulative_performance_of_prev_year if row.cumulative_performance_of_prev_year and row.cumulative_performance_of_prev_year != 0 else 'Not Available',
            'kpi_name': row.kpi_name,
            'unit': row.unit,
            'annual_target': row.annual_target,
            'section_name': row.section_name,
            'dept_name': row.dept_name,
            'monthly_val': monthly_val,
            'cumulative_val': cumulative_val
        }
        rows_list.append(row_dict)
    
    return render_template(
        "hod_review.html",
        rows=rows_list,
        selected_month=selected_month,
        selected_year=selected_year
    )

# HOD Document Upload Routes

@app.route("/hod/download_template/<int:monthly_data_id>")
def hod_download_template(monthly_data_id):
    """Download a PDF template for HOD to sign"""
    if "user_id" not in session:
        return redirect("/login")
    
    if session["role"] != "LEVEL2":
        return "Access Denied"
    
    try:
        # Fetch KPI data
        result = db.session.execute(
            db.text("""
                SELECT 
                    md.id,
                    md.performance_month,
                    md.cumulative_performance,
                    md.month,
                    md.year,
                    md.remarks,
                    k.kpi_name,
                    k.unit,
                    k.annual_target,
                    d.dept_name,
                    u.username as entered_by_name
                FROM monthly_data md
                JOIN kpis k ON md.kpi_id = k.id
                JOIN departments d ON k.department_id = d.id
                JOIN users u ON md.entered_by = u.id
                WHERE md.id = :id
                AND md.status = 'SUBMITTED'
                AND k.department_id = :dept_id
            """),
            {
                "id": monthly_data_id,
                "dept_id": session["department_id"]
            }
        ).fetchone()
        
        if not result:
            return "KPI not found or not in SUBMITTED status", 404
        
        # Create PDF
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, 
                               rightMargin=72, leftMargin=72,
                               topMargin=72, bottomMargin=72)
        
        # Story for PDF
        story = []
        styles = getSampleStyleSheet()
        
        # Title style
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=16,
            alignment=TA_CENTER,
            spaceAfter=20,
            textColor=colors.HexColor('#003366')
        )
        
        # Subtitle style
        subtitle_style = ParagraphStyle(
            'CustomSubtitle',
            parent=styles['Heading2'],
            fontSize=12,
            alignment=TA_CENTER,
            spaceAfter=10,
            textColor=colors.HexColor('#004080')
        )
        
        # Normal style
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=10,
            alignment=TA_LEFT,
            spaceAfter=6
        )
        
        # Bold style
        bold_style = ParagraphStyle(
            'CustomBold',
            parent=styles['Normal'],
            fontSize=10,
            alignment=TA_LEFT,
            spaceAfter=6,
            fontName='Helvetica-Bold'
        )
        
        # Add header
        story.append(Paragraph("INDIAN RAILWAYS", title_style))
        story.append(Paragraph("Palakkad Division - Southern Railway", subtitle_style))
        story.append(Paragraph("HOD Approval Form", subtitle_style))
        story.append(Spacer(1, 20))
        
        # Add KPI details
        story.append(Paragraph(f"<b>KPI Name:</b> {result.kpi_name}", normal_style))
        story.append(Paragraph(f"<b>Department:</b> {result.dept_name}", normal_style))
        story.append(Paragraph(f"<b>Month/Year:</b> {result.month} {result.year}", normal_style))
        story.append(Paragraph(f"<b>Monthly Performance:</b> {result.performance_month if result.performance_month else 'N/A'} {result.unit if result.unit else ''}", normal_style))
        story.append(Paragraph(f"<b>Cumulative Performance:</b> {result.cumulative_performance if result.cumulative_performance else 'N/A'} {result.unit if result.unit else ''}", normal_style))
        story.append(Paragraph(f"<b>Annual Target:</b> {result.annual_target if result.annual_target else 'N/A'} {result.unit if result.unit else ''}", normal_style))
        story.append(Spacer(1, 20))
        
        # Add remarks section
        story.append(Paragraph("<b>Remarks:</b>", bold_style))
        story.append(Paragraph(f"{result.remarks if result.remarks else 'No remarks provided'}", normal_style))
        story.append(Spacer(1, 20))
        
        # Add signature section
        story.append(Paragraph("<b>HOD Approval</b>", bold_style))
        story.append(Spacer(1, 10))
        
        # Add signature lines
        story.append(Paragraph("I, the undersigned, hereby approve the above KPI performance data.", normal_style))
        story.append(Spacer(1, 20))
        
        # Signature box
        signature_data = [
            ['', ''],
            ['Signature:', '_________________________'],
            ['', ''],
            ['Name:', '_________________________'],
            ['', ''],
            ['Designation:', 'Head of Department'],
            ['', ''],
            ['Date:', '_________________________'],
            ['', ''],
            ['Place:', '_________________________']
        ]
        
        signature_table = Table(signature_data, colWidths=[2*inch, 3*inch])
        signature_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        
        story.append(signature_table)
        story.append(Spacer(1, 20))
        
        # Add footer
        story.append(Paragraph("<i>This document is a template for HOD approval. Please sign and upload the signed copy.</i>", normal_style))
        story.append(Paragraph(f"<i>Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>", normal_style))
        
        # Build PDF
        doc.build(story)
        
        # Get PDF data
        pdf_data = buffer.getvalue()
        buffer.close()
        
        # Return PDF
        return send_file(
            BytesIO(pdf_data),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f"HOD_Approval_Template_{result.kpi_name}_{result.month}_{result.year}.pdf"
        )
        
    except Exception as e:
        print(f"Error generating template: {str(e)}")
        return f"Error generating template: {str(e)}", 500

@app.route("/hod/bulk_download_template", methods=["POST"])
def hod_bulk_download_template():
    """Download a combined PDF template for multiple KPIs"""
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Login required"}), 401
    
    if session["role"] != "LEVEL2":
        return jsonify({"success": False, "message": "Access Denied"}), 403
    
    try:
        data = request.get_json()
        ids = data.get("ids", [])
        
        if not ids:
            return jsonify({"success": False, "message": "No KPI IDs provided"}), 400
        
        # Fetch KPI data for all IDs
        placeholders = ','.join([':id' + str(i) for i in range(len(ids))])
        params = {}
        for i, kpi_id in enumerate(ids):
            params[f'id{i}'] = kpi_id
        
        results = db.session.execute(
            db.text(f"""
                SELECT 
                    md.id,
                    md.performance_month,
                    md.cumulative_performance,
                    md.month,
                    md.year,
                    md.remarks,
                    k.kpi_name,
                    k.unit,
                    k.annual_target,
                    d.dept_name,
                    u.username as entered_by_name
                FROM monthly_data md
                JOIN kpis k ON md.kpi_id = k.id
                JOIN departments d ON k.department_id = d.id
                JOIN users u ON md.entered_by = u.id
                WHERE md.id IN ({placeholders})
                AND md.status = 'SUBMITTED'
                AND k.department_id = :dept_id
                ORDER BY k.display_order, k.id
            """),
            {**params, "dept_id": session["department_id"]}
        ).fetchall()
        
        if not results:
            return jsonify({"success": False, "message": "No valid KPIs found"}), 400
        
        # Create PDF
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, 
                               rightMargin=72, leftMargin=72,
                               topMargin=72, bottomMargin=72)
        
        # Story for PDF
        story = []
        styles = getSampleStyleSheet()
        
        # Title style
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=16,
            alignment=TA_CENTER,
            spaceAfter=20,
            textColor=colors.HexColor('#003366')
        )
        
        subtitle_style = ParagraphStyle(
            'CustomSubtitle',
            parent=styles['Heading2'],
            fontSize=12,
            alignment=TA_CENTER,
            spaceAfter=10,
            textColor=colors.HexColor('#004080')
        )
        
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=9,
            alignment=TA_LEFT,
            spaceAfter=4
        )
        
        bold_style = ParagraphStyle(
            'CustomBold',
            parent=styles['Normal'],
            fontSize=9,
            alignment=TA_LEFT,
            spaceAfter=4,
            fontName='Helvetica-Bold'
        )
        
        # Add header
        story.append(Paragraph("INDIAN RAILWAYS", title_style))
        story.append(Paragraph("Palakkad Division - Southern Railway", subtitle_style))
        story.append(Paragraph("HOD Bulk Approval Form", subtitle_style))
        story.append(Spacer(1, 20))
        
        # Add each KPI
        for idx, row in enumerate(results):
            if idx > 0:
                story.append(Spacer(1, 20))
                story.append(Paragraph("-" * 80, normal_style))
                story.append(Spacer(1, 10))
            
            story.append(Paragraph(f"<b>KPI #{idx + 1}:</b>", bold_style))
            story.append(Paragraph(f"<b>KPI Name:</b> {row.kpi_name}", normal_style))
            story.append(Paragraph(f"<b>Department:</b> {row.dept_name}", normal_style))
            story.append(Paragraph(f"<b>Month/Year:</b> {row.month} {row.year}", normal_style))
            story.append(Paragraph(f"<b>Monthly Performance:</b> {row.performance_month if row.performance_month else 'N/A'} {row.unit if row.unit else ''}", normal_style))
            story.append(Paragraph(f"<b>Cumulative Performance:</b> {row.cumulative_performance if row.cumulative_performance else 'N/A'} {row.unit if row.unit else ''}", normal_style))
            story.append(Paragraph(f"<b>Annual Target:</b> {row.annual_target if row.annual_target else 'N/A'} {row.unit if row.unit else ''}", normal_style))
            story.append(Paragraph(f"<b>Remarks:</b> {row.remarks if row.remarks else 'No remarks provided'}", normal_style))
            story.append(Spacer(1, 5))
        
        # Add signature section
        story.append(Spacer(1, 20))
        story.append(Paragraph("-" * 80, normal_style))
        story.append(Spacer(1, 10))
        story.append(Paragraph("<b>HOD Approval</b>", bold_style))
        story.append(Spacer(1, 10))
        story.append(Paragraph("I, the undersigned, hereby approve the above KPI performance data.", normal_style))
        story.append(Spacer(1, 20))
        
        # Signature box
        signature_data = [
            ['', ''],
            ['Signature:', '_________________________'],
            ['', ''],
            ['Name:', '_________________________'],
            ['', ''],
            ['Designation:', 'Head of Department'],
            ['', ''],
            ['Date:', '_________________________'],
            ['', ''],
            ['Place:', '_________________________']
        ]
        
        signature_table = Table(signature_data, colWidths=[2*inch, 3*inch])
        signature_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        
        story.append(signature_table)
        story.append(Spacer(1, 20))
        
        # Add footer
        story.append(Paragraph("<i>This document is a template for HOD bulk approval. Please sign and upload the signed copy.</i>", normal_style))
        story.append(Paragraph(f"<i>Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>", normal_style))
        
        # Build PDF
        doc.build(story)
        
        # Get PDF data
        pdf_data = buffer.getvalue()
        buffer.close()
        
        # Return PDF
        return send_file(
            BytesIO(pdf_data),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f"HOD_Bulk_Approval_Template_{datetime.now().strftime('%Y%m%d')}.pdf"
        )
        
    except Exception as e:
        print(f"Error generating bulk template: {str(e)}")
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500

@app.route("/hod/upload_signed_document/<int:monthly_data_id>", methods=["POST"])
def hod_upload_signed_document(monthly_data_id):
    """HOD uploads signed PDF approval document for a specific KPI"""
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Login required"}), 401
    
    if session["role"] != "LEVEL2":
        return jsonify({"success": False, "message": "Access Denied"}), 403
    
    try:
        remarks = request.form.get("remarks", "")
        
        # Check if file was uploaded
        if 'pdf_file' not in request.files:
            return jsonify({"success": False, "message": "No file uploaded"}), 400
        
        file = request.files['pdf_file']
        
        if file.filename == '':
            return jsonify({"success": False, "message": "No file selected"}), 400
        
        if not allowed_file(file.filename):
            return jsonify({"success": False, "message": "Only PDF files are allowed"}), 400
        
        # Verify the KPI belongs to HOD's department and is in SUBMITTED status
        result = db.session.execute(
            db.text("""
                SELECT md.id, md.status, k.kpi_name, md.month, md.year, k.department_id
                FROM monthly_data md
                JOIN kpis k ON md.kpi_id = k.id
                WHERE md.id = :id
                AND md.status = 'SUBMITTED'
                AND k.department_id = :dept_id
            """),
            {
                "id": monthly_data_id,
                "dept_id": session["department_id"]
            }
        ).fetchone()
        
        if not result:
            return jsonify({
                "success": False, 
                "message": "KPI not found, not in SUBMITTED status, or not in your department"
            }), 404
        
        # Delete existing document if any (including bulk mapping)
        existing_doc = get_document_path(monthly_data_id)
        if existing_doc:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], existing_doc)
            if os.path.exists(file_path):
                os.remove(file_path)
            # Delete mapping file if exists
            mapping_file = os.path.join(app.config['UPLOAD_FOLDER'], f"bulk_mapping_{monthly_data_id}.txt")
            if os.path.exists(mapping_file):
                os.remove(mapping_file)
        
        # Generate unique filename
        original_filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_filename = f"{monthly_data_id}_{timestamp}_{original_filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        
        # Save the file
        file.save(file_path)
        
        # Store remarks
        remarks_file = os.path.join(app.config['UPLOAD_FOLDER'], f"{monthly_data_id}_remarks.txt")
        with open(remarks_file, 'w') as f:
            f.write(remarks)
        
        return jsonify({
            "success": True,
            "message": "Signed PDF document uploaded successfully. You can now approve this KPI.",
            "filename": original_filename
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error uploading signed document: {str(e)}")
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500

@app.route("/hod/download_signed_document/<int:monthly_data_id>")
def hod_download_signed_document(monthly_data_id):
    """Download the signed approval document for a KPI"""
    if "user_id" not in session:
        return redirect("/login")
    
    try:
        doc_info = get_document_info(monthly_data_id)
        if not doc_info:
            return "Signed document not found", 404
        
        return send_from_directory(
            app.config['UPLOAD_FOLDER'],
            doc_info['path'],
            as_attachment=True,
            download_name=doc_info['original_name']
        )
        
    except Exception as e:
        print(f"Error downloading document: {str(e)}")
        return "Error downloading document", 500

@app.route("/hod/view_signed_document/<int:monthly_data_id>")
def hod_view_signed_document(monthly_data_id):
    """View the signed document inline in browser"""
    if "user_id" not in session:
        return redirect("/login")
    
    try:
        doc_info = get_document_info(monthly_data_id)
        if not doc_info:
            return "Signed document not found", 404
        
        return send_from_directory(
            app.config['UPLOAD_FOLDER'],
            doc_info['path'],
            as_attachment=False  # This will display in browser
        )
        
    except Exception as e:
        print(f"Error viewing document: {str(e)}")
        return "Error viewing document", 500

@app.route("/hod/delete_signed_document/<int:monthly_data_id>", methods=["POST"])
def hod_delete_signed_document(monthly_data_id):
    """Delete the uploaded signed document (for re-upload)"""
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Login required"}), 401
    
    if session["role"] != "LEVEL2":
        return jsonify({"success": False, "message": "Access Denied"}), 403
    
    try:
        # Delete the document file
        doc_path = get_document_path(monthly_data_id)
        if doc_path:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], doc_path)
            if os.path.exists(file_path):
                os.remove(file_path)
        
        # Delete mapping file if exists
        mapping_file = os.path.join(app.config['UPLOAD_FOLDER'], f"bulk_mapping_{monthly_data_id}.txt")
        if os.path.exists(mapping_file):
            os.remove(mapping_file)
        
        # Delete remarks file
        remarks_file = os.path.join(app.config['UPLOAD_FOLDER'], f"{monthly_data_id}_remarks.txt")
        if os.path.exists(remarks_file):
            os.remove(remarks_file)
        
        return jsonify({
            "success": True,
            "message": "Document deleted successfully. You can upload a new one."
        })
        
    except Exception as e:
        print(f"Error deleting document: {str(e)}")
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500

@app.route("/hod/check_document_status/<int:monthly_data_id>")
def hod_check_document_status(monthly_data_id):
    """Check if a signed document is uploaded for a KPI"""
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Login required"}), 401
    
    try:
        doc_info = get_document_info(monthly_data_id)
        remarks = get_document_remarks(monthly_data_id)
        
        return jsonify({
            "success": True,
            "uploaded": doc_info is not None,
            "filename": doc_info['original_name'] if doc_info else None,
            "remarks": remarks
        })
            
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/hod/bulk_upload_signed_document", methods=["POST"])
def hod_bulk_upload_signed_document():
    """HOD uploads a single signed PDF for multiple KPIs"""
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Login required"}), 401
    
    if session["role"] != "LEVEL2":
        return jsonify({"success": False, "message": "Access Denied"}), 403
    
    try:
        remarks = request.form.get("remarks", "")
        kpi_ids_json = request.form.get("kpi_ids", "[]")
        kpi_ids = json.loads(kpi_ids_json)
        
        if not kpi_ids:
            return jsonify({"success": False, "message": "No KPI IDs provided"}), 400
        
        # Check if file was uploaded
        if 'pdf_file' not in request.files:
            return jsonify({"success": False, "message": "No file uploaded"}), 400
        
        file = request.files['pdf_file']
        
        if file.filename == '':
            return jsonify({"success": False, "message": "No file selected"}), 400
        
        if not allowed_file(file.filename):
            return jsonify({"success": False, "message": "Only PDF files are allowed"}), 400
        
        # Verify all KPIs belong to HOD's department and are in SUBMITTED status
        placeholders = ','.join([':id' + str(i) for i in range(len(kpi_ids))])
        params = {}
        for i, kpi_id in enumerate(kpi_ids):
            params[f'id{i}'] = kpi_id
        
        # Check if all KPIs are valid
        result = db.session.execute(
            db.text(f"""
                SELECT COUNT(*) as count
                FROM monthly_data md
                JOIN kpis k ON md.kpi_id = k.id
                WHERE md.id IN ({placeholders})
                AND md.status = 'SUBMITTED'
                AND k.department_id = :dept_id
            """),
            {**params, "dept_id": session["department_id"]}
        ).fetchone()
        
        if result.count != len(kpi_ids):
            return jsonify({
                "success": False, 
                "message": "Some KPIs are not in SUBMITTED status or not in your department"
            }), 400
        
        # Generate unique filename for bulk upload
        original_filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_filename = f"bulk_{timestamp}_{original_filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        
        # Save the file
        file.save(file_path)
        
        # Associate the same document with all KPIs
        for kpi_id in kpi_ids:
            # Delete existing document if any (including mapping)
            existing_doc = get_document_path(kpi_id)
            if existing_doc:
                existing_path = os.path.join(app.config['UPLOAD_FOLDER'], existing_doc)
                if os.path.exists(existing_path) and existing_doc != unique_filename:
                    os.remove(existing_path)
                # Delete existing mapping file
                existing_mapping = os.path.join(app.config['UPLOAD_FOLDER'], f"bulk_mapping_{kpi_id}.txt")
                if os.path.exists(existing_mapping):
                    os.remove(existing_mapping)
            
            # Store remarks for each KPI
            remarks_file = os.path.join(app.config['UPLOAD_FOLDER'], f"{kpi_id}_remarks.txt")
            with open(remarks_file, 'w') as f:
                f.write(f"Bulk upload: {remarks}" if remarks else "Bulk upload")
            
            # Create a mapping file to link the KPI to the bulk file
            mapping_file = os.path.join(app.config['UPLOAD_FOLDER'], f"bulk_mapping_{kpi_id}.txt")
            with open(mapping_file, 'w') as f:
                f.write(unique_filename)
        
        return jsonify({
            "success": True,
            "message": f"Bulk PDF uploaded successfully for {len(kpi_ids)} KPI(s). You can now approve them.",
            "filename": original_filename
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error uploading bulk document: {str(e)}")
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500

@app.route("/approve_bulk", methods=["POST"])
def approve_bulk():
    if "user_id" not in session:
        return jsonify({"message": "Login Required"}), 401

    if session["role"] != "LEVEL2":
        return jsonify({"message": "Access Denied"}), 403

    data = request.get_json()
    if not data:
        return jsonify({"message": "Invalid request data"}), 400
    
    ids = data.get("ids", [])

    if not ids:
        return jsonify({"message": "No KPI Selected"}), 400

    ids = [int(id_val) for id_val in ids]
    
    # Check if all selected KPIs have uploaded documents
    missing_docs = []
    try:
        for id_val in ids:
            doc_info = get_document_info(id_val)
            if not doc_info:
                missing_docs.append(str(id_val))
        
        if missing_docs:
            return jsonify({
                "message": f"KPI(s) {', '.join(missing_docs)} require uploaded signed PDF document before approval"
            }), 400
        
        for id_val in ids:
            # Update status to APPROVED
            db.session.execute(
                db.text("""
                    UPDATE monthly_data
                    SET status = 'APPROVED'
                    WHERE id = :id AND status = 'SUBMITTED'
                """),
                {"id": id_val}
            )
        db.session.commit()
        return jsonify({
            "message": f"{len(ids)} KPI(s) Approved Successfully"
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": f"Error: {str(e)}"}), 500

@app.route("/return/<int:id>", methods=["GET", "POST"])
def return_entry(id):
    if "user_id" not in session:
        return redirect("/login")
    
    if session["role"] != "LEVEL2":
        return "Access Denied"
    
    if request.method == "POST":
        remarks = request.form.get("remarks", "")
        
        try:
            db.session.execute(
                db.text("""
                    UPDATE monthly_data
                    SET status = 'RETURNED',
                        remarks = :remarks
                    WHERE id = :id AND status = 'SUBMITTED'
                """),
                {
                    "id": id,
                    "remarks": remarks
                }
            )
            db.session.commit()
            return redirect("/hod")
        except Exception as e:
            db.session.rollback()
            return f"Error: {str(e)}", 500
    
    # GET request - show the return form with error handling
    try:
        # Test connection first
        db.session.execute(db.text("SELECT 1"))
        
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
        
        if not kpi:
            return "KPI not found", 404
        
        return render_template("return_form.html", kpi=kpi)
        
    except Exception as e:
        db.session.rollback()
        print(f"Error in return_entry GET: {str(e)}")
        return f"Database error: {str(e)}. Please try again.", 500

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

    # Query to get all APPROVED KPIs with annual targets for performance indicators
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
                COALESCE(CAST(md.previous_year_value AS DECIMAL(10,2)), 0) as previous_year_value,
                COALESCE(CAST(md.cumulative_performance_of_prev_year AS DECIMAL(10,2)), 0) as cumulative_performance_of_prev_year,
                k.kpi_name,
                COALESCE(k.unit, '') as unit,
                k.annual_target,
                k.section_name,
                d.dept_name
            FROM monthly_data md
            JOIN kpis k ON md.kpi_id = k.id
            JOIN departments d ON k.department_id = d.id
            WHERE md.status = 'APPROVED'
            AND UPPER(md.month) = UPPER(:month)
            AND md.year = :year
            ORDER BY k.display_order, k.id
        """),
        {
            "month": selected_month,
            "year": selected_year
        }
    )

    rows = result.fetchall()
    
    # Convert to list of dictionaries with calculated values for indicators
    rows_list = []
    for row in rows:
        # Safely convert values to float for calculation
        try:
            monthly_val = float(row.performance_month) if row.performance_month and row.performance_month != '-' else 0
        except (ValueError, TypeError):
            monthly_val = 0
            
        try:
            cumulative_val = float(row.cumulative_performance) if row.cumulative_performance and row.cumulative_performance != '-' else 0
        except (ValueError, TypeError):
            cumulative_val = 0
            
        try:
            annual_target = float(row.annual_target) if row.annual_target else 0
        except (ValueError, TypeError):
            annual_target = 0
        
        row_dict = {
            'id': row.id,
            'performance_month': row.performance_month if row.performance_month is not None else '-',
            'cumulative_performance': row.cumulative_performance if row.cumulative_performance is not None else '-',
            'status': row.status,
            'month': row.month,
            'year': row.year,
            'remarks': row.remarks,
            'created_at': row.created_at,
            'previous_year_value': row.previous_year_value if row.previous_year_value and row.previous_year_value != 0 else 'Not Available',
            'cumulative_performance_of_prev_year': row.cumulative_performance_of_prev_year if row.cumulative_performance_of_prev_year and row.cumulative_performance_of_prev_year != 0 else 'Not Available',
            'kpi_name': row.kpi_name,
            'unit': row.unit,
            'annual_target': row.annual_target,
            'section_name': row.section_name,
            'dept_name': row.dept_name,
            'monthly_val': monthly_val,
            'cumulative_val': cumulative_val,
            'annual_target_val': annual_target
        }
        rows_list.append(row_dict)

    return render_template(
        "nodal.html",
        rows=rows_list,
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

    # Query to get all FORWARDED_TO_ADRM KPIs with annual targets for performance indicators
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
                COALESCE(CAST(md.previous_year_value AS DECIMAL(10,2)), 0) as previous_year_value,
                COALESCE(CAST(md.cumulative_performance_of_prev_year AS DECIMAL(10,2)), 0) as cumulative_performance_of_prev_year,
                k.kpi_name,
                COALESCE(k.unit, '') as unit,
                k.annual_target,
                k.section_name,
                d.dept_name
            FROM monthly_data md
            JOIN kpis k ON md.kpi_id = k.id
            JOIN departments d ON k.department_id = d.id
            WHERE md.status = 'FORWARDED_TO_ADRM'
            AND UPPER(md.month) = UPPER(:month)
            AND md.year = :year
            ORDER BY k.display_order, k.id
        """),
        {
            "month": selected_month,
            "year": selected_year
        }
    )

    rows = result.fetchall()
    
    # Convert to list of dictionaries with calculated values for indicators
    rows_list = []
    for row in rows:
        # Safely convert values to float for calculation
        try:
            monthly_val = float(row.performance_month) if row.performance_month and row.performance_month != '-' else 0
        except (ValueError, TypeError):
            monthly_val = 0
            
        try:
            cumulative_val = float(row.cumulative_performance) if row.cumulative_performance and row.cumulative_performance != '-' else 0
        except (ValueError, TypeError):
            cumulative_val = 0
            
        try:
            annual_target = float(row.annual_target) if row.annual_target else 0
        except (ValueError, TypeError):
            annual_target = 0
        
        row_dict = {
            'id': row.id,
            'performance_month': row.performance_month if row.performance_month is not None else '-',
            'cumulative_performance': row.cumulative_performance if row.cumulative_performance is not None else '-',
            'status': row.status,
            'month': row.month,
            'year': row.year,
            'remarks': row.remarks,
            'created_at': row.created_at,
            'previous_year_value': row.previous_year_value if row.previous_year_value and row.previous_year_value != 0 else 'Not Available',
            'cumulative_performance_of_prev_year': row.cumulative_performance_of_prev_year if row.cumulative_performance_of_prev_year and row.cumulative_performance_of_prev_year != 0 else 'Not Available',
            'kpi_name': row.kpi_name,
            'unit': row.unit,
            'annual_target': row.annual_target,
            'section_name': row.section_name,
            'dept_name': row.dept_name,
            'monthly_val': monthly_val,
            'cumulative_val': cumulative_val,
            'annual_target_val': annual_target
        }
        rows_list.append(row_dict)

    return render_template(
        "adrm.html",
        rows=rows_list,
        selected_month=selected_month,
        selected_year=selected_year
    )

@app.route("/return_kpi_to_nodal/<int:id>", methods=["POST"])
def return_kpi_to_nodal(id):
    """ADRM returns KPI to Nodal Officer"""
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Login required"}), 401
    
    if session["role"] != "LEVEL4":
        return jsonify({"success": False, "message": "Access Denied"}), 403
    
    try:
        data = request.get_json()
        remarks = data.get("remarks", "")
        
        # Update the status to RETURNED (back to nodal officer)
        db.session.execute(
            db.text("""
                UPDATE monthly_data
                SET 
                    status = 'RETURNED',
                    remarks = :remarks
                WHERE id = :id 
                AND status = 'FORWARDED_TO_ADRM'
            """),
            {
                "id": id,
                "remarks": remarks
            }
        )
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "Application returned to Nodal Officer successfully"
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error returning KPI to nodal: {str(e)}")
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500

@app.route("/forward_to_drm/<int:id>")
def forward_to_drm(id):
    if "user_id" not in session:
        return redirect("/login")
    
    if session["role"] != "LEVEL4":
        return "Access Denied"
    
    try:
        # REMOVED: copy_to_approved_table(id) - Only DRM should copy to approved_data
        
        db.session.execute(
            db.text("""
                UPDATE monthly_data
                SET status='FORWARDED_TO_DRM'
                WHERE id=:id AND status='FORWARDED_TO_ADRM'
            """),
            {"id": id}
        )
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return f"Error: {str(e)}", 500
    
    return redirect("/adrm")

@app.route("/forward_to_adrm/<int:id>")
def forward_to_adrm(id):
    if "user_id" not in session:
        return redirect("/login")
    
    if session["role"] != "LEVEL3":
        return "Access Denied"
    
    try:
        # REMOVED: copy_to_approved_table(id) - Only DRM should copy to approved_data
        
        db.session.execute(
            db.text("""
                UPDATE monthly_data
                SET status='FORWARDED_TO_ADRM'
                WHERE id=:id AND status='APPROVED'
            """),
            {"id": id}
        )
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return f"Error: {str(e)}", 500
    
    return redirect("/nodal")

@app.route("/reject_to_employee/<int:id>", methods=["GET", "POST"])
def reject_to_employee(id):
    if "user_id" not in session:
        return redirect("/login")

    if session["role"] != "LEVEL3":
        return "Access Denied"

    if request.method == "POST":
        remarks = request.form.get("remarks", "")

        try:
            # Get the current record to know which month/year to preserve
            current_record = db.session.execute(
                db.text("""
                    SELECT month, year, kpi_id, entered_by
                    FROM monthly_data 
                    WHERE id = :id AND status = 'APPROVED'
                """),
                {"id": id}
            ).fetchone()
            
            if not current_record:
                return "Record not found or not in APPROVED status", 404
            
            # Update the status to RETURNED
            db.session.execute(
                db.text("""
                    UPDATE monthly_data
                    SET
                        status = 'RETURNED',
                        remarks = :remarks
                    WHERE id = :id
                    AND status = 'APPROVED'
                """),
                {
                    "id": id,
                    "remarks": remarks
                }
            )

            db.session.commit()

        except Exception as e:
            db.session.rollback()
            return f"Error : {str(e)}"

        return redirect("/nodal")

    # GET request - show the return form (using return_form.html)
    result = db.session.execute(
        db.text("""
            SELECT
                md.id,
                md.performance_month,
                md.cumulative_performance,
                md.previous_year_value,
                md.cumulative_performance_of_prev_year,
                k.kpi_name
            FROM monthly_data md
            JOIN kpis k
            ON md.kpi_id = k.id
            WHERE md.id = :id
        """),
        {"id": id}
    )

    row = result.fetchone()
    
    # Create a kpi object compatible with return_form.html
    class KpiObject:
        def __init__(self, data):
            self.id = data.id
            self.kpi_name = data.kpi_name
            self.performance_month = data.performance_month
            self.cumulative_performance = data.cumulative_performance
    
    kpi = KpiObject(row)

    return render_template(
        "return_form.html",
        kpi=kpi
    )

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

    try:
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
    except Exception as e:
        db.session.rollback()
        return f"Error: {str(e)}", 500

    return redirect("/admin/kpis")

@app.route("/approve_kpi/<int:id>")
def approve_kpi(id):
    """DRM approves KPI - ONLY HERE data is copied to approved_data table"""
    if "user_id" not in session:
        return redirect("/login")
    
    if session["role"] != "LEVEL5":  # DRM role
        return "Access Denied"
    
    try:
        # Copy to approved_data ONLY when DRM approves
        copy_to_approved_table(id)
        
        # Update status to FROZEN
        db.session.execute(
            db.text("""
                UPDATE monthly_data
                SET status='FROZEN'
                WHERE id=:id AND status='FORWARDED_TO_DRM'
            """),
            {"id": id}
        )
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return f"Error: {str(e)}", 500
    
    return redirect("/drm")

@app.route("/return_kpi_to_adrm/<int:id>", methods=["POST"])
def return_kpi_to_adrm(id):
    """DRM returns KPI to ADRM Officer - NO copy to approved_data"""
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Login required"}), 401
    
    if session["role"] != "LEVEL5":  # DRM role
        return jsonify({"success": False, "message": "Access Denied"}), 403
    
    try:
        data = request.get_json()
        remarks = data.get("remarks", "")
        
        # Update the status to RETURNED (back to ADRM officer)
        # NO copy to approved_data on reject
        db.session.execute(
            db.text("""
                UPDATE monthly_data
                SET 
                    status = 'RETURNED',
                    remarks = :remarks
                WHERE id = :id 
                AND status = 'FORWARDED_TO_DRM'
            """),
            {
                "id": id,
                "remarks": remarks
            }
        )
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": "Application returned to ADRM Officer successfully"
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error returning KPI to ADRM: {str(e)}")
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

if __name__ == "__main__":
    app.run(debug=True)
    