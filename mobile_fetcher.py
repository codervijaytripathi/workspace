"""
MP Bhoj Open University - Enrollment No. -> Mobile No. fetcher
Module version (Flask app ke liye) - selenium se real browser control
karke Migration.aspx form fill karta hai aur Mobile No. nikalta hai.
"""

import re
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

URL = "https://mpbou.mponline.gov.in/portal/Services/BHOJ/BrochureFee/Migration.aspx"
WAIT_TIMEOUT = 20
POLL_INTERVAL = 0.5


def xpath_literal(s):
    """Build a safe XPath string literal even if s contains ' or " characters."""
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    parts = s.split("'")
    return "concat('" + "', \"'\", '".join(parts) + "')"


def find_select_near_label(driver, label_text):
    lit = xpath_literal(label_text)
    xpaths = [
        f"//td[contains(normalize-space(.),{lit})]/following-sibling::td[1]//select",
        f"//td[contains(normalize-space(.),{lit})]/following::select[1]",
        f"//label[contains(normalize-space(.),{lit})]/following::select[1]",
        f"//*[contains(normalize-space(.),{lit})]/following::select[1]",
    ]
    for xp in xpaths:
        elems = driver.find_elements(By.XPATH, xp)
        if elems:
            return elems[0]
    raise NoSuchElementException(f"Select dropdown near label '{label_text}' not found")


def find_input_near_label(driver, label_text):
    lit = xpath_literal(label_text)
    xpaths = [
        f"//td[contains(normalize-space(.),{lit})]/following-sibling::td[1]//input",
        f"//td[contains(normalize-space(.),{lit})]/following::input[1]",
        f"//label[contains(normalize-space(.),{lit})]/following::input[1]",
        f"//*[contains(normalize-space(.),{lit})]/following::input[1]",
    ]
    for xp in xpaths:
        elems = driver.find_elements(By.XPATH, xp)
        if elems:
            return elems[0]
    raise NoSuchElementException(f"Input near label '{label_text}' not found")


def select_by_visible_text_contains(select_el, text_fragment):
    sel = Select(select_el)
    for option in sel.options:
        if text_fragment.lower() in option.text.lower():
            sel.select_by_visible_text(option.text)
            return
    raise NoSuchElementException(f"Option containing '{text_fragment}' not found in dropdown")

def setup_driver(headless=True):
    options = Options()

    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1400,1000")
    options.add_argument("--disable-notifications")

    driver = webdriver.Chrome(
        options=options
    )

    return driver

def _wait_for_postback(driver, old_ref, wait):
    try:
        wait.until(EC.staleness_of(old_ref))
    except TimeoutException:
        pass
    try:
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    except TimeoutException:
        pass
    time.sleep(0.5)


def setup_form(driver):
    driver.get(URL)
    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    course_type_select = find_select_near_label(driver, "Course Type")
    old_ref = course_type_select
    select_by_visible_text_contains(course_type_select, "UG")
    _wait_for_postback(driver, old_ref, wait)

    apply_for_select = find_select_near_label(driver, "Apply For")
    old_ref = apply_for_select
    select_by_visible_text_contains(apply_for_select, "No Objection Certificate")
    _wait_for_postback(driver, old_ref, wait)


from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException


def _fetch_one_impl(driver, enrollment_no):
    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    enrollment_input = find_input_near_label(driver, "Enrollment No")
    enrollment_input.clear()
    enrollment_input.send_keys(enrollment_no)

    old_ref = enrollment_input
    enrollment_input.send_keys("\t")  # Tab out to trigger postback/reload

    # Page might do a FULL postback (reload) - wait for the old element to
    # go stale (confirms navigation started), then wait for the new page
    # to finish loading. If it's actually just a partial (AJAX) update and
    # the element never goes stale, we just move on after a short timeout.
    try:
        wait.until(EC.staleness_of(old_ref))
    except TimeoutException:
        pass

    try:
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    except TimeoutException:
        pass

    time.sleep(0.5)  # small buffer for any post-render JS to settle

    mobile_input = find_input_near_label(driver, "Mobile No")

    start = time.time()
    mobile_value = ""
    while time.time() - start < WAIT_TIMEOUT:
        try:
            mobile_value = mobile_input.get_attribute("value") or ""
        except StaleElementReferenceException:
            mobile_input = find_input_near_label(driver, "Mobile No")
            mobile_value = mobile_input.get_attribute("value") or ""
        if mobile_value.strip():
            break
        time.sleep(POLL_INTERVAL)

    try:
        name_input = find_input_near_label(driver, "Candidate's Name")
        name_value = name_input.get_attribute("value") or ""
    except Exception:
        name_value = ""

    if mobile_value.strip():
        return {"Enrollment No": enrollment_no, "Candidate Name": name_value,
                "Mobile No": mobile_value.strip(), "Status": "Found"}
    else:
        return {"Enrollment No": enrollment_no, "Candidate Name": name_value,
                "Mobile No": "", "Status": "Not Found / No Response"}


def fetch_one(driver, enrollment_no, max_retries=3):
    """Wrapper with retry: stale element races can still slip through occasionally,
    so if one happens, just retry the whole lookup for this enrollment number."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            return _fetch_one_impl(driver, enrollment_no)
        except StaleElementReferenceException as e:
            last_exc = e
            time.sleep(1.0)
            continue
    raise last_exc


def test_single(enrollment_no, headless=False, screenshot_path=None):
    """One-off test: opens browser, sets up form, fetches one number, quits. Returns result dict."""
    driver = setup_driver(headless=headless)
    try:
        setup_form(driver)
        result = fetch_one(driver, enrollment_no)
        return result
    except Exception as e:
        if screenshot_path:
            try:
                driver.save_screenshot(str(screenshot_path))
            except Exception:
                pass
        raise
    finally:
        driver.quit()


def read_enrollment_numbers(input_path):
    import pandas as pd
    input_path = Path(input_path)
    if input_path.suffix.lower() in (".xlsx", ".xls", ".csv"):
        if input_path.suffix.lower() == ".csv":
            df = pd.read_csv(input_path, dtype=str)
        else:
            df = pd.read_excel(input_path, dtype=str)
        target_col = None
        for col in df.columns:
            if re.search(r'enroll|roll', str(col), re.IGNORECASE):
                target_col = col
                break
        if target_col is None:
            target_col = df.columns[0]
        numbers = df[target_col].dropna().astype(str).str.strip().tolist()
        return [n for n in numbers if n]
    elif input_path.suffix.lower() == ".pdf":
        import pdfplumber
        text = ""
        with pdfplumber.open(input_path) as pdf:
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
        matches = re.findall(r'\b[A-Z]?\d{10,12}\b', text)
        seen = set()
        numbers = []
        for m in matches:
            if m not in seen:
                seen.add(m)
                numbers.append(m)
        return numbers
    else:
        raise ValueError("Unsupported file type. Use .xlsx, .csv or .pdf")


def save_results_to_excel(results, output_path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "Mobile Numbers"

    headers = ["Enrollment No", "Candidate Name", "Mobile No", "Status"]
    ws.append(headers)

    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(name="Arial", bold=True, color="FFFFFF", size=11)
    thin = Side(style="thin", color="B7B7B7")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for col_idx, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=col_idx)
        c.fill = header_fill
        c.font = header_font
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = border

    for r in results:
        ws.append([r.get("Enrollment No", ""), r.get("Candidate Name", ""),
                   r.get("Mobile No", ""), r.get("Status", "")])

    for r in range(2, ws.max_row + 1):
        for c in range(1, len(headers) + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = Font(name="Arial", size=10)
            cell.border = border
            cell.alignment = Alignment(vertical="center")

    widths = {'A': 18, 'B': 26, 'C': 16, 'D': 26}
    for col, w in widths.items():
        ws.column_dimensions[col].width = w

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    wb.save(output_path)
