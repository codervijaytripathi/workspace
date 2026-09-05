"""
MP Bhoj PDF -> Excel + Student Lookup local Flask app.

Important:
- Single lookup endpoint is available at BOTH:
    POST /mobile/test
    POST /api/mobile/test
  so frontend/API path mismatch no longer causes the previous 405 problem.
- Student lookup supports UG and PG.
- Bulk lookup supports PDF/XLS/XLSX/CSV and selected fields.
"""

import sys
import uuid
import logging
import threading
import traceback
from pathlib import Path

from flask import Flask, request, render_template, send_file, jsonify, url_for
from werkzeug.exceptions import HTTPException

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "error_log.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("mp_bhoj_app")

try:
    from extractor import extract_pdf_to_dataframe, save_to_excel
    import mobile_fetcher
    import whatsapp_module
except Exception:
    logger.error("Startup import fail hua:\n" + traceback.format_exc())
    raise

UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
DEBUG_DIR = BASE_DIR / "debug"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
DEBUG_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

JOBS = {}
MOBILE_JOBS = {}

WA_STATUS = {
    "status": "idle",
    "session_sent": 0,
    "session_limit": whatsapp_module.SESSION_LIMIT,
    "total_sent": 0,
    "total_numbers": 0,
    "current_number": None,
    "error": None,
}
WA_STOP_EVENT = threading.Event()


@app.errorhandler(Exception)
def handle_any_error(e):
    if isinstance(e, HTTPException):
        return e
    logger.error(
        f"UNHANDLED ERROR on {request.path}:\n" + traceback.format_exc()
    )
    return jsonify({
        "error": f"Server error: {e}. Poora detail 'error_log.txt' me hai."
    }), 500


# ----------------------------------------------------------------------
# Existing PDF -> Excel routes
# ----------------------------------------------------------------------

def process_job(job_id, pdf_path):
    JOBS[job_id]["status"] = "processing"
    try:
        def progress_cb(current, total):
            JOBS[job_id]["current_page"] = current
            JOBS[job_id]["total_pages"] = total

        df, info = extract_pdf_to_dataframe(
            str(pdf_path), progress_callback=progress_cb
        )

        if df.empty:
            JOBS[job_id]["status"] = "error"
            JOBS[job_id]["error"] = (
                "Koi record extract nahi hua. PDF expected MP Bhoj format "
                "se match nahi karta."
            )
            return

        output_filename = f"{job_id}.xlsx"
        output_path = OUTPUT_DIR / output_filename
        save_to_excel(df, str(output_path))

        JOBS[job_id].update({
            "status": "done",
            "info": info,
            "output_file": output_filename,
            "preview": df.head(15).to_dict(orient="records"),
            "columns": list(df.columns),
        })

    except Exception as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(e)
        JOBS[job_id]["traceback"] = traceback.format_exc()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
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

    JOBS[job_id] = {
        "status": "queued",
        "current_page": 0,
        "total_pages": 0,
    }

    threading.Thread(
        target=process_job,
        args=(job_id, pdf_path),
        daemon=True,
    ).start()

    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    resp = {
        "status": job["status"],
        "current_page": job.get("current_page", 0),
        "total_pages": job.get("total_pages", 0),
    }

    if job["status"] == "done":
        resp.update({
            "info": job["info"],
            "preview": job["preview"],
            "columns": job["columns"],
            "download_url": url_for("download", job_id=job_id),
        })

    if job["status"] == "error":
        resp["error"] = job.get("error", "Unknown error")

    return jsonify(resp)


@app.route("/download/<job_id>")
def download(job_id):
    job = JOBS.get(job_id)
    if not job or job["status"] != "done":
        return "File abhi ready nahi hai", 404

    return send_file(
        str(OUTPUT_DIR / job["output_file"]),
        as_attachment=True,
        download_name="MP_Bhoj_Result_Converted.xlsx",
    )


# ----------------------------------------------------------------------
# Student Lookup - Single
# ----------------------------------------------------------------------

def _student_test_impl():
    data = request.get_json(silent=True) or {}

    enrollment_no = str(data.get("enrollment_no") or "").strip()
    course_type = mobile_fetcher.normalize_course_type(
        data.get("course_type", "UG")
    )
    selected_fields = data.get("selected_fields")

    if selected_fields is not None and not isinstance(selected_fields, list):
        selected_fields = None

    headless = bool(data.get("headless", True))

    if not enrollment_no:
        return jsonify({"success": False, "error": "Enrollment number khali hai"}), 400

    screenshot_name = f"test_{uuid.uuid4().hex[:8]}.png"
    screenshot_path = DEBUG_DIR / screenshot_name

    try:
        logger.info(
            "Single lookup: enrollment=%s course_type=%s",
            enrollment_no,
            course_type,
        )

        result = mobile_fetcher.test_single(
            enrollment_no,
            course_type=course_type,
            selected_fields=selected_fields,
            headless=headless,
            screenshot_path=screenshot_path,
        )

        result["Course Type"] = course_type
        return jsonify({
            "success": True,
            "result": result,
        })

    except Exception as e:
        logger.error("Student lookup error:\n" + traceback.format_exc())

        shot_url = None
        if screenshot_path.exists():
            shot_url = url_for(
                "debug_screenshot",
                filename=screenshot_name,
            )

        return jsonify({
            "success": False,
            "error": str(e),
            "screenshot_url": shot_url,
        }), 500


