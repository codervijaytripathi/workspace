"""
MP Bhoj PDF -> Excel + Student Lookup.

Bulk lookup is intentionally sequential and uses the official MP Bhoj form.
No CAPTCHA/authentication/rate-limit bypass is attempted.
"""
import sys
import uuid
import logging
import threading
import traceback
import os
from pathlib import Path

from flask import Flask, request, render_template, send_file, jsonify
from werkzeug.exceptions import HTTPException

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "error_log.txt"
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
DEBUG_DIR = BASE_DIR / "debug"
for d in (UPLOAD_DIR, OUTPUT_DIR, DEBUG_DIR):
    d.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("mp_bhoj_app")

try:
    from extractor import extract_pdf_to_dataframe, save_to_excel
    import mobile_fetcher
except Exception:
    logger.error("Startup import fail hua:\n" + traceback.format_exc())
    raise

app = Flask(__name__)
APP_VERSION = "bulk-v4"
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

JOBS = {}
MOBILE_JOBS = {}
MOBILE_STOP_EVENTS = {}

FIELD_LABELS = {
    "Candidate Name": "Name",
    "Father's Name": "Father's Name",
    "Mobile No": "Mobile Number",
    "Enrollment No": "Enrollment Number",
    "Course": "Course",
    "Date of Birth": "Date of Birth",
    "Study Centre": "Study Centre",
    "Course Type": "Course Type",
    "Status": "Status",
}


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(Exception)
def handle_any_error(e):
    if isinstance(e, HTTPException):
        return e
    logger.error(f"UNHANDLED ERROR on {request.path}:\n" + traceback.format_exc())
    return jsonify({"error": f"Server error: {e}. Poora detail error_log.txt me hai."}), 500


# ----------------------------------------------------------------------
# Existing PDF -> Excel routes
# ----------------------------------------------------------------------
def process_job(job_id, pdf_path):
    JOBS[job_id]["status"] = "processing"
    try:
        def progress_cb(current, total):
            JOBS[job_id]["current_page"] = current
            JOBS[job_id]["total_pages"] = total

        df, info = extract_pdf_to_dataframe(str(pdf_path), progress_callback=progress_cb)
        if df.empty:
            JOBS[job_id].update({"status": "error", "error": "Koi record extract nahi hua. PDF expected MP Bhoj format se match nahi karta."})
            return
        output_filename = f"{job_id}.xlsx"
        save_to_excel(df, str(OUTPUT_DIR / output_filename))
        JOBS[job_id].update({
            "status": "done", "info": info, "output_file": output_filename,
            "preview": df.head(15).to_dict(orient="records"), "columns": list(df.columns),
        })
    except Exception as e:
        JOBS[job_id].update({"status": "error", "error": str(e), "traceback": traceback.format_exc()})

@app.route("/")
def index():
    return jsonify({
        "ok": True,
        "service": "mp-bhoj-backend"
    })


