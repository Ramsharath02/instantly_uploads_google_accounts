#!/usr/bin/env python3

from seleniumbase import Driver  # type: ignore
import csv
import time
import traceback
import random
import os
import threading
import requests
import io
import re
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException, NoSuchElementException

# Google Sheet URLs (same as provided in the first script)
GOOGLE_SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/1UHIi8wuc1UGo-c6chZvmSygeLG1KopNY6FPY201Now4/export?format=csv&gid=0"
APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbwTcjjUR-LAvxDQw5ldjLVqo5kstZ2vVKZUfPLXBW1iY8b9GAJwcxL1yyOMaIydp2VJFw/exec"

# SMSPool API Configuration
# ⚠️ IMPORTANT: Replace this with your actual SMSPool API key from https://www.smspool.net/
SMSPOOL_KEY = "QeskwlgA2hjV3nm5kMZ5VWR2SjJ9y0Ly"  # ← PUT YOUR API KEY HERE

# Enable/Disable phone verification (True = enabled, False = disabled)
ENABLE_PHONE_VERIFICATION = True  # ← Set to False if you want to disable phone verification

# Create a global lock for thread-safe operations
csv_lock = threading.Lock()

# Create screenshots directory
SCREENSHOT_DIR = "screenshots"
if not os.path.exists(SCREENSHOT_DIR):
    os.makedirs(SCREENSHOT_DIR)

def read_google_sheet():
    response = requests.get(GOOGLE_SHEET_CSV_URL)
    response.raise_for_status()
    csv_file = io.StringIO(response.text)
    csv_reader = list(csv.reader(csv_file))
    header = csv_reader[0]
    rows = csv_reader[1:]
    for row in rows:
        if len(row) < 5:
            row.extend([""] * (5 - len(row)))  # Ensure at least 5 columns
        if len(row) < 6:
            row.append("pending")  # Add 'status' if not present
        if len(row) < 7:
            row.append("")  # Add 'max_parallel_tabs' if not present
    return header, rows

def update_status_in_sheet(email, status):
    max_retries = 3
    for attempt in range(max_retries):
        try:
            headers = {'Content-Type': 'application/json'}
            payload = {"email": email, "status": status}
            response = requests.post(APPS_SCRIPT_URL, json=payload, headers=headers, timeout=10)
            response.raise_for_status()  # Raise an exception for bad status codes (e.g., 4xx, 5xx)
            print(f"[{threading.current_thread().name}] Successfully updated status for {email} to '{status}': HTTP {response.status_code}, Response: {response.text}")
            return True
        except requests.exceptions.RequestException as e:
            print(f"[{threading.current_thread().name}] Failed to update status for {email} on attempt {attempt + 1}/{max_retries}: {str(e)}")
            if attempt < max_retries - 1:
                random_delay(2)  # Wait before retrying
            else:
                print(f"[{threading.current_thread().name}] All retries failed for updating status for {email}. Final error: {str(e)}")
                traceback.print_exc()
                return False

def random_delay(seconds):
    lower_bound = max(seconds - 1, 0)
    upper_bound = seconds + 1
    delay = random.uniform(lower_bound, upper_bound)
    time.sleep(delay)

def human_type(element, text):
    """Simulate human-like typing by sending keys one at a time with random delays."""
    for char in text:
        element.send_keys(char)
        time.sleep(random.uniform(0.05, 0.2))  # Random delay between 50ms and 200ms per character

def save_screenshot(driver, gemail, worker_id, step):
    """Save a screenshot with timestamp and account email."""
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    screenshot_path = os.path.join(SCREENSHOT_DIR, f"{gemail}_{timestamp}.png")
    try:
        driver.save_screenshot(screenshot_path)
        print(f"[{threading.current_thread().name}] Screenshot saved for {gemail} at {step}: {screenshot_path}")
        return screenshot_path
    except Exception as e:
        print(f"[{threading.current_thread().name}] Failed to save screenshot for {gemail} at {step}: {str(e)}")
        return None

