"""
MP Bhoj PDF -> Excel Converter - Local Web App
================================================
Chalane ka tarika:
    pip install -r requirements.txt
    python app.py

Fir browser me kholo: http://127.0.0.1:5000

Agar kabhi kuch crash ho ya "Failed to fetch" jaisa error aaye, sabse
pehle isi folder me "error_log.txt" file check karo - usme exact
wajah (poora traceback) likhi hoti hai.
"""

import os
import sys
import uuid
import logging
import threading
import traceback
from pathlib import Path

from flask import Flask, request, render_template, send_file, jsonify, url_for

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "error_log.txt"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("mp_bhoj_app")
logger.info("=" * 60)
logger.info("App shuru ho raha hai...")

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
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB max upload

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response



from werkzeug.exceptions import HTTPException

@app.errorhandler(Exception)
def handle_any_error(e):
    """Koi bhi route me anhandled exception aaye, poora server crash nahi
    hoga - error log file me likha jayega aur browser ko clean JSON error
    milega (jisse 'Failed to fetch' jaisa vague error nahi aayega).
    Normal HTTP errors (404 etc) ko yahan crash jaisa treat nahi karte."""
    if isinstance(e, HTTPException):
        return e
    logger.error(f"UNHANDLED ERROR on {request.path}:\n" + traceback.format_exc())
    return jsonify({"error": f"Server error: {e}. Poora detail 'error_log.txt' me hai."}), 500


# in-memory job tracker (single-user local app, so this is fine)
JOBS = {}
MOBILE_JOBS = {}

# WhatsApp sender live state
WA_STATUS = {'status': 'idle', 'session_sent': 0, 'session_limit': whatsapp_module.SESSION_LIMIT,
             'total_sent': 0, 'total_numbers': 0, 'current_number': None, 'error': None}
WA_STOP_EVENT = threading.Event()


def process_job(job_id, pdf_path):
    JOBS[job_id]['status'] = 'processing'
    try:
        def progress_cb(current, total):
            JOBS[job_id]['current_page'] = current
            JOBS[job_id]['total_pages'] = total

        df, info = extract_pdf_to_dataframe(str(pdf_path), progress_callback=progress_cb)

        if df.empty:
            JOBS[job_id]['status'] = 'error'
            JOBS[job_id]['error'] = (
                "Koi record extract nahi hua. Ye PDF shayad is tool ke expected format "
                "(MP Bhoj Result Sheet, Crystal Reports) se match nahi karta."
            )
            return

        output_filename = f"{job_id}.xlsx"
        output_path = OUTPUT_DIR / output_filename
        save_to_excel(df, str(output_path))

        JOBS[job_id]['status'] = 'done'
        JOBS[job_id]['info'] = info
        JOBS[job_id]['output_file'] = output_filename
        JOBS[job_id]['preview'] = df.head(15).to_dict(orient='records')
        JOBS[job_id]['columns'] = list(df.columns)

    except Exception as e:
        JOBS[job_id]['status'] = 'error'
        JOBS[job_id]['error'] = f"{e}"
        JOBS[job_id]['traceback'] = traceback.format_exc()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload():
    if 'pdf_file' not in request.files:
        return jsonify({"error": "Koi file nahi mili"}), 400
    file = request.files['pdf_file']
    if file.filename == '':
        return jsonify({"error": "Koi file select nahi ki"}), 400
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({"error": "Sirf .pdf file allowed hai"}), 400

    job_id = uuid.uuid4().hex[:12]
    pdf_path = UPLOAD_DIR / f"{job_id}.pdf"
    file.save(str(pdf_path))

    JOBS[job_id] = {
        'status': 'queued',
        'current_page': 0,
        'total_pages': 0,
    }

    thread = threading.Thread(target=process_job, args=(job_id, pdf_path), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id})


@app.route('/status/<job_id>')
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    resp = {
        "status": job['status'],
        "current_page": job.get('current_page', 0),
        "total_pages": job.get('total_pages', 0),
    }
    if job['status'] == 'done':
        resp['info'] = job['info']
        resp['preview'] = job['preview']
        resp['columns'] = job['columns']
        resp['download_url'] = url_for('download', job_id=job_id)
    if job['status'] == 'error':
        resp['error'] = job.get('error', 'Unknown error')

    return jsonify(resp)


@app.route('/download/<job_id>')
def download(job_id):
    job = JOBS.get(job_id)
    if not job or job['status'] != 'done':
        return "File abhi ready nahi hai", 404
    output_path = OUTPUT_DIR / job['output_file']
    friendly_name = "MP_Bhoj_Result_Converted.xlsx"
    return send_file(str(output_path), as_attachment=True, download_name=friendly_name)


