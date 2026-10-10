import os
import csv
import sqlite3
from functools import wraps
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, g, send_from_directory, flash, send_file, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
# Optional PDF support
try:
    from fpdf import FPDF
    FPDF_AVAILABLE = True
except Exception:
    FPDF_AVAILABLE = False
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")  # change this in production
# ------------------ DATABASE SETUP ------------------ #
DATABASE = os.path.join(app.root_path, "labportal.db")
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
    return db
@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()
def init_db():
    db = get_db()
    c = db.cursor()
    # Users
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        )
    """)
    # Submissions
    c.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT,
            filename TEXT,
            submitted_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Experiments
    c.execute("""
        CREATE TABLE IF NOT EXISTS experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            filename TEXT,
            uploaded_on TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Admin activity logs
    c.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT,
            action TEXT,
            role TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.commit()
    migrate_db()
    print("✅ Database ready!")
def migrate_db():
    db = get_db()
    c = db.cursor()
    cols = [row[1] for row in c.execute("PRAGMA table_info(submissions)").fetchall()]
    def add_col(name, coldef):
        c.execute(f"ALTER TABLE submissions ADD COLUMN {name} {coldef}")
    if "grade"     not in cols: add_col("grade", "TEXT")
    if "feedback"  not in cols: add_col("feedback", "TEXT")
    if "graded_on" not in cols: add_col("graded_on", "TIMESTAMP")
    if "graded_by" not in cols: add_col("graded_by", "TEXT")
    db.commit()
# Existing database is intentionally not initialized or migrated automatically.
# This project expects the existing labportal.db to already be set up.
# ------------------ HELPERS ------------------ #

with app.app_context():
    init_db()

def login_required(role=None):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if 'user' not in session:
                return redirect(url_for('login'))
            if role and session.get('role') != role:
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return wrapper
    return decorator
def log_action(user_email, action, role):
    """Admin-level audit logging helper."""
    db = get_db()
    c = db.cursor()
    c.execute("INSERT INTO logs (user_email, action, role) VALUES (?, ?, ?)",
              (user_email, action, role))
    db.commit()
# ------------------ FILE UPLOAD SETUP ------------------ #
UPLOAD_REPORTS = os.path.join(app.root_path, "uploads", "reports")
UPLOAD_EXPERIMENTS = os.path.join(app.root_path, "uploads", "experiments")
os.makedirs(UPLOAD_REPORTS, exist_ok=True)
os.makedirs(UPLOAD_EXPERIMENTS, exist_ok=True)
ALLOWED_REPORT_EXTS = {'pdf', 'png', 'jpg', 'jpeg'}
ALLOWED_EXP_EXTS    = {'mp4', 'mov', 'avi', 'mkv', 'pdf', 'png', 'jpg', 'jpeg'}
def allowed_file(filename, allowed):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed
# ------------------ ROUTES ------------------ #
@app.route('/')
def home():
    return render_template('index.html')
# ---------- REGISTER ---------- #
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name     = request.form["name"]
        email    = request.form["email"]
        password = request.form["password"]
        confirm  = request.form["confirm_password"]
        role     = request.form["role"]
        # Admin PIN validation
        if role == "admin":
            admin_pin = request.form.get("admin_pin", "")
            if admin_pin != os.environ.get("ADMIN_PIN"):
                return render_template("register.html", error="Invalid Admin PIN. Access denied.")
        if password != confirm:
            return render_template("register.html", error="Passwords do not match")
        db = get_db()
        c = db.cursor()
        c.execute("SELECT 1 FROM users WHERE email=?", (email,))
        if c.fetchone():
            return render_template("register.html", error="Email already exists")
        hashed = generate_password_hash(password)
        c.execute("INSERT INTO users (name, email, password, role) VALUES (?, ?, ?, ?)",
                  (name, email, hashed, role))
        db.commit()
        # Log registration (role-scoped; your policy is admin-only logs, but keeping for completeness is ok)
        # Remove next line if you want strictly admin-only:
        # log_action(email, f"registered as {role}", role)
        return redirect(url_for("login"))
    return render_template("register.html")