def get_smspool_number():
    """
    Purchase/rent a phone number from SMSPool API.
    Returns the JSON response from the API on success, or None.
    """
    if not SMSPOOL_KEY:
        print(f"[{threading.current_thread().name}] SMSPool API key not configured")
        return None
    
    url = "https://api.smspool.net/purchase/sms"
    payload = {
        'create_token': 0,
        'country': 9,  # Country code (9 = USA, adjust as needed)
        'service': 395,  # Service ID for Google
        'pricing_option': 1,
        'quantity': 1,
        'pool': 3,
        'max_price': 0.50,
        'key': SMSPOOL_KEY
    }
    headers = {
        'Authorization': SMSPOOL_KEY
    }
    try:
        response = requests.post(url, headers=headers, data=payload, timeout=30)
        if response.status_code != 200:
            print(f"[{threading.current_thread().name}] Could not get number, status: {response.status_code}, response: {response.text}")
            return None
        
        data = response.json()
        print(f"[{threading.current_thread().name}] SMSPool getNumber response: {data}")
        return data
    except Exception as e:
        print(f"[{threading.current_thread().name}] SMSPool getNumber failed: {str(e)}")
        return None

def get_smspool_sms(orderid):
    """
    Polls SMSPool API for the SMS text for a given orderid.
    Returns the SMS string (OTP) or None on timeout/error.
    """
    if not SMSPOOL_KEY:
        print(f"[{threading.current_thread().name}] SMSPool API key not configured")
        return None
    
    url = "https://api.smspool.net/sms/check"
    payload = {
        'key': SMSPOOL_KEY,
        'orderid': orderid
    }
    headers = {
        'Authorization': SMSPOOL_KEY
    }
    
    try:
        response = requests.post(url, headers=headers, data=payload, timeout=30)
        print(f"[{threading.current_thread().name}] SMSPool getSMS initial response: {response.text}")
        
        if response.status_code != 200:
            print(f"[{threading.current_thread().name}] Error getting SMS, status: {response.status_code}")
            return None
        
        res = response.json()
        
        # If SMS present directly
        if "sms" in res and res["sms"]:
            print(f"[{threading.current_thread().name}] SMS found: {res['sms']}")
            return res["sms"]
        
        # Otherwise retry/poll a few times
        retries = 6
        while retries > 0:
            time_left = int(res.get("time_left", 30))
            if time_left <= 0:
                print(f"[{threading.current_thread().name}] SMSPool time_left expired")
                return None
            
            time.sleep(15)  # Wait before re-checking
            response = requests.post(url, headers=headers, data=payload, timeout=30)
            
            if response.status_code != 200:
                print(f"[{threading.current_thread().name}] Error getting SMS (poll), status: {response.status_code}")
                return None
            
            res = response.json()
            if "sms" in res and res["sms"]:
                print(f"[{threading.current_thread().name}] SMS received: {res['sms']}")
                return res["sms"]
            
            retries -= 1
        
        print(f"[{threading.current_thread().name}] SMSPool timeout - no SMS received")
        return None
    except Exception as e:
        print(f"[{threading.current_thread().name}] SMSPool getSMS failed: {str(e)}")
        return None

