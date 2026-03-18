#include <LilyGoLib.h>
#include <LV_Helper.h>
#include <WiFi.h>
#include <esp_sleep.h>
#include <esp_wifi.h>
#include <time.h>

#include "ui.h"


const char* ssid = "Xior-residents";
const char* password = "Xior2022!";
const char* tz_netherlands = "CET-1CEST,M3.5.0/2,M10.5.0/3";
static const bool BATTERY_SAVER = true;
static bool time_synced = false;
static uint32_t last_ntp_retry_ms = 0;
static const uint32_t NTP_RETRY_INTERVAL_MS = 30000;
static const uint32_t WIFI_SUSPEND_IDLE_MS = 120000;
static const uint32_t SCREEN_OFF_IDLE_MS = 15000;      // Turn off screen after 15s locked
static const uint32_t LIGHT_SLEEP_IDLE_MS = 30000;     // Enter light sleep after 30s locked
static const uint32_t DEEP_SLEEP_IDLE_MS = 300000;     // Enter deep sleep after 5min locked
static bool wifi_suspended = false;
static bool screen_off = false;
static const int PHYSICAL_BUTTON_PIN = 0;
static const bool PHYSICAL_BUTTON_ACTIVE_LOW = true;
static const uint32_t BUTTON_DEBOUNCE_MS = 30;
static const uint32_t BUTTON_LONG_PRESS_MS = 1500;
static bool button_last_pressed = false;
static uint32_t button_press_start_ms = 0;
static bool button_long_action_fired = false;

bool is_physical_button_pressed() {
    const int raw = digitalRead(PHYSICAL_BUTTON_PIN);
    return PHYSICAL_BUTTON_ACTIVE_LOW ? (raw == LOW) : (raw == HIGH);
}

void enter_deep_sleep_by_button() {
    Serial.println("Physical button long press: entering deep sleep");
    WiFi.disconnect(true, false);
    WiFi.mode(WIFI_OFF);

    const int wake_level = PHYSICAL_BUTTON_ACTIVE_LOW ? 0 : 1;
    esp_sleep_enable_ext0_wakeup(static_cast<gpio_num_t>(PHYSICAL_BUTTON_PIN), wake_level);
    delay(50);
    esp_deep_sleep_start();
}

void handle_physical_button() {
    const uint32_t now_ms = millis();
    const bool pressed = is_physical_button_pressed();

    if (pressed && !button_last_pressed) {
        button_press_start_ms = now_ms;
        button_long_action_fired = false;
        
        // Wake screen on any button press
        if (screen_off) {
            screen_off = false;
            instance.setBrightness(DEVICE_MAX_BRIGHTNESS_LEVEL);
            ui_mark_interaction();
            Serial.println("Screen woken by button");
        }
    }

    if (pressed && !button_long_action_fired && (now_ms - button_press_start_ms) >= BUTTON_LONG_PRESS_MS) {
        button_long_action_fired = true;
        enter_deep_sleep_by_button();
    }

    if (!pressed && button_last_pressed) {
        const uint32_t press_ms = now_ms - button_press_start_ms;
        if (!button_long_action_fired && press_ms >= BUTTON_DEBOUNCE_MS && press_ms < BUTTON_LONG_PRESS_MS) {
            if (!ui_is_locked()) {
                ui_lock();
                Serial.println("Physical button short press: lock screen");
            }
        }
    }

    button_last_pressed = pressed;
}

bool sync_time_netherlands() {
    setenv("TZ", tz_netherlands, 1);
    tzset();
    configTzTime(tz_netherlands, "nl.pool.ntp.org", "pool.ntp.org", "time.cloudflare.com");

    struct tm time_info;
    for (int attempt = 0; attempt < 30; ++attempt) {
        if (getLocalTime(&time_info, 700) && time_info.tm_year >= (2024 - 1900)) {
            Serial.printf("Time synchronized: %04d-%02d-%02d %02d:%02d:%02d Europe/Amsterdam\n",
                          time_info.tm_year + 1900,
                          time_info.tm_mon + 1,
                          time_info.tm_mday,
                          time_info.tm_hour,
                          time_info.tm_min,
                          time_info.tm_sec);
            return true;
        }
    }

    Serial.println("Time sync failed; retrying in background");
    return false;
}