# ---------- LOGIN ---------- #
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form['email']
        password = request.form['password']
        role     = request.form['role']
        db = get_db()
        c = db.cursor()
        c.execute("SELECT id, name, email, password, role FROM users WHERE email=? AND role=?",
                  (email, role))
        row = c.fetchone()
        if row:
            stored_hash = row[3]
            if stored_hash == password or check_password_hash(stored_hash, password):
                session['user'] = email
                session['role'] = role
                if role == 'student': return redirect(url_for('student_dash'))
                if role == 'teacher': return redirect(url_for('teacher_dash'))
                return redirect(url_for('admin_dashboard'))
        return render_template('login.html', error='Invalid email, password, or role')
    return render_template('login.html')
# ---------- STUDENT DASHBOARD ---------- #
@app.route('/student_dash')
@login_required('student')
def student_dash():
    db = get_db()
    c = db.cursor()
    # Student's own report history (existing feature)
    c.execute("""
        SELECT filename, submitted_on, grade, feedback, graded_on
          FROM submissions
         WHERE user_email=?
      ORDER BY submitted_on DESC
    """, (session['user'],))
    uploads = c.fetchall()
    # Make teacher-uploaded experiments available to students
    c.execute("""
        SELECT title, filename, uploaded_on
          FROM experiments
      ORDER BY uploaded_on DESC
    """)
    experiments = c.fetchall()
    return render_template(
        'student_dash.html',
        uploads=uploads,
        experiments=experiments
    )
@app.route('/upload_submission', methods=['POST'])
@login_required('student')
def upload_submission():
    file = request.files.get('file')
    if not file or file.filename == '':
        return render_template('student_dash.html', message='Please select a file to upload.')
    if not allowed_file(file.filename, ALLOWED_REPORT_EXTS):
        return render_template('student_dash.html', message='File type not allowed. Upload PDF/PNG/JPG only.')
    safe_name = secure_filename(file.filename)
    path = os.path.join(UPLOAD_REPORTS, safe_name)
    file.save(path)
    db = get_db()
    c = db.cursor()
    c.execute("INSERT INTO submissions (user_email, filename) VALUES (?, ?)",
              (session['user'], safe_name))
    db.commit()
    return redirect(url_for('student_dash'))
# ---------- TEACHER DASHBOARD ---------- #
@app.route('/teacher_dash')
@login_required('teacher')
def teacher_dash():
    db = get_db()
    c = db.cursor()
    c.execute("SELECT title, filename, uploaded_on FROM experiments ORDER BY uploaded_on DESC")
    experiments = c.fetchall()
    c.execute("""
        SELECT user_email, filename, submitted_on, grade, feedback, graded_on
          FROM submissions
      ORDER BY submitted_on DESC
    """)
    submissions = c.fetchall()
    # Template file name is 'tecaherdash.html' in your project
    return render_template('tecaherdash.html', experiments=experiments, submissions=submissions)
@app.route('/upload_experiment', methods=['POST'])
@login_required('teacher')
def upload_experiment():
    title = request.form.get('title', '').strip()
    file  = request.files.get('video')
    if not title:
        flash('Title is required.')
        return redirect(url_for('teacher_dash'))
    if not file or file.filename == '':
        flash('Please select a file to upload.')
        return redirect(url_for('teacher_dash'))
    if not allowed_file(file.filename, ALLOWED_EXP_EXTS):
        flash('Unsupported file type.')
        return redirect(url_for('teacher_dash'))
    safe_name = secure_filename(file.filename)
    path = os.path.join(UPLOAD_EXPERIMENTS, safe_name)
    file.save(path)
    db = get_db()
    c = db.cursor()
    c.execute("INSERT INTO experiments (title, filename) VALUES (?, ?)", (title, safe_name))
    db.commit()
    flash('✅ Experiment uploaded successfully!')
    return redirect(url_for('teacher_dash'))
@app.route('/grade_submission', methods=['POST'])
@login_required('teacher')
def grade_submission():
    student  = request.form.get('student')
    filename = request.form.get('filename')
    grade    = request.form.get('grade')
    feedback = request.form.get('feedback', '').strip()
    if not (student and filename and grade):
        flash('Missing data for grading.')
        return redirect(url_for('teacher_dash'))
    db = get_db()
    c = db.cursor()
    c.execute("""
        UPDATE submissions
           SET grade=?,
               feedback=?,
               graded_on=CURRENT_TIMESTAMP,
               graded_by=?
         WHERE user_email=? AND filename=?
    """, (grade, feedback, session['user'], student, filename))
    db.commit()
    flash('✅ Grade saved!')
    return redirect(url_for('teacher_dash'))