def handle_phone_verification(driver, gemail, worker_id):
    """
    Handles Google phone verification using SMSPool API.
    Returns True if verification was handled successfully, False otherwise.
    """
    if not ENABLE_PHONE_VERIFICATION:
        print(f"[{threading.current_thread().name}] Phone verification is disabled")
        return False
    
    try:
        print(f"[{threading.current_thread().name}] Checking for phone verification prompt...")
        random_delay(3)
        
        # Try to find phone number input field
        phone_input = None
        phone_selectors = [
            'input[id="phoneNumberId"]',
            'input[type="tel"]',
            'input[name="phoneNumber"]',
            'input[aria-label*="phone" i]',
            'input[aria-label*="number" i]'
        ]
        
        for selector in phone_selectors:
            try:
                phone_input = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                if phone_input:
                    print(f"[{threading.current_thread().name}] Phone input field found with selector: {selector}")
                    break
            except (TimeoutException, NoSuchElementException):
                continue
        
        if not phone_input:
            print(f"[{threading.current_thread().name}] No phone verification prompt detected")
            return False
        
        print(f"[{threading.current_thread().name}] Phone verification detected - requesting number from SMSPool...")
        
        # Get phone number from SMSPool
        number_data = get_smspool_number()
        if not number_data:
            print(f"[{threading.current_thread().name}] Failed to get phone number from SMSPool")
            return False
        
        # Extract phone number and order ID from response
        phone_number = None
        order_id = None
        
        # Try various key names for phone number
        for key in ("number", "phone", "msisdn", "phonenumber"):
            if key in number_data:
                phone_number = number_data[key]
                break
        
        # Try various key names for order ID
        for key in ("orderid", "order_id", "orderId", "id"):
            if key in number_data:
                order_id = number_data[key]
                break
        
        # Check nested structure if not found
        if not phone_number and "result" in number_data and isinstance(number_data["result"], dict):
            phone_number = number_data["result"].get("number") or number_data["result"].get("phone")
            order_id = number_data["result"].get("orderid") or number_data["result"].get("order_id")
        
        if not phone_number:
            print(f"[{threading.current_thread().name}] No phone number found in SMSPool response")
            return False
        
        print(f"[{threading.current_thread().name}] Acquired number: {phone_number}, order ID: {order_id}")
        
        # Format and enter phone number
        formatted_number = str(phone_number)
        if not formatted_number.startswith("+"):
            formatted_number = "+" + formatted_number.lstrip("0")
        
        phone_input.clear()
        human_type(phone_input, formatted_number)
        random_delay(2)
        print(f"[{threading.current_thread().name}] Entered phone number: {formatted_number}")
        
        # Click Next button
        next_btn_selectors = [
            "//button[.//span[text()='Next']]",
            "//button[contains(., 'Next')]",
            "//div[@role='button' and contains(., 'Next')]"
        ]
        
        next_clicked = False
        for selector in next_btn_selectors:
            try:
                next_btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                driver.execute_script("arguments[0].click();", next_btn)
                print(f"[{threading.current_thread().name}] Clicked Next button after phone number")
                next_clicked = True
                break
            except:
                continue
        
        if not next_clicked:
            print(f"[{threading.current_thread().name}] Warning: Could not click Next button, attempting to continue...")
        
        random_delay(5)
        
        # Wait for and retrieve SMS code
        if not order_id:
            print(f"[{threading.current_thread().name}] No order ID available for SMS polling")
            return False
        
        print(f"[{threading.current_thread().name}] Polling for SMS code...")
        sms_text = get_smspool_sms(order_id)
        
        if not sms_text:
            print(f"[{threading.current_thread().name}] Failed to retrieve SMS code")
            save_screenshot(driver, gemail, worker_id, "phone_verification_no_sms")
            return False
        
        # Extract OTP code from SMS (typically 4-8 digits)
        otp_match = re.search(r"(\d{4,8})", sms_text)
        if otp_match:
            otp_code = otp_match.group(1)
        else:
            # If no digits found, use the whole SMS text
            otp_code = sms_text.strip()
        
        print(f"[{threading.current_thread().name}] OTP code extracted: {otp_code}")
        
        # Find and fill OTP input field
        otp_input = None
        otp_selectors = [
            'input[id="idvAnyPhonePin"]',
            'input[type="tel"]',
            'input[name="pin"]',
            'input[aria-label*="code" i]',
            'input[aria-label*="verification" i]'
        ]
        
        for selector in otp_selectors:
            try:
                otp_input = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                if otp_input:
                    print(f"[{threading.current_thread().name}] OTP input field found with selector: {selector}")
                    break
            except:
                continue
        
        if not otp_input:
            # Try finding inputs by maxlength attribute (OTP fields typically have maxlength 6-8)
            try:
                inputs = driver.find_elements(By.CSS_SELECTOR, 'input[type="tel"], input[type="text"]')
                for inp in inputs:
                    try:
                        maxlen = inp.get_attribute("maxlength")
                        if maxlen and 4 <= int(maxlen) <= 8:
                            otp_input = inp
                            break
                    except:
                        pass
            except:
                pass
        
        if not otp_input:
            print(f"[{threading.current_thread().name}] Could not find OTP input field")
            save_screenshot(driver, gemail, worker_id, "phone_verification_no_otp_input")
            return False
        
        # Enter OTP code
        otp_input.clear()
        human_type(otp_input, otp_code)
        random_delay(2)
        print(f"[{threading.current_thread().name}] Entered OTP code")
        
        # Click Next/Verify button
        verify_clicked = False
        for selector in next_btn_selectors:
            try:
                verify_btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                driver.execute_script("arguments[0].click();", verify_btn)
                print(f"[{threading.current_thread().name}] Clicked Next/Verify button after OTP")
                verify_clicked = True
                break
            except:
                continue
        
        if not verify_clicked:
            print(f"[{threading.current_thread().name}] Warning: Could not click Next/Verify button")
        
        random_delay(5)
        print(f"[{threading.current_thread().name}] Phone verification completed successfully")
        return True
        
    except Exception as e:
        print(f"[{threading.current_thread().name}] Error during phone verification: {str(e)}")
        traceback.print_exc()
        save_screenshot(driver, gemail, worker_id, "phone_verification_error")
        return False

