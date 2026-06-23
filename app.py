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
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
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

# ============ FINANCIAL YEAR HELPER FUNCTIONS ============

def get_financial_year(year, month):
    """
    Get the financial year for a given year and month.
    Financial year runs from April 1 to March 31.
    If month is Jan-Mar (0-2), it belongs to previous financial year.
    
    Args:
        year: The calendar year (e.g., 2025)
        month: 0-indexed month (0=January, 11=December)
    
    Returns:
        int: The financial year (e.g., 2024 for March 2025)
    """
    if month in [0, 1, 2]:  # Jan, Feb, Mar
        return year - 1
    else:  # Apr-Dec
        return year

def get_financial_year_from_month_string(year, month_string):
    """
    Get financial year from year and month name string.
    
    Args:
        year: The calendar year (e.g., 2025)
        month_string: Month name (e.g., 'MARCH', 'APRIL')
    
    Returns:
        int: The financial year (e.g., 2024 for March 2025)
    """
    month_map = {
        'JANUARY': 0, 'FEBRUARY': 1, 'MARCH': 2,
        'APRIL': 3, 'MAY': 4, 'JUNE': 5,
        'JULY': 6, 'AUGUST': 7, 'SEPTEMBER': 8,
        'OCTOBER': 9, 'NOVEMBER': 10, 'DECEMBER': 11
    }
    month_num = month_map.get(month_string.upper(), 0)
    return get_financial_year(year, month_num)

def get_financial_year_display(financial_year):
    """
    Get display string for financial year.
    
    Args:
        financial_year: The financial year (e.g., 2024)
    
    Returns:
        str: Display format (e.g., "2024-25")
    """
    return f"{financial_year}-{str(financial_year + 1)[-2:]}"

# ============ END FINANCIAL YEAR HELPER FUNCTIONS ============

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

@app.route("/nodal/bulk_forward", methods=["POST"])
def nodal_bulk_forward():
    """Bulk forward multiple APPROVED KPIs to ADRM"""
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Login required"}), 401
    
    if session["role"] != "LEVEL3":
        return jsonify({"success": False, "message": "Access Denied"}), 403
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request data"}), 400
        
        ids = data.get("ids", [])
        if not ids:
            return jsonify({"success": False, "message": "No KPI IDs provided"}), 400
        
        # Convert IDs to integers
        ids = [int(id_val) for id_val in ids]
        
        # Verify all KPIs are in APPROVED status
        placeholders = ','.join([':id' + str(i) for i in range(len(ids))])
        params = {}
        for i, kpi_id in enumerate(ids):
            params[f'id{i}'] = kpi_id
        
        # Check if all selected KPIs are in APPROVED status
        result = db.session.execute(
            db.text(f"""
                SELECT COUNT(*) as count
                FROM monthly_data
                WHERE id IN ({placeholders})
                AND status = 'APPROVED'
            """),
            params
        ).fetchone()
        
        if result.count != len(ids):
            return jsonify({
                "success": False, 
                "message": "Some selected KPIs are not in APPROVED status"
            }), 400
        
        # Update all selected KPIs to FORWARDED_TO_ADRM
        for kpi_id in ids:
            db.session.execute(
                db.text("""
                    UPDATE monthly_data
                    SET status = 'FORWARDED_TO_ADRM'
                    WHERE id = :id AND status = 'APPROVED'
                """),
                {"id": kpi_id}
            )
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": f"{len(ids)} KPI(s) forwarded to ADRM successfully"
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error in bulk forward: {str(e)}")
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500