bool connect_wifi(uint32_t timeout_ms = 12000) {

    Serial.println("Connecting to WiFi");

    WiFi.begin(ssid, password);

    const uint32_t start = millis();

    while (WiFi.status() != WL_CONNECTED) {

        if (millis() - start > timeout_ms) {
            Serial.println("");
            Serial.println("WiFi connection timeout");
            return false;
        }

        delay(500);
        Serial.print(".");
    }

    Serial.println("");
    Serial.println("WiFi connected");

    Serial.print("IP: ");
    Serial.println(WiFi.localIP());

    return true;
}

// -------- SETUP --------

void setup() {

    Serial.begin(115200);

    // iniciar reloj (API oficial LilyGoLib para T-Watch-S3)
    instance.begin();

    // iniciar helper de LVGL
    beginLvglHelper(instance);

    // brillo pantalla
    instance.setBrightness(DEVICE_MAX_BRIGHTNESS_LEVEL);

    pinMode(PHYSICAL_BUTTON_PIN, PHYSICAL_BUTTON_ACTIVE_LOW ? INPUT_PULLUP : INPUT);

    // iniciar interfaz (siempre visible, con o sin WiFi)
    init_ui();

    // conectar wifi y sincronizar hora local (Maastricht)
    if (connect_wifi()) {
        time_synced = sync_time_netherlands();
    }
}

// -------- LOOP --------

void loop() {

    handle_physical_button();

    const bool locked = ui_is_locked();
    const uint32_t idle_ms = millis() - ui_get_last_interaction_ms();

    // Wake screen on touch if screen was off
    if (screen_off && instance.getTouched()) {
        screen_off = false;
        instance.setBrightness(DEVICE_MAX_BRIGHTNESS_LEVEL);
        ui_mark_interaction();
        Serial.println("Screen woken by touch");
    }

    if (BATTERY_SAVER && locked) {
        // Turn off screen after idle
        if (!screen_off && idle_ms >= SCREEN_OFF_IDLE_MS) {
            Serial.println("Battery saver: turning off screen");
            instance.setBrightness(0);
            screen_off = true;
        }

        // Suspend WiFi after longer idle
        if (!wifi_suspended && WiFi.status() == WL_CONNECTED && idle_ms >= WIFI_SUSPEND_IDLE_MS) {
            Serial.println("Battery saver: suspending WiFi");
            WiFi.disconnect(true, false);
            WiFi.mode(WIFI_OFF);
            wifi_suspended = true;
        }

        // Enter light sleep when screen off + wifi suspended
        if (screen_off && wifi_suspended && idle_ms >= LIGHT_SLEEP_IDLE_MS && idle_ms < DEEP_SLEEP_IDLE_MS) {
            Serial.println("Battery saver: entering light sleep");
            Serial.flush();
            
            // Configure wake on touch interrupt (GPIO for touch on T-Watch S3)
            esp_sleep_enable_ext0_wakeup(static_cast<gpio_num_t>(PHYSICAL_BUTTON_PIN), 0);
            esp_sleep_enable_timer_wakeup(10000000); // Wake every 10s to check
            
            esp_light_sleep_start();
            
            // After waking
            ui_mark_interaction();
            Serial.println("Woke from light sleep");
        }
        
        // Enter deep sleep after 5 minutes of inactivity
        if (idle_ms >= DEEP_SLEEP_IDLE_MS) {
            Serial.println("Battery saver: entering deep sleep (5min idle)");
            Serial.flush();
            
            WiFi.disconnect(true, false);
            WiFi.mode(WIFI_OFF);
            
            esp_sleep_enable_ext0_wakeup(static_cast<gpio_num_t>(PHYSICAL_BUTTON_PIN), 0);
            delay(50);
            esp_deep_sleep_start();
        }
    }

    // Resume when unlocked
    if (!locked) {
        if (screen_off) {
            screen_off = false;
            instance.setBrightness(DEVICE_MAX_BRIGHTNESS_LEVEL);
        }
        
        if (wifi_suspended) {
            Serial.println("Battery saver: resuming WiFi");
            WiFi.mode(WIFI_STA);
            wifi_suspended = false;
            if (connect_wifi()) {
                if (!time_synced) {
                    time_synced = sync_time_netherlands();
                }
            }
        }
    }

    if (WiFi.status() == WL_CONNECTED && !time_synced) {
        const uint32_t now_ms = millis();
        if (now_ms - last_ntp_retry_ms >= NTP_RETRY_INTERVAL_MS) {
            last_ntp_retry_ms = now_ms;
            time_synced = sync_time_netherlands();
        }
    }

    // necesario para LVGL
    lv_timer_handler();
    yield(); // Feed watchdog after LVGL processing

    delay(screen_off ? 50 : 5); // Slower refresh when screen off
}