def find_and_click_element(driver, selectors, description, worker_id, timeout=15, use_js_click=False):
    """Helper function to find and click an element with multiple selectors, with optional JS click"""
    for selector in selectors:
        try:
            # Check within iframes
            iframes = driver.find_elements(By.TAG_NAME, 'iframe')
            for iframe in iframes:
                driver.switch_to.frame(iframe)
                try:
                    element = WebDriverWait(driver, timeout).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                    if use_js_click:
                        driver.execute_script("arguments[0].click();", element)
                        print(f"[{threading.current_thread().name}] JavaScript clicked {description} in iframe using selector: {selector}")
                    else:
                        element.click()
                        print(f"[{threading.current_thread().name}] Clicked {description} in iframe using selector: {selector}")
                    driver.switch_to.default_content()
                    return True
                except:
                    driver.switch_to.default_content()
                    continue
            # Try outside iframe as fallback
            driver.switch_to.default_content()
            try:
                element = WebDriverWait(driver, timeout).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                if use_js_click:
                    driver.execute_script("arguments[0].click();", element)
                    print(f"[{threading.current_thread().name}] JavaScript clicked {description} using selector: {selector}")
                else:
                    element.click()
                    print(f"[{threading.current_thread().name}] Clicked {description} using selector: {selector}")
                return True
            except:
                continue
        except:
            driver.switch_to.default_content()
            continue
    print(f"[{threading.current_thread().name}] {description} not found with any selector")
    return False