@app.route("/adrm/bulk_forward", methods=["POST"])
def adrm_bulk_forward():
    """Bulk forward multiple FORWARDED_TO_ADRM KPIs to DRM"""
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Login required"}), 401
    
    if session["role"] != "LEVEL4":
        return jsonify({"success": False, "message": "Access Denied"}), 403
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request data"}), 400
        
        ids = data.get("ids", [])
        if not ids:
            return jsonify({"success": False, "message": "No KPI IDs provided"}), 400
        
        # Convert IDs to integers
        ids = [int(id_val) for id_val in ids]
        
        # Verify all KPIs are in FORWARDED_TO_ADRM status
        placeholders = ','.join([':id' + str(i) for i in range(len(ids))])
        params = {}
        for i, kpi_id in enumerate(ids):
            params[f'id{i}'] = kpi_id
        
        # Check if all selected KPIs are in FORWARDED_TO_ADRM status
        result = db.session.execute(
            db.text(f"""
                SELECT COUNT(*) as count
                FROM monthly_data
                WHERE id IN ({placeholders})
                AND status = 'FORWARDED_TO_ADRM'
            """),
            params
        ).fetchone()
        
        if result.count != len(ids):
            return jsonify({
                "success": False, 
                "message": "Some selected KPIs are not in FORWARDED_TO_ADRM status"
            }), 400
        
        # Update all selected KPIs to FORWARDED_TO_DRM
        for kpi_id in ids:
            db.session.execute(
                db.text("""
                    UPDATE monthly_data
                    SET status = 'FORWARDED_TO_DRM'
                    WHERE id = :id AND status = 'FORWARDED_TO_ADRM'
                """),
                {"id": kpi_id}
            )
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": f"{len(ids)} KPI(s) forwarded to DRM successfully"
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error in ADRM bulk forward: {str(e)}")
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500

@app.route("/drm/bulk_approve", methods=["POST"])
def drm_bulk_approve():
    """Bulk approve multiple FORWARDED_TO_DRM KPIs"""
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Login required"}), 401
    
    if session["role"] != "LEVEL5":
        return jsonify({"success": False, "message": "Access Denied"}), 403
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "Invalid request data"}), 400
        
        ids = data.get("ids", [])
        if not ids:
            return jsonify({"success": False, "message": "No KPI IDs provided"}), 400
        
        # Convert IDs to integers
        ids = [int(id_val) for id_val in ids]
        
        # Verify all KPIs are in FORWARDED_TO_DRM status
        placeholders = ','.join([':id' + str(i) for i in range(len(ids))])
        params = {}
        for i, kpi_id in enumerate(ids):
            params[f'id{i}'] = kpi_id
        
        # Check if all selected KPIs are in FORWARDED_TO_DRM status
        result = db.session.execute(
            db.text(f"""
                SELECT COUNT(*) as count
                FROM monthly_data
                WHERE id IN ({placeholders})
                AND status = 'FORWARDED_TO_DRM'
            """),
            params
        ).fetchone()
        
        if result.count != len(ids):
            return jsonify({
                "success": False, 
                "message": "Some selected KPIs are not in FORWARDED_TO_DRM status"
            }), 400
        
        # Approve each KPI (copy to approved_data and freeze)
        approved_count = 0
        failed_ids = []
        for kpi_id in ids:
            try:
                # Copy to approved_data
                copy_to_approved_table(kpi_id)
                
                # Update status to FROZEN
                db.session.execute(
                    db.text("""
                        UPDATE monthly_data
                        SET status = 'FROZEN'
                        WHERE id = :id AND status = 'FORWARDED_TO_DRM'
                    """),
                    {"id": kpi_id}
                )
                approved_count += 1
            except Exception as e:
                print(f"Error approving KPI {kpi_id}: {str(e)}")
                failed_ids.append(str(kpi_id))
        
        db.session.commit()
        
        if failed_ids:
            return jsonify({
                "success": True,
                "message": f"{approved_count} KPI(s) approved successfully. Failed: {', '.join(failed_ids)}"
            })
        else:
            return jsonify({
                "success": True,
                "message": f"{approved_count} KPI(s) approved and frozen successfully"
            })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error in DRM bulk approve: {str(e)}")
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500

def get_document_info(monthly_data_id):
    """Get document info for a given KPI ID"""
    doc_path = get_document_path(monthly_data_id)
    if doc_path:
        # Check if it's a bulk file (starts with HOD_Bulk_Approval_)
        if doc_path.startswith('HOD_Bulk_Approval_'):
            return {
                'path': doc_path,
                'original_name': doc_path,
                'is_bulk': True
            }
        # Check if it's a bulk file (starts with bulk_)
        elif doc_path.startswith('bulk_'):
            # For bulk files, extract the original filename
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

