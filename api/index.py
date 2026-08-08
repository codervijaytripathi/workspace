from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import re

app = Flask(__name__)

BHOJ_URL = "https://mpbou.mponline.gov.in/portal/Services/BHOJ/BrochureFee/Migration.aspx"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
}

TIMEOUT = 30


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def hidden_fields(soup):
    data = {}

    for element in soup.select("input[type='hidden']"):
        name = element.get("name")

        if name:
            data[name] = element.get("value", "")

    return data


def find_select(soup, text):
    text = text.lower()

    for select in soup.find_all("select"):
        name = select.get("name", "")
        element_id = select.get("id", "")

        if text in name.lower() or text in element_id.lower():
            return select

        parent = select.parent

        if parent and text in parent.get_text(" ", strip=True).lower():
            return select

    return None


def find_input(soup, text):
    text = text.lower()

    for element in soup.find_all("input"):
        name = element.get("name", "")
        element_id = element.get("id", "")

        if text in name.lower() or text in element_id.lower():
            return element

    for element in soup.find_all("input"):
        parent = element.parent

        if parent and text in parent.get_text(" ", strip=True).lower():
            return element

    return None


def get_option(select, text):
    if not select:
        return None

    text = text.lower()

    for option in select.find_all("option"):
        option_text = option.get_text(" ", strip=True)

        if text in option_text.lower():
            return option

    return None


def get_postback_target(element):
    if not element:
        return None

    onchange = element.get("onchange", "")

    match = re.search(
        r"__doPostBack\(['\"]([^'\"]+)['\"]",
        onchange
    )

    if match:
        return match.group(1)

    return None


def postback(session, soup, target, extra=None):
    data = hidden_fields(soup)

    data["__EVENTTARGET"] = target
    data["__EVENTARGUMENT"] = ""

    if extra:
        data.update(extra)

    response = session.post(
        BHOJ_URL,
        data=data,
        headers={
            **HEADERS,
            "Referer": BHOJ_URL,
            "Content-Type": "application/x-www-form-urlencoded"
        },
        timeout=TIMEOUT
    )

    response.raise_for_status()

    return BeautifulSoup(response.text, "html.parser")


def fetch_student(enrollment_no):
    session = requests.Session()

    response = session.get(
        BHOJ_URL,
        headers=HEADERS,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    # Course Type -> UG
    course_select = find_select(
        soup,
        "course"
    )

    if course_select:
        option = get_option(
            course_select,
            "UG"
        )

        if option:
            target = get_postback_target(
                course_select
            )

            if target:
                soup = postback(
                    session,
                    soup,
                    target,
                    {
                        course_select.get("name"): option.get("value", "")
                    }
                )

    # Apply For -> No Objection Certificate
    apply_select = find_select(
        soup,
        "apply"
    )

    if apply_select:
        option = get_option(
            apply_select,
            "No Objection Certificate"
        )

        if option:
            target = get_postback_target(
                apply_select
            )

            if target:
                soup = postback(
                    session,
                    soup,
                    target,
                    {
                        apply_select.get("name"): option.get("value", "")
                    }
                )

    # Enrollment No
    enrollment_input = find_input(
        soup,
        "enrollment"
    )

    if not enrollment_input:
        return {
            "Enrollment No": enrollment_no,
            "Candidate Name": "",
            "Mobile No": "",
            "Status": "Enrollment field not found",
            "Current Year": datetime.now().year
        }

    enrollment_name = enrollment_input.get("name")

    data = hidden_fields(soup)

    if enrollment_name:
        data[enrollment_name] = enrollment_no

    # Try input onchange postback
    target = get_postback_target(
        enrollment_input
    )

    if target:
        soup = postback(
            session,
            soup,
            target,
            {
                enrollment_name: enrollment_no
            }
        )
    else:
        response = session.post(
            BHOJ_URL,
            data=data,
            headers={
                **HEADERS,
                "Referer": BHOJ_URL,
                "Content-Type": "application/x-www-form-urlencoded"
            },
            timeout=TIMEOUT
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

    # Candidate Name
    name_input = find_input(
        soup,
        "Candidate"
    )

    candidate_name = ""

    if name_input:
        candidate_name = (
            name_input.get("value", "") or ""
        ).strip()

    if not candidate_name:
        name_input = find_input(
            soup,
            "Name"
        )

        if name_input:
            candidate_name = (
                name_input.get("value", "") or ""
            ).strip()

    # Mobile
    mobile_input = find_input(
        soup,
        "Mobile"
    )

    mobile_no = ""

    if mobile_input:
        mobile_no = (
            mobile_input.get("value", "") or ""
        ).strip()

    status = "Found" if mobile_no else "Not Found / No Response"

    return {
        "Enrollment No": enrollment_no,
        "Candidate Name": candidate_name,
        "Mobile No": mobile_no,
        "Status": status,
        "Current Year": datetime.now().year
    }


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "online",
        "service": "MP Bhoj Student API",
        "year": datetime.now().year
    })


@app.route("/api/mobile/test", methods=["POST", "OPTIONS"])
def mobile_test():
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(silent=True) or {}

    enrollment_no = str(
        data.get("enrollment_no", "")
    ).strip()

    if not enrollment_no:
        return jsonify({
            "success": False,
            "error": "Enrollment number is required"
        }), 400

    try:
        result = fetch_student(
            enrollment_no
        )

        return jsonify({
            "success": True,
            "result": result
        })

    except requests.RequestException as e:
        return jsonify({
            "success": False,
            "error": f"MP Bhoj connection failed: {str(e)}"
        }), 502

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
