from pathlib import Path

code = r'''from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import re
from urllib.parse import urljoin

app = Flask(__name__)

BHOJ_URL = "https://mpbou.mponline.gov.in/portal/Services/BHOJ/BrochureFee/Migration.aspx"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
    "Connection": "keep-alive",
}

TIMEOUT = 30


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def clean_text(value):
    """Normalize scraped text."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_course_type(value):
    """
    Accept common UI values:
    UG / PG / Under Graduate / Post Graduate
    """
    value = clean_text(value).upper()

    if value in {"PG", "POST GRADUATE", "POST-GRADUATE", "POSTGRADUATE"}:
        return "PG"

    return "UG"


def hidden_fields(soup):
    """
    Collect ASP.NET hidden fields required for postbacks.
    """
    data = {}

    for element in soup.select("input[type='hidden']"):
        name = element.get("name")
        if name:
            data[name] = element.get("value", "")

    return data


def label_text(element):
    """
    Get nearby label/container text for an input/select.
    """
    parent = element.parent

    for _ in range(4):
        if parent is None:
            break

        text = clean_text(parent.get_text(" ", strip=True))
        if text:
            return text

        parent = parent.parent

    return ""


def find_select(soup, text):
    """
    Find a select element by matching its id/name/nearby text.
    """
    needle = clean_text(text).lower()

    # First try id/name.
    for select in soup.find_all("select"):
        ident = clean_text(select.get("id", "")).lower()
        name = clean_text(select.get("name", "")).lower()

        if needle in ident or needle in name:
            return select

    # Then try nearby label/container text.
    for select in soup.find_all("select"):
        nearby = label_text(select).lower()

        if needle in nearby:
            return select

    return None


def find_input(soup, text):
    """
    Find an input/textarea by matching id/name/placeholder or nearby text.
    """
    needle = clean_text(text).lower()

    for element in soup.find_all(["input", "textarea"]):
        ident = clean_text(element.get("id", "")).lower()
        name = clean_text(element.get("name", "")).lower()
        placeholder = clean_text(element.get("placeholder", "")).lower()

        if needle in ident or needle in name or needle in placeholder:
            return element

    for element in soup.find_all(["input", "textarea"]):
        nearby = label_text(element).lower()

        if needle in nearby:
            return element

    return None


def find_input_by_candidates(soup, candidates):
    """
    Try multiple names/labels for a field.
    """
    for candidate in candidates:
        element = find_input(soup, candidate)
        if element is not None:
            return element

    return None


def get_option(select, wanted):
    """
    Find an option using visible text first, then value.
    """
    wanted = clean_text(wanted).lower()

    options = select.find_all("option")

    # Exact visible text.
    for option in options:
        text = clean_text(option.get_text(" ", strip=True)).lower()
        value = clean_text(option.get("value", "")).lower()

        if text == wanted or value == wanted:
            return option

    # Contains match.
    for option in options:
        text = clean_text(option.get_text(" ", strip=True)).lower()
        value = clean_text(option.get("value", "")).lower()

        if wanted in text or wanted in value:
            return option

    return None


def get_postback_target(element):
    """
    Extract __doPostBack target from onchange/onclick.
    """
    if element is None:
        return None

    for attr in ("onchange", "onclick"):
        script = element.get(attr, "")
        if not script:
            continue

        # __doPostBack('target','argument')
        match = re.search(
            r"__doPostBack\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]*)['\"]\s*\)",
            script,
            flags=re.I,
        )

        if match:
            return match.group(1), match.group(2)

    return None


def postback(session, soup, target, extra=None):
    """
    Perform an ASP.NET postback while preserving hidden fields.
    """
    data = hidden_fields(soup)

    if extra:
        data.update(extra)

    # ASP.NET postback target.
    data["__EVENTTARGET"] = target
    data["__EVENTARGUMENT"] = ""

    # Remove submit button values that may interfere.
    for key in list(data.keys()):
        if key.lower().startswith("__"):
            continue

    response = session.post(
        BHOJ_URL,
        data=data,
        headers=HEADERS,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    return BeautifulSoup(response.text, "html.parser")


def submit_form(session, soup, extra):
    """
    Submit the current form without guessing an endpoint.
    """
    form = soup.find("form")

    if not form:
        return soup

    action = form.get("action") or BHOJ_URL
    action = urljoin(BHOJ_URL, action)

    data = hidden_fields(soup)
    data.update(extra)

    response = session.post(
        action,
        data=data,
        headers=HEADERS,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    return BeautifulSoup(response.text, "html.parser")


def select_course_type(session, soup, course_type):
    """
    Select UG or PG from the portal's Course Type dropdown.

    We intentionally match the live option text/value instead of hard-coding
    an internal option value.
    """
    course_type = normalize_course_type(course_type)

    select = find_select(soup, "course")

    if select is None:
        return soup, False, "Course Type dropdown not found"

    option = get_option(select, course_type)

    # If exact UG/PG text is not found, inspect options for likely labels.
    if option is None:
        for candidate in select.find_all("option"):
            text = clean_text(candidate.get_text(" ", strip=True)).upper()

            if course_type == "UG" and (
                "UNDER" in text or text.startswith("UG")
            ):
                option = candidate
                break

            if course_type == "PG" and (
                "POST" in text or text.startswith("PG")
            ):
                option = candidate
                break

    if option is None:
        available = [
            clean_text(o.get_text(" ", strip=True))
            for o in select.find_all("option")
            if clean_text(o.get_text(" ", strip=True))
        ]

        return (
            soup,
            False,
            f"{course_type} option not found. Available: {available}",
        )

    select_name = select.get("name")
    option_value = option.get("value", "")

    if not select_name:
        return soup, False, "Course Type select has no name"

    postback_target = get_postback_target(select)

    if postback_target:
        target, argument = postback_target

        data = {
            select_name: option_value,
            "__EVENTTARGET": target,
            "__EVENTARGUMENT": argument,
        }

        new_soup = postback(session, soup, target, data)
        return new_soup, True, ""

    # Some ASP.NET pages update through normal form submission.
    new_soup = submit_form(
        session,
        soup,
        {
            select_name: option_value,
        },
    )

    return new_soup, True, ""


def select_apply_for(session, soup):
    """
    Select No Objection Certificate if the portal exposes Apply For.
    """
    select = find_select(soup, "apply")

    if select is None:
        return soup, True, ""

    option = get_option(select, "No Objection Certificate")

    if option is None:
        # Try common shorter wording.
        option = get_option(select, "No Objection")

    if option is None:
        # Do not fail the whole request if the portal changed this dropdown.
        return soup, True, ""

    select_name = select.get("name")

    if not select_name:
        return soup, True, ""

    option_value = option.get("value", "")
    postback_target = get_postback_target(select)

    if postback_target:
        target, argument = postback_target

        data = {
            select_name: option_value,
            "__EVENTTARGET": target,
            "__EVENTARGUMENT": argument,
        }

        return postback(session, soup, target, data), True, ""

    return (
        submit_form(
            session,
            soup,
            {
                select_name: option_value,
            },
        ),
        True,
        "",
    )


def set_enrollment_number(session, soup, enrollment_no):
    """
    Put the enrollment number into the live ASP.NET form.

    The portal can trigger a postback when the user tabs out of this field,
    so we try the field's onchange/blur postback first and fall back to
    normal form submission.
    """
    field = find_input_by_candidates(
        soup,
        [
            "enrollment",
            "enrollmentno",
            "enrollment_no",
            "enroll",
        ],
    )

    if field is None:
        return soup, False, "Enrollment No field not found"

    name = field.get("name")

    if not name:
        return soup, False, "Enrollment No field has no name"

    extra = {
        name: enrollment_no,
    }

    postback_target = get_postback_target(field)

    if postback_target:
        target, argument = postback_target

        extra["__EVENTTARGET"] = target
        extra["__EVENTARGUMENT"] = argument

        return postback(session, soup, target, extra), True, ""

    # Sometimes the onchange is attached to a surrounding element.
    parent = field.parent

    for _ in range(3):
        if parent is None:
            break

        postback_target = get_postback_target(parent)

        if postback_target:
            target, argument = postback_target

            extra["__EVENTTARGET"] = target
            extra["__EVENTARGUMENT"] = argument

            return postback(session, soup, target, extra), True, ""

        parent = parent.parent

    return (
        submit_form(
            session,
            soup,
            {
                name: enrollment_no,
            },
        ),
        True,
        "",
    )


def extract_value(soup, candidates):
    """
    Read a field's value after the portal has populated it.
    """
    field = find_input_by_candidates(soup, candidates)

    if field is None:
        return ""

    value = field.get("value")

    if value:
        return clean_text(value)

    return clean_text(field.get_text(" ", strip=True))


def extract_field_by_row_label(soup, label_candidates):
    """
    Fallback for portals that render populated values inside table rows/divs
    instead of input elements.
    """
    wanted = [clean_text(x).lower() for x in label_candidates]

    for row in soup.find_all(["tr", "div", "li"]):
        text = clean_text(row.get_text(" ", strip=True))

        if not text:
            continue

        lower = text.lower()

        for label in wanted:
            if label in lower:
                # Look for a value cell.
                cells = row.find_all(["td", "th"])

                if len(cells) >= 2:
                    value = clean_text(cells[-1].get_text(" ", strip=True))
                    if value.lower() != label:
                        return value

                # Otherwise remove the label from the visible text.
                cleaned = re.sub(
                    re.escape(label),
                    "",
                    text,
                    flags=re.I,
                )
                cleaned = clean_text(cleaned).strip(" :-")
                if cleaned:
                    return cleaned

    return ""


def extract_student_data(soup, enrollment_no, course_type):
    """
    Extract currently available student fields from the portal.
    """
    candidate_name = extract_value(
        soup,
        [
            "candidate name",
            "candidatename",
            "candidate_name",
            "name",
        ],
    )

    if not candidate_name:
        candidate_name = extract_field_by_row_label(
            soup,
            [
                "Candidate's Name",
                "Candidate Name",
            ],
        )

    father_name = extract_value(
        soup,
        [
            "father",
            "fathername",
            "father name",
            "father_name",
        ],
    )

    if not father_name:
        father_name = extract_field_by_row_label(
            soup,
            [
                "Father's Name",
                "Father Name",
            ],
        )

    mobile_no = extract_value(
        soup,
        [
            "mobile",
            "mobileno",
            "mobile no",
            "mobile_no",
        ],
    )

    if not mobile_no:
        mobile_no = extract_field_by_row_label(
            soup,
            [
                "Mobile No",
                "Mobile Number",
            ],
        )

    dob = extract_value(
        soup,
        [
            "date of birth",
            "dob",
            "dateofbirth",
        ],
    )

    if not dob:
        dob = extract_field_by_row_label(
            soup,
            [
                "Date Of Birth",
                "Date of Birth",
            ],
        )

    regional_centre = extract_value(
        soup,
        [
            "regional centre",
            "regionalcenter",
            "regional_centre",
            "regionalcentre",
        ],
    )

    if not regional_centre:
        regional_centre = extract_field_by_row_label(
            soup,
            [
                "Regional Centre",
                "Regional Center",
            ],
        )

    course = extract_value(
        soup,
        [
            "course",
        ],
    )

    if not course:
        course = extract_field_by_row_label(
            soup,
            [
                "Course",
            ],
        )

    # Detect whether the page appears to have populated data.
    body_text = clean_text(soup.get_text(" ", strip=True))

    not_found_markers = [
        "record not found",
        "no record found",
        "student not found",
        "data not found",
        "invalid enrollment",
        "not available",
    ]

    is_not_found = any(
        marker in body_text.lower()
        for marker in not_found_markers
    )

    status = "Found" if (
        not is_not_found
        and (candidate_name or mobile_no or father_name)
    ) else "Not Found"

    return {
        "Enrollment No": enrollment_no,
        "Course Type": course_type,
        "Candidate Name": candidate_name,
        "Father's Name": father_name,
        "Mobile No": mobile_no,
        "Date Of Birth": dob,
        "Regional Centre": regional_centre,
        "Course": course,
        "Status": status,
        "Current Year": datetime.now().year,
    }


def fetch_student(enrollment_no, course_type="UG"):
    """
    Fetch one student.

    course_type can be:
        UG
        PG
    """
    enrollment_no = clean_text(enrollment_no)

    if not enrollment_no:
        raise ValueError("Enrollment number is required")

    course_type = normalize_course_type(course_type)

    session = requests.Session()
    session.headers.update(HEADERS)

    # Initial page.
    response = session.get(
        BHOJ_URL,
        timeout=TIMEOUT,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # 1. Course Type: UG or PG.
    soup, ok, error = select_course_type(
        session,
        soup,
        course_type,
    )

    if not ok:
        raise RuntimeError(error)

    # 2. Apply For.
    soup, ok, error = select_apply_for(
        session,
        soup,
    )

    if not ok:
        raise RuntimeError(error)

    # 3. Enrollment No.
    soup, ok, error = set_enrollment_number(
        session,
        soup,
        enrollment_no,
    )

    if not ok:
        raise RuntimeError(error)

    # 4. Extract the returned student data.
    result = extract_student_data(
        soup,
        enrollment_no,
        course_type,
    )

    return result


@app.route("/", methods=["GET"])
def home():
    return jsonify(
        {
            "status": "online",
            "service": "MP Bhoj Student API",
            "supported_course_types": ["UG", "PG"],
            "year": datetime.now().year,
        }
    )


@app.route("/api/mobile/test", methods=["POST", "OPTIONS"])
def mobile_test():
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(silent=True) or {}

    enrollment_no = str(
        data.get("enrollment_no", "")
    ).strip()

    course_type = normalize_course_type(
        data.get("course_type", "UG")
    )

    if not enrollment_no:
        return jsonify(
            {
                "success": False,
                "error": "Enrollment number is required",
            }
        ), 400

    try:
        result = fetch_student(
            enrollment_no,
            course_type=course_type,
        )

        return jsonify(
            {
                "success": True,
                "course_type": course_type,
                "result": result,
            }
        )

    except requests.RequestException as e:
        return jsonify(
            {
                "success": False,
                "error": (
                    "MP Bhoj connection failed: "
                    + str(e)
                ),
            }
        ), 502

    except Exception as e:
        return jsonify(
            {
                "success": False,
                "error": str(e),
            }
        ), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
    )
'''

path = Path("/mnt/data/api_index_ug_pg.py")
path.write_text(code, encoding="utf-8")

print(f"Created: {path}")
print("This file supports course_type=UG or PG in POST /api/mobile/test.")