@app.route("/api/get_annual_target/<int:kpi_id>/<int:year>")
def get_annual_target(kpi_id, year):
    """Get annual target for a KPI for a specific year from annualtarget_info"""
    try:
        # Get month from request if provided, default to current month
        month = request.args.get('month', 'MARCH', type=str)
        
        # Calculate financial year based on selected month
        financial_year = get_financial_year_from_month_string(year, month)
        
        # Check if year-specific target exists in annualtarget_info
        result = db.session.execute(
            db.text("""
                SELECT annual_target 
                FROM annualtarget_info 
                WHERE ref_id = :kpi_id AND year = :financial_year
            """),
            {"kpi_id": kpi_id, "financial_year": financial_year}
        ).fetchone()
        
        if result and result.annual_target is not None:
            return jsonify({
                "success": True,
                "annual_target": float(result.annual_target),
                "year": financial_year,
                "selected_year": year,
                "selected_month": month,
                "financial_year": get_financial_year_display(financial_year),
                "source": "annualtarget_info"
            })
        
        # No target found - return 0 as default
        return jsonify({
            "success": True,
            "annual_target": 0,
            "year": financial_year,
            "selected_year": year,
            "selected_month": month,
            "financial_year": get_financial_year_display(financial_year),
            "source": "default",
            "message": "No target found for this KPI and financial year"
        })
        
    except Exception as e:
        print(f"Error fetching annual target: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/hod/forward_all", methods=["POST"])