# ----------------------------------------------------------------------
# Mobile Number Fetcher routes
# ----------------------------------------------------------------------

@app.route('/mobile/test', methods=['POST'])
def mobile_test():
    data = request.get_json(force=True)
    enrollment_no = (data.get('enrollment_no') or '').strip()
    headless = bool(data.get('headless', False))
    if not enrollment_no:
        return jsonify({"error": "Enrollment number khali hai"}), 400

    screenshot_name = f"test_{uuid.uuid4().hex[:8]}.png"
    screenshot_path = DEBUG_DIR / screenshot_name
    try:
        result = mobile_fetcher.test_single(enrollment_no, headless=headless, screenshot_path=screenshot_path)
        return jsonify({"result": result})
    except Exception as e:
        shot_url = None
        if screenshot_path.exists():
            shot_url = url_for('debug_screenshot', filename=screenshot_name)
        return jsonify({
            "error": f"Error aaya: {e}",
            "screenshot_url": shot_url
        }), 500


@app.route('/debug/<filename>')
def debug_screenshot(filename):
    path = DEBUG_DIR / filename
    if not path.exists():
        return "Not found", 404
    return send_file(str(path))


MOBILE_STOP_EVENTS = {}


def process_mobile_job(job_id, input_path, headless, course_type="UG", selected_fields=None):
    MOBILE_JOBS[job_id]['status'] = 'processing'
    MOBILE_JOBS[job_id]['course_type'] = course_type
    MOBILE_JOBS[job_id]['selected_fields'] = selected_fields or []

    stop_event = MOBILE_STOP_EVENTS.setdefault(job_id, threading.Event())

    try:
        numbers = mobile_fetcher.read_enrollment_numbers(input_path)

        if not numbers:
            MOBILE_JOBS[job_id]['status'] = 'error'
            MOBILE_JOBS[job_id]['error'] = "Koi enrollment number nahi mila is file me."
            return

        MOBILE_JOBS[job_id]['total'] = len(numbers)
        MOBILE_JOBS[job_id]['current'] = 0
        MOBILE_JOBS[job_id]['found'] = 0
        MOBILE_JOBS[job_id]['failed'] = 0
        MOBILE_JOBS[job_id]['current_number'] = None
        MOBILE_JOBS[job_id]['results'] = []

        logger.info(
            "Bulk job %s started: %s numbers, course=%s, fields=%s",
            job_id, len(numbers), course_type, selected_fields
        )

        driver = mobile_fetcher.setup_driver(headless=headless)
        results = []

        try:
            # Course Type + Apply For are selected only once.
            mobile_fetcher.setup_form(driver, course_type=course_type)

            for i, enr in enumerate(numbers, 1):
                if stop_event.is_set():
                    MOBILE_JOBS[job_id]['status'] = 'stopped'
                    break

                MOBILE_JOBS[job_id]['current_number'] = str(enr)

                try:
                    full_result = mobile_fetcher.fetch_one(driver, enr)
                    result = mobile_fetcher.filter_result(
                        full_result,
                        selected_fields=selected_fields
                    )
                except Exception as e:
                    logger.error(
                        "Bulk %s: enrollment %s failed: %s",
                        job_id, enr, e
                    )
                    result = {
                        "Enrollment No": str(enr),
                        "Status": f"Error: {e}"
                    }

                result["Course Type"] = course_type
                results.append(result)

                if result.get("Status") == "Found":
                    MOBILE_JOBS[job_id]['found'] = MOBILE_JOBS[job_id].get('found', 0) + 1
                else:
                    MOBILE_JOBS[job_id]['failed'] = MOBILE_JOBS[job_id].get('failed', 0) + 1

                MOBILE_JOBS[job_id]['current'] = i
                MOBILE_JOBS[job_id]['results'] = results[-15:]

                # Save periodically so a long batch has a usable checkpoint.
                if i % 10 == 0 or i == len(numbers):
                    out_path = OUTPUT_DIR / f"{job_id}_mobile.xlsx"
                    mobile_fetcher.save_results_to_excel(results, str(out_path))

        finally:
            driver.quit()

        out_path = OUTPUT_DIR / f"{job_id}_mobile.xlsx"
        mobile_fetcher.save_results_to_excel(results, str(out_path))

        if MOBILE_JOBS[job_id]['status'] != 'stopped':
            MOBILE_JOBS[job_id]['status'] = 'done'

        MOBILE_JOBS[job_id]['output_file'] = f"{job_id}_mobile.xlsx"
        MOBILE_JOBS[job_id]['preview'] = results[:15]
        MOBILE_JOBS[job_id]['total_processed'] = len(results)
        MOBILE_JOBS[job_id]['failed_numbers'] = [
            r.get("Enrollment No", "")
            for r in results
            if r.get("Status") != "Found"
        ]

        logger.info(
            "Bulk job %s finished: processed=%s found=%s failed=%s status=%s",
            job_id,
            len(results),
            MOBILE_JOBS[job_id].get('found', 0),
            MOBILE_JOBS[job_id].get('failed', 0),
            MOBILE_JOBS[job_id]['status']
        )

    except Exception as e:
        MOBILE_JOBS[job_id]['status'] = 'error'
        MOBILE_JOBS[job_id]['error'] = f"{e}"
        MOBILE_JOBS[job_id]['traceback'] = traceback.format_exc()
        logger.error("Bulk job %s crashed:\n%s", job_id, traceback.format_exc())

    finally:
        MOBILE_STOP_EVENTS.pop(job_id, None)


