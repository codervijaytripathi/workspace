from flask import Flask, request, jsonify
import mobile_fetcher

app = Flask(__name__)


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return response


@app.route("/api/mobile/test", methods=["POST", "OPTIONS"])
def mobile_test():
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(silent=True) or {}

    enrollment_no = str(
        data.get("enrollment_no", "")
    ).strip()

    headless = bool(
        data.get("headless", True)
    )

    if not enrollment_no:
        return jsonify({
            "error": "Enrollment number is required"
        }), 400

    try:
        result = mobile_fetcher.test_single(
            enrollment_no,
            headless=headless
        )

        return jsonify({
            "result": result
        })

    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500
