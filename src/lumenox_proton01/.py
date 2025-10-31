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