def process_single_account(gemail, gpassword, instantly_email, instantly_password, worker_id):
    driver = None
    try:
        # Create unique user data directory for each browser instance
        user_data_dir = f"/tmp/seleniumbase_user_data_{worker_id}_{os.getpid()}_{random.randint(1000, 9999)}"
        driver = Driver(
            uc=True,
            incognito=True,
            user_data_dir=user_data_dir,
            headless=False  # Set to False for debugging; can be True for production
        )
        driver.maximize_window()

        # Step 1: Navigate to Instantly login page
        try:
            url = 'https://app.instantly.ai/app/accounts'
            driver.get(url)
            random_delay(3)
            print(f"[{threading.current_thread().name}] Navigated to Instantly login page for {gemail}")
        except Exception as e:
            error_message = f"Failed to navigate to Instantly login: {str(e)}"
            save_screenshot(driver, gemail, worker_id, "navigate_instantly")
            update_status_in_sheet(gemail, f"issue: {error_message}")
            raise Exception(error_message)

        # Step 2: Login to Instantly - Email
        print(f"[{threading.current_thread().name}] Logging in to Instantly for {gemail}...")
        for attempt in range(3):
            try:
                email_input = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='email']"))
                )
                email_input.clear()
                human_type(email_input, instantly_email)
                random_delay(1)
                print(f"[{threading.current_thread().name}] Entered Instantly email")
                break
            except (StaleElementReferenceException, TimeoutException, NoSuchElementException) as e:
                print(f"[{threading.current_thread().name}] Retry {attempt + 1}/3 for email input: {str(e)}")
                random_delay(2)
                if attempt == 2:
                    error_message = f"Failed to enter Instantly email: {str(e)}"
                    save_screenshot(driver, gemail, worker_id, "instantly_email_input")
                    update_status_in_sheet(gemail, f"issue: {error_message}")
                    raise Exception(error_message)

        # Step 3: Login to Instantly - Password
        for attempt in range(3):
            try:
                password_input = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='password']"))
                )
                password_input.clear()
                human_type(password_input, instantly_password)
                random_delay(1)
                print(f"[{threading.current_thread().name}] Entered Instantly password")
                break
            except (StaleElementReferenceException, TimeoutException, NoSuchElementException) as e:
                print(f"[{threading.current_thread().name}] Retry {attempt + 1}/3 for password input: {str(e)}")
                random_delay(2)
                if attempt == 2:
                    error_message = f"Failed to enter Instantly password: {str(e)}"
                    save_screenshot(driver, gemail, worker_id, "instantly_password_input")
                    update_status_in_sheet(gemail, f"issue: {error_message}")
                    raise Exception(error_message)

        # Step 4: Click Login Button
        for attempt in range(3):
            try:
                driver.click('button[form="loginForm"]', timeout=30)
                random_delay(5)
                print(f"[{threading.current_thread().name}] Clicked Instantly login button")
                break
            except (StaleElementReferenceException, TimeoutException, NoSuchElementException) as e:
                print(f"[{threading.current_thread().name}] Retry {attempt + 1}/3 for login button: {str(e)}")
                random_delay(2)
                if attempt == 2:
                    error_message = f"Failed to click Instantly login button: {str(e)}"
                    save_screenshot(driver, gemail, worker_id, "instantly_login_button")
                    update_status_in_sheet(gemail, f"issue: {error_message}")
                    raise Exception(error_message)

        # Step 5: Click Add New
        print(f"[{threading.current_thread().name}] Starting Gmail account addition process...")
        for attempt in range(3):
            try:
                add_new_btn = '//button[contains(., "Add New")]'
                driver.wait_for_element_visible(add_new_btn, timeout=15)
                driver.click(add_new_btn)
                random_delay(2)
                print(f"[{threading.current_thread().name}] Clicked Add New button")
                break
            except (StaleElementReferenceException, TimeoutException, NoSuchElementException) as e:
                print(f"[{threading.current_thread().name}] Retry {attempt + 1}/3 for Add New button: {str(e)}")
                random_delay(2)
                if attempt == 2:
                    error_message = f"Failed to click Add New button: {str(e)}"
                    save_screenshot(driver, gemail, worker_id, "add_new_button")
                    update_status_in_sheet(gemail, f"issue: {error_message}")
                    raise Exception(error_message)

        # Step 6: Click Gmail option
        for attempt in range(3):
            try:
                gmail_option = "(//h6[text()='Gmail / G-Suite'])[2]"
                driver.wait_for_element_visible(gmail_option, timeout=30)
                driver.click(gmail_option)
                random_delay(2)
                print(f"[{threading.current_thread().name}] Clicked Gmail option")
                break
            except (StaleElementReferenceException, TimeoutException, NoSuchElementException) as e:
                print(f"[{threading.current_thread().name}] Retry {attempt + 1}/3 for Gmail option: {str(e)}")
                random_delay(2)
                if attempt == 2:
                    error_message = f"Failed to click Gmail option: {str(e)}"
                    save_screenshot(driver, gemail, worker_id, "gmail_option")
                    update_status_in_sheet(gemail, f"issue: {error_message}")
                    raise Exception(error_message)

        # Step 7: Click OAuth option
        for attempt in range(3):
            try:
                oauth_option = '//div[h6[text()="Option 1: oAuth"]]'
                driver.wait_for_element_visible(oauth_option, timeout=30)
                driver.click(oauth_option)
                random_delay(3)
                print(f"[{threading.current_thread().name}] Clicked OAuth option")
                break
            except (StaleElementReferenceException, TimeoutException, NoSuchElementException) as e:
                print(f"[{threading.current_thread().name}] Retry {attempt + 1}/3 for OAuth option: {str(e)}")
                random_delay(2)
                if attempt == 2:
                    error_message = f"Failed to click OAuth option: {str(e)}"
                    save_screenshot(driver, gemail, worker_id, "oauth_option")
                    update_status_in_sheet(gemail, f"issue: {error_message}")
                    raise Exception(error_message)

        # Step 8: Click Login button
        for attempt in range(3):
            try:
                login_btn = '//button[h6[text()="Login"]]'
                driver.wait_for_element_visible(login_btn, timeout=30)
                driver.click(login_btn)
                random_delay(3)
                print(f"[{threading.current_thread().name}] Clicked Login button for OAuth")
                break
            except (StaleElementReferenceException, TimeoutException, NoSuchElementException) as e:
                print(f"[{threading.current_thread().name}] Retry {attempt + 1}/3 for OAuth Login button: {str(e)}")
                random_delay(2)
                if attempt == 2:
                    error_message = f"Failed to click OAuth Login button: {str(e)}"
                    save_screenshot(driver, gemail, worker_id, "oauth_login_button")
                    update_status_in_sheet(gemail, f"issue: {error_message}")
                    raise Exception(error_message)

        # Step 9: Switch to Google login window
        try:
            driver.switch_to_newest_window()
            random_delay(3)
            print(f"[{threading.current_thread().name}] Switched to Google login window...")
        except Exception as e:
            error_message = f"Failed to switch to Google login window: {str(e)}"
            save_screenshot(driver, gemail, worker_id, "switch_window")
            update_status_in_sheet(gemail, f"issue: {error_message}")
            raise Exception(error_message)

        # Step 10: Click "Use another account" if present
        try:
            another_account = '//div[text()="Use another account"]'
            if driver.wait_for_element_visible(another_account, timeout=5):
                driver.click(another_account)
                random_delay(2)
                print(f"[{threading.current_thread().name}] Clicked Use another account")
        except Exception as e:
            print(f"[{threading.current_thread().name}] Could not click Use another account (optional): {str(e)}")

        # Step 11: Enter Gmail credentials - Email
        print(f"[{threading.current_thread().name}] Entering Gmail credentials...")
        for attempt in range(3):
            try:
                email_input = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='email']"))
                )
                email_input.clear()
                human_type(email_input, gemail)
                random_delay(1)
                next_btn = "//button[.//span[text()='Next']]"
                driver.wait_for_element_visible(next_btn, timeout=30)
                driver.click(next_btn)
                random_delay(5)
                print(f"[{threading.current_thread().name}] Entered Gmail email and clicked Next")
                break
            except (StaleElementReferenceException, TimeoutException, NoSuchElementException) as e:
                print(f"[{threading.current_thread().name}] Retry {attempt + 1}/3 for Gmail email input: {str(e)}")
                random_delay(2)
                if attempt == 2:
                    error_message = f"Failed to enter Gmail email or click Next: {str(e)}"
                    save_screenshot(driver, gemail, worker_id, "gmail_email_input")
                    update_status_in_sheet(gemail, f"issue: {error_message}")
                    raise Exception(error_message)

        # Step 12: Enter Gmail credentials - Password
        for attempt in range(3):
            try:
                password_input = WebDriverWait(driver, 30).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='password']"))
                )
                password_input.clear()
                human_type(password_input, gpassword)
                random_delay(1)
                next_btn = "//button[.//span[text()='Next']]"
                driver.wait_for_element_visible(next_btn, timeout=30)
                driver.click(next_btn)
                random_delay(5)
                print(f"[{threading.current_thread().name}] Entered Gmail password and clicked Next")
                break
            except (StaleElementReferenceException, TimeoutException, NoSuchElementException) as e:
                print(f"[{threading.current_thread().name}] Retry {attempt + 1}/3 for Gmail password input: {str(e)}")
                random_delay(2)
                if attempt == 2:
                    error_message = f"Failed to enter Gmail password or click Next: {str(e)}"
                    save_screenshot(driver, gemail, worker_id, "gmail_password_input")
                    update_status_in_sheet(gemail, f"issue: {error_message}")
                    raise Exception(error_message)

        # Step 12.5: Handle phone verification if present (OPTIONAL)
        print(f"[{threading.current_thread().name}] Checking for phone verification...")
        try:
            phone_verification_handled = handle_phone_verification(driver, gemail, worker_id)
            if phone_verification_handled:
                print(f"[{threading.current_thread().name}] Phone verification was successfully handled")
                random_delay(3)
            else:
                print(f"[{threading.current_thread().name}] No phone verification required or feature disabled")
        except Exception as e:
            print(f"[{threading.current_thread().name}] Phone verification check failed (non-critical): {str(e)}")
            # Don't fail the entire process if phone verification check fails
            # It might just not be needed

        # Step 13: Handle "I understand" if present with 5-second timeout
        print(f"[{threading.current_thread().name}] Checking for 'I understand' button...")
        random_delay(2)
        i_understand_clicked = False
        understand_btns = [
            '//input[@id="confirm"]',  # Primary selector from provided HTML
            '//input[@name="confirm"]',
            '//input[@value="I understand"]',
            '//input[contains(@class, "MK9CEd") and @type="submit"]',
            '//input[@type="submit" and contains(@value, "I understand")]'
        ]
        try:
            for understand_btn in understand_btns:
                try:
                    # Check within iframes
                    iframes = driver.find_elements(By.TAG_NAME, 'iframe')
                    for iframe in iframes:
                        driver.switch_to.frame(iframe)
                        try:
                            element = WebDriverWait(driver, 5).until(
                                EC.element_to_be_clickable((By.XPATH, understand_btn))
                            )
                            # Try JavaScript click first
                            try:
                                driver.execute_script("arguments[0].click();", element)
                                print(f"[{threading.current_thread().name}] JavaScript clicked 'I understand' in iframe using selector: {understand_btn}")
                                i_understand_clicked = True
                                random_delay(2)
                                driver.switch_to.default_content()
                                break
                            except Exception as e:
                                print(f"[{threading.current_thread().name}] JS click failed for 'I understand' in iframe: {str(e)}")
                                # Fallback to regular click
                                element.click()
                                print(f"[{threading.current_thread().name}] Clicked 'I understand' in iframe using selector: {understand_btn}")
                                i_understand_clicked = True
                                random_delay(2)
                                driver.switch_to.default_content()
                                break
                        except (StaleElementReferenceException, TimeoutException, NoSuchElementException):
                            driver.switch_to.default_content()
                            continue
                    if i_understand_clicked:
                        break
                    # Check outside iframe
                    driver.switch_to.default_content()
                    try:
                        element = WebDriverWait(driver, 5).until(
                            EC.element_to_be_clickable((By.XPATH, understand_btn))
                        )
                        # Try JavaScript click first
                        try:
                            driver.execute_script("arguments[0].click();", element)
                            print(f"[{threading.current_thread().name}] JavaScript clicked 'I understand' using selector: {understand_btn}")
                            i_understand_clicked = True
                            random_delay(2)
                            break
                        except Exception as e:
                            print(f"[{threading.current_thread().name}] JS click failed for 'I understand': {str(e)}")
                            # Fallback to regular click
                            element.click()
                            print(f"[{threading.current_thread().name}] Clicked 'I understand' using selector: {understand_btn}")
                            i_understand_clicked = True
                            random_delay(2)
                            break
                    except (StaleElementReferenceException, TimeoutException, NoSuchElementException):
                        continue
                except Exception as e:
                    print(f"[{threading.current_thread().name}] Error checking 'I understand' with selector {understand_btn}: {str(e)}")
                    driver.switch_to.default_content()
                    continue
            if not i_understand_clicked:
                print(f"[{threading.current_thread().name}] 'I understand' button not found within 5 seconds, proceeding to Continue")
        except Exception as e:
            print(f"[{threading.current_thread().name}] General error checking 'I understand' button: {str(e)}")
            save_screenshot(driver, gemail, worker_id, "i_understand_button")
            driver.switch_to.default_content()

        # Step 14: Click Continue (10 second timeout - close browser if not found)
        print(f"[{threading.current_thread().name}] Attempting to click Continue button (10 second timeout)...")
        try:
            continue_btns = [
                '//button[span[text()="Continue"]]',
                '//button[contains(., "Continue")]',
                '//button[span[contains(text(), "Continue")]]',
                '//div[@role="button" and contains(., "Continue")]',
                '//button[@id="submit"]',
                '//button[@type="button" and contains(., "Continue")]',
                '//button[contains(@class, "VfPpkd-LgbsSe") and contains(., "Continue")]',
                '//button[@jsname="LgbsSe"]',
                '//input[@type="submit" and contains(@value, "Continue")]',
                '//button[contains(text(), "Continue")]'
            ]
            if find_and_click_element(driver, continue_btns, "Continue button", worker_id, timeout=10, use_js_click=True):
                random_delay(5)
                print(f"[{threading.current_thread().name}] Successfully clicked Continue button")
            else:
                error_message = "Continue button not found within 10 seconds - closing browser"
                print(f"[{threading.current_thread().name}] {error_message}")
                save_screenshot(driver, gemail, worker_id, "continue_button_not_found")
                update_status_in_sheet(gemail, f"issue: {error_message}")
                # Close browser immediately
                driver.quit()
                print(f"[{threading.current_thread().name}] Browser closed for {gemail} - Continue button timeout")
                return False
        except (StaleElementReferenceException, TimeoutException, NoSuchElementException) as e:
            error_message = f"Continue button not found within 10 seconds: {str(e)}"
            print(f"[{threading.current_thread().name}] {error_message}")
            save_screenshot(driver, gemail, worker_id, "continue_button_error")
            update_status_in_sheet(gemail, f"issue: {error_message}")
            # Close browser immediately
            driver.quit()
            print(f"[{threading.current_thread().name}] Browser closed for {gemail} - Continue button error")
            return False

        # Step 15: Click Allow (10 second timeout - close browser if not found)
        print(f"[{threading.current_thread().name}] Attempting to click Allow button (10 second timeout)...")
        try:
            allow_btn = '//button[span[text()="Allow"]]'
            driver.wait_for_element_visible(allow_btn, timeout=10)
            driver.click(allow_btn)
            random_delay(5)
            print(f"[{threading.current_thread().name}] Clicked Allow button")
            
            # Update status to "done" after successful Allow button click
            print(f"[{threading.current_thread().name}] Updating status to 'done' for {gemail}")
            update_status_in_sheet(gemail, 'done')
            print(f"[{threading.current_thread().name}] Successfully updated status to 'done' for {gemail}")
        except (StaleElementReferenceException, TimeoutException, NoSuchElementException) as e:
            error_message = f"Allow button not found within 10 seconds: {str(e)}"
            print(f"[{threading.current_thread().name}] {error_message}")
            save_screenshot(driver, gemail, worker_id, "allow_button_not_found")
            update_status_in_sheet(gemail, f"issue: {error_message}")
            # Close browser immediately
            driver.quit()
            print(f"[{threading.current_thread().name}] Browser closed for {gemail} - Allow button timeout")
            return False

        # Step 16: Switch back to main window
        try:
            driver.switch_to_newest_window()
            print(f"[{threading.current_thread().name}] Switched back to main window...")
        except Exception as e:
            error_message = f"Failed to switch back to main window: {str(e)}"
            save_screenshot(driver, gemail, worker_id, "switch_back_window")
            update_status_in_sheet(gemail, f"issue: {error_message}")
            raise Exception(error_message)

        # Step 17: Optional verification (removed problematic timeout parameter)
        print(f"[{threading.current_thread().name}] Verifying connection status for {gemail}...")
        random_delay(5)
        try:
            success_msg = '//div[@role="status" and @aria-live="polite" and text()="Connected"]'
            if driver.is_element_visible(success_msg):
                print(f"[{threading.current_thread().name}] Success message found for {gemail}")
            else:
                print(f"[{threading.current_thread().name}] Success message not found, but process completed successfully")
        except Exception as e:
            print(f"[{threading.current_thread().name}] Error checking success message (non-critical): {str(e)}")

        # Try clicking Back button if present
        try:
            back_btn = '//span//h6[text()="Back"]'
            if driver.is_element_visible(back_btn):
                driver.click(back_btn)
                random_delay(3)
                print(f"[{threading.current_thread().name}] Clicked Back button")
        except Exception as e:
            print(f"[{threading.current_thread().name}] Could not click Back button (optional): {str(e)}")

        # Final success message
        print(f"[{threading.current_thread().name}] Successfully completed process for {gemail}")
        return True

    except Exception as e:
        print(f"[{threading.current_thread().name}] Error processing {gemail}: {str(e)}")
        traceback.print_exc()
        # Update status to "issue" for any error that occurs
        update_status_in_sheet(gemail, f"issue: {str(e)}")
        return False
    finally:
        if driver:
            try:
                driver.quit()
                print(f"[{threading.current_thread().name}] Browser closed for {gemail}")
            except Exception as e:
                print(f"[{threading.current_thread().name}] Failed to close browser for {gemail}: {str(e)}")

