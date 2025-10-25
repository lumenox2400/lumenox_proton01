# Data Processing
import os
import re
import time
import json
import numpy as np
import pandas as pd
from datetime import date, datetime, timedelta

# Scraper
from playwright.sync_api import sync_playwright, TimeoutError
from bs4 import BeautifulSoup

# Send Email
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Google Apis
import gspread
from oauth2client.service_account import ServiceAccountCredentials


class LumeProton00:
    # 00. Initialize variables
    def __init__(self, config, api_drive):

        # Define variable for the day
        self.today = pd.Timestamp.now()

        # Save config
        self.config = config

        # Web page
        self.page_id = self.config['global']['page_id']

        # Variables to notify by email
        self.sender_email = self.config['global']['sender_email']
        self.password_email = self.config['global']['password_email']
        self.recipient_email = self.config['global']['recipient_email']

        # Variable of the number of months to go through in the calendars
        self.months_to_extract = self.config['global']['months_to_extract']

        # Variable of the number of days between the current date and the appointment to change status
        # And not to continue advancing this person
        self.motionless_days = self.config['global']['motionless_days']

        # Initialize Google Sheets for writing
        scope = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(api_drive, scope)
        self.client = gspread.authorize(creds)
        self.spreadsheet = self.client.open_by_key(
            self.config['global']['admin_sheet_id']
        )



    # 01. Read drive, update and select the user to overtake
    def read_drive(self):
        # 00. Initialize variables
        self.final_msj = None
        self.username_chosen = None
        self.password_chosen = None
        self.type_appointment = None
        self.diff_bios = None

        # 01. Read user sheet
        self.sheet_users = self.spreadsheet.worksheet("forms")
        self.data_users = self.sheet_users.get_all_records()
        self.df_users = pd.DataFrame(self.data_users)
        self.headers = self.sheet_users.row_values(1)

        # 02. Read log sheet
        self.sheet_log = self.spreadsheet.worksheet("log")
        self.data_log = self.sheet_log.get_all_records()
        self.df_log = pd.DataFrame(self.data_log)
        self.headers = self.sheet_log.row_values(1)

        # 03. Identify users to exclude
        # # Exclude those who are already ahead of schedule, so as not to waste server time
        users_to_exclude = self.df_log.loc[self.df_log.iloc[:, 8] == 'TRUE', self.df_log.columns[2]].unique().tolist()
        df_users_copy = self.df_users[~self.df_users.iloc[:, 2].isin(users_to_exclude)].reset_index(drop=True)

        # 04. Create a copy of the sheet to define the user to advance
        # df_users_copy = self.df_users.copy()
        n_cols = df_users_copy.shape[1]
        df_users_copy.columns = [f"col_{i}" for i in range(n_cols)]

        # 05. Sort and create viability rules for users
        # # Default value
        df_users_copy["viable"] = False

        # # - If col_11 (Adelantado) is blank or = 'NO'  → no restriction
        # # - If col_12 (Estado del negocio) = 'Activo' → business continues
        df_users_copy.loc[
            ((df_users_copy["col_11"].isna()) | df_users_copy["col_11"] == 0 |(df_users_copy["col_11"].str.upper() == "NO")) &
            (df_users_copy["col_12"].str.upper() == "ACTIVO"),
            "viable"
        ] = True

        # # Handle month restrictions
        # # If there are future date restrictions, mark as not viable until the month has passed
        df_users_copy["col_7"] = pd.to_datetime(df_users_copy["col_7"], errors="coerce")
        df_users_copy.loc[
            df_users_copy["col_7"].dt.month > self.today.month,
            "viable"
        ] = False

        # 06. Select user
        df_candidates = df_users_copy[df_users_copy["viable"] == True].copy()
        df_candidates = df_candidates[1:]

        if df_candidates.empty:
            self.username_chosen = None
            self.password_chosen = None
            self.type_appointment = None
            self.diff_bios = None
            self.final_msj = "Ningún usuario cumple reglas de adelantamiento"
        else:
            self.username_chosen = df_candidates.col_2.iloc[0]
            self.password_chosen = df_candidates.col_3.iloc[0]
            self.type_appointment = df_candidates.col_4.iloc[0]
            self.diff_bios = df_candidates.col_8.iloc[0]
            self.final_msj = "Usuario seleccionado"

        return self.username_chosen, self.password_chosen



    # 02. Overtaking process
    def overtaking_process(self, username_chosen, password_chosen, type_chosen=None):

        # Initialize variables
        self.appointment_date = None
        self.user_problem = None
        self.reschedule_success = False
        self.appointment_date_new = None
        self.appointment_hour_new = None
        self.biometrics_date_new = None
        self.biometrics_hour_new = None

        with sync_playwright() as p:
            # 00. Activate browser and create new page
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--window-size=1280,800",
                ],
            )
            # Add human-like browser context
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )

            page = context.new_page()

            # 01. Parameters to iterate
            month_mapping = {
                'enero': 'January', 'febrero': 'February', 'marzo': 'March', 'abril': 'April',
                'mayo': 'May', 'junio': 'June', 'julio': 'July', 'agosto': 'August',
                'septiembre': 'September', 'octubre': 'October', 'noviembre': 'November', 'diciembre': 'December'
            }
            pattern = re.compile(r'\b(' + '|'.join(month_mapping.keys()) + r')\b')
            month_mapping_num = {
                'January':1, 'February':2, 'March':3, 'April':4,
                'May':5, 'June':6, 'July':7, 'August':8,
                'September':9, 'October':10, 'November':11, 'December':12
            }


            # 02. Enter the page and log in
            try:
                page.goto(self.page_id)
                page.wait_for_selector('#user_email', state='visible', timeout=100000)
                # Now disable animations for better stability
                page.add_style_tag(content="* { transition: none !important; animation: none !important; }")
                page.fill("#user_email", username_chosen)
                page.fill("#user_password", password_chosen)
                page.check("#policy_confirmed", force=True)
                page.click('input[type="submit"]')
                page.wait_for_load_state("networkidle")
                self.final_msj = f"{self.final_msj} | Pagina de logeo habilitada"

            except Exception as e:
                self.final_msj = f"{self.final_msj} | Pagina de logeo no habilitada: {e}"
                return self.final_msj


            # 03. Extract current appointment
            status_text = page.inner_text("h4.status").strip()
            new_appointment = None

            if "Programe la cita" in status_text:
                # Doesnt have an appointment
                new_appointment = True
                self.appointment_date = (self.today + pd.DateOffset(years=1))
            else:
                # Reschedule
                new_appointment = False
                appointment_text = page.inner_text(".consular-appt")
                match = re.search(r'(\d{1,2}) (\w+), (\d{4})', appointment_text)
                date_str = match.group(0).replace('\t', ' ')
                date_str_en = pattern.sub(lambda m: month_mapping[m.group(0)], date_str)
                self.appointment_date = pd.to_datetime(date_str_en, format="%d %B, %Y", dayfirst=True, errors='coerce')


            # 04. Validate, if the appointment is within the range of months, notify to deactivate user
            months_diff = (self.appointment_date.year - self.today.year) * 12 + (self.appointment_date.month - self.today.month)
            if months_diff <= self.months_to_extract:
                # Break function
                self.final_msj = f"{self.final_msj} | Cita dentro de {self.months_to_extract} meses | No elegible"
                self.user_problem = self.username_chosen
                self.reschedule_success = True
                return self.final_msj
            else:
                # Continue process
                self.final_msj = f"{self.final_msj} | Cita fuera de rango | Elegible"


            # 05. Click on "Continue"
            try:
                # Try first button
                continue_button = page.wait_for_selector(
                    'xpath=//*[@id="main"]/div[2]/div[2]/div[1]/div/div/div[1]/div[2]/ul/li/a',
                    state='visible', timeout=60000
                )
                continue_button.click()
                page.wait_for_load_state("networkidle")
            except:
                try:
                    # Try second button
                    continue_button = page.wait_for_selector(
                        'xpath=//*[@id="main"]/div[2]/div[3]/div[1]/div/div[1]/div[1]/div[2]/ul/li/a',
                        state='visible', timeout=60000
                    )
                    continue_button.click()
                    page.wait_for_load_state("networkidle")
                except Exception as e:
                    self.final_msj = f"{self.final_msj} | Ninguno de los botones fue encontrado: {e}"
                    return self.final_msj


            # 06. Go to "Reschedule Appointment" or "Schedule Appointment"
            # Differentiating between new and rescheduling users
            if new_appointment:
                # First time
                reschedule_button = page.wait_for_selector('.fa-calendar-alt', state='visible', timeout=60000)
                reschedule_button.click()

                # Confirm "Schedule Appointment"
                reschedule_confirm_button = page.wait_for_selector(
                    'xpath=//a[@class="button small primary small-only-expanded" and text()="Programe la cita"]',
                    state='visible',
                    timeout=60000
                )
                reschedule_confirm_button.click()
                page.wait_for_load_state("networkidle")
            else:
                # Reschedule
                reschedule_button = page.wait_for_selector('.fa-calendar-minus', state='visible', timeout=60000)
                reschedule_button.click()

                # Confirm "Reschedule Appointment"
                reschedule_confirm_button = page.wait_for_selector(
                    'xpath=//a[@class="button small primary small-only-expanded" and text()="Reprogramar cita"]',
                    state='visible',
                    timeout=60000
                )
                reschedule_confirm_button.click()
                page.wait_for_load_state("networkidle")


            # 07. Make maximum retries for the calendar to appear
            max_retries = 20
            retry_count = 0

            while retry_count < max_retries:
                try:
                    calendar_input = page.query_selector('#appointments_consulate_appointment_date')

                    if calendar_input and calendar_input.is_visible():
                        self.final_msj = f"{self.final_msj} | Aparece calendario"
                        break
                    else:
                        raise TimeoutError("Input del calendario no visible aún.")
                except TimeoutError:
                    retry_count += 1
                    print(f"Intento {retry_count}: calendario no disponible, refrescando...")
                    time.sleep(30)

                    page.go_back()
                    page.wait_for_load_state("networkidle")

                    if new_appointment:
                        # First time
                        reschedule_button = page.wait_for_selector('.fa-calendar-alt', state='visible', timeout=60000)
                        reschedule_button.click()

                        # Confirm "Schedule Appointment"
                        reschedule_confirm_button = page.wait_for_selector(
                            'xpath=//a[@class="button small primary small-only-expanded" and text()="Programe la cita"]',
                            state='visible',
                            timeout=60000
                        )
                        reschedule_confirm_button.click()
                        page.wait_for_load_state("networkidle")
                    else:
                        # Reschedule
                        reschedule_button = page.wait_for_selector('.fa-calendar-minus', state='visible', timeout=60000)
                        reschedule_button.click()

                        # Confirm "Reschedule Appointment"
                        reschedule_confirm_button = page.wait_for_selector(
                            'xpath=//a[@class="button small primary small-only-expanded" and text()="Reprogramar cita"]',
                            state='visible',
                            timeout=60000
                        )
                        reschedule_confirm_button.click()
                        page.wait_for_load_state("networkidle")
            else:
                self.final_msj = f"{self.final_msj} | No aparece calendario"
                return self.final_msj


            # 08. Wait and click on the calendar field
            calendar_button = page.wait_for_selector('xpath=//*[@id="appointments_consulate_appointment_date"]',
                                                    state='visible', timeout=60000)
            calendar_button.click()
            page.wait_for_load_state("networkidle")
            df_dates = self.extract_dates(page)


            # 09. Filter dates to see if there are available ones
            df_dates_norm = pd.DataFrame(df_dates)
            df_filtered = df_dates_norm.loc[~df_dates_norm["is_disabled"]].copy()

            if not df_filtered.empty:
                df_filtered = df_filtered.sort_values(by=["year", "month", "day"],ascending=[True, True, True]).reset_index(drop=True)
                idx = 1 if len(df_filtered) >= 2 else 0
                date_str = df_filtered.iloc[idx][["day", "month", "year"]].astype(str).str.cat(sep=" ")
                filtered_date = datetime.strptime(date_str, "%d %B %Y")
                self.final_msj = f"{self.final_msj} | Fecha disponible: {filtered_date}"
            else:
                self.final_msj = f"{self.final_msj} | No hay fechas disponibles"
                return self.final_msj

            if filtered_date < self.appointment_date:
                try:
                    # Remove the readonly attribute
                    page.evaluate("(el) => el.removeAttribute('readonly')", calendar_input)

                    # Set the value and trigger events
                    calendar_input.evaluate(
                        """(el, value) => {
                            el.value = value;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                        }""",
                        filtered_date.strftime("%Y-%m-%d")
                    )
                    page.click("div.callout")
                    calendar_button.click()
                    # Select the desired day (according to the library)
                    page.click("a.ui-state-default.ui-state-active")
                    self.appointment_date_new = filtered_date.strftime("%Y-%m-%d")
                    self.final_msj = f"{self.final_msj} | Toma la fecha {self.appointment_date_new}"

                    # Select first hour
                    time_select = page.wait_for_selector(
                        '//*[@id="appointments_consulate_appointment_time_input"]',
                        state='visible', timeout=30000
                    )
                    time_select.click()

                    # Wait for real options to be available
                    page.wait_for_function("""
                    () => {
                    const s = document.querySelector('#appointments_consulate_appointment_time');
                    return s && s.options && s.options.length > 1 && s.options[1].value;
                    }
                    """, timeout=10000)

                    # Extract available hours directly with JS
                    available_times = page.eval_on_selector_all(
                        '#appointments_consulate_appointment_time option',
                        'opts => opts.map(o => o.value).filter(v => v)'
                    )

                    print("available_times:", available_times)

                    if available_times:
                        self.appointment_hour_new = available_times[0]
                        page.select_option('#appointments_consulate_appointment_time', self.appointment_hour_new)
                        page.click("div.callout")
                        time.sleep(1)
                        time_select.click()
                        page.click("div.callout")
                        self.final_msj = f"{self.final_msj} | Toma la hora {self.appointment_hour_new}"
                    else:
                        self.final_msj = f"{self.final_msj} | No hay horarios disponibles validos"
                        return self.final_msj


                    # Select Biometrics Calendar // Or First Appointment
                    if self.type_appointment == 'Visa (Entrevista + Biométricos)' or new_appointment:

                        print("Checking if biometrics input exists...")
                        input_exists = page.locator("#appointments_asc_appointment_date").count()
                        print(f"Found {input_exists} elements with that ID")

                        # Check visibility and status
                        is_visible = page.locator("#appointments_asc_appointment_date").is_visible()
                        print(f"Is visible before JS fix? {is_visible}")

                        html_snippet = page.eval_on_selector("#appointments_asc_appointment_date", "el => el.outerHTML")
                        print("Input HTML before fix:", html_snippet)

                        display_status = page.eval_on_selector("#appointments_asc_appointment_date", "el => window.getComputedStyle(el).display")
                        visibility_status = page.eval_on_selector("#appointments_asc_appointment_date", "el => window.getComputedStyle(el).visibility")
                        print(f"CSS display={display_status}, visibility={visibility_status}")

                        # FIX: Force-enable and trigger the biometric date input manually
                        print("Trying to force show biometrics date input via JS...")

                        for attempt in range(3):
                            page.wait_for_timeout(2000)

                            page.evaluate("""
                            () => {
                                const el = document.querySelector('#appointments_asc_appointment_date');
                                if (!el) return;

                                // Forzar visibilidad en el input y todos sus padres
                                let parent = el;
                                while (parent) {
                                    parent.style.display = 'block';
                                    parent.style.visibility = 'visible';
                                    parent.style.opacity = '1';
                                    parent.style.height = 'auto';
                                    parent = parent.parentElement;
                                }

                                // Quitar readonly y asegurar foco
                                el.removeAttribute('readonly');
                                el.style.display = 'block';
                                el.style.visibility = 'visible';
                                el.style.opacity = '1';
                                el.dispatchEvent(new Event('focus', { bubbles: true }));
                                el.dispatchEvent(new Event('click', { bubbles: true }));

                                // Intentar abrir jQuery datepicker
                                if (window.jQuery && jQuery.fn.datepicker) {
                                    try {
                                        jQuery(el).datepicker('show');
                                    } catch (err) {
                                        console.log('Error abriendo datepicker:', err);
                                    }
                                }
                            }
                            """)

                            # Wait for real visibility of the element
                            try:
                                page.wait_for_selector("#appointments_asc_appointment_date", state="visible", timeout=5000)
                                print(f"Visible tras intento {attempt+1}")
                                return true
                            except:
                                print(f"No visible en intento {attempt+1}")

                        # Recheck visibility after JS trigger
                        is_visible_after = page.locator("#appointments_asc_appointment_date").is_visible()
                        print(f"Is visible after JS fix? {is_visible_after}")

                        html_after = page.eval_on_selector("#appointments_asc_appointment_date", "el => el.outerHTML")
                        print("Input HTML after fix:", html_after)

                        # Try to click the now-visible input
                        print("Waiting for biometrics input to be visible...")
                        calendar_button_bios = page.wait_for_selector(
                            'xpath=//*[@id="appointments_asc_appointment_date"]',
                            state='visible',
                            timeout=60000
                        )

                        print("Biometrics date input visible, clicking...")
                        calendar_button_bios.click(force = True)
                        page.wait_for_load_state("networkidle")

                        calendar_input = page.query_selector("#appointments_asc_appointment_date")
                        if not calendar_input:
                            self.final_msj = f"{self.final_msj} | No aparece calendario de biometricos"
                            return self.final_msj

                        df_dates_bios = self.extract_dates(page)

                        df_dates_norm_bios = pd.DataFrame(df_dates_bios)
                        df_filtered_bios = df_dates_norm_bios.loc[~df_dates_norm_bios["is_disabled"]]

                        if not df_filtered_bios.empty:
                            df_filtered_bios["month"] = pd.to_datetime(df_filtered_bios["month"], format="%B").dt.month
                            df_filtered_bios["date"] = pd.to_datetime(df_filtered_bios[["year", "month", "day"]])

                            # Calculate date range (2 days before to the appointment)
                            var_start_date = datetime.strptime(self.appointment_date_new, "%Y-%m-%d") - timedelta(days=2)
                            var_end_date = datetime.strptime(self.appointment_date_new, "%Y-%m-%d")

                            print('Biometricos df disponibles')
                            print(df_filtered_bios.head())

                            # Filter rows in that range
                            df_filtered_bios = df_filtered_bios[
                                (df_filtered_bios["date"] >= var_start_date) &
                                (df_filtered_bios["date"] < var_end_date)]

                            if df_filtered_bios.empty:
                                self.final_msj = f"{self.final_msj} | No hay fechas disponibles para biometricos"
                                return self.final_msj
                            else:
                                df_filtered_bios = df_filtered_bios.sort_values(by=["date"],ascending=[False]).reset_index(drop=True)
                                filtered_bios_date = pd.to_datetime(df_filtered_bios.iloc[0]["date"])
                                self.final_msj = f"{self.final_msj} | Fecha biometricos disponible: {filtered_bios_date}"
                        else:
                            self.final_msj = f"{self.final_msj} | No hay fechas disponibles para biometricos"
                            return self.final_msj

                        if filtered_bios_date >= datetime.strptime(self.appointment_date_new, "%Y-%m-%d") - timedelta(days=int(self.diff_bios)):
                            page.evaluate("""
                                ({ selector, value }) => {
                                    const el = document.querySelector(selector);
                                    if (!el) return "Element not found";
                                    el.style.display = 'block';          // force visible
                                    el.removeAttribute('readonly');       // allow editing
                                    el.value = value;                     // set date
                                    // trigger site JS
                                    el.dispatchEvent(new Event('input',  { bubbles: true }));
                                    el.dispatchEvent(new Event('change', { bubbles: true }));
                                    el.dispatchEvent(new Event('blur',   { bubbles: true }));
                                    return "Date injected";
                                }
                            """, { "selector": "#appointments_asc_appointment_date", "value": filtered_bios_date.strftime("%Y-%m-%d") })

                            page.click("div.callout")
                            calendar_button_bios.click()
                            # Select the desired day (according to the library)
                            page.click("a.ui-state-default.ui-state-active")

                            self.biometrics_date_new = filtered_bios_date.strftime("%Y-%m-%d")
                            self.final_msj = f"{self.final_msj} | Toma para biometricos la fecha {self.biometrics_date_new}"
                            
                            
                            print("Forzando campo de hora para biometricos (debug mode)...")

                            # Log initial state
                            try:
                                initial_html = page.eval_on_selector("#appointments_asc_appointment_time", "el => el ? el.outerHTML : 'no encontrado'")
                                print("HTML inicial del select de hora:", initial_html)
                            except Exception as e:
                                print("Error al obtener HTML inicial:", e)

                            # Try forcing visual
                            page.evaluate("""
                            () => {
                                const el = document.querySelector('#appointments_asc_appointment_time');
                                if (!el) return "Element not found";

                                console.log("Forzando visibilidad en el select de hora...");

                                let parent = el;
                                while (parent) {
                                    parent.style.display = 'block';
                                    parent.style.visibility = 'visible';
                                    parent.style.opacity = '1';
                                    parent.style.height = 'auto';
                                    parent = parent.parentElement;
                                }

                                el.removeAttribute('disabled');
                                el.disabled = false;

                                el.dispatchEvent(new Event('focus', { bubbles: true }));
                                el.dispatchEvent(new Event('click', { bubbles: true }));
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                                el.dispatchEvent(new Event('change', { bubbles: true }));

                                console.log("Campo de hora forzado visible.");
                                return "Time select forced visible";
                            }
                            """)

                            # Wait for the select to become visible
                            try:
                                page.wait_for_selector('#appointments_asc_appointment_time', state='visible', timeout=10000)
                                print("Select de hora visible.")
                            except Exception as e:
                                print("Select de hora no visible tras forzado:", e)

                            # Dump de opciones para depurar
                            try:
                                option_count = page.eval_on_selector("#appointments_asc_appointment_time", "el => el ? el.options.length : 0")
                                print(f"Número de opciones en el select (antes del wait_for_function): {option_count}")

                                option_values = page.eval_on_selector_all(
                                    '#appointments_asc_appointment_time option',
                                    'opts => opts.map(o => ({text: o.textContent.trim(), value: o.value}))'
                                )
                                print("Contenido actual del select de hora:")
                                for opt in option_values:
                                    print("  →", opt)
                            except Exception as e:
                                print("Error al obtener opciones:", e)

                            # Wait for valid options (visible retries)
                            try:
                                print("Esperando opciones válidas en el select de hora (timeout 20s)...")
                                page.wait_for_function("""
                                    () => {
                                        const s = document.querySelector('#appointments_asc_appointment_time');
                                        return s && s.options && s.options.length > 1 && s.options[1].value;
                                    }
                                """, timeout=20000)
                                print("Opciones válidas detectadas.")
                            except Exception as e:
                                print("Timeout esperando opciones válidas:", e)
                                # Dump del HTML actual para analizar qué está pasando
                                try:
                                    html_after = page.eval_on_selector("#appointments_asc_appointment_time", "el => el.outerHTML")
                                    print("HTML final del select de hora (tras timeout):", html_after)
                                except Exception as ee:
                                    print("Error al leer HTML tras timeout:", ee)

                            # Obtain and select the first available option
                            available_times_bios = page.eval_on_selector_all(
                                '#appointments_asc_appointment_time option',
                                'opts => opts.map(o => o.value).filter(v => v && v.trim() !== "")'
                            )
                            print("available_times_bios:", available_times_bios)

                            if available_times_bios and len(available_times_bios) > 0:
                                self.biometrics_hour_new = available_times_bios[0]
                                page.select_option('#appointments_asc_appointment_time', self.biometrics_hour_new)
                                page.evaluate("""
                                    () => {
                                        const el = document.querySelector('#appointments_asc_appointment_time');
                                        if (!el) return;
                                        el.dispatchEvent(new Event('change', { bubbles: true }));
                                        el.dispatchEvent(new Event('blur', { bubbles: true }));
                                    }
                                """)
                                page.click("div.callout")
                                self.final_msj = f"{self.final_msj} | Toma para biometricos la hora {self.biometrics_hour_new}"
                            else:
                                self.final_msj = f"{self.final_msj} | No hay horarios disponibles válidos para biometricos"
                                return self.final_msj
                        else:
                            self.final_msj = f"{self.final_msj} | No hay horarios disponibles validos para biometricos que cumplan las reglas"
                            return self.final_msj

                    elif self.type_appointment == 'Visa grupal (Entrevista + Biométricos)':
                        pass
                    else: # Renovación (Entrevista)
                        pass

                    
                    # Clic on "Reprogramar"
                    try:
                        page.evaluate("""
                        () => {
                            const btn = document.querySelector('#appointments_submit');
                            if (btn) {
                                btn.removeAttribute('disabled');
                                btn.style.display = 'block';
                                btn.style.visibility = 'visible';
                                btn.style.opacity = '1';
                            }
                        }
                        """)

                        submit_button = page.wait_for_selector(
                            '//*[@id="appointments_submit"]',
                            state='visible', timeout=30000
                        )
                        submit_button.click()
                        self.final_msj = f"{self.final_msj} | Clic en reprogramar accionado"
                    except Exception as e:
                        self.final_msj = f"{self.final_msj} | Error al reprogramar: {e}"
                        print("Could not find reprogramar button:", e)

                    # Confirm action
                    try:
                        page.evaluate("""
                        () => {
                            const confirmBtn = Array.from(document.querySelectorAll('a.button'))
                                .find(el => el.textContent.trim().includes('Confirmar'));
                            if (confirmBtn) {
                                confirmBtn.style.display = 'block';
                                confirmBtn.style.visibility = 'visible';
                                confirmBtn.style.opacity = '1';
                                confirmBtn.style.pointerEvents = 'auto';
                                confirmBtn.removeAttribute('disabled');
                                confirmBtn.removeAttribute('aria-disabled');
                            }
                        }
                        """)
                        page.wait_for_timeout(1000)

                        confirm_button = page.wait_for_selector(
                            "//a[contains(@class, 'button') and contains(text(), 'Confirmar')]",
                            state='visible',
                            timeout=15000
                        )
                        confirm_button.click()
                        self.final_msj = f"{self.final_msj} | Clic en confirmar accionado"
                        print("Clic en Confirmar accionado")

                    except Exception as e:
                        self.final_msj = f"{self.final_msj} | Error al confirmar: {e}"
                        print("Could not find Confirmar button:", e)
                    
                    # Wait for confirmation message
                    page.wait_for_load_state("networkidle")
                    notice_element = page.wait_for_selector("div.notice", timeout=15000)
                    notice_text = notice_element.inner_text().strip()

                    # Validate message
                    if "no pudo ser programada" in notice_text.lower():
                        self.final_msj = f"{self.final_msj} | Reprogramación fallida"
                        self.reschedule_success = False
                    else:
                        self.final_msj = f"{self.final_msj} | Reprogramado exitosamente"
                        self.reschedule_success = True

                except Exception as e:
                    print(e)
                    self.final_msj = f"{self.final_msj} | Fallo al reprogramar: {e}"
                    return self.final_msj

            else:
                self.final_msj = f"{self.final_msj} | Fecha disponible no es util"
                return self.final_msj

            browser.close()
            return self.final_msj


    # 03. Auxiliary function to loop through and identify selectables
    # The appointment and bios calendars are looped through
    def extract_dates(self, page):
        all_dates = []
        for _ in range(self.months_to_extract):
            # Obtain the HTML of the current page
            soup = BeautifulSoup(page.content(), 'html.parser')
            calendar_groups = soup.find_all("div", class_="ui-datepicker-group")

            for group in calendar_groups:
                month = group.find("span", class_="ui-datepicker-month").text
                year = group.find("span", class_="ui-datepicker-year").text
                calendar = group.find("table", class_="ui-datepicker-calendar")
                days = calendar.find_all("td")

                for day in days:
                    classes = day.get("class", [])
                    is_selectable = "ui-datepicker-unselectable" not in classes
                    is_disabled = "ui-state-disabled" in classes

                    if day.text and "ui-datepicker-other-month" not in classes:
                        date_info = {
                            'day': day.text.strip(),
                            'month': month,
                            'year': year,
                            'is_selectable': is_selectable,
                            'is_disabled': is_disabled
                        }
                        all_dates.append(date_info)

            # Wait and click on the "Next" button
            next_button = page.wait_for_selector('xpath=//a[@title="Next"]', state='visible', timeout=60000)
            next_button.click()

        return all_dates


    # 04. Send email notification
    # # Scenario 1: User issue
    # # Scenario 2: Successful scheduling
    def send_email_notification(self, scenery):
        smtp_server = "smtp.gmail.com"
        smtp_port = 465

        if scenery == 1:
            subject = "Problema con el usuario"
            body = (
                f"Hola,\n\n"
                f"Se ha detectado un inconveniente con el usuario {self.username_chosen} durante el proceso.\n\n"
                f"Parece que el usuario ya cuenta con una cita disponible dentro de los próximos {self.months_to_extract} meses.\n\n"
                f"Para desactivarlo, ve al documento de administración y cambia el valor de la columna "
                f"\"Estado del negocio\" a 'Inactivo' para que no sea tomado nuevamente.\n\n"
                f"Feliz día."
            )
        else:
            subject = "Reprogramación exitosa - Nueva cita disponible"
            body = (
                f"Hola,\n\n"
                f"Se ha encontrado una cita más cercana disponible para el usuario {self.username_chosen}.\n\n"
                f"Fecha de ejecución: {self.today}\n"
                f"Cita actual: {self.appointment_date}\n"
                f"Nueva cita disponible: {self.appointment_date_new} a las {self.appointment_hour_new}\n\n"
                f"Por favor, inicia sesión en tu cuenta para confirmar la nueva cita."
            )

        # Create email
        msg = MIMEMultipart()
        msg['From'] = self.sender_email
        msg['To'] = self.recipient_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Send email
        try:
            with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
                server.login(self.sender_email, self.password_email)
                server.send_message(msg)
            print(f"Email sent to {self.recipient_email}")
        except Exception as e:
            print(f"Failed to send email: {e}")



    # 05. Orquestador / Run function
    def run(self):
        # Read Google Sheet to get users
        self.username_chosen, self.password_chosen = self.read_drive()

        # Execute overtaking program
        self.final_msj = self.overtaking_process(self.username_chosen, self.password_chosen)

        # Add a new row to the log sheet
        new_row = [
            str(self.today),
            str(pd.Timestamp.now()),
            self.username_chosen,
            str(self.appointment_date),
            str(self.appointment_date_new),
            str(self.appointment_hour_new),
            str(self.biometrics_date_new),
            str(self.biometrics_hour_new),
            self.reschedule_success,
            self.final_msj
        ]
        self.sheet_log = self.spreadsheet.worksheet("log")
        self.sheet_log.append_row(new_row)

        # If exists, different from None, send email notifying problem
        if self.user_problem != None:
            self.send_email_notification(scenery=1)

        # In case of having successfully rescheduled
        if self.reschedule_success and self.user_problem != None:
            self.send_email_notification(scenery=2)


if __name__ == "__main__":

    api_drive = {
        "type": "service_account",
        "project_id": os.getenv("GCP_PROJECT_ID"),
        "private_key_id": os.getenv("GCP_PRIVATE_KEY_ID"),
        "private_key": os.getenv("GCP_PRIVATE_KEY").strip("'").replace("\\n", "\n"),
        "client_email": os.getenv("GCP_CLIENT_EMAIL"),
        "client_id": os.getenv("GCP_CLIENT_ID"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": os.getenv("GCP_CLIENT_X509_CERT_URL"),
        "universe_domain": "googleapis.com"
    }

    config = {
    "global": {
        "page_id": os.getenv("CFG_PAGE_ID"),
        "admin_sheet_id": os.getenv("CFG_ADMIN_SHEET_ID"),

        "sender_email": os.getenv("CFG_SENDER_EMAIL"),
        "password_email": os.getenv("CFG_PASSWORD_EMAIL"),
        "recipient_email": os.getenv("CFG_RECIPIENT_EMAIL"),

        "months_to_extract": 2,
        "motionless_days": 1
        }
    }

    # Create an instance of the class and run it
    checker = LumeProton00(config, api_drive)
    checker.run()