@app.route('/mobile/upload', methods=['POST', 'OPTIONS'])
def mobile_upload():
    if request.method == 'OPTIONS':
        return '', 204

    if 'data_file' not in request.files:
        return jsonify({"error": "Koi file nahi mili"}), 400

    file = request.files['data_file']
    if file.filename == '':
        return jsonify({"error": "Koi file select nahi ki"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in ('.xlsx', '.xls', '.csv', '.pdf'):
        return jsonify({"error": "Sirf .xlsx, .xls, .csv ya .pdf file allowed hai"}), 400

    headless = request.form.get('headless', 'true').lower() == 'true'
    course_type = mobile_fetcher.normalize_course_type(
        request.form.get('course_type', 'UG')
    )

    raw_fields = request.form.get('selected_fields', '')
    selected_fields = [
        item.strip()
        for item in raw_fields.split(',')
        if item.strip()
    ]

    job_id = uuid.uuid4().hex[:12]
    input_path = UPLOAD_DIR / f"{job_id}{ext}"
    file.save(str(input_path))

    MOBILE_JOBS[job_id] = {
        'status': 'queued',
        'current': 0,
        'total': 0,
        'found': 0,
        'failed': 0,
        'current_number': None,
        'course_type': course_type,
        'selected_fields': selected_fields,
    }
    MOBILE_STOP_EVENTS[job_id] = threading.Event()

    thread = threading.Thread(
        target=process_mobile_job,
        args=(job_id, input_path, headless, course_type, selected_fields),
        daemon=True
    )
    thread.start()

    return jsonify({
        "success": True,
        "job_id": job_id,
        "course_type": course_type,
        "selected_fields": selected_fields
    })


@app.route('/mobile/status/<job_id>', methods=['GET', 'OPTIONS'])
def mobile_status(job_id):
    if request.method == 'OPTIONS':
        return '', 204

    job = MOBILE_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    resp = {
        "status": job.get('status', 'unknown'),
        "current": job.get('current', 0),
        "total": job.get('total', 0),
        "found": job.get('found', 0),
        "failed": job.get('failed', 0),
        "current_number": job.get('current_number'),
        "course_type": job.get('course_type', 'UG'),
    }

    if job.get('status') in ('done', 'stopped'):
        resp['preview'] = job.get('preview', [])
        resp['total_processed'] = job.get('total_processed', job.get('current', 0))
        resp['failed_numbers'] = job.get('failed_numbers', [])

        if job.get('output_file'):
            resp['download_url'] = (
                request.host_url.rstrip('/') +
                url_for('mobile_download', job_id=job_id)
            )

    if job.get('status') == 'error':
        resp['error'] = job.get('error', 'Unknown error')

    return jsonify(resp)


@app.route('/mobile/stop/<job_id>', methods=['POST', 'OPTIONS'])
def mobile_stop(job_id):
    if request.method == 'OPTIONS':
        return '', 204

    if job_id not in MOBILE_JOBS:
        return jsonify({"error": "Job not found"}), 404

    event = MOBILE_STOP_EVENTS.get(job_id)
    if event:
        event.set()

    if MOBILE_JOBS[job_id].get('status') == 'processing':
        MOBILE_JOBS[job_id]['status'] = 'stopping'

    return jsonify({"success": True, "status": "stopping"})


@app.route('/mobile/download/<job_id>')
def mobile_download(job_id):
    job = MOBILE_JOBS.get(job_id)
    if not job or job['status'] != 'done':
        return "File abhi ready nahi hai", 404
    output_path = OUTPUT_DIR / job['output_file']
    return send_file(str(output_path), as_attachment=True, download_name="MP_Bhoj_Student_Bulk_Results.xlsx")


# ----------------------------------------------------------------------
# WhatsApp Bulk Sender routes
# ----------------------------------------------------------------------

@app.route('/whatsapp/open', methods=['POST'])
def whatsapp_open():
    """STEP 1: apna browser me web.whatsapp.com khol deta hai taaki user
    login/QR-scan kar sake."""
    try:
        whatsapp_module.open_whatsapp_web()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/whatsapp/debug_windows')
def whatsapp_debug_windows():
    """Debug: dikhata hai ki script ko abhi konse windows/titles dikh rahe hain."""
    return jsonify({"titles": whatsapp_module.list_all_window_titles()})


@app.route('/whatsapp/summary')
def whatsapp_summary():
    return jsonify(whatsapp_module.job_summary())


@app.route('/whatsapp/upload', methods=['POST'])
def whatsapp_upload():
    logger.info("whatsapp_upload route hit")
    if 'numbers_file' not in request.files:
        return jsonify({"error": "Koi file nahi mili"}), 400
    file = request.files['numbers_file']
    if file.filename == '':
        return jsonify({"error": "Koi file select nahi ki"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in ('.xlsx', '.xls', '.csv'):
        return jsonify({"error": "Sirf .xlsx ya .csv file allowed hai"}), 400

    save_path = UPLOAD_DIR / f"wa_numbers{ext}"
    file.save(str(save_path))

    try:
        job = whatsapp_module.create_job_from_file(save_path)
    except Exception as e:
        logger.error("whatsapp_upload me error:\n" + traceback.format_exc())
        return jsonify({"error": f"File padhne me error: {e}"}), 400

    if len(job.get('numbers', [])) == 0:
        return jsonify({
            "error": "Is file me koi valid mobile number nahi mila. Column header "
                     "\"Number\"/\"Mobile\"/\"Phone\" hona chahiye, aur uske niche "
                     "asli 10-digit numbers hone chahiye."
        }), 400

    logger.info(f"whatsapp_upload safal: {whatsapp_module.job_summary()}")
    return jsonify(whatsapp_module.job_summary())


@app.route('/whatsapp/reset', methods=['POST'])
def whatsapp_reset():
    whatsapp_module.reset_job()
    return jsonify({"ok": True})


@app.route('/whatsapp/start', methods=['POST'])
def whatsapp_start():
    """STEP 2: WhatsApp window khud dhoondh kar activate karta hai (andar
    run_session ke shuru me), phir bhejna shuru karta hai."""
    if WA_STATUS.get('status') == 'running':
        return jsonify({"error": "Pehle se chal raha hai"}), 400

    data = request.get_json(force=True)
    message = (data.get('message') or '').strip()
    if not message:
        return jsonify({"error": "Message khali hai"}), 400

    summary = whatsapp_module.job_summary()
    if not summary.get('exists') or summary.get('remaining', 0) <= 0:
        return jsonify({"error": "Bhejne ke liye koi number nahi bacha / list upload nahi hui"}), 400

    WA_STOP_EVENT.clear()
    WA_STATUS.update({'status': 'running', 'session_sent': 0, 'error': None, 'current_number': None})

    def _safe_run_session():
        try:
            logger.info(f"WhatsApp session shuru: {summary.get('remaining')} number baaki")
            whatsapp_module.run_session(message, WA_STATUS, WA_STOP_EVENT)
            logger.info(f"WhatsApp session khatam. Status: {WA_STATUS.get('status')}")
        except Exception:
            logger.error("WhatsApp session me CRASH hua:\n" + traceback.format_exc())
            WA_STATUS['status'] = 'error'
            WA_STATUS['error'] = 'Automation crash ho gaya - "error_log.txt" file me poora detail hai.'

    thread = threading.Thread(target=_safe_run_session, daemon=True)
    thread.start()

    return jsonify({"ok": True})


@app.route('/whatsapp/stop', methods=['POST'])
def whatsapp_stop():
    WA_STOP_EVENT.set()
    return jsonify({"ok": True})


@app.route('/whatsapp/status')
def whatsapp_status():
    return jsonify(WA_STATUS)


if __name__ == '__main__':
    print("=" * 60)
    print("MP Bhoj PDF -> Excel Converter")
    print("Browser me kholo: http://127.0.0.1:5000")
    print("Band karne ke liye: Ctrl+C")
    print("=" * 60)
    app.run(debug=False, host='127.0.0.1', port=5000, threaded=True)