@app.route("/mobile/test", methods=["POST", "OPTIONS"])
def mobile_test():
    if request.method == "OPTIONS":
        return "", 204
    return _student_test_impl()


@app.route("/api/mobile/test", methods=["POST", "OPTIONS"])
def api_mobile_test():
    """
    Compatibility alias.
    This prevents the old frontend from receiving HTTP 405 when it calls
    /api/mobile/test.
    """
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
# Student Lookup - Bulk
# ----------------------------------------------------------------------

def process_mobile_job(job_id, input_path, course_type, selected_fields, headless):
    job = MOBILE_JOBS[job_id]
    job["status"] = "processing"

    driver = None
    results = []

    try:
        numbers = mobile_fetcher.read_enrollment_numbers(input_path)

        if not numbers:
            job["status"] = "error"
            job["error"] = "Koi enrollment/roll number nahi mila is file me."
            return

        course_type = mobile_fetcher.normalize_course_type(course_type)

        job.update({
            "total": len(numbers),
            "current": 0,
            "found": 0,
            "failed": 0,
            "current_number": None,
            "course_type": course_type,
        })

        driver = mobile_fetcher.setup_driver(headless=headless)
        mobile_fetcher.setup_form(driver, course_type=course_type)

        for i, enrollment_no in enumerate(numbers, 1):
            job["current"] = i
            job["current_number"] = enrollment_no

            try:
                raw_result = mobile_fetcher.fetch_one(driver, enrollment_no)
                result = mobile_fetcher.filter_result(
                    raw_result,
                    selected_fields,
                )
                result["Course Type"] = course_type

                if raw_result.get("Status") == "Found":
                    job["found"] += 1
                else:
                    job["failed"] += 1

            except Exception as e:
                result = {
                    "Course Type": course_type,
                    "Enrollment No": enrollment_no,
                    "Status": f"Error: {e}",
                }
                job["failed"] += 1

            results.append(result)
            job["preview"] = results[:15]

            # Save periodically so a long batch has a usable partial file.
            if i % 10 == 0 or i == len(numbers):
                out_path = OUTPUT_DIR / f"{job_id}_mobile.xlsx"
                mobile_fetcher.save_results_to_excel(results, str(out_path))

        out_path = OUTPUT_DIR / f"{job_id}_mobile.xlsx"
        mobile_fetcher.save_results_to_excel(results, str(out_path))

        job.update({
            "status": "done",
            "output_file": f"{job_id}_mobile.xlsx",
            "preview": results[:15],
            "total_processed": len(results),
            "download_url": url_for("mobile_download", job_id=job_id),
        })

    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        job["traceback"] = traceback.format_exc()
        logger.error("Bulk mobile job error:\n" + traceback.format_exc())

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