# ---------- ADMIN DASHBOARD ---------- #
@app.route("/admin_dashboard")
@login_required("admin")
def admin_dashboard():
    db = get_db()
    c  = db.cursor()
    c.execute("SELECT id, name, email, role FROM users ORDER BY role, name")
    users = c.fetchall()
    c.execute("SELECT COUNT(*) FROM users WHERE role='student'");  total_students  = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM users WHERE role='teacher'");  total_teachers  = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM submissions");                 total_submissions = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM submissions WHERE grade IS NOT NULL"); graded_reports = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM experiments");                 total_experiments = c.fetchone()[0]
    c.execute("SELECT title, filename, uploaded_on FROM experiments ORDER BY uploaded_on DESC")
    experiments_list = c.fetchall()
    c.execute("SELECT user_email, filename, submitted_on, grade FROM submissions ORDER BY submitted_on DESC")
    submissions_list = c.fetchall()
    c.execute("""
        SELECT user_email, action, role, timestamp
          FROM logs
      ORDER BY timestamp DESC
         LIMIT 50
    """)
    logs = c.fetchall()
    stats = {
        "students": total_students,
        "teachers": total_teachers,
        "submissions": total_submissions,
        "graded": graded_reports,
        "experiments": total_experiments,
        "experiments_list": experiments_list,
        "submissions_list": submissions_list
    }
    return render_template("admin_dash.html", users=users, stats=stats, logs=logs)
# ---------- ADMIN ACTIONS ---------- #
@app.route("/admin/delete_user/<int:user_id>", methods=["POST"])
@login_required("admin")
def delete_user(user_id):
    db = get_db()
    c  = db.cursor()
    c.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    flash("🗑️ User deleted successfully!")
    log_action(session['user'], f"deleted user ID {user_id}", "admin")
    return redirect(url_for("admin_dashboard"))
@app.route("/admin/delete_experiment/<filename>", methods=["POST"])
@login_required("admin")
def delete_experiment(filename):
    db = get_db()
    c  = db.cursor()
    c.execute("DELETE FROM experiments WHERE filename=?", (filename,))
    db.commit()
    file_path = os.path.join(UPLOAD_EXPERIMENTS, filename)
    if os.path.exists(file_path):
        os.remove(file_path)
    flash(f"🎥 Experiment '{filename}' deleted successfully!")
    log_action(session['user'], f"deleted experiment {filename}", "admin")
    return redirect(url_for("admin_dashboard"))
@app.route("/admin/delete_submission/<filename>", methods=["POST"])
@login_required("admin")
def delete_submission(filename):
    db = get_db()
    c  = db.cursor()
    c.execute("DELETE FROM submissions WHERE filename=?", (filename,))
    db.commit()
    file_path = os.path.join(UPLOAD_REPORTS, filename)
    if os.path.exists(file_path):
        os.remove(file_path)
    flash(f"📄 Submission '{filename}' deleted successfully!")
    log_action(session['user'], f"deleted submission {filename}", "admin")
    return redirect(url_for("admin_dashboard"))
@app.route("/admin/export_csv")
@login_required("admin")
def export_csv():
    db = get_db()
    c  = db.cursor()
    data_dir = os.path.join(app.root_path, "exports")
    os.makedirs(data_dir, exist_ok=True)
    filepath = os.path.join(data_dir, "labportal_export.csv")
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["--- USERS ---"])
        c.execute("SELECT id, name, email, role FROM users")
        writer.writerow(["ID", "Name", "Email", "Role"])
        writer.writerows(c.fetchall())
        writer.writerow([])
        writer.writerow(["--- SUBMISSIONS ---"])
        c.execute("SELECT user_email, filename, submitted_on, grade, feedback FROM submissions")
        writer.writerow(["Student Email", "Filename", "Submitted On", "Grade", "Feedback"])
        writer.writerows(c.fetchall())
        writer.writerow([])
        writer.writerow(["--- EXPERIMENTS ---"])
        c.execute("SELECT title, filename, uploaded_on FROM experiments")
        writer.writerow(["Title", "Filename", "Uploaded On"])
        writer.writerows(c.fetchall())
    log_action(session['user'], "exported system CSV report", "admin")
    return send_file(filepath, as_attachment=True, download_name="labportal_export.csv")