def main():
    DEFAULT_MAX_PARALLEL_TABS = 1
    header, rows = read_google_sheet()

    # Dynamically fetch MAX_PARALLEL_TABS from sheet column
    try:
        if "max_parallel_tabs" in header:
            idx = header.index("max_parallel_tabs")
            MAX_PARALLEL_TABS = int(rows[0][idx]) if rows[0][idx].strip().isdigit() else DEFAULT_MAX_PARALLEL_TABS
        else:
            MAX_PARALLEL_TABS = DEFAULT_MAX_PARALLEL_TABS
    except Exception as e:
        print(f"Error reading max_parallel_tabs from sheet: {e}")
        MAX_PARALLEL_TABS = DEFAULT_MAX_PARALLEL_TABS

    print(f"🚀 MAX_PARALLEL_TABS set to {MAX_PARALLEL_TABS}")

    for i in range(0, len(rows), MAX_PARALLEL_TABS):
        batch = rows[i:i + MAX_PARALLEL_TABS]
        threads = []
        for row in batch:
            status = row[4].strip().lower()
            if status == "done":
                continue
            gemail = row[0].strip()
            gpassword = row[1].strip()
            instantly_email = row[2].strip()
            instantly_password = row[3].strip()
            worker_id = f"thread_{i+1}"
            thread = threading.Thread(target=process_single_account, args=(gemail, gpassword, instantly_email, instantly_password, worker_id))
            threads.append(thread)
            thread.start()
            time.sleep(1.5)
        for thread in threads:
            thread.join()

    print("✅ All accounts processed.")

if __name__ == "__main__":
    main()