@app.route("/mobile/upload", methods=["POST"])
def mobile_upload():
    if "data_file" not in request.files:
        return jsonify({"error": "Koi file nahi mili"}), 400

    file = request.files["data_file"]
    if not file.filename:
        return jsonify({"error": "Koi file select nahi ki"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in (".xlsx", ".xls", ".csv", ".pdf"):
        return jsonify({
            "error": "Sirf .xlsx, .xls, .csv ya .pdf file allowed hai"
        }), 400

    course_type = mobile_fetcher.normalize_course_type(
        request.form.get("course_type", "UG")
    )

    headless = request.form.get("headless", "true").lower() == "true"

    fields_raw = request.form.get("selected_fields", "")
    selected_fields = [
        item.strip()
        for item in fields_raw.split(",")
        if item.strip()
    ]

    job_id = uuid.uuid4().hex[:12]
    input_path = UPLOAD_DIR / f"{job_id}{ext}"
    file.save(str(input_path))

    MOBILE_JOBS[job_id] = {
        "status": "queued",
        "current": 0,
        "total": 0,
        "found": 0,
        "failed": 0,
        "current_number": None,
        "course_type": course_type,
        "selected_fields": selected_fields,
    }

    threading.Thread(
        target=process_mobile_job,
        args=(
            job_id,
            input_path,
            course_type,
            selected_fields,
            headless,
        ),
        daemon=True,
    ).start()

    return jsonify({
        "success": True,
        "job_id": job_id,
        "course_type": course_type,
    })


@app.route("/mobile/status/<job_id>")
def mobile_status(job_id):
    job = MOBILE_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    resp = {
        "status": job.get("status"),
        "current": job.get("current", 0),
        "total": job.get("total", 0),
        "found": job.get("found", 0),
        "failed": job.get("failed", 0),
        "current_number": job.get("current_number"),
        "course_type": job.get("course_type"),
    }

    if job.get("status") == "done":
        resp.update({
            "preview": job.get("preview", []),
            "total_processed": job.get("total_processed", 0),
            "download_url": url_for(
                "mobile_download",
                job_id=job_id,
            ),
        })

    if job.get("status") == "error":
        resp["error"] = job.get("error", "Unknown error")

    return jsonify(resp)


@app.route("/mobile/download/<job_id>")
def mobile_download(job_id):
    job = MOBILE_JOBS.get(job_id)
    if not job or job.get("status") != "done":
        return "File abhi ready nahi hai", 404

    return send_file(
        str(OUTPUT_DIR / job["output_file"]),
        as_attachment=True,
        download_name="MP_Bhoj_Student_Results.xlsx",
    )


# ----------------------------------------------------------------------
# Existing WhatsApp routes
# ----------------------------------------------------------------------

@app.route("/whatsapp/open", methods=["POST"])
def whatsapp_open():
    try:
        whatsapp_module.open_whatsapp_web()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/whatsapp/debug_windows")
def whatsapp_debug_windows():
    return jsonify({"titles": whatsapp_module.list_all_window_titles()})


@app.route("/whatsapp/summary")
def whatsapp_summary():
    return jsonify(whatsapp_module.job_summary())


@app.route("/whatsapp/upload", methods=["POST"])
def whatsapp_upload():
    if "numbers_file" not in request.files:
        return jsonify({"error": "Koi file nahi mili"}), 400

    file = request.files["numbers_file"]
    if not file.filename:
        return jsonify({"error": "Koi file select nahi ki"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in (".xlsx", ".xls", ".csv"):
        return jsonify({"error": "Sirf .xlsx, .xls ya .csv file allowed hai"}), 400

    save_path = UPLOAD_DIR / f"wa_numbers{ext}"
    file.save(str(save_path))

    try:
        job = whatsapp_module.create_job_from_file(save_path)
    except Exception as e:
        logger.error("whatsapp_upload me error:\n" + traceback.format_exc())
        return jsonify({"error": f"File padhne me error: {e}"}), 400

    if len(job.get("numbers", [])) == 0:
        return jsonify({
            "error": (
                'Is file me koi valid mobile number nahi mila. '
                'Column header "Number"/"Mobile"/"Phone" hona chahiye.'
            )
        }), 400

    return jsonify(whatsapp_module.job_summary())


@app.route("/whatsapp/reset", methods=["POST"])
def whatsapp_reset():
    whatsapp_module.reset_job()
    return jsonify({"ok": True})


@app.route("/whatsapp/start", methods=["POST"])
def whatsapp_start():
    if WA_STATUS.get("status") == "running":
        return jsonify({"error": "Pehle se chal raha hai"}), 400

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()

    if not message:
        return jsonify({"error": "Message khali hai"}), 400

    summary = whatsapp_module.job_summary()
    if not summary.get("exists") or summary.get("remaining", 0) <= 0:
        return jsonify({
            "error": "Bhejne ke liye koi number nahi bacha / list upload nahi hui"
        }), 400

    WA_STOP_EVENT.clear()
    WA_STATUS.update({
        "status": "running",
        "session_sent": 0,
        "error": None,
        "current_number": None,
    })

    def _safe_run_session():
        try:
            whatsapp_module.run_session(
                message,
                WA_STATUS,
                WA_STOP_EVENT,
            )
        except Exception:
            logger.error("WhatsApp session crash:\n" + traceback.format_exc())
            WA_STATUS["status"] = "error"
            WA_STATUS["error"] = (
                'Automation crash ho gaya - "error_log.txt" check karein.'
            )

    threading.Thread(target=_safe_run_session, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/whatsapp/stop", methods=["POST"])
def whatsapp_stop():
    WA_STOP_EVENT.set()
    return jsonify({"ok": True})


@app.route("/whatsapp/status")
def whatsapp_status():
    return jsonify(WA_STATUS)


if __name__ == "__main__":
    print("=" * 60)
    print("MP Bhoj PDF -> Excel + Student Lookup")
    print("Browser: http://127.0.0.1:5000")
    print("Stop: Ctrl+C")
    print("=" * 60)
    app.run(debug=False, host="127.0.0.1", port=5000, threaded=True)