@app.route("/upload", methods=["POST", "OPTIONS"])
def upload():
    if request.method == "OPTIONS":
        return "", 204
    if "pdf_file" not in request.files:
        return jsonify({"error": "Koi file nahi mili"}), 400
    file = request.files["pdf_file"]
    if not file.filename:
        return jsonify({"error": "Koi file select nahi ki"}), 400
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Sirf .pdf file allowed hai"}), 400
    job_id = uuid.uuid4().hex[:12]
    pdf_path = UPLOAD_DIR / f"{job_id}.pdf"
    file.save(str(pdf_path))
    JOBS[job_id] = {"status": "queued", "current_page": 0, "total_pages": 0}
    threading.Thread(target=process_job, args=(job_id, pdf_path), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    resp = {"status": job["status"], "current_page": job.get("current_page", 0), "total_pages": job.get("total_pages", 0)}
    if job["status"] == "done":
        resp.update({"info": job["info"], "preview": job["preview"], "columns": job["columns"], "download_url": request.host_url.rstrip("/") + f"/download/{job_id}"})
    if job["status"] == "error":
        resp["error"] = job.get("error", "Unknown error")
    return jsonify(resp)


@app.route("/download/<job_id>")
def download(job_id):
    job = JOBS.get(job_id)
    if not job or job.get("status") != "done":
        return "File abhi ready nahi hai", 404
    return send_file(str(OUTPUT_DIR / job["output_file"]), as_attachment=True, download_name="MP_Bhoj_Result_Converted.xlsx")


# ----------------------------------------------------------------------
# Student Lookup - Single
# ----------------------------------------------------------------------
def _student_test_impl():
    data = request.get_json(silent=True) or {}
    enrollment_no = str(data.get("enrollment_no") or "").strip()
    course_type = mobile_fetcher.normalize_course_type(data.get("course_type", "UG"))
    selected_fields = data.get("selected_fields")
    if selected_fields is not None and not isinstance(selected_fields, list):
        selected_fields = None
    headless = bool(data.get("headless", True))
    if not enrollment_no:
        return jsonify({"success": False, "error": "Enrollment number khali hai"}), 400

    screenshot_name = f"test_{uuid.uuid4().hex[:8]}.png"
    screenshot_path = DEBUG_DIR / screenshot_name
    try:
        result = mobile_fetcher.test_single(
            enrollment_no, course_type=course_type, selected_fields=selected_fields,
            headless=headless, screenshot_path=screenshot_path,
        )
        result["Course Type"] = course_type
        return jsonify({"success": True, "result": result})
    except Exception as e:
        logger.error("Student lookup error:\n" + traceback.format_exc())
        shot_url = request.host_url.rstrip("/") + f"/debug/{screenshot_name}" if screenshot_path.exists() else None
        return jsonify({"success": False, "error": str(e), "screenshot_url": shot_url}), 500


@app.route("/mobile/test", methods=["POST", "OPTIONS"])
def mobile_test():
    if request.method == "OPTIONS":
        return "", 204
    return _student_test_impl()


@app.route("/api/mobile/test", methods=["POST", "OPTIONS"])
def api_mobile_test():
    if request.method == "OPTIONS":
        return "", 204
    return _student_test_impl()


@app.route("/debug/<filename>")
def debug_screenshot(filename):
    path = DEBUG_DIR / filename
    if not path.exists():
        return "Not found", 404
    return send_file(str(path))


# ----------------------------------------------------------------------
# Bulk helpers
# ----------------------------------------------------------------------
def _write_partial_files(job_id, results):
    if not results:
        return
    xlsx_path = OUTPUT_DIR / f"{job_id}_mobile.xlsx"
    mobile_fetcher.save_results_to_excel(results, str(xlsx_path))
    try:
        _write_pdf(results, OUTPUT_DIR / f"{job_id}_mobile.pdf")
    except Exception:
        logger.warning("Partial PDF save fail hua:\n" + traceback.format_exc())


def _find_pdf_font():
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        str(BASE_DIR / "DejaVuSans.ttf"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def _write_pdf(results, path):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font_name = "Helvetica"
    font_path = _find_pdf_font()
    if font_path:
        try:
            if "DejaVuSans" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("DejaVuSans", font_path))
            font_name = "DejaVuSans"
        except Exception:
            pass

    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=8*mm, rightMargin=8*mm, topMargin=9*mm, bottomMargin=9*mm)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontName=font_name, fontSize=7.5, leading=9, alignment=TA_LEFT)
    title = ParagraphStyle("title", parent=styles["Title"], fontName=font_name, fontSize=15, leading=18, spaceAfter=5)
    meta = ParagraphStyle("meta", parent=styles["BodyText"], fontName=font_name, fontSize=8, leading=10, spaceAfter=7)

    keys = []
    for row in results:
        for k in row.keys():
            if k not in keys:
                keys.append(k)
    # Keep identification/status first, then requested data.
    preferred = ["Enrollment No", "Candidate Name", "Father's Name", "Mobile No", "Course Type", "Course", "Date of Birth", "Study Centre", "Status"]
    keys = [k for k in preferred if k in keys] + [k for k in keys if k not in preferred]

    data = [[Paragraph(str(k), body) for k in keys]]
    for row in results:
        data.append([Paragraph(str(row.get(k, "") or ""), body) for k in keys])

    table = Table(data, repeatRows=1, colWidths=None, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), font_name),
        ("FONTSIZE", (0,0), (-1,-1), 7.5),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#17191d")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.35, colors.HexColor("#cfd3d8")),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 4),
        ("RIGHTPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ]))
    story = [Paragraph("MP Bhoj University — Student Lookup", title), Paragraph(f"Records: {len(results)}", meta), table]
    doc.build(story)


def _job_response(job_id, job):
    status = job.get("status", "unknown")
    response = {
        "success": True,
        "job_id": job_id,
        "status": status,
        "current": job.get("current", 0),
        "total": job.get("total", 0),
        "found": job.get("found", 0),
        "failed": job.get("failed", 0),
        "current_number": job.get("current_number"),
        "course_type": job.get("course_type", "UG"),
        "total_processed": job.get("total_processed", len(job.get("results", []))),
        "preview": job.get("preview", []),
        "failed_numbers": job.get("failed_numbers", []),
        "download_url": request.host_url.rstrip("/") + f"/mobile/download/{job_id}" if job.get("output_file") else None,
        "pdf_download_url": request.host_url.rstrip("/") + f"/mobile/download-pdf/{job_id}" if job.get("pdf_output_file") else None,
        "app_version": APP_VERSION,
    }
    if status == "error":
        response["error"] = job.get("error", "Unknown error")
    if status == "stopping":
        response["message"] = "Stop requested. Current student complete hote hi batch ruk jayega."
    return response


def process_mobile_job(job_id, input_path, course_type, selected_fields, headless):
    job = MOBILE_JOBS[job_id]
    job["status"] = "processing"
    driver = None
    results = []
    stop_event = MOBILE_STOP_EVENTS[job_id]
    try:
        numbers = mobile_fetcher.read_enrollment_numbers(input_path)
        if not numbers:
            job.update({"status": "error", "error": "Koi enrollment/roll number nahi mila is file me."})
            return
        course_type = mobile_fetcher.normalize_course_type(course_type)
        job.update({"total": len(numbers), "course_type": course_type})

        driver = mobile_fetcher.setup_driver(headless=headless)
        mobile_fetcher.setup_form(driver, course_type=course_type)

        for i, enrollment_no in enumerate(numbers, 1):
            if stop_event.is_set():
                break
            job["current"] = i
            job["current_number"] = enrollment_no
            try:
                raw_result = mobile_fetcher.fetch_one(driver, enrollment_no)
                result = mobile_fetcher.filter_result(raw_result, selected_fields)
                result["Course Type"] = course_type
                if raw_result.get("Status") == "Found":
                    job["found"] += 1
                else:
                    job["failed"] += 1
                    job["failed_numbers"].append(enrollment_no)
            except Exception as e:
                result = {"Course Type": course_type, "Enrollment No": enrollment_no, "Status": f"Error: {e}"}
                job["failed"] += 1
                job["failed_numbers"].append(enrollment_no)
                logger.warning("Bulk lookup failed for %s: %s", enrollment_no, e)

            results.append(result)
            job["results"] = results
            job["output_file"] = f"{job_id}_mobile.xlsx"
            job["pdf_output_file"] = f"{job_id}_mobile.pdf"
            # Exactly 10 rows are kept visible in the live UI.
            job["preview"] = results[-10:]
            job["total_processed"] = len(results)
            # Write after EVERY completed number so a disconnect/stop never loses completed work.
            _write_partial_files(job_id, results)

            if stop_event.is_set():
                break

        if stop_event.is_set() and len(results) < len(numbers):
            job["status"] = "stopped"
            job["current_number"] = None
        else:
            job["status"] = "done"
            job["current_number"] = None

        job["output_file"] = f"{job_id}_mobile.xlsx"
        job["pdf_output_file"] = f"{job_id}_mobile.pdf"
        job["total_processed"] = len(results)
        job["preview"] = results[-10:]
    except Exception as e:
        job.update({"status": "error", "error": str(e), "traceback": traceback.format_exc()})
        logger.error("Bulk mobile job error:\n" + traceback.format_exc())
        # If at least one record was completed, expose partial files even on a backend error.
        if results:
            job["output_file"] = f"{job_id}_mobile.xlsx"
            job["pdf_output_file"] = f"{job_id}_mobile.pdf"
            job["total_processed"] = len(results)
            job["preview"] = results[-10:]
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        job["current_number"] = None if job.get("status") in ("done", "stopped", "error") else job.get("current_number")


@app.route("/mobile/upload", methods=["POST", "OPTIONS"])
def mobile_upload():
    if request.method == "OPTIONS":
        return "", 204
    if "data_file" not in request.files:
        return jsonify({"error": "Koi file nahi mili"}), 400
    file = request.files["data_file"]
    if not file.filename:
        return jsonify({"error": "Koi file select nahi ki"}), 400
    ext = Path(file.filename).suffix.lower()
    if ext not in (".xlsx", ".xls", ".csv", ".pdf"):
        return jsonify({"error": "Sirf .xlsx, .xls, .csv ya .pdf file allowed hai"}), 400

    course_type = mobile_fetcher.normalize_course_type(request.form.get("course_type", "UG"))
    headless = request.form.get("headless", "true").lower() == "true"
    fields_raw = request.form.get("selected_fields", "")
    selected_fields = [x.strip() for x in fields_raw.split(",") if x.strip()]

    job_id = uuid.uuid4().hex[:12]
    input_path = UPLOAD_DIR / f"{job_id}{ext}"
    file.save(str(input_path))
    MOBILE_JOBS[job_id] = {
        "status": "queued",
        "output_file": f"{job_id}_mobile.xlsx",
        "pdf_output_file": f"{job_id}_mobile.pdf", "current": 0, "total": 0, "found": 0, "failed": 0,
        "current_number": None, "course_type": course_type, "selected_fields": selected_fields,
        "results": [], "preview": [], "failed_numbers": [], "total_processed": 0,
    }
    MOBILE_STOP_EVENTS[job_id] = threading.Event()
    threading.Thread(target=process_mobile_job, args=(job_id, input_path, course_type, selected_fields, headless), daemon=True).start()
    return jsonify({"success": True, "job_id": job_id, "course_type": course_type})


@app.route("/mobile/status/<job_id>", methods=["GET", "OPTIONS"])
def mobile_status(job_id):
    if request.method == "OPTIONS":
        return "", 204
    job = MOBILE_JOBS.get(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found"}), 404
    return jsonify(_job_response(job_id, job))


@app.route("/mobile/stop/<job_id>", methods=["POST", "OPTIONS"])
def mobile_stop(job_id):
    if request.method == "OPTIONS":
        return "", 204
    job = MOBILE_JOBS.get(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found"}), 404
    if job.get("status") in ("done", "stopped", "error"):
        return jsonify({"success": True, "status": job.get("status")})
    event = MOBILE_STOP_EVENTS.get(job_id)
    if event:
        event.set()
    job["status"] = "stopping"
    return jsonify({"success": True, "status": "stopping", "message": "Current student complete hote hi batch stop hoga."})


@app.route("/mobile/download/<job_id>")
def mobile_download(job_id):
    job = MOBILE_JOBS.get(job_id)
    path = OUTPUT_DIR / f"{job_id}_mobile.xlsx"
    if not job or not path.exists():
        return "Abhi koi completed record download ke liye available nahi hai", 404
    return send_file(str(path), as_attachment=True, download_name="MP_Bhoj_Student_Results.xlsx")


@app.route("/mobile/download-pdf/<job_id>")
def mobile_download_pdf(job_id):
    job = MOBILE_JOBS.get(job_id)
    path = OUTPUT_DIR / f"{job_id}_mobile.pdf"
    if not job or not path.exists():
        return "Abhi koi completed record PDF me available nahi hai", 404
    return send_file(str(path), as_attachment=True, download_name="MP_Bhoj_Student_Results.pdf")


@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "mp-bhoj-backend", "version": APP_VERSION})


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), threaded=True)
