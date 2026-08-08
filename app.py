from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime
import re

app = Flask(__name__)

BHOJ_URL = "https://mpbou.mponline.gov.in/portal/Services/BHOJ/BrochureFee/Migration.aspx"

TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def get_page(session, url):
    response = session.get(
        url,
        headers=HEADERS,
        timeout=TIMEOUT
    )
    response.raise_for_status()
    return response.text


def hidden_fields(soup):
    data = {}

    for element in soup.select("input[type='hidden']"):
        name = element.get("name")
        if name:
            data[name] = element.get("value", "")

    return data


def find_control(soup, text):
    text = text.lower()

    for element in soup.find_all(["input", "select", "textarea"]):
        name = element.get("name", "")
        element_id = element.get("id", "")

        if text in name.lower() or text in element_id.lower():
            return element

    return None


def find_select(soup, label_text):
    label_text = label_text.lower()

    for select in soup.find_all("select"):
        name = select.get("name", "")
        element_id = select.get("id", "")

        if label_text in name.lower() or label_text in element_id.lower():
            return select

        parent_text = select.parent.get_text(" ", strip=True).lower()

        if label_text in parent_text:
            return select

    return None


def find_input(soup, label_text):
    label_text = label_text.lower()

    for element in soup.find_all("input"):
        name = element.get("name", "")
        element_id = element.get("id", "")
        value = element.get("value", "")

        combined = f"{name} {element_id} {value}".lower()

        if label_text in combined:
            return element

    for element in soup.find_all("input"):
        parent = element.parent

        if parent:
            parent_text = parent.get_text(" ", strip=True).lower()

            if label_text in parent_text:
                return element

    return None


def option_value(select, text):
    if not select:
        return None

    text = text.lower()

    for option in select.find_all("option"):
        option_text = option.get_text(" ", strip=True)

        if text in option_text.lower():
            return option.get("value", "")

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


def submit_postback(session, soup, target, extra_data=None):
    data = hidden_fields(soup)

    data["__EVENTTARGET"] = target
    data["__EVENTARGUMENT"] = ""

    if extra_data:
        data.update(extra_data)

    response = session.post(
        BHOJ_URL,
        data=data,
        headers={
            **HEADERS,
            "Referer": BHOJ_URL,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=TIMEOUT
    )

    response.raise_for_status()

    return BeautifulSoup(
        response.text,
        "html.parser"
    )


def fetch_student(enrollment_no):
    session = requests.Session()

    html = get_page(session, BHOJ_URL)
    soup = BeautifulSoup(html, "html.parser")

    # -------------------------------------------------
    # STEP 1: Course Type -> UG
    # -------------------------------------------------

    course_select = find_select(soup, "course")

    if course_select:
        ug_value = option_value(course_select, "ug")

        if ug_value is not None:
            target = get_postback_target(course_select)

            if target:
                soup = submit_postback(
                    session,
                    soup,
                    target,
                    {
                        course_select.get("name"): ug_value
                    }
                )

    # -------------------------------------------------
    # STEP 2: Apply For -> No Objection Certificate
    # -------------------------------------------------

    apply_select = find_select(soup, "apply")

    if apply_select:
        noc_value = option_value(
            apply_select,
            "no objection certificate"
        )

        if noc_value is not None:
            target = get_postback_target(apply_select)

            if target:
                soup = submit_postback(
                    session,
                    soup,
                    target,
                    {
                        apply_select.get("name"): noc_value
                    }
                )

    # -------------------------------------------------
    # STEP 3: Enrollment Number
    # -------------------------------------------------

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

    # Try the input's postback first.
    target = get_postback_target(enrollment_input)

    if target:
        soup = submit_postback(
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
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=TIMEOUT
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

    # -------------------------------------------------
    # STEP 4: Read Candidate Name
    # -------------------------------------------------

    name_input = find_input(
        soup,
        "candidate"
    )

    candidate_name = ""

    if name_input:
        candidate_name = (
            name_input.get("value", "") or ""
        ).strip()

    if not candidate_name:
        name_input = find_input(
            soup,
            "name"
        )

        if name_input:
            candidate_name = (
                name_input.get("value", "") or ""
            ).strip()

    # -------------------------------------------------
    # STEP 5: Read Mobile Number
    # -------------------------------------------------

    mobile_input = find_input(
        soup,
        "mobile"
    )

    mobile_no = ""

    if mobile_input:
        mobile_no = (
            mobile_input.get("value", "") or ""
        ).strip()

    # -------------------------------------------------
    # RESULT
    # -------------------------------------------------

    if mobile_no:
        status = "Found"
    else:
        status = "Not Found / No Response"

    return {
        "Enrollment No": enrollment_no,
        "Candidate Name": candidate_name,
        "Mobile No": mobile_no,
        "Status": status,
        "Current Year": datetime.now().year
    }


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
            "error": "Enrollment number khali hai"
        }), 400

    try:
        result = fetch_student(enrollment_no)

        return jsonify({
            "success": True,
            "result": result
        })

    except requests.RequestException as e:
        return jsonify({
            "success": False,
            "error": f"MP Bhoj site se connection failed: {e}"
        }), 502

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "online",
        "service": "MP Bhoj Student API",
        "year": datetime.now().year
    })
