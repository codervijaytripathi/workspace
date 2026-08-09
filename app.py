from flask import Flask, request, jsonify
from flask_cors import CORS
import mobile_fetcher

app = Flask(__name__)
CORS(app)

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "online",
        "service": "MP Bhoj API"
    })

@app.route("/mobile/test", methods=["POST"])
def mobile_test():
    data = request.get_json(silent=True) or {}
    enrollment_no = str(data.get("enrollment_no", "")).strip()

    if not enrollment_no:
        return jsonify({
            "success": False,
            "error": "Enrollment number is required"
        }), 400

    try:
        result = mobile_fetcher.test_single(
            enrollment_no,
            headless=True
        )

        return jsonify({
            "success": True,
            "result": result
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