def hod_forward_all():
    """Forward all submitted KPIs for the selected month/year to Nodal Officer"""
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Login required"}), 401
    
    if session["role"] != "LEVEL2":
        return jsonify({"success": False, "message": "Access Denied"}), 403
    
    try:
        selected_month = request.form.get("month")
        selected_year = request.form.get("year")
        
        if not selected_month or not selected_year:
            return jsonify({"success": False, "message": "Month and Year are required"}), 400
        
        try:
            selected_year = int(selected_year)
        except ValueError:
            return jsonify({"success": False, "message": "Invalid year"}), 400
        
        department_id = session["department_id"]
        
        # Get all SUBMITTED KPIs for this department, month, and year
        result = db.session.execute(
            db.text("""
                SELECT id, kpi_id 
                FROM monthly_data 
                WHERE status = 'SUBMITTED'
                AND kpi_id IN (
                    SELECT id FROM kpis WHERE department_id = :department_id
                )
                AND UPPER(month) = UPPER(:month)
                AND year = :year
            """),
            {
                "department_id": department_id,
                "month": selected_month,
                "year": selected_year
            }
        )
        
        submitted_kpis = result.fetchall()
        
        if not submitted_kpis:
            return jsonify({"success": False, "message": "No submitted KPIs found to forward"}), 400
        
        # Check if all submitted KPIs have uploaded documents
        missing_docs = []
        for kpi in submitted_kpis:
            doc_info = get_document_info(kpi.id)
            if not doc_info:
                # Get KPI name for better error message
                kpi_name_result = db.session.execute(
                    db.text("SELECT kpi_name FROM kpis WHERE id = :id"),
                    {"id": kpi.kpi_id}
                ).fetchone()
                kpi_name = kpi_name_result.kpi_name if kpi_name_result else f"KPI #{kpi.kpi_id}"
                missing_docs.append(f"{kpi_name} (ID: {kpi.id})")
        
        if missing_docs:
            return jsonify({
                "success": False, 
                "message": f"The following KPIs do not have signed documents uploaded:\n{', '.join(missing_docs)}\n\nPlease upload signed documents for all KPIs before forwarding."
            }), 400
        
        # Update all submitted KPIs to APPROVED status
        approved_count = 0
        for kpi in submitted_kpis:
            db.session.execute(
                db.text("""
                    UPDATE monthly_data
                    SET status = 'APPROVED'
                    WHERE id = :id AND status = 'SUBMITTED'
                """),
                {"id": kpi.id}
            )
            approved_count += 1
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "message": f"✅ {approved_count} KPI(s) forwarded to Nodal Officer successfully!"
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"Error in hod_forward_all: {str(e)}")
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500

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

    # Calculate financial year based on selected month
    financial_year = get_financial_year_from_month_string(selected_year, selected_month)

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
                COALESCE(at.annual_target, 0) as annual_target,
                COALESCE(k.unit, '') as unit
            FROM monthly_data md
            JOIN kpis k ON md.kpi_id = k.id
            LEFT JOIN annualtarget_info at ON at.ref_id = k.id AND at.year = :financial_year
            WHERE md.status = 'FORWARDED_TO_DRM'
            AND md.month = :month
            AND md.year = :year
            ORDER BY md.created_at DESC
        """),
        {
            "month": selected_month,
            "year": selected_year,
            "financial_year": financial_year
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
            'annual_target_val': annual_target,
            'financial_year_display': get_financial_year_display(financial_year)
        }
        rows_list.append(row_dict)

    return render_template(
        "drm.html",
        rows=rows_list,
        selected_month=selected_month,
        selected_year=selected_year,
        financial_year_display=get_financial_year_display(financial_year)
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

    # Calculate financial year based on selected month
    financial_year = get_financial_year_from_month_string(selected_year, selected_month)

    # Query for KPIs with their current data and year-specific annual target
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
                prev.cumulative_performance AS previous_year_cumulative,
                COALESCE(at.annual_target, 0) as display_annual_target
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
            LEFT JOIN annualtarget_info at
            ON at.ref_id = k.id
            AND at.year = :financial_year
            WHERE k.department_id = :dept_id
            ORDER BY
                k.display_order,
                k.id
        """),
        {
            "user_id": session["user_id"],
            "month": selected_month,
            "year": selected_year,
            "previous_year": selected_year - 1,
            "dept_id": user_department,
            "financial_year": financial_year
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
                COALESCE(at.annual_target, 0) as display_annual_target,
                k.section_name
            FROM monthly_data md
            JOIN kpis k ON md.kpi_id = k.id
            LEFT JOIN annualtarget_info at ON at.ref_id = k.id AND at.year = :financial_year
            WHERE md.entered_by = :user_id
            AND md.status = 'RETURNED'
            AND md.month = :month
            AND md.year = :year
            ORDER BY md.created_at DESC
        """),
        {
            "user_id": session["user_id"],
            "month": selected_month,
            "year": selected_year,
            "financial_year": financial_year
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
        
        # Validate mandatory fields for submission
        validation_errors = []
        missing_fields = []
        
        for kpi in kpis:
            if kpi.department_id != user_department:
                continue
                
            month_value = request.form.get(f"month_{kpi.id}")
            cumulative_value = request.form.get(f"cum_{kpi.id}")
            prev_cum_value = request.form.get(f"prev_cum_{kpi.id}")
            prev_year_value = request.form.get(f"prev_{kpi.id}")
            
            # Check if any field is empty or None
            if action == "submit":
                # All four fields must have values for submission
                if month_value is None or month_value == "":
                    missing_fields.append(f"Performance For Month - {kpi.kpi_name}")
                if cumulative_value is None or cumulative_value == "":
                    missing_fields.append(f"Cumulative (YTD) - {kpi.kpi_name}")
                if prev_year_value is None or prev_year_value == "":
                    missing_fields.append(f"Prev Year Value - {kpi.kpi_name}")
                if prev_cum_value is None or prev_cum_value == "":
                    missing_fields.append(f"Prev Year Cumulative - {kpi.kpi_name}")
        
        # If there are missing fields, return error
        if missing_fields:
            submission_error = "❌ Cannot submit: The following fields are empty:<br><ul>"
            for field in missing_fields:
                submission_error += f"<li>{field}</li>"
            submission_error += "</ul>Please fill all mandatory fields before submitting."
            
            # Re-render with error message
            return render_template(
                "department_form.html",
                kpis=kpis,
                returned_kpis=returned_kpis,
                returned_kpi_ids=returned_kpi_ids,
                user_department=session["department_id"],
                submission_error=submission_error,
                selected_month=selected_month,
                selected_year=selected_year,
                financial_year_display=get_financial_year_display(financial_year)
            )

        # Process the form data
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

            # For drafts, we allow partial data; for submit, we already validated
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
                    COALESCE(at.annual_target, 0) as display_annual_target,
                    k.section_name
                FROM monthly_data md
                JOIN kpis k ON md.kpi_id = k.id
                LEFT JOIN annualtarget_info at ON at.ref_id = k.id AND at.year = :financial_year
                WHERE md.entered_by = :user_id
                AND md.status = 'RETURNED'
                AND md.month = :month
                AND md.year = :year
                ORDER BY md.created_at DESC
            """),
            {
                "user_id": session["user_id"],
                "month": selected_month,
                "year": selected_year,
                "financial_year": financial_year
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
            selected_year=selected_year,
            financial_year_display=get_financial_year_display(financial_year)
        )

    return render_template(
        "department_form.html",
        kpis=kpis,
        returned_kpis=returned_kpis,
        returned_kpi_ids=returned_kpi_ids,
        user_department=session["department_id"],
        selected_month=selected_month,
        selected_year=selected_year,
        financial_year_display=get_financial_year_display(financial_year)
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

    # Calculate financial year based on selected month
    financial_year = get_financial_year_from_month_string(selected_year, selected_month)

    # Query to get all submitted KPIs with previous year data and year-specific targets
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
                COALESCE(at.annual_target, 0) as annual_target,
                k.section_name,
                d.dept_name
            FROM monthly_data md
            JOIN kpis k ON md.kpi_id = k.id
            JOIN departments d ON k.department_id = d.id
            LEFT JOIN annualtarget_info at ON at.ref_id = k.id AND at.year = :financial_year
            WHERE md.status = 'SUBMITTED'
            AND k.department_id = :department_id
            AND UPPER(md.month) = UPPER(:month)
            AND md.year = :year
            ORDER BY k.display_order, k.id
        """),
        {
            "department_id": department_id,
            "month": selected_month,
            "year": selected_year,
            "financial_year": financial_year
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
            'cumulative_val': cumulative_val,
            'financial_year_display': get_financial_year_display(financial_year)
        }
        rows_list.append(row_dict)
    
    return render_template(
        "hod_review.html",
        rows=rows_list,
        selected_month=selected_month,
        selected_year=selected_year,
        financial_year_display=get_financial_year_display(financial_year)
    )

# ============ UPDATED PDF GENERATION FUNCTIONS WITH TABLE FORMAT ============

@app.route("/hod/download_template/<int:monthly_data_id>")
def hod_download_template(monthly_data_id):
    """Download a PDF template for HOD to sign with KPI data in table format"""
    if "user_id" not in session:
        return redirect("/login")
    
    if session["role"] != "LEVEL2":
        return "Access Denied"
    
    try:
        # Fetch KPI data with year-specific target
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
                    COALESCE(at.annual_target, 0) as annual_target,
                    d.dept_name,
                    u.username as entered_by_name
                FROM monthly_data md
                JOIN kpis k ON md.kpi_id = k.id
                JOIN departments d ON k.department_id = d.id
                JOIN users u ON md.entered_by = u.id
                LEFT JOIN annualtarget_info at ON at.ref_id = k.id AND at.year = md.year
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
        
        # Table header style
        table_header_style = ParagraphStyle(
            'TableHeader',
            parent=styles['Normal'],
            fontSize=9,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold',
            textColor=colors.white
        )
        
        # Table cell style
        table_cell_style = ParagraphStyle(
            'TableCell',
            parent=styles['Normal'],
            fontSize=9,
            alignment=TA_CENTER
        )
        
        # Add header
        story.append(Paragraph("INDIAN RAILWAYS", title_style))
        story.append(Paragraph("Palakkad Division - Southern Railway", subtitle_style))
        story.append(Paragraph("HOD Approval Form", subtitle_style))
        story.append(Spacer(1, 20))
        
        # Create KPI details table
        table_data = [
            ['Parameter', 'Value'],
            ['KPI Name', result.kpi_name],
            ['Department', result.dept_name],
            ['Month/Year', f"{result.month} {result.year}"],
            ['Monthly Performance', f"{result.performance_month if result.performance_month else 'N/A'} {result.unit if result.unit else ''}"],
            ['Cumulative Performance', f"{result.cumulative_performance if result.cumulative_performance else 'N/A'} {result.unit if result.unit else ''}"],
            ['Annual Target', f"{result.annual_target if result.annual_target else 'N/A'} {result.unit if result.unit else ''}"],
            ['Entered By', result.entered_by_name],
            ['Remarks', result.remarks if result.remarks else 'No remarks provided']
        ]
        
        # Create table with proper styling
        kpi_table = Table(table_data, colWidths=[2.5*inch, 3.5*inch])
        kpi_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#003366')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('BACKGROUND', (0, 1), (-1, -1), colors.whitesmoke),
        ]))
        
        story.append(kpi_table)
        story.append(Spacer(1, 20))
        
        # Add signature section
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
    """Bulk download template for HOD approval with month and year from report"""
    if "user_id" not in session:
        return jsonify({
            "success": False,
            "message": "Login required"
        }), 401

    if session.get("role") != "LEVEL2":
        return jsonify({
            "success": False,
            "message": "Access denied"
        }), 403

    try:
        data = request.get_json() or {}
        ids = data.get("ids", [])

        if not ids:
            return jsonify({
                "success": False,
                "message": "No KPI IDs provided"
            }), 400

        ids = [int(x) for x in ids]

        # Get the first KPI to extract month/year from the report
        first_result = db.session.execute(
            db.text("""
                SELECT md.month, md.year
                FROM monthly_data md
                WHERE md.id = :id
            """),
            {"id": ids[0]}
        ).fetchone()

        if not first_result:
            return jsonify({
                "success": False,
                "message": "Could not determine month/year"
            }), 400

        report_month = first_result.month.upper()
        report_year = first_result.year

        # Get department name
        dept_result = db.session.execute(
            db.text("""
                SELECT dept_name 
                FROM departments 
                WHERE id = :dept_id
            """),
            {"dept_id": session["department_id"]}
        ).fetchone()
        
        department_name = dept_result.dept_name if dept_result else "Unknown"

        placeholders = ",".join(
            f":id{i}" for i in range(len(ids))
        )

        params = {
            f"id{i}": value
            for i, value in enumerate(ids)
        }

        params["dept_id"] = session["department_id"]

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
                    COALESCE(at.annual_target, 0) AS annual_target,
                    d.dept_name,
                    u.username AS entered_by_name
                FROM monthly_data md
                JOIN kpis k ON md.kpi_id = k.id
                JOIN departments d ON k.department_id = d.id
                JOIN users u ON md.entered_by = u.id
                LEFT JOIN annualtarget_info at
                ON at.ref_id = k.id AND at.year = md.year
                WHERE md.id IN ({placeholders})
                AND md.status='SUBMITTED'
                AND k.department_id=:dept_id
                ORDER BY k.display_order, k.id
            """),
            params
        ).fetchall()

        if not results:
            return jsonify({
                "success": False,
                "message": "No valid KPIs found"
            }), 400

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=40,
            rightMargin=40,
            topMargin=40,
            bottomMargin=40
        )

        styles = getSampleStyleSheet()
        story = []

        title_style = ParagraphStyle(
            "Title",
            parent=styles["Heading1"],
            fontSize=16,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#003366")
        )

        subtitle_style = ParagraphStyle(
            "Subtitle",
            parent=styles["Heading2"],
            fontSize=12,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#004080")
        )

        normal_style = ParagraphStyle(
            "Normal",
            parent=styles["Normal"],
            fontSize=8
        )

        bold_style = ParagraphStyle(
            "Bold",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10
        )

        # Header with report month/year and department
        story.append(Paragraph("INDIAN RAILWAYS", title_style))
        story.append(Paragraph("Palakkad Division - Southern Railway", subtitle_style))
        story.append(Paragraph(f"HOD Bulk Approval Form - {department_name} - {report_month} {report_year}", subtitle_style))
        story.append(Spacer(1, 20))

        table_data = [[
            "S.No",
            "KPI Name",
            "Department",
            "Month/Year",
            "Monthly Perf.",
            "Cumulative Perf.",
            "Annual Target",
            "Remarks"
        ]]

        for index, row in enumerate(results, start=1):
            unit = row.unit or ""
            table_data.append([
                str(index),
                row.kpi_name,
                row.dept_name,
                f"{row.month}/{row.year}",
                f"{row.performance_month or 'N/A'} {unit}",
                f"{row.cumulative_performance or 'N/A'} {unit}",
                f"{row.annual_target or 'N/A'} {unit}",
                row.remarks or "-"
            ])

        main_table = Table(
            table_data,
            colWidths=[
                0.5*inch,   # S.No
                4.8*inch,   # KPI Name
                1.0*inch,   # Department
                1.0*inch,   # Month/Year
                1.0*inch,   # Monthly Perf
                1.2*inch,   # Cumulative Perf
                1.1*inch,   # Annual Target
                0.8*inch    # Remarks
            ]
        )

        table_style = [
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#003366')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 1, colors.grey),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4)
        ]

        for i in range(1, len(table_data)):
            bg = colors.HexColor('#f0f4f9') if i % 2 else colors.white
            table_style.append(('BACKGROUND', (0, i), (-1, i), bg))

        main_table.setStyle(TableStyle(table_style))
        story.append(main_table)
        story.append(Spacer(1, 20))

        # HOD signing section
        story.append(Paragraph("<b>HOD Approval</b>", bold_style))
        story.append(Spacer(1, 10))
        story.append(Paragraph("I hereby approve the above KPI performance data.", normal_style))
        story.append(Spacer(1, 15))

        signature_table = Table(
            [
                ["Signature :", "________________________"],
                ["Name :", "________________________"],
                ["Designation :", "Head of Department"],
                ["Date :", "________________________"],
                ["Place :", "________________________"]
            ],
            colWidths=[2*inch, 3*inch]
        )

        signature_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6)
        ]))

        story.append(signature_table)
        story.append(Spacer(1, 20))
        story.append(Paragraph(f"<i>Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>", normal_style))

        doc.build(story)

        pdf = buffer.getvalue()
        buffer.close()

        # Generate filename with report month, year, and department
        # Format: HOD_Bulk_Approval_DEPT_MONTH_YEAR.pdf
        # Clean department name for filename (replace spaces with underscores)
        clean_dept_name = department_name.replace(" ", "_")
        filename = f"HOD_Bulk_Approval_{clean_dept_name}_{report_month}_{report_year}.pdf"

        return send_file(
            BytesIO(pdf),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        print(f"Error in bulk download template: {str(e)}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500

# ============ END UPDATED PDF GENERATION FUNCTIONS ============

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
    """HOD uploads a single signed PDF for multiple KPIs with report month/year/department filename"""
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
        
        # Get the report month/year from the first KPI
        first_result = db.session.execute(
            db.text("""
                SELECT md.month, md.year
                FROM monthly_data md
                WHERE md.id = :id
            """),
            {"id": kpi_ids[0]}
        ).fetchone()
        
        if not first_result:
            return jsonify({
                "success": False, 
                "message": "Could not determine report month/year"
            }), 400
        
        report_month = first_result.month.upper()
        report_year = first_result.year
        
        # Get department name
        dept_result = db.session.execute(
            db.text("""
                SELECT dept_name 
                FROM departments 
                WHERE id = :dept_id
            """),
            {"dept_id": session["department_id"]}
        ).fetchone()
        
        department_name = dept_result.dept_name if dept_result else "Unknown"
        
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
        
        # Generate filename with report month, year, and department
        # Format: HOD_Bulk_Approval_DEPT_MONTH_YEAR.pdf
        clean_dept_name = department_name.replace(" ", "_")
        ext = file.filename.rsplit('.', 1)[1].lower() if '.' in file.filename else 'pdf'
        unique_filename = f"HOD_Bulk_Approval_{clean_dept_name}_{report_month}_{report_year}.{ext}"
        
        # If file already exists, add a counter
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        counter = 1
        while os.path.exists(file_path):
            unique_filename = f"HOD_Bulk_Approval_{clean_dept_name}_{report_month}_{report_year}_{counter}.{ext}"
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            counter += 1
        
        # Save the file
        file.save(file_path)
        
        # Associate the same document with all KPIs
        for kpi_id in kpi_ids:
            # Delete existing document if any (including mapping)
            existing_doc = get_document_path(kpi_id)
            if existing_doc:
                existing_path = os.path.join(app.config['UPLOAD_FOLDER'], existing_doc)
                if os.path.exists(existing_path) and existing_doc != unique_filename:
                    try:
                        os.remove(existing_path)
                    except:
                        pass
                # Delete existing mapping file
                existing_mapping = os.path.join(app.config['UPLOAD_FOLDER'], f"bulk_mapping_{kpi_id}.txt")
                if os.path.exists(existing_mapping):
                    try:
                        os.remove(existing_mapping)
                    except:
                        pass
            
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
            "filename": unique_filename
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

    # Calculate financial year based on selected month
    financial_year = get_financial_year_from_month_string(selected_year, selected_month)

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
                COALESCE(at.annual_target, 0) as annual_target,
                k.section_name,
                d.dept_name
            FROM monthly_data md
            JOIN kpis k ON md.kpi_id = k.id
            JOIN departments d ON k.department_id = d.id
            LEFT JOIN annualtarget_info at ON at.ref_id = k.id AND at.year = :financial_year
            WHERE md.status = 'APPROVED'
            AND UPPER(md.month) = UPPER(:month)
            AND md.year = :year
            ORDER BY k.display_order, k.id
        """),
        {
            "month": selected_month,
            "year": selected_year,
            "financial_year": financial_year
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
            'annual_target_val': annual_target,
            'financial_year_display': get_financial_year_display(financial_year)
        }
        rows_list.append(row_dict)

    return render_template(
        "nodal.html",
        rows=rows_list,
        selected_month=selected_month,
        selected_year=selected_year,
        financial_year_display=get_financial_year_display(financial_year)
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

    # Calculate financial year based on selected month
    financial_year = get_financial_year_from_month_string(selected_year, selected_month)

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
                COALESCE(at.annual_target, 0) as annual_target,
                k.section_name,
                d.dept_name
            FROM monthly_data md
            JOIN kpis k ON md.kpi_id = k.id
            JOIN departments d ON k.department_id = d.id
            LEFT JOIN annualtarget_info at ON at.ref_id = k.id AND at.year = :financial_year
            WHERE md.status = 'FORWARDED_TO_ADRM'
            AND UPPER(md.month) = UPPER(:month)
            AND md.year = :year
            ORDER BY k.display_order, k.id
        """),
        {
            "month": selected_month,
            "year": selected_year,
            "financial_year": financial_year
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
            'annual_target_val': annual_target,
            'financial_year_display': get_financial_year_display(financial_year)
        }
        rows_list.append(row_dict)

    return render_template(
        "adrm.html",
        rows=rows_list,
        selected_month=selected_month,
        selected_year=selected_year,
        financial_year_display=get_financial_year_display(financial_year)
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
        # Get the selected financial year from the form
        financial_year = request.form.get("financial_year", datetime.now().year)
        try:
            financial_year = int(financial_year)
        except ValueError:
            financial_year = datetime.now().year
        
        # Check if record exists for this financial year
        existing = db.session.execute(
            db.text("""
                SELECT * FROM annualtarget_info 
                WHERE ref_id = :ref_id AND year = :year
            """),
            {"ref_id": id, "year": financial_year}
        ).fetchone()
        
        if existing:
            # Update existing
            db.session.execute(
                db.text("""
                    UPDATE annualtarget_info
                    SET annual_target = :annual_target
                    WHERE ref_id = :ref_id AND year = :year
                """),
                {
                    "annual_target": annual_target,
                    "ref_id": id,
                    "year": financial_year
                }
            )
        else:
            # Insert new
            db.session.execute(
                db.text("""
                    INSERT INTO annualtarget_info (ref_id, year, annual_target)
                    VALUES (:ref_id, :year, :annual_target)
                """),
                {
                    "ref_id": id,
                    "year": financial_year,
                    "annual_target": annual_target
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
    app.run(host="0.0.0.0", port=5000, debug=True)