# ---------- REAL-TIME LOG FETCHING (AJAX) ---------- #
@app.route("/admin/get_logs")
@login_required("admin")
def get_logs():
    db = get_db()
    c  = db.cursor()
    c.execute("""
        SELECT user_email, action, role, timestamp
          FROM logs
      ORDER BY timestamp DESC
         LIMIT 20
    """)
    logs = c.fetchall()
    return jsonify({"logs": [
        {"user_email": l[0], "action": l[1], "role": l[2], "timestamp": l[3]}
        for l in logs
    ]})
# ---------- FILE SERVING ---------- #
@app.route('/uploads/reports/<path:filename>')
def serve_reports(filename):
    return send_from_directory(UPLOAD_REPORTS, filename)
@app.route('/uploads/experiments/<path:filename>')
def serve_experiments(filename):
    return send_from_directory(UPLOAD_EXPERIMENTS, filename)
# ---------- LOGOUT ---------- #
@app.route('/logout')
def logout():
    # If you want to log only admin logouts, uncomment:
    # if session.get('role') == 'admin':
    #     log_action(session['user'], "logged out", "admin")
    session.clear()
    return redirect(url_for('login'))
# ---------- EXPORT ADMIN LOGS (CSV) ---------- #
@app.route("/admin/export_logs")
@login_required("admin")
def export_logs():
    db = get_db()
    c  = db.cursor()
    c.execute("SELECT user_email, action, role, timestamp FROM logs ORDER BY timestamp DESC")
    logs = c.fetchall()
    export_dir = os.path.join(app.root_path, "exports")
    os.makedirs(export_dir, exist_ok=True)
    csv_path = os.path.join(export_dir, "admin_activity_log.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["User Email", "Action", "Role", "Timestamp"])
        writer.writerows(logs)
    log_action(session['user'], "exported admin logs report", "admin")
    flash("✅ Admin activity logs exported successfully!")
    return send_file(csv_path, as_attachment=True, download_name="admin_activity_log.csv")
# ---------- DATABASE BACKUP (ADMIN ONLY) ---------- #
@app.route("/admin/backup_db")
@login_required("admin")
def backup_db():
    import shutil
    src = os.path.join(app.root_path, "labportal.db")
    backup_dir = os.path.join(app.root_path, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backup_dir, f"labportal_backup_{timestamp}.db")
    shutil.copy(src, dest)
    log_action(session.get("user"), f"created database backup at {timestamp}", "admin")
    flash(f"✅ Database backup created successfully ({timestamp})")
    return redirect(url_for("admin_dashboard"))
# ---------- CLEAR LOGS (ADMIN ONLY) ---------- #
@app.route("/admin/clear_logs", methods=["POST"])
@login_required("admin")
def clear_logs():
    db = get_db()
    c  = db.cursor()
    c.execute("DELETE FROM logs")
    db.commit()
    log_action(session['user'], "cleared activity logs", "admin")
    flash("🧹 All admin logs cleared.")
    return redirect(url_for("admin_dashboard"))
# ---------- EXPORT ADMIN LOGS (PDF) ---------- #
@app.route("/admin/export_logs_pdf")
@login_required("admin")
def export_logs_pdf():
    if not FPDF_AVAILABLE:
        flash("❌ PDF export requires 'fpdf'. Install with: pip install fpdf")
        return redirect(url_for("admin_dashboard"))
    db = get_db()
    c  = db.cursor()
    c.execute("SELECT user_email, action, role, timestamp FROM logs ORDER BY timestamp DESC")
    logs = c.fetchall()
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt="Admin Activity Report", ln=True, align="C")
    pdf.ln(10)
    for log in logs:
        pdf.multi_cell(0, 8, txt=f"{log[3]} - {log[0]} ({log[2]}) → {log[1]}")
    pdf_path = os.path.join(app.root_path, "exports", "admin_activity_log.pdf")
    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
    pdf.output(pdf_path)
    log_action(session['user'], "exported admin logs PDF", "admin")
    flash("📄 Admin activity logs PDF exported successfully!")
    return send_file(pdf_path, as_attachment=True, download_name="admin_activity_log.pdf")
# ------------------ MAIN ------------------ #
if __name__ == "__main__":
    import webbrowser
    try:
        webbrowser.open("http://127.0.0.1:5000")
    except Exception:
        pass
    app.run(debug=True)