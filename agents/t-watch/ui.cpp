#include "ui.h"
#include "api.h"

#include <Arduino.h>
#include <LilyGoLib.h>
#include <WiFi.h>
#include <lvgl.h>
#include <time.h>
#include <esp_sleep.h>

static lv_obj_t *lock_screen = nullptr;
static lv_obj_t *main_screen = nullptr;
static lv_timer_t *auto_lock_timer = nullptr;
static lv_obj_t *lock_date_label = nullptr;
static lv_obj_t *lock_time_label = nullptr;
static lv_obj_t *lock_wifi_label = nullptr;
static lv_obj_t *lock_battery_label = nullptr;
static lv_obj_t *lock_water_label = nullptr;
static lv_obj_t *lock_pending_tasks_label = nullptr;
static lv_obj_t *lock_current_activity_label = nullptr;
static lv_obj_t *lock_next_activity_label = nullptr;
static lv_obj_t *water_total_label = nullptr;
static lv_obj_t *water_status_label = nullptr;

static const bool BATTERY_SAVER = true;
static const uint8_t LOCK_BRIGHTNESS_LEVEL = 70;
static const uint8_t ACTIVE_BRIGHTNESS_LEVEL = DEVICE_MAX_BRIGHTNESS_LEVEL;
static const uint8_t LOCK_SCREEN_OFF_BRIGHTNESS = 0;
static const uint32_t LOCK_ALERT_VISIBLE_MS = 20000;
static const uint32_t LOCK_TOUCH_PREVIEW_MS = 10000;

static bool is_locked = true;
static uint32_t last_interaction_ms = 0;
static const uint32_t AUTO_LOCK_TIMEOUT_MS = 10000;
static uint32_t last_pending_tasks_refresh_ms = 0;
static const uint32_t PENDING_TASKS_REFRESH_MS = BATTERY_SAVER ? 90000 : 20000;
static int cached_pending_tasks = -1;
static int cached_water_total = -1;
static uint32_t last_calendar_lock_refresh_ms = 0;
static const uint32_t CALENDAR_LOCK_REFRESH_MS = BATTERY_SAVER ? 120000 : 30000;
static uint32_t last_water_lock_refresh_ms = 0;
static const uint32_t WATER_LOCK_REFRESH_MS = BATTERY_SAVER ? 120000 : 30000;
static String cached_lock_current_activity = "Now: --";
static String cached_lock_next_activity = "Next: --";
static uint32_t lock_alert_visible_until_ms = 0;

static void load_main_screen(lv_event_t *e);
static void load_lock_screen(lv_event_t *e);
static void mark_user_activity();
static void global_touch_event(lv_event_t *e);
static void update_lock_status_labels();
static lv_obj_t *create_tasks_screen();
static lv_obj_t *create_calendar_screen();
static lv_obj_t *create_water_screen();
static lv_obj_t *create_gym_screen();
static lv_obj_t *create_shopping_screen();
static lv_obj_t *create_pomodoro_screen();
static lv_obj_t *create_settings_screen();
static void refresh_lock_calendar_notification(bool force = false);
static void refresh_lock_water_notification(bool force = false);
static void update_lock_brightness();
static bool is_persistent_screen(lv_obj_t *screen);
static void delete_if_temporary_screen(lv_obj_t *screen);
static bool json_get_bool_value(const String &json, const String &key, bool &value, int start_pos = 0);
static int json_get_int_value(const String &json, const String &key, int start_pos = 0);
static String json_get_string_value(const String &json, const String &key, int start_pos = 0);

struct MenuItem {
    const char *title;
    const char *icon;
};

struct TaskToggleContext {
    int occurrences_id;
};

struct CalendarEvent {
    String title;
    String start_iso;
    String end_iso;
    int start_minutes;
    int end_minutes;
};

struct GymExercise {
    int log_id;
    int routine_exercise_id;
    String name;
    int target_series;
    int target_reps;
};

struct GymSession {
    int id;
    int routine_id;
    String routine_name;
    GymExercise exercises[12];
    int exercise_count;
};

static GymSession cached_gym_session = {0, 0, "", {}, 0};
static int current_exercise_log_id = 0;
static String current_exercise_name = "";

static TaskToggleContext task_toggle_contexts[16];

static int parse_minutes_from_iso(const String &iso) {
    if (iso.length() < 16) return -1;

    const int t_pos = iso.indexOf('T');
    if (t_pos < 0 || (t_pos + 5) >= iso.length()) return -1;

    const int hour = iso.substring(t_pos + 1, t_pos + 3).toInt();
    const int minute = iso.substring(t_pos + 4, t_pos + 6).toInt();
    if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return -1;
    return hour * 60 + minute;
}

static String add_minutes_to_iso(const String &iso, int minutes) {
    if (iso.length() < 16) return iso;

    struct tm tm_info = {};
    tm_info.tm_year = iso.substring(0, 4).toInt() - 1900;
    tm_info.tm_mon = iso.substring(5, 7).toInt() - 1;
    tm_info.tm_mday = iso.substring(8, 10).toInt();
    tm_info.tm_hour = iso.substring(11, 13).toInt();
    tm_info.tm_min = iso.substring(14, 16).toInt();
    tm_info.tm_sec = 0;

    time_t base = mktime(&tm_info);
    if (base <= 0) return iso;

    base += static_cast<time_t>(minutes) * 60;
    struct tm out_tm;
    localtime_r(&base, &out_tm);

    char buffer[24];
    strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%S", &out_tm);
    return String(buffer);
}

static String short_time_from_iso(const String &iso) {
    const int t_pos = iso.indexOf('T');
    if (t_pos < 0 || (t_pos + 5) >= iso.length()) return "--:--";
    return iso.substring(t_pos + 1, t_pos + 6);
}

static int collect_calendar_events(const String &payload, CalendarEvent *events, int max_events) {
    int count = 0;
    int scan = 0;

    while (count < max_events) {
        const int slot_pos = payload.indexOf("\"slot_id\":", scan);
        if (slot_pos < 0) break;

        const int next_slot_pos = payload.indexOf("\"slot_id\":", slot_pos + 1);
        const int slot_limit = (next_slot_pos < 0) ? payload.length() : next_slot_pos;

        const String slot_start = json_get_string_value(payload, "start_time", slot_pos);
        const String slot_end = json_get_string_value(payload, "end_time", slot_pos);

        const int item_pos = payload.indexOf("\"item\":", slot_pos);
        if (item_pos < 0 || item_pos >= slot_limit || payload.startsWith("\"item\":null", item_pos)) {
            scan = slot_limit;
            continue;
        }

        String title = json_get_string_value(payload, "title", item_pos);
        if (!title.length()) {
            scan = slot_limit;
            continue;
        }

        String start_iso = json_get_string_value(payload, "start_time", item_pos);
        String end_iso = json_get_string_value(payload, "end_time", item_pos);
        const int start_minute = json_get_int_value(payload, "start_minute", item_pos);
        const int duration_minutes = json_get_int_value(payload, "duration_minutes", item_pos);

        if (!start_iso.length()) {
            start_iso = slot_start;
            if (start_minute > 0) {
                start_iso = add_minutes_to_iso(start_iso, start_minute);
            }
        }

        if (!end_iso.length()) {
            if (slot_end.length() && start_iso == slot_start && duration_minutes <= 0) {
                end_iso = slot_end;
            } else {
                end_iso = add_minutes_to_iso(start_iso, duration_minutes > 0 ? duration_minutes : 60);
            }
        }

        const int start_minutes = parse_minutes_from_iso(start_iso);
        const int end_minutes = parse_minutes_from_iso(end_iso);
        if (start_minutes < 0 || end_minutes < 0 || end_minutes <= start_minutes) {
            scan = slot_limit;
            continue;
        }

        events[count].title = title;
        events[count].start_iso = start_iso;
        events[count].end_iso = end_iso;
        events[count].start_minutes = start_minutes;
        events[count].end_minutes = end_minutes;
        count++;

        scan = slot_limit;
    }

    for (int i = 0; i < count; ++i) {
        for (int j = i + 1; j < count; ++j) {
            if (events[j].start_minutes < events[i].start_minutes) {
                CalendarEvent temp = events[i];
                events[i] = events[j];
                events[j] = temp;
            }
        }
    }

    return count;
}

static bool json_get_bool_value(const String &json, const String &key, bool &value, int start_pos) {
    const String pattern = "\"" + key + "\":";
    const int key_pos = json.indexOf(pattern, start_pos);
    if (key_pos < 0) return false;

    const int value_start = key_pos + pattern.length();
    if (json.startsWith("true", value_start)) {
        value = true;
        return true;
    }
    if (json.startsWith("false", value_start)) {
        value = false;
        return true;
    }
    return false;
}

static int json_get_int_value(const String &json, const String &key, int start_pos) {
    const String pattern = "\"" + key + "\":";
    const int key_pos = json.indexOf(pattern, start_pos);
    if (key_pos < 0) return -1;

    int value_start = key_pos + pattern.length();
    while (value_start < json.length() && (json[value_start] == ' ' || json[value_start] == '"')) {
        value_start++;
    }

    int value_end = value_start;
    while (value_end < json.length() && isdigit(static_cast<unsigned char>(json[value_end]))) {
        value_end++;
    }

    if (value_end <= value_start) return -1;
    return json.substring(value_start, value_end).toInt();
}

static void tasks_checkbox_event(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_VALUE_CHANGED) return;

    lv_obj_t *checkbox = static_cast<lv_obj_t *>(lv_event_get_target(e));
    TaskToggleContext *ctx = static_cast<TaskToggleContext *>(lv_event_get_user_data(e));
    if (!checkbox || !ctx || ctx->occurrences_id <= 0) return;

    const bool checked = lv_obj_has_state(checkbox, LV_STATE_CHECKED);
    
    lv_refr_now(NULL);  // Update display before blocking call
    yield();
    
    const bool ok = set_task_occurrence_completed(ctx->occurrences_id, checked);
    
    yield();
    
    if (!ok) {
        if (checked) {
            lv_obj_clear_state(checkbox, LV_STATE_CHECKED);
        } else {
            lv_obj_add_state(checkbox, LV_STATE_CHECKED);
        }
        Serial.print("[Tasks] Toggle failed: ");
        Serial.println(get_last_api_error());
    }
}

static String json_get_string_value(const String &json, const String &key, int start_pos) {
    const String pattern = "\"" + key + "\":\"";
    const int key_pos = json.indexOf(pattern, start_pos);
    if (key_pos < 0) return "";

    const int value_start = key_pos + pattern.length();
    int value_end = value_start;
    while (value_end < json.length()) {
        const char c = json[value_end];
        if (c == '"' && json[value_end - 1] != '\\') {
            break;
        }
        value_end++;
    }

    if (value_end <= value_start) return "";
    return json.substring(value_start, value_end);
}

static int count_pending_tasks_from_payload(const String &payload) {
    int pending_count = 0;
    int scan = 0;
    while (true) {
        const int completed_key_pos = payload.indexOf("\"completed\":", scan);
        if (completed_key_pos < 0) break;

        bool completed = false;
        if (json_get_bool_value(payload, "completed", completed, completed_key_pos) && !completed) {
            pending_count++;
        }
        scan = completed_key_pos + 12;
    }
    return pending_count;
}

static int parse_water_total_from_payload(const String &payload) {
    const int total = json_get_int_value(payload, "water_total");
    if (total >= 0) return total;
    return json_get_int_value(payload, "water");
}

static void set_water_module_labels(int total_ml, const String &status = "") {
    if (water_total_label) {
        lv_label_set_text_fmt(water_total_label, "%d ml", total_ml >= 0 ? total_ml : 0);
    }
    if (water_status_label) {
        lv_label_set_text(water_status_label, status.c_str());
    }
}

static void refresh_lock_water_notification(bool force) {
    if (!is_locked || !lock_water_label) return;

    const uint32_t now_ms = millis();
    if (!force && (now_ms - last_water_lock_refresh_ms) < WATER_LOCK_REFRESH_MS) {
        if (cached_water_total >= 0) {
            lv_label_set_text_fmt(lock_water_label, "Water: %d ml", cached_water_total);
        }
        return;
    }

    last_water_lock_refresh_ms = now_ms;
    if (WiFi.status() != WL_CONNECTED) {
        return;
    }

    yield();
    const String payload = get_today_water_json();
    yield();
    if (!payload.length()) return;

    const int new_total = parse_water_total_from_payload(payload);
    if (new_total < 0) return;

    const bool changed = (cached_water_total >= 0 && cached_water_total != new_total);
    cached_water_total = new_total;
    lv_label_set_text_fmt(lock_water_label, "Water: %d ml", cached_water_total);

    if (changed) {
        lock_alert_visible_until_ms = now_ms + LOCK_ALERT_VISIBLE_MS;
    }
}

static void water_add_event(lv_event_t *e) {
    if (lv_event_get_code(e) != LV_EVENT_CLICKED) return;

    const int *ml_ptr = static_cast<const int *>(lv_event_get_user_data(e));
    const int amount_ml = ml_ptr ? *ml_ptr : 0;
    if (amount_ml <= 0) return;

    set_water_module_labels(cached_water_total, "Updating...");
    lv_refr_now(NULL);
    yield();

    const bool ok = add_water_intake(amount_ml, "watch_quick_add");
    yield();
    if (!ok) {
        set_water_module_labels(cached_water_total, "Update failed");
        Serial.print("[Water] Update failed: ");
        Serial.println(get_last_api_error());
        return;
    }

    yield();
    const String payload = get_today_water_json();
    yield();
    const int total_ml = parse_water_total_from_payload(payload);
    if (total_ml >= 0) {
        cached_water_total = total_ml;
        last_water_lock_refresh_ms = 0;
        set_water_module_labels(cached_water_total, String("+") + String(amount_ml) + String(" ml"));
        if (lock_water_label) {
            lv_label_set_text_fmt(lock_water_label, "Water: %d ml", cached_water_total);
        }
        lock_alert_visible_until_ms = millis() + LOCK_ALERT_VISIBLE_MS;
    } else {
        set_water_module_labels(cached_water_total, "Updated");
    }
}

static void refresh_lock_calendar_notification(bool force) {
    if (!is_locked || !lock_current_activity_label || !lock_next_activity_label) return;

    const uint32_t now_ms = millis();
    if (!force && (now_ms - last_calendar_lock_refresh_ms) < CALENDAR_LOCK_REFRESH_MS) {
        lv_label_set_text(lock_current_activity_label, cached_lock_current_activity.c_str());
        lv_label_set_text(lock_next_activity_label, cached_lock_next_activity.c_str());
        return;
    }

    last_calendar_lock_refresh_ms = now_ms;
    if (WiFi.status() != WL_CONNECTED) {
        return;
    }

    yield();
    const String payload = get_today_calendar_json();
    yield();
    if (!payload.length()) {
        return;
    }

    CalendarEvent events[20];
    const int events_count = collect_calendar_events(payload, events, 20);

    String new_current = "Now: No active activity";
    String new_next = "Next: No more activities";

    if (events_count > 0) {
        time_t now = time(nullptr);
        struct tm now_tm;
        const int now_minutes = (localtime_r(&now, &now_tm) ? (now_tm.tm_hour * 60 + now_tm.tm_min) : -1);

        int current_index = -1;
        int next_index = -1;

        for (int i = 0; i < events_count; ++i) {
            if (now_minutes >= 0 && now_minutes >= events[i].start_minutes && now_minutes < events[i].end_minutes) {
                current_index = i;
            }
            if (now_minutes >= 0 && events[i].start_minutes > now_minutes) {
                next_index = i;
                break;
            }
        }

        if (current_index >= 0) {
            new_current = "Now: " + short_time_from_iso(events[current_index].start_iso) + " " + events[current_index].title;
        }
        if (next_index >= 0) {
            new_next = "Next: " + short_time_from_iso(events[next_index].start_iso) + " " + events[next_index].title;
        }
    } else {
        new_current = "Now: No activities today";
        new_next = "Next: --";
    }

    const bool changed = (new_current != cached_lock_current_activity) || (new_next != cached_lock_next_activity);
    cached_lock_current_activity = new_current;
    cached_lock_next_activity = new_next;

    lv_label_set_text(lock_current_activity_label, cached_lock_current_activity.c_str());
    lv_label_set_text(lock_next_activity_label, cached_lock_next_activity.c_str());

    if (changed) {
        lock_alert_visible_until_ms = now_ms + LOCK_ALERT_VISIBLE_MS;
    }
}

static void refresh_lock_pending_tasks(bool force = false) {
    if (!lock_pending_tasks_label || !is_locked) return;

    if (WiFi.status() != WL_CONNECTED) {
        lv_label_set_text(lock_pending_tasks_label, "");
        return;
    }

    const uint32_t now_ms = millis();
    if (!force && (now_ms - last_pending_tasks_refresh_ms) < PENDING_TASKS_REFRESH_MS) {
        if (cached_pending_tasks > 0) {
            lv_label_set_text_fmt(lock_pending_tasks_label,
                                  cached_pending_tasks == 1 ? "%d pending task" : "%d pending tasks",
                                  cached_pending_tasks);
        } else {
            lv_label_set_text(lock_pending_tasks_label, "");
        }
        update_lock_brightness();
        return;
    }

    last_pending_tasks_refresh_ms = now_ms;
    yield();
    const String payload = get_current_occurrence_tasks_json();
    yield();
    if (payload.length() == 0) {
        return;
    }

    const int previous_pending_tasks = cached_pending_tasks;
    cached_pending_tasks = count_pending_tasks_from_payload(payload);

    if (previous_pending_tasks >= 0 && cached_pending_tasks != previous_pending_tasks) {
        lock_alert_visible_until_ms = now_ms + LOCK_ALERT_VISIBLE_MS;
    }

    if (cached_pending_tasks > 0) {
        lv_label_set_text_fmt(lock_pending_tasks_label,
                              cached_pending_tasks == 1 ? "%d pending task" : "%d pending tasks",
                              cached_pending_tasks);
    } else {
        lv_label_set_text(lock_pending_tasks_label, "");
    }

    update_lock_brightness();
}

static lv_obj_t *create_tasks_screen() {
    lv_obj_t *screen = lv_obj_create(NULL);
    lv_obj_set_style_pad_all(screen, 12, 0);
    lv_obj_set_style_bg_color(screen, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(screen, 0, 0);
    lv_obj_add_event_cb(screen, global_touch_event, LV_EVENT_PRESSED, NULL);

    lv_obj_t *title = lv_label_create(screen);
    lv_label_set_text(title, "Tasks");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_20, 0);
    lv_obj_set_style_text_color(title, lv_palette_lighten(LV_PALETTE_GREY, 2), 0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);

    lv_obj_t *occurrence_label = lv_label_create(screen);
    lv_label_set_text(occurrence_label, "Period: --");
    lv_obj_set_style_text_font(occurrence_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(occurrence_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_align(occurrence_label, LV_ALIGN_TOP_MID, 0, 28);

    lv_obj_t *tasks_list = lv_list_create(screen);
    lv_obj_set_size(tasks_list, lv_pct(92), 130);
    lv_obj_align(tasks_list, LV_ALIGN_TOP_MID, 0, 52);
    lv_obj_set_style_bg_color(tasks_list, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(tasks_list, LV_OPA_COVER, 0);
    lv_obj_set_style_border_color(tasks_list, lv_palette_darken(LV_PALETTE_GREY, 2), 0);
    lv_obj_set_style_border_width(tasks_list, 1, 0);
    lv_obj_set_style_radius(tasks_list, 8, 0);
    lv_obj_set_style_pad_row(tasks_list, 6, 0);
    lv_obj_clear_flag(tasks_list, LV_OBJ_FLAG_SCROLL_MOMENTUM);
    lv_obj_clear_flag(tasks_list, LV_OBJ_FLAG_SCROLL_ELASTIC);

    yield();
    String payload = get_current_occurrence_tasks_json();
    yield();
    if (payload.length() == 0) {
        const String api_error = get_last_api_error();
        if (api_error.length()) {
            lv_obj_t *err = lv_label_create(tasks_list);
            lv_label_set_text_fmt(err, "API error: %s", api_error.c_str());
            lv_obj_set_style_text_color(err, lv_palette_lighten(LV_PALETTE_RED, 2), 0);
        } else {
            lv_obj_t *err = lv_label_create(tasks_list);
            lv_label_set_text(err, "No API response");
            lv_obj_set_style_text_color(err, lv_palette_lighten(LV_PALETTE_RED, 2), 0);
        }
    } else {
        const String occ = json_get_string_value(payload, "occurrence");
        lv_label_set_text_fmt(occurrence_label, "Period: %s", occ.length() ? occ.c_str() : "--");

        int rendered = 0;
        int scan = 0;
        while (true) {
            const int occurrence_key_pos = payload.indexOf("\"occurrences_id\":", scan);
            if (occurrence_key_pos < 0) break;

            const int occurrences_id = json_get_int_value(payload, "occurrences_id", occurrence_key_pos);
            const String name = json_get_string_value(payload, "name", occurrence_key_pos);
            bool completed = false;
            json_get_bool_value(payload, "completed", completed, occurrence_key_pos);

            if (occurrences_id > 0 && name.length() && rendered < 16) {
                task_toggle_contexts[rendered].occurrences_id = occurrences_id;

                lv_obj_t *cb = lv_checkbox_create(tasks_list);
                lv_checkbox_set_text(cb, name.c_str());
                lv_obj_set_width(cb, lv_pct(100));
                lv_obj_set_style_text_font(cb, &lv_font_montserrat_14, 0);
                lv_obj_set_style_text_color(cb, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
                if (completed) {
                    lv_obj_add_state(cb, LV_STATE_CHECKED);
                }
                lv_obj_add_event_cb(cb, tasks_checkbox_event, LV_EVENT_VALUE_CHANGED, &task_toggle_contexts[rendered]);
                rendered++;
            }

            scan = occurrence_key_pos + 17;
        }

        if (rendered == 0) {
            lv_obj_t *empty = lv_label_create(tasks_list);
            lv_label_set_text(empty, "No tasks for this period");
            lv_obj_set_style_text_color(empty, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
        }
    }

    lv_obj_t *back_btn = lv_btn_create(screen);
    lv_obj_set_size(back_btn, 100, 42);
    lv_obj_align(back_btn, LV_ALIGN_BOTTOM_MID, 0, -8);
    lv_obj_set_style_radius(back_btn, 12, 0);
    lv_obj_set_style_bg_color(back_btn, lv_palette_darken(LV_PALETTE_GREY, 4), 0);
    lv_obj_set_style_bg_opa(back_btn, LV_OPA_70, 0);
    lv_obj_set_style_border_color(back_btn, lv_palette_darken(LV_PALETTE_GREY, 2), 0);
    lv_obj_set_style_border_width(back_btn, 1, 0);
    lv_obj_add_event_cb(back_btn, load_main_screen, LV_EVENT_CLICKED, NULL);

    lv_obj_t *back_label = lv_label_create(back_btn);
    lv_label_set_text(back_label, "Back");
    lv_obj_set_style_text_color(back_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_center(back_label);

    return screen;
}

static lv_obj_t *create_calendar_screen() {
    lv_obj_t *screen = lv_obj_create(NULL);
    lv_obj_set_style_pad_all(screen, 12, 0);
    lv_obj_set_style_bg_color(screen, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(screen, 0, 0);
    lv_obj_add_event_cb(screen, global_touch_event, LV_EVENT_PRESSED, NULL);

    lv_obj_t *title = lv_label_create(screen);
    lv_label_set_text(title, "Calendar · Today");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_color(title, lv_palette_lighten(LV_PALETTE_GREY, 2), 0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);

    lv_obj_t *current_label = lv_label_create(screen);
    lv_label_set_text(current_label, "Current: --");
    lv_obj_set_style_text_font(current_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(current_label, lv_palette_lighten(LV_PALETTE_GREEN, 2), 0);
    lv_obj_align(current_label, LV_ALIGN_TOP_LEFT, 0, 26);

    lv_obj_t *next_label = lv_label_create(screen);
    lv_label_set_text(next_label, "Next: --");
    lv_obj_set_style_text_font(next_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(next_label, lv_palette_lighten(LV_PALETTE_BLUE, 2), 0);
    lv_obj_align(next_label, LV_ALIGN_TOP_LEFT, 0, 46);

    lv_obj_t *events_list = lv_list_create(screen);
    lv_obj_set_size(events_list, lv_pct(92), 108);
    lv_obj_align(events_list, LV_ALIGN_TOP_MID, 0, 70);
    lv_obj_set_style_bg_color(events_list, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(events_list, LV_OPA_COVER, 0);
    lv_obj_set_style_border_color(events_list, lv_palette_darken(LV_PALETTE_GREY, 2), 0);
    lv_obj_set_style_border_width(events_list, 1, 0);
    lv_obj_set_style_radius(events_list, 8, 0);
    lv_obj_set_style_pad_row(events_list, 6, 0);
    lv_obj_clear_flag(events_list, LV_OBJ_FLAG_SCROLL_MOMENTUM);
    lv_obj_clear_flag(events_list, LV_OBJ_FLAG_SCROLL_ELASTIC);

    yield();
    String payload = get_today_calendar_json();
    yield();
    if (payload.length() == 0) {
        const String api_error = get_last_api_error();
        lv_obj_t *err = lv_label_create(events_list);
        if (api_error.length()) {
            lv_label_set_text_fmt(err, "API error: %s", api_error.c_str());
        } else {
            lv_label_set_text(err, "No calendar response");
        }
        lv_obj_set_style_text_color(err, lv_palette_lighten(LV_PALETTE_RED, 2), 0);
    } else {
        CalendarEvent events[20];
        const int events_count = collect_calendar_events(payload, events, 20);

        if (events_count <= 0) {
            lv_obj_t *empty = lv_label_create(events_list);
            lv_label_set_text(empty, "No activities today");
            lv_obj_set_style_text_color(empty, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
        } else {
            time_t now = time(nullptr);
            struct tm now_tm;
            const int now_minutes = (localtime_r(&now, &now_tm) ? (now_tm.tm_hour * 60 + now_tm.tm_min) : -1);

            int current_index = -1;
            int next_index = -1;

            for (int i = 0; i < events_count; ++i) {
                if (now_minutes >= 0 && now_minutes >= events[i].start_minutes && now_minutes < events[i].end_minutes) {
                    current_index = i;
                }
                if (now_minutes >= 0 && events[i].start_minutes > now_minutes) {
                    next_index = i;
                    break;
                }
            }

            if (current_index >= 0) {
                lv_label_set_text_fmt(current_label,
                                      "Current: %s %s",
                                      short_time_from_iso(events[current_index].start_iso).c_str(),
                                      events[current_index].title.c_str());
            } else {
                lv_label_set_text(current_label, "Current: No active activity");
            }

            if (next_index >= 0) {
                lv_label_set_text_fmt(next_label,
                                      "Next: %s %s",
                                      short_time_from_iso(events[next_index].start_iso).c_str(),
                                      events[next_index].title.c_str());
            } else {
                lv_label_set_text(next_label, "Next: No more activities");
            }

            for (int i = 0; i < events_count; ++i) {
                lv_obj_t *row = lv_label_create(events_list);
                lv_label_set_text_fmt(row,
                                      "%s-%s  %s",
                                      short_time_from_iso(events[i].start_iso).c_str(),
                                      short_time_from_iso(events[i].end_iso).c_str(),
                                      events[i].title.c_str());
                lv_obj_set_style_text_font(row, &lv_font_montserrat_14, 0);
                lv_obj_set_style_text_color(row, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
                lv_label_set_long_mode(row, LV_LABEL_LONG_WRAP);
                lv_obj_set_width(row, lv_pct(100));
            }
        }
    }

    lv_obj_t *back_btn = lv_btn_create(screen);
    lv_obj_set_size(back_btn, 100, 42);
    lv_obj_align(back_btn, LV_ALIGN_BOTTOM_MID, 0, -8);
    lv_obj_set_style_radius(back_btn, 12, 0);
    lv_obj_set_style_bg_color(back_btn, lv_palette_darken(LV_PALETTE_GREY, 4), 0);
    lv_obj_set_style_bg_opa(back_btn, LV_OPA_70, 0);
    lv_obj_set_style_border_color(back_btn, lv_palette_darken(LV_PALETTE_GREY, 2), 0);
    lv_obj_set_style_border_width(back_btn, 1, 0);
    lv_obj_add_event_cb(back_btn, load_main_screen, LV_EVENT_CLICKED, NULL);

    lv_obj_t *back_label = lv_label_create(back_btn);
    lv_label_set_text(back_label, "Back");
    lv_obj_set_style_text_color(back_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_center(back_label);

    return screen;
}

static lv_obj_t *create_water_screen() {
    static const int QUICK_WATER_AMOUNTS[] = {250, 500, 750};

    lv_obj_t *screen = lv_obj_create(NULL);
    lv_obj_set_style_pad_all(screen, 12, 0);
    lv_obj_set_style_bg_color(screen, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(screen, 0, 0);
    lv_obj_add_event_cb(screen, global_touch_event, LV_EVENT_PRESSED, NULL);

    lv_obj_t *title = lv_label_create(screen);
    lv_label_set_text(title, "Water Intake");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_20, 0);
    lv_obj_set_style_text_color(title, lv_palette_lighten(LV_PALETTE_GREY, 2), 0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);

    lv_obj_t *subtitle = lv_label_create(screen);
    lv_label_set_text(subtitle, "Today");
    lv_obj_set_style_text_font(subtitle, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(subtitle, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_align(subtitle, LV_ALIGN_TOP_MID, 0, 26);

    water_total_label = lv_label_create(screen);
    lv_label_set_text(water_total_label, "-- ml");
    lv_obj_set_style_text_font(water_total_label, &lv_font_montserrat_32, 0);
    lv_obj_set_style_text_color(water_total_label, lv_palette_lighten(LV_PALETTE_BLUE, 1), 0);
    lv_obj_align(water_total_label, LV_ALIGN_TOP_MID, 0, 46);

    water_status_label = lv_label_create(screen);
    lv_label_set_text(water_status_label, "");
    lv_obj_set_style_text_font(water_status_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(water_status_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_align(water_status_label, LV_ALIGN_TOP_MID, 0, 84);

    lv_obj_t *buttons_row = lv_obj_create(screen);
    lv_obj_set_size(buttons_row, lv_pct(92), 64);
    lv_obj_align(buttons_row, LV_ALIGN_TOP_MID, 0, 108);
    lv_obj_set_layout(buttons_row, LV_LAYOUT_FLEX);
    lv_obj_set_flex_flow(buttons_row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(buttons_row, LV_FLEX_ALIGN_SPACE_EVENLY, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_all(buttons_row, 4, 0);
    lv_obj_set_style_bg_color(buttons_row, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(buttons_row, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(buttons_row, 0, 0);

    for (size_t i = 0; i < (sizeof(QUICK_WATER_AMOUNTS) / sizeof(QUICK_WATER_AMOUNTS[0])); ++i) {
        lv_obj_t *btn = lv_btn_create(buttons_row);
        lv_obj_set_size(btn, 66, 50);
        lv_obj_set_style_radius(btn, 10, 0);
        lv_obj_set_style_bg_color(btn, lv_palette_darken(LV_PALETTE_BLUE, 3), 0);
        lv_obj_set_style_bg_opa(btn, LV_OPA_80, 0);
        lv_obj_set_style_border_width(btn, 0, 0);
        lv_obj_add_event_cb(btn, water_add_event, LV_EVENT_CLICKED, const_cast<int *>(&QUICK_WATER_AMOUNTS[i]));

        lv_obj_t *lbl = lv_label_create(btn);
        lv_label_set_text_fmt(lbl, "+%d", QUICK_WATER_AMOUNTS[i]);
        lv_obj_set_style_text_font(lbl, &lv_font_montserrat_16, 0);
        lv_obj_set_style_text_color(lbl, lv_color_white(), 0);
        lv_obj_center(lbl);
    }

    yield();
    const String today_payload = get_today_water_json();
    yield();
    const int today_total = parse_water_total_from_payload(today_payload);
    if (today_total >= 0) {
        cached_water_total = today_total;
        set_water_module_labels(cached_water_total);
    } else {
        set_water_module_labels(cached_water_total, get_last_api_error().length() ? "API error" : "No response");
    }

    lv_obj_t *back_btn = lv_btn_create(screen);
    lv_obj_set_size(back_btn, 100, 42);
    lv_obj_align(back_btn, LV_ALIGN_BOTTOM_MID, 0, -8);
    lv_obj_set_style_radius(back_btn, 12, 0);
    lv_obj_set_style_bg_color(back_btn, lv_palette_darken(LV_PALETTE_GREY, 4), 0);
    lv_obj_set_style_bg_opa(back_btn, LV_OPA_70, 0);
    lv_obj_set_style_border_color(back_btn, lv_palette_darken(LV_PALETTE_GREY, 2), 0);
    lv_obj_set_style_border_width(back_btn, 1, 0);
    lv_obj_add_event_cb(back_btn, load_main_screen, LV_EVENT_CLICKED, NULL);

    lv_obj_t *back_label = lv_label_create(back_btn);
    lv_label_set_text(back_label, "Back");
    lv_obj_set_style_text_color(back_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_center(back_label);

    return screen;
}

// ========== GYM SCREEN ==========

static lv_obj_t *gym_screen = nullptr;
static lv_obj_t *gym_status_label = nullptr;
static lv_obj_t *gym_exercises_container = nullptr;

static void parse_gym_session_from_json(const String &json) {
    cached_gym_session.id = json_get_int_value(json, "id");
    cached_gym_session.routine_id = json_get_int_value(json, "routine_id");
    cached_gym_session.routine_name = json_get_string_value(json, "routine");
    cached_gym_session.exercise_count = 0;
    
    // Parse exercises array
    int scan = json.indexOf("\"exercises\":");
    if (scan < 0) return;
    
    while (cached_gym_session.exercise_count < 12) {
        int log_id_pos = json.indexOf("\"log_id\":", scan);
        if (log_id_pos < 0) break;
        
        GymExercise &ex = cached_gym_session.exercises[cached_gym_session.exercise_count];
        ex.log_id = json_get_int_value(json, "log_id", log_id_pos - 1);
        ex.routine_exercise_id = json_get_int_value(json, "routine_exercise_id", log_id_pos);
        ex.name = json_get_string_value(json, "exercise", log_id_pos);
        ex.target_series = json_get_int_value(json, "target_series", log_id_pos);
        ex.target_reps = json_get_int_value(json, "target_reps", log_id_pos);
        
        if (ex.log_id > 0 && ex.name.length() > 0) {
            cached_gym_session.exercise_count++;
        }
        scan = log_id_pos + 10;
    }
}

static lv_obj_t *create_gym_set_screen(int exercise_log_id, const char *exercise_name);

static void gym_exercise_click_event(lv_event_t *e) {
    mark_user_activity();
    int *data = static_cast<int *>(lv_event_get_user_data(e));
    if (!data) return;
    
    int log_id = data[0];
    int ex_index = data[1];
    
    if (ex_index >= 0 && ex_index < cached_gym_session.exercise_count) {
        current_exercise_log_id = log_id;
        current_exercise_name = cached_gym_session.exercises[ex_index].name;
        
        lv_obj_t *set_screen = create_gym_set_screen(log_id, current_exercise_name.c_str());
        lv_scr_load(set_screen);
    }
}

static void gym_start_session_event(lv_event_t *e) {
    mark_user_activity();
    int *routine_id_ptr = static_cast<int *>(lv_event_get_user_data(e));
    if (!routine_id_ptr || *routine_id_ptr <= 0) return;
    
    if (gym_status_label) {
        lv_label_set_text(gym_status_label, "Starting session...");
    }
    lv_refr_now(NULL);
    yield();
    
    const String json = get_today_gym_session_json(*routine_id_ptr);
    yield();
    if (json.length() == 0) {
        if (gym_status_label) {
            lv_label_set_text(gym_status_label, "Failed to start");
        }
        return;
    }
    
    parse_gym_session_from_json(json);
    
    // Reload gym screen to show exercises
    lv_obj_t *new_gym_screen = create_gym_screen();
    lv_scr_load(new_gym_screen);
    
    if (gym_screen && gym_screen != new_gym_screen) {
        lv_obj_del_async(gym_screen);
    }
    gym_screen = new_gym_screen;
}

static int gym_exercise_data[12][2]; // [log_id, index]

static lv_obj_t *create_gym_screen() {
    lv_obj_t *screen = lv_obj_create(NULL);
    lv_obj_set_style_pad_all(screen, 8, 0);
    lv_obj_set_style_bg_color(screen, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(screen, 0, 0);
    lv_obj_add_event_cb(screen, global_touch_event, LV_EVENT_PRESSED, NULL);
    
    gym_screen = screen;
    
    // Title
    lv_obj_t *title = lv_label_create(screen);
    lv_label_set_text(title, "Gym");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_20, 0);
    lv_obj_set_style_text_color(title, lv_palette_lighten(LV_PALETTE_GREY, 2), 0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);
    
    // Status label
    gym_status_label = lv_label_create(screen);
    lv_label_set_text(gym_status_label, "Loading...");
    lv_obj_set_style_text_font(gym_status_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(gym_status_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_align(gym_status_label, LV_ALIGN_TOP_MID, 0, 24);
    
    // Load session data
    yield();
    const String json = get_today_gym_session_json();
    yield();
    parse_gym_session_from_json(json);
    
    if (cached_gym_session.id > 0) {
        // Session exists - show routine name and exercises
        lv_label_set_text_fmt(gym_status_label, "%s", cached_gym_session.routine_name.c_str());
        
        // Create scrollable container for exercises
        lv_obj_t *list = lv_obj_create(screen);
        lv_obj_set_size(list, lv_pct(100), 130);
        lv_obj_align(list, LV_ALIGN_TOP_MID, 0, 44);
        lv_obj_set_style_pad_all(list, 4, 0);
        lv_obj_set_style_bg_color(list, lv_color_black(), 0);
        lv_obj_set_style_bg_opa(list, LV_OPA_COVER, 0);
        lv_obj_set_style_border_width(list, 0, 0);
        lv_obj_set_flex_flow(list, LV_FLEX_FLOW_COLUMN);
        lv_obj_set_flex_align(list, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
        lv_obj_set_scroll_dir(list, LV_DIR_VER);
        lv_obj_clear_flag(list, LV_OBJ_FLAG_SCROLL_MOMENTUM);
        lv_obj_clear_flag(list, LV_OBJ_FLAG_SCROLL_ELASTIC);
        
        for (int i = 0; i < cached_gym_session.exercise_count && i < 12; i++) {
            const GymExercise &ex = cached_gym_session.exercises[i];
            
            gym_exercise_data[i][0] = ex.log_id;
            gym_exercise_data[i][1] = i;
            
            lv_obj_t *btn = lv_btn_create(list);
            lv_obj_set_size(btn, lv_pct(98), 36);
            lv_obj_set_style_radius(btn, 8, 0);
            lv_obj_set_style_bg_color(btn, lv_palette_darken(LV_PALETTE_GREEN, 3), 0);
            lv_obj_set_style_bg_opa(btn, LV_OPA_80, 0);
            lv_obj_set_style_border_width(btn, 0, 0);
            lv_obj_add_event_cb(btn, gym_exercise_click_event, LV_EVENT_CLICKED, gym_exercise_data[i]);
            
            lv_obj_t *lbl = lv_label_create(btn);
            lv_label_set_text_fmt(lbl, "%s (%dx%d)", ex.name.c_str(), ex.target_series, ex.target_reps);
            lv_obj_set_style_text_font(lbl, &lv_font_montserrat_14, 0);
            lv_obj_set_style_text_color(lbl, lv_color_white(), 0);
            lv_obj_center(lbl);
        }
        
        if (cached_gym_session.exercise_count == 0) {
            lv_obj_t *empty_lbl = lv_label_create(list);
            lv_label_set_text(empty_lbl, "No exercises yet");
            lv_obj_set_style_text_color(empty_lbl, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
        }
        
    } else {
        // No session - show start button
        lv_label_set_text(gym_status_label, "No session today");
        
        // Get active routine ID (we'll use routine_id 1 by default for simplicity)
        // In a full implementation, you'd fetch routines and let user pick
        static int default_routine_id = 1;
        
        lv_obj_t *start_btn = lv_btn_create(screen);
        lv_obj_set_size(start_btn, 160, 50);
        lv_obj_align(start_btn, LV_ALIGN_CENTER, 0, 0);
        lv_obj_set_style_radius(start_btn, 12, 0);
        lv_obj_set_style_bg_color(start_btn, lv_palette_darken(LV_PALETTE_GREEN, 2), 0);
        lv_obj_set_style_bg_opa(start_btn, LV_OPA_90, 0);
        lv_obj_set_style_border_width(start_btn, 0, 0);
        lv_obj_add_event_cb(start_btn, gym_start_session_event, LV_EVENT_CLICKED, &default_routine_id);
        
        lv_obj_t *start_lbl = lv_label_create(start_btn);
        lv_label_set_text(start_lbl, "Start Session");
        lv_obj_set_style_text_font(start_lbl, &lv_font_montserrat_16, 0);
        lv_obj_set_style_text_color(start_lbl, lv_color_white(), 0);
        lv_obj_center(start_lbl);
    }
    
    // Back button
    lv_obj_t *back_btn = lv_btn_create(screen);
    lv_obj_set_size(back_btn, 100, 36);
    lv_obj_align(back_btn, LV_ALIGN_BOTTOM_MID, 0, -4);
    lv_obj_set_style_radius(back_btn, 10, 0);
    lv_obj_set_style_bg_color(back_btn, lv_palette_darken(LV_PALETTE_GREY, 4), 0);
    lv_obj_set_style_bg_opa(back_btn, LV_OPA_70, 0);
    lv_obj_set_style_border_color(back_btn, lv_palette_darken(LV_PALETTE_GREY, 2), 0);
    lv_obj_set_style_border_width(back_btn, 1, 0);
    lv_obj_add_event_cb(back_btn, load_main_screen, LV_EVENT_CLICKED, NULL);
    
    lv_obj_t *back_label = lv_label_create(back_btn);
    lv_label_set_text(back_label, "Back");
    lv_obj_set_style_text_color(back_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_center(back_label);
    
    return screen;
}

// ========== GYM SET LOGGING SCREEN ==========

static lv_obj_t *set_weight_spinbox = nullptr;
static lv_obj_t *set_reps_spinbox = nullptr;
static lv_obj_t *set_number_label = nullptr;
static int current_set_number = 1;
static int logged_sets_count = 0;

static void gym_back_to_exercises_event(lv_event_t *e) {
    mark_user_activity();
    lv_obj_t *current_screen = lv_scr_act();
    lv_obj_t *new_gym_screen = create_gym_screen();
    lv_scr_load(new_gym_screen);
    
    if (current_screen && current_screen != new_gym_screen) {
        lv_obj_del_async(current_screen);
    }
}

static void gym_save_set_event(lv_event_t *e) {
    mark_user_activity();
    
    if (!set_weight_spinbox || !set_reps_spinbox) return;
    
    int weight_val = lv_spinbox_get_value(set_weight_spinbox);
    int reps_val = lv_spinbox_get_value(set_reps_spinbox);
    
    float weight = weight_val / 10.0f; // Spinbox stores weight * 10
    
    if (reps_val <= 0) {
        return;
    }
    
    lv_refr_now(NULL);
    yield();
    
    bool ok = save_gym_set(current_exercise_log_id, current_set_number, weight, reps_val, -1);
    yield();
    
    if (ok) {
        current_set_number++;
        logged_sets_count++;
        
        if (set_number_label) {
            lv_label_set_text_fmt(set_number_label, "Set #%d", current_set_number);
        }
        
        // Reset spinboxes for next set
        lv_spinbox_set_value(set_reps_spinbox, 0);
    }
}

static void spinbox_increment_event(lv_event_t *e) {
    lv_obj_t *spinbox = static_cast<lv_obj_t *>(lv_event_get_user_data(e));
    if (spinbox) lv_spinbox_increment(spinbox);
}

static void spinbox_decrement_event(lv_event_t *e) {
    lv_obj_t *spinbox = static_cast<lv_obj_t *>(lv_event_get_user_data(e));
    if (spinbox) lv_spinbox_decrement(spinbox);
}

static lv_obj_t *create_gym_set_screen(int exercise_log_id, const char *exercise_name) {
    lv_obj_t *screen = lv_obj_create(NULL);
    lv_obj_set_style_pad_all(screen, 8, 0);
    lv_obj_set_style_bg_color(screen, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(screen, 0, 0);
    lv_obj_add_event_cb(screen, global_touch_event, LV_EVENT_PRESSED, NULL);
    
    // Title - exercise name
    lv_obj_t *title = lv_label_create(screen);
    lv_label_set_text(title, exercise_name);
    lv_obj_set_style_text_font(title, &lv_font_montserrat_16, 0);
    lv_obj_set_style_text_color(title, lv_palette_lighten(LV_PALETTE_GREY, 2), 0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);
    
    // Load existing sets to get next set number
    yield();
    const String json = get_exercise_sets_json(exercise_log_id);
    yield();
    logged_sets_count = 0;
    int scan = 0;
    while (true) {
        int pos = json.indexOf("\"set_number\":", scan);
        if (pos < 0) break;
        logged_sets_count++;
        scan = pos + 13;
    }
    current_set_number = logged_sets_count + 1;
    
    // Set number label
    set_number_label = lv_label_create(screen);
    lv_label_set_text_fmt(set_number_label, "Set #%d", current_set_number);
    lv_obj_set_style_text_font(set_number_label, &lv_font_montserrat_20, 0);
    lv_obj_set_style_text_color(set_number_label, lv_palette_lighten(LV_PALETTE_GREEN, 1), 0);
    lv_obj_align(set_number_label, LV_ALIGN_TOP_MID, 0, 22);
    
    // Weight row
    lv_obj_t *weight_row = lv_obj_create(screen);
    lv_obj_set_size(weight_row, lv_pct(100), 44);
    lv_obj_align(weight_row, LV_ALIGN_TOP_MID, 0, 50);
    lv_obj_set_style_pad_all(weight_row, 2, 0);
    lv_obj_set_style_bg_color(weight_row, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(weight_row, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(weight_row, 0, 0);
    lv_obj_set_flex_flow(weight_row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(weight_row, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    
    lv_obj_t *weight_lbl = lv_label_create(weight_row);
    lv_label_set_text(weight_lbl, "Kg:");
    lv_obj_set_style_text_color(weight_lbl, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_set_style_text_font(weight_lbl, &lv_font_montserrat_14, 0);
    
    lv_obj_t *weight_minus = lv_btn_create(weight_row);
    lv_obj_set_size(weight_minus, 36, 36);
    lv_obj_set_style_bg_color(weight_minus, lv_palette_darken(LV_PALETTE_GREY, 3), 0);
    
    set_weight_spinbox = lv_spinbox_create(weight_row);
    lv_spinbox_set_range(set_weight_spinbox, 0, 2000); // 0-200.0 kg
    lv_spinbox_set_digit_format(set_weight_spinbox, 4, 3); // 4 digits, 1 decimal
    lv_spinbox_set_step(set_weight_spinbox, 25); // 2.5kg steps
    lv_spinbox_set_value(set_weight_spinbox, 0);
    lv_obj_set_size(set_weight_spinbox, 70, 36);
    lv_obj_set_style_text_color(set_weight_spinbox, lv_color_white(), 0);
    lv_obj_set_style_bg_color(set_weight_spinbox, lv_palette_darken(LV_PALETTE_GREY, 3), 0);
    
    lv_obj_t *weight_plus = lv_btn_create(weight_row);
    lv_obj_set_size(weight_plus, 36, 36);
    lv_obj_set_style_bg_color(weight_plus, lv_palette_darken(LV_PALETTE_GREY, 3), 0);
    
    lv_obj_t *minus_lbl1 = lv_label_create(weight_minus);
    lv_label_set_text(minus_lbl1, "-");
    lv_obj_center(minus_lbl1);
    lv_obj_t *plus_lbl1 = lv_label_create(weight_plus);
    lv_label_set_text(plus_lbl1, "+");
    lv_obj_center(plus_lbl1);
    
    lv_obj_add_event_cb(weight_minus, spinbox_decrement_event, LV_EVENT_CLICKED, set_weight_spinbox);
    lv_obj_add_event_cb(weight_plus, spinbox_increment_event, LV_EVENT_CLICKED, set_weight_spinbox);
    
    // Reps row
    lv_obj_t *reps_row = lv_obj_create(screen);
    lv_obj_set_size(reps_row, lv_pct(100), 44);
    lv_obj_align(reps_row, LV_ALIGN_TOP_MID, 0, 98);
    lv_obj_set_style_pad_all(reps_row, 2, 0);
    lv_obj_set_style_bg_color(reps_row, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(reps_row, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(reps_row, 0, 0);
    lv_obj_set_flex_flow(reps_row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(reps_row, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    
    lv_obj_t *reps_lbl = lv_label_create(reps_row);
    lv_label_set_text(reps_lbl, "Reps:");
    lv_obj_set_style_text_color(reps_lbl, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_set_style_text_font(reps_lbl, &lv_font_montserrat_14, 0);
    
    lv_obj_t *reps_minus = lv_btn_create(reps_row);
    lv_obj_set_size(reps_minus, 36, 36);
    lv_obj_set_style_bg_color(reps_minus, lv_palette_darken(LV_PALETTE_GREY, 3), 0);
    
    set_reps_spinbox = lv_spinbox_create(reps_row);
    lv_spinbox_set_range(set_reps_spinbox, 0, 100);
    lv_spinbox_set_digit_format(set_reps_spinbox, 2, 0);
    lv_spinbox_set_step(set_reps_spinbox, 1);
    lv_spinbox_set_value(set_reps_spinbox, 0);
    lv_obj_set_size(set_reps_spinbox, 50, 36);
    lv_obj_set_style_text_color(set_reps_spinbox, lv_color_white(), 0);
    lv_obj_set_style_bg_color(set_reps_spinbox, lv_palette_darken(LV_PALETTE_GREY, 3), 0);
    
    lv_obj_t *reps_plus = lv_btn_create(reps_row);
    lv_obj_set_size(reps_plus, 36, 36);
    lv_obj_set_style_bg_color(reps_plus, lv_palette_darken(LV_PALETTE_GREY, 3), 0);
    
    lv_obj_t *minus_lbl2 = lv_label_create(reps_minus);
    lv_label_set_text(minus_lbl2, "-");
    lv_obj_center(minus_lbl2);
    lv_obj_t *plus_lbl2 = lv_label_create(reps_plus);
    lv_label_set_text(plus_lbl2, "+");
    lv_obj_center(plus_lbl2);
    
    lv_obj_add_event_cb(reps_minus, spinbox_decrement_event, LV_EVENT_CLICKED, set_reps_spinbox);
    lv_obj_add_event_cb(reps_plus, spinbox_increment_event, LV_EVENT_CLICKED, set_reps_spinbox);
    
    // Save button
    lv_obj_t *save_btn = lv_btn_create(screen);
    lv_obj_set_size(save_btn, 120, 40);
    lv_obj_align(save_btn, LV_ALIGN_BOTTOM_MID, 0, -44);
    lv_obj_set_style_radius(save_btn, 10, 0);
    lv_obj_set_style_bg_color(save_btn, lv_palette_darken(LV_PALETTE_GREEN, 2), 0);
    lv_obj_set_style_bg_opa(save_btn, LV_OPA_90, 0);
    lv_obj_set_style_border_width(save_btn, 0, 0);
    lv_obj_add_event_cb(save_btn, gym_save_set_event, LV_EVENT_CLICKED, NULL);
    
    lv_obj_t *save_lbl = lv_label_create(save_btn);
    lv_label_set_text(save_lbl, "Save Set");
    lv_obj_set_style_text_font(save_lbl, &lv_font_montserrat_16, 0);
    lv_obj_set_style_text_color(save_lbl, lv_color_white(), 0);
    lv_obj_center(save_lbl);
    
    // Back button
    lv_obj_t *back_btn = lv_btn_create(screen);
    lv_obj_set_size(back_btn, 80, 32);
    lv_obj_align(back_btn, LV_ALIGN_BOTTOM_MID, 0, -4);
    lv_obj_set_style_radius(back_btn, 8, 0);
    lv_obj_set_style_bg_color(back_btn, lv_palette_darken(LV_PALETTE_GREY, 4), 0);
    lv_obj_set_style_bg_opa(back_btn, LV_OPA_70, 0);
    lv_obj_set_style_border_color(back_btn, lv_palette_darken(LV_PALETTE_GREY, 2), 0);
    lv_obj_set_style_border_width(back_btn, 1, 0);
    lv_obj_add_event_cb(back_btn, gym_back_to_exercises_event, LV_EVENT_CLICKED, NULL);
    
    lv_obj_t *back_label = lv_label_create(back_btn);
    lv_label_set_text(back_label, "Back");
    lv_obj_set_style_text_color(back_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_center(back_label);
    
    return screen;
}

// ========== SHOPPING LIST SCREEN ==========

static String shopping_items[20];
static int shopping_item_count = 0;

static void parse_shopping_list_json(const String &json) {
    shopping_item_count = 0;
    
    // JSON is a simple array: ["item1", "item2", ...]
    int scan = 0;
    while (shopping_item_count < 20) {
        int quote_start = json.indexOf('"', scan);
        if (quote_start < 0) break;
        
        int quote_end = json.indexOf('"', quote_start + 1);
        if (quote_end < 0) break;
        
        String item = json.substring(quote_start + 1, quote_end);
        if (item.length() > 0 && item != "[" && item != "]") {
            shopping_items[shopping_item_count] = item;
            shopping_item_count++;
        }
        scan = quote_end + 1;
    }
}

static void shopping_item_click_event(lv_event_t *e) {
    mark_user_activity();
    
    int *index_ptr = static_cast<int *>(lv_event_get_user_data(e));
    if (!index_ptr || *index_ptr < 0 || *index_ptr >= shopping_item_count) return;
    
    const String &item = shopping_items[*index_ptr];
    
    // Delete the item (mark as bought)
    lv_refr_now(NULL);
    yield();
    
    bool ok = delete_shopping_item(item);
    yield();
    
    if (ok) {
        // Reload screen to refresh list
        lv_obj_t *current_screen = lv_scr_act();
        lv_obj_t *new_screen = create_shopping_screen();
        lv_scr_load(new_screen);
        
        if (current_screen && current_screen != new_screen) {
            lv_obj_del_async(current_screen);
        }
    }
}

static int shopping_item_indices[20];

static lv_obj_t *create_shopping_screen() {
    lv_obj_t *screen = lv_obj_create(NULL);
    lv_obj_set_style_pad_all(screen, 8, 0);
    lv_obj_set_style_bg_color(screen, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(screen, 0, 0);
    lv_obj_add_event_cb(screen, global_touch_event, LV_EVENT_PRESSED, NULL);
    
    // Title
    lv_obj_t *title = lv_label_create(screen);
    lv_label_set_text(title, "Shopping List");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_color(title, lv_palette_lighten(LV_PALETTE_GREY, 2), 0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);
    
    // Status label
    lv_obj_t *status_label = lv_label_create(screen);
    lv_label_set_text(status_label, "Loading...");
    lv_obj_set_style_text_font(status_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(status_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_align(status_label, LV_ALIGN_TOP_MID, 0, 22);
    
    // Load shopping list
    yield();
    const String json = get_shopping_list_json();
    yield();
    parse_shopping_list_json(json);
    
    if (shopping_item_count > 0) {
        lv_label_set_text_fmt(status_label, "%d items - tap to check off", shopping_item_count);
        
        // Create scrollable container for items
        lv_obj_t *list = lv_obj_create(screen);
        lv_obj_set_size(list, lv_pct(100), 140);
        lv_obj_align(list, LV_ALIGN_TOP_MID, 0, 42);
        lv_obj_set_style_pad_all(list, 4, 0);
        lv_obj_set_style_bg_color(list, lv_color_black(), 0);
        lv_obj_set_style_bg_opa(list, LV_OPA_COVER, 0);
        lv_obj_set_style_border_width(list, 0, 0);
        lv_obj_set_flex_flow(list, LV_FLEX_FLOW_COLUMN);
        lv_obj_set_flex_align(list, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
        lv_obj_set_scroll_dir(list, LV_DIR_VER);
        lv_obj_clear_flag(list, LV_OBJ_FLAG_SCROLL_MOMENTUM);
        lv_obj_clear_flag(list, LV_OBJ_FLAG_SCROLL_ELASTIC);
        
        for (int i = 0; i < shopping_item_count && i < 20; i++) {
            shopping_item_indices[i] = i;
            
            lv_obj_t *btn = lv_btn_create(list);
            lv_obj_set_size(btn, lv_pct(98), 32);
            lv_obj_set_style_radius(btn, 8, 0);
            lv_obj_set_style_bg_color(btn, lv_palette_darken(LV_PALETTE_ORANGE, 3), 0);
            lv_obj_set_style_bg_opa(btn, LV_OPA_80, 0);
            lv_obj_set_style_border_width(btn, 0, 0);
            lv_obj_add_event_cb(btn, shopping_item_click_event, LV_EVENT_CLICKED, &shopping_item_indices[i]);
            
            lv_obj_t *lbl = lv_label_create(btn);
            lv_label_set_text(lbl, shopping_items[i].c_str());
            lv_obj_set_style_text_font(lbl, &lv_font_montserrat_14, 0);
            lv_obj_set_style_text_color(lbl, lv_color_white(), 0);
            lv_obj_center(lbl);
        }
    } else {
        lv_label_set_text(status_label, "List is empty");
    }
    
    // Back button
    lv_obj_t *back_btn = lv_btn_create(screen);
    lv_obj_set_size(back_btn, 100, 36);
    lv_obj_align(back_btn, LV_ALIGN_BOTTOM_MID, 0, -4);
    lv_obj_set_style_radius(back_btn, 10, 0);
    lv_obj_set_style_bg_color(back_btn, lv_palette_darken(LV_PALETTE_GREY, 4), 0);
    lv_obj_set_style_bg_opa(back_btn, LV_OPA_70, 0);
    lv_obj_set_style_border_color(back_btn, lv_palette_darken(LV_PALETTE_GREY, 2), 0);
    lv_obj_set_style_border_width(back_btn, 1, 0);
    lv_obj_add_event_cb(back_btn, load_main_screen, LV_EVENT_CLICKED, NULL);
    
    lv_obj_t *back_label = lv_label_create(back_btn);
    lv_label_set_text(back_label, "Back");
    lv_obj_set_style_text_color(back_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_center(back_label);
    
    return screen;
}

// ========== POMODORO SCREEN ==========

static lv_obj_t *pomodoro_screen = nullptr;
static lv_obj_t *pomodoro_timer_label = nullptr;
static lv_obj_t *pomodoro_mode_label = nullptr;
static lv_obj_t *pomodoro_status_label = nullptr;
static lv_timer_t *pomodoro_refresh_timer = nullptr;
static String pomodoro_active_type = "";
static int pomodoro_study_remaining = 0;
static int pomodoro_rest_remaining = 0;
static bool pomodoro_has_session = false;

static String format_time_mmss(int total_seconds) {
    int mins = total_seconds / 60;
    int secs = total_seconds % 60;
    char buf[16];
    snprintf(buf, sizeof(buf), "%02d:%02d", mins, secs);
    return String(buf);
}

static void parse_pomodoro_current(const String &json) {
    if (json.length() == 0 || json == "null" || json.indexOf("pomodoro_id") < 0) {
        pomodoro_has_session = false;
        pomodoro_active_type = "";
        pomodoro_study_remaining = 0;
        pomodoro_rest_remaining = 0;
        return;
    }
    
    pomodoro_has_session = true;
    pomodoro_active_type = json_get_string_value(json, "active_type");
    pomodoro_study_remaining = json_get_int_value(json, "study_remaining");
    pomodoro_rest_remaining = json_get_int_value(json, "rest_remaining");
    
    if (pomodoro_study_remaining < 0) pomodoro_study_remaining = 0;
    if (pomodoro_rest_remaining < 0) pomodoro_rest_remaining = 0;
}

static void update_pomodoro_display() {
    if (!pomodoro_timer_label || !pomodoro_mode_label) return;
    
    if (!pomodoro_has_session) {
        lv_label_set_text(pomodoro_timer_label, "--:--");
        lv_label_set_text(pomodoro_mode_label, "No session");
        lv_obj_set_style_text_color(pomodoro_mode_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    } else if (pomodoro_active_type == "study") {
        lv_label_set_text(pomodoro_timer_label, format_time_mmss(pomodoro_study_remaining).c_str());
        lv_label_set_text(pomodoro_mode_label, "STUDY");
        lv_obj_set_style_text_color(pomodoro_mode_label, lv_palette_main(LV_PALETTE_RED), 0);
    } else if (pomodoro_active_type == "rest") {
        lv_label_set_text(pomodoro_timer_label, format_time_mmss(pomodoro_rest_remaining).c_str());
        lv_label_set_text(pomodoro_mode_label, "REST");
        lv_obj_set_style_text_color(pomodoro_mode_label, lv_palette_main(LV_PALETTE_GREEN), 0);
    } else {
        lv_label_set_text(pomodoro_timer_label, "--:--");
        lv_label_set_text(pomodoro_mode_label, "Unknown");
    }
}

static void pomodoro_refresh_cb(lv_timer_t *timer) {
    LV_UNUSED(timer);
    
    // Don't refresh if we're not on the pomodoro screen
    if (lv_scr_act() != pomodoro_screen) return;
    
    yield();
    const String json = get_pomodoro_current_json();
    yield();
    parse_pomodoro_current(json);
    update_pomodoro_display();
}

static void pomodoro_start_event(lv_event_t *e) {
    mark_user_activity();
    if (pomodoro_status_label) lv_label_set_text(pomodoro_status_label, "Starting...");
    lv_refr_now(NULL); // Force UI update before blocking call
    
    if (start_pomodoro_session()) {
        yield();
        if (pomodoro_status_label) lv_label_set_text(pomodoro_status_label, "Started!");
        pomodoro_refresh_cb(nullptr);
    } else {
        if (pomodoro_status_label) lv_label_set_text(pomodoro_status_label, "Failed");
    }
}

static void pomodoro_switch_event(lv_event_t *e) {
    mark_user_activity();
    if (pomodoro_status_label) lv_label_set_text(pomodoro_status_label, "Switching...");
    lv_refr_now(NULL); // Force UI update before blocking call
    
    if (change_pomodoro_state()) {
        yield();
        if (pomodoro_status_label) lv_label_set_text(pomodoro_status_label, "Switched!");
        pomodoro_refresh_cb(nullptr);
    } else {
        if (pomodoro_status_label) lv_label_set_text(pomodoro_status_label, "Failed");
    }
}

static void pomodoro_end_event(lv_event_t *e) {
    mark_user_activity();
    if (pomodoro_status_label) lv_label_set_text(pomodoro_status_label, "Ending...");
    lv_refr_now(NULL); // Force UI update before blocking call
    
    if (end_pomodoro_session()) {
        yield();
        if (pomodoro_status_label) lv_label_set_text(pomodoro_status_label, "Ended");
        pomodoro_has_session = false;
        update_pomodoro_display();
    } else {
        if (pomodoro_status_label) lv_label_set_text(pomodoro_status_label, "Failed");
    }
}

static void pomodoro_back_event(lv_event_t *e) {
    mark_user_activity();
    
    // Stop the refresh timer when leaving
    if (pomodoro_refresh_timer) {
        lv_timer_del(pomodoro_refresh_timer);
        pomodoro_refresh_timer = nullptr;
    }
    
    load_main_screen(e);
}

static lv_obj_t *create_pomodoro_screen() {
    lv_obj_t *screen = lv_obj_create(NULL);
    lv_obj_set_style_pad_all(screen, 10, 0);
    lv_obj_set_style_bg_color(screen, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(screen, 0, 0);
    lv_obj_add_event_cb(screen, global_touch_event, LV_EVENT_PRESSED, NULL);
    
    pomodoro_screen = screen;
    
    // Title
    lv_obj_t *title = lv_label_create(screen);
    lv_label_set_text(title, "Pomodoro");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_color(title, lv_palette_lighten(LV_PALETTE_GREY, 2), 0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);
    
    // Mode label (STUDY / REST)
    pomodoro_mode_label = lv_label_create(screen);
    lv_label_set_text(pomodoro_mode_label, "Loading...");
    lv_obj_set_style_text_font(pomodoro_mode_label, &lv_font_montserrat_16, 0);
    lv_obj_set_style_text_color(pomodoro_mode_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_align(pomodoro_mode_label, LV_ALIGN_TOP_MID, 0, 22);
    
    // Timer label (big)
    pomodoro_timer_label = lv_label_create(screen);
    lv_label_set_text(pomodoro_timer_label, "--:--");
    lv_obj_set_style_text_font(pomodoro_timer_label, &lv_font_montserrat_32, 0);
    lv_obj_set_style_text_color(pomodoro_timer_label, lv_color_white(), 0);
    lv_obj_align(pomodoro_timer_label, LV_ALIGN_TOP_MID, 0, 42);
    
    // Status label (small, for feedback)
    pomodoro_status_label = lv_label_create(screen);
    lv_label_set_text(pomodoro_status_label, "");
    lv_obj_set_style_text_font(pomodoro_status_label, &lv_font_montserrat_12, 0);
    lv_obj_set_style_text_color(pomodoro_status_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_align(pomodoro_status_label, LV_ALIGN_TOP_MID, 0, 78);
    
    // Buttons row
    lv_obj_t *btn_row = lv_obj_create(screen);
    lv_obj_set_size(btn_row, lv_pct(100), 50);
    lv_obj_align(btn_row, LV_ALIGN_TOP_MID, 0, 95);
    lv_obj_set_style_pad_all(btn_row, 4, 0);
    lv_obj_set_style_bg_opa(btn_row, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(btn_row, 0, 0);
    lv_obj_set_flex_flow(btn_row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(btn_row, LV_FLEX_ALIGN_SPACE_EVENLY, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    
    // Start button
    lv_obj_t *start_btn = lv_btn_create(btn_row);
    lv_obj_set_size(start_btn, 60, 40);
    lv_obj_set_style_radius(start_btn, 8, 0);
    lv_obj_set_style_bg_color(start_btn, lv_palette_darken(LV_PALETTE_BLUE, 2), 0);
    lv_obj_set_style_border_width(start_btn, 0, 0);
    lv_obj_add_event_cb(start_btn, pomodoro_start_event, LV_EVENT_CLICKED, NULL);
    lv_obj_t *start_lbl = lv_label_create(start_btn);
    lv_label_set_text(start_lbl, "Start");
    lv_obj_set_style_text_font(start_lbl, &lv_font_montserrat_12, 0);
    lv_obj_center(start_lbl);
    
    // Switch button
    lv_obj_t *switch_btn = lv_btn_create(btn_row);
    lv_obj_set_size(switch_btn, 60, 40);
    lv_obj_set_style_radius(switch_btn, 8, 0);
    lv_obj_set_style_bg_color(switch_btn, lv_palette_darken(LV_PALETTE_ORANGE, 2), 0);
    lv_obj_set_style_border_width(switch_btn, 0, 0);
    lv_obj_add_event_cb(switch_btn, pomodoro_switch_event, LV_EVENT_CLICKED, NULL);
    lv_obj_t *switch_lbl = lv_label_create(switch_btn);
    lv_label_set_text(switch_lbl, "Switch");
    lv_obj_set_style_text_font(switch_lbl, &lv_font_montserrat_12, 0);
    lv_obj_center(switch_lbl);
    
    // End button
    lv_obj_t *end_btn = lv_btn_create(btn_row);
    lv_obj_set_size(end_btn, 60, 40);
    lv_obj_set_style_radius(end_btn, 8, 0);
    lv_obj_set_style_bg_color(end_btn, lv_palette_darken(LV_PALETTE_GREY, 3), 0);
    lv_obj_set_style_border_width(end_btn, 0, 0);
    lv_obj_add_event_cb(end_btn, pomodoro_end_event, LV_EVENT_CLICKED, NULL);
    lv_obj_t *end_lbl = lv_label_create(end_btn);
    lv_label_set_text(end_lbl, "End");
    lv_obj_set_style_text_font(end_lbl, &lv_font_montserrat_12, 0);
    lv_obj_center(end_lbl);
    
    // Back button
    lv_obj_t *back_btn = lv_btn_create(screen);
    lv_obj_set_size(back_btn, 100, 34);
    lv_obj_align(back_btn, LV_ALIGN_BOTTOM_MID, 0, -4);
    lv_obj_set_style_radius(back_btn, 10, 0);
    lv_obj_set_style_bg_color(back_btn, lv_palette_darken(LV_PALETTE_GREY, 4), 0);
    lv_obj_set_style_bg_opa(back_btn, LV_OPA_70, 0);
    lv_obj_set_style_border_color(back_btn, lv_palette_darken(LV_PALETTE_GREY, 2), 0);
    lv_obj_set_style_border_width(back_btn, 1, 0);
    lv_obj_add_event_cb(back_btn, pomodoro_back_event, LV_EVENT_CLICKED, NULL);
    
    lv_obj_t *back_label = lv_label_create(back_btn);
    lv_label_set_text(back_label, "Back");
    lv_obj_set_style_text_color(back_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_center(back_label);
    
    // Load initial data
    pomodoro_refresh_cb(nullptr);
    
    // Start refresh timer (30 seconds to save battery and avoid watchdog)
    pomodoro_refresh_timer = lv_timer_create(pomodoro_refresh_cb, 30000, NULL);
    
    return screen;
}

// ============== SETTINGS SCREEN ==============
static int settings_brightness = DEVICE_MAX_BRIGHTNESS_LEVEL;
static lv_obj_t *settings_brightness_slider = nullptr;
static lv_obj_t *settings_battery_label = nullptr;
static lv_obj_t *settings_wifi_label = nullptr;
static lv_obj_t *settings_ip_label = nullptr;

static void settings_brightness_event(lv_event_t *e) {
    mark_user_activity();
    lv_obj_t *slider = static_cast<lv_obj_t *>(lv_event_get_target(e));
    settings_brightness = lv_slider_get_value(slider);
    instance.setBrightness(settings_brightness);
}

static void settings_reboot_event(lv_event_t *e) {
    mark_user_activity();
    Serial.println("Rebooting...");
    ESP.restart();
}

static void settings_sleep_event(lv_event_t *e) {
    mark_user_activity();
    Serial.println("Entering deep sleep");
    WiFi.disconnect(true, false);
    WiFi.mode(WIFI_OFF);
    esp_sleep_enable_ext0_wakeup(GPIO_NUM_0, 0);
    delay(50);
    esp_deep_sleep_start();
}

static lv_obj_t *create_settings_screen() {
    lv_obj_t *screen = lv_obj_create(NULL);
    lv_obj_set_style_pad_all(screen, 10, 0);
    lv_obj_set_style_bg_color(screen, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(screen, 0, 0);
    lv_obj_add_event_cb(screen, global_touch_event, LV_EVENT_PRESSED, NULL);
    
    // Title
    lv_obj_t *title = lv_label_create(screen);
    lv_label_set_text(title, LV_SYMBOL_SETTINGS " Settings");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_color(title, lv_palette_lighten(LV_PALETTE_GREY, 2), 0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);
    
    // Brightness section
    lv_obj_t *brightness_label = lv_label_create(screen);
    lv_label_set_text(brightness_label, "Brightness");
    lv_obj_set_style_text_font(brightness_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(brightness_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_align(brightness_label, LV_ALIGN_TOP_LEFT, 0, 26);
    
    settings_brightness_slider = lv_slider_create(screen);
    lv_obj_set_size(settings_brightness_slider, lv_pct(85), 12);
    lv_obj_align(settings_brightness_slider, LV_ALIGN_TOP_MID, 0, 44);
    lv_slider_set_range(settings_brightness_slider, 10, DEVICE_MAX_BRIGHTNESS_LEVEL);
    lv_slider_set_value(settings_brightness_slider, settings_brightness, LV_ANIM_OFF);
    lv_obj_set_style_bg_color(settings_brightness_slider, lv_palette_darken(LV_PALETTE_GREY, 3), LV_PART_MAIN);
    lv_obj_set_style_bg_color(settings_brightness_slider, lv_palette_main(LV_PALETTE_BLUE), LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(settings_brightness_slider, lv_color_white(), LV_PART_KNOB);
    lv_obj_add_event_cb(settings_brightness_slider, settings_brightness_event, LV_EVENT_VALUE_CHANGED, NULL);
    
    // Info section
    settings_battery_label = lv_label_create(screen);
    int battery_percent = instance.pmu.getBatteryPercent();
    bool charging = instance.pmu.isCharging();
    lv_label_set_text_fmt(settings_battery_label, "Battery: %d%% %s", 
                          battery_percent, charging ? "(charging)" : "");
    lv_obj_set_style_text_font(settings_battery_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(settings_battery_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_align(settings_battery_label, LV_ALIGN_TOP_LEFT, 0, 66);
    
    settings_wifi_label = lv_label_create(screen);
    bool wifi_connected = (WiFi.status() == WL_CONNECTED);
    lv_label_set_text_fmt(settings_wifi_label, "WiFi: %s", wifi_connected ? "Connected" : "Disconnected");
    lv_obj_set_style_text_font(settings_wifi_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(settings_wifi_label, 
                                 wifi_connected ? lv_palette_main(LV_PALETTE_GREEN) : lv_palette_main(LV_PALETTE_RED), 0);
    lv_obj_align(settings_wifi_label, LV_ALIGN_TOP_LEFT, 0, 86);
    
    settings_ip_label = lv_label_create(screen);
    if (wifi_connected) {
        lv_label_set_text_fmt(settings_ip_label, "IP: %s", WiFi.localIP().toString().c_str());
    } else {
        lv_label_set_text(settings_ip_label, "IP: --");
    }
    lv_obj_set_style_text_font(settings_ip_label, &lv_font_montserrat_12, 0);
    lv_obj_set_style_text_color(settings_ip_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_align(settings_ip_label, LV_ALIGN_TOP_LEFT, 0, 106);
    
    // Button row
    lv_obj_t *btn_row = lv_obj_create(screen);
    lv_obj_set_size(btn_row, lv_pct(100), 38);
    lv_obj_align(btn_row, LV_ALIGN_TOP_MID, 0, 126);
    lv_obj_set_style_pad_all(btn_row, 2, 0);
    lv_obj_set_style_bg_opa(btn_row, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(btn_row, 0, 0);
    lv_obj_set_flex_flow(btn_row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(btn_row, LV_FLEX_ALIGN_SPACE_EVENLY, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    
    // Reboot button
    lv_obj_t *reboot_btn = lv_btn_create(btn_row);
    lv_obj_set_size(reboot_btn, 80, 32);
    lv_obj_set_style_radius(reboot_btn, 8, 0);
    lv_obj_set_style_bg_color(reboot_btn, lv_palette_darken(LV_PALETTE_ORANGE, 2), 0);
    lv_obj_set_style_border_width(reboot_btn, 0, 0);
    lv_obj_add_event_cb(reboot_btn, settings_reboot_event, LV_EVENT_CLICKED, NULL);
    lv_obj_t *reboot_lbl = lv_label_create(reboot_btn);
    lv_label_set_text(reboot_lbl, LV_SYMBOL_REFRESH " Reboot");
    lv_obj_set_style_text_font(reboot_lbl, &lv_font_montserrat_12, 0);
    lv_obj_center(reboot_lbl);
    
    // Sleep button
    lv_obj_t *sleep_btn = lv_btn_create(btn_row);
    lv_obj_set_size(sleep_btn, 80, 32);
    lv_obj_set_style_radius(sleep_btn, 8, 0);
    lv_obj_set_style_bg_color(sleep_btn, lv_palette_darken(LV_PALETTE_PURPLE, 2), 0);
    lv_obj_set_style_border_width(sleep_btn, 0, 0);
    lv_obj_add_event_cb(sleep_btn, settings_sleep_event, LV_EVENT_CLICKED, NULL);
    lv_obj_t *sleep_lbl = lv_label_create(sleep_btn);
    lv_label_set_text(sleep_lbl, LV_SYMBOL_POWER " Sleep");
    lv_obj_set_style_text_font(sleep_lbl, &lv_font_montserrat_12, 0);
    lv_obj_center(sleep_lbl);
    
    // Back button
    lv_obj_t *back_btn = lv_btn_create(screen);
    lv_obj_set_size(back_btn, 100, 34);
    lv_obj_align(back_btn, LV_ALIGN_BOTTOM_MID, 0, -4);
    lv_obj_set_style_radius(back_btn, 10, 0);
    lv_obj_set_style_bg_color(back_btn, lv_palette_darken(LV_PALETTE_GREY, 4), 0);
    lv_obj_set_style_bg_opa(back_btn, LV_OPA_70, 0);
    lv_obj_set_style_border_color(back_btn, lv_palette_darken(LV_PALETTE_GREY, 2), 0);
    lv_obj_set_style_border_width(back_btn, 1, 0);
    lv_obj_add_event_cb(back_btn, load_main_screen, LV_EVENT_CLICKED, NULL);
    
    lv_obj_t *back_label = lv_label_create(back_btn);
    lv_label_set_text(back_label, "Back");
    lv_obj_set_style_text_color(back_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_center(back_label);
    
    return screen;
}

static void mark_user_activity() {
    last_interaction_ms = millis();
}

static void global_touch_event(lv_event_t *e) {
    mark_user_activity();
}

static void auto_lock_check_cb(lv_timer_t *timer) {
    LV_UNUSED(timer);
    update_lock_status_labels();

    if (is_locked || !lock_screen) return;

    if (millis() - last_interaction_ms >= AUTO_LOCK_TIMEOUT_MS) {
        load_lock_screen(NULL);
    }
}

static void update_lock_status_labels() {
    if (!lock_screen || !lock_date_label || !lock_time_label || !lock_wifi_label || !lock_battery_label) return;

    time_t now = time(nullptr);
    struct tm time_info;
    if (now > 1704067200 && localtime_r(&now, &time_info)) {
        char date_buffer[24];
        strftime(date_buffer, sizeof(date_buffer), "%a %d %b", &time_info);
        lv_label_set_text_fmt(lock_date_label, "%s", date_buffer);

        char time_buffer[12];
        strftime(time_buffer, sizeof(time_buffer), "%H:%M", &time_info);
        lv_label_set_text_fmt(lock_time_label, "%s", time_buffer);
    } else {
        lv_label_set_text(lock_date_label, "-- -- ---");
        lv_label_set_text(lock_time_label, "--:--");
    }

    const bool wifi_connected = (WiFi.status() == WL_CONNECTED);
    lv_label_set_text_fmt(lock_wifi_label, "WiFi: %s", wifi_connected ? "Connected" : "Offline");

#ifdef USING_PMU_MANAGE
    lv_label_set_text_fmt(lock_battery_label, "Battery: %d%%", instance.pmu.getBatteryPercent());
#else
    lv_label_set_text(lock_battery_label, "Battery: --");
#endif

    refresh_lock_water_notification();
    refresh_lock_calendar_notification();
    refresh_lock_pending_tasks();
}

static void unlock_event(lv_event_t *e) {
    LV_UNUSED(e);

    if (is_locked && BATTERY_SAVER) {
        const uint32_t now_ms = millis();
        const bool lock_screen_is_off = lock_alert_visible_until_ms <= now_ms;
        if (lock_screen_is_off) {
            mark_user_activity();
            lock_alert_visible_until_ms = now_ms + LOCK_TOUCH_PREVIEW_MS;
            update_lock_status_labels();
            update_lock_brightness();
            return;
        }
    }

    is_locked = false;
    mark_user_activity();
    lock_alert_visible_until_ms = 0;
    if (BATTERY_SAVER) {
        instance.setBrightness(ACTIVE_BRIGHTNESS_LEVEL);
    }
    if (main_screen) {
        lv_scr_load(main_screen);
    }
}

static lv_obj_t *create_lock_screen() {
    lv_obj_t *screen = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(screen, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(screen, 0, 0);

    lock_date_label = lv_label_create(screen);
    lv_label_set_text(lock_date_label, "-- -- ---");
    lv_obj_set_style_text_font(lock_date_label, &lv_font_montserrat_16, 0);
    lv_obj_set_style_text_color(lock_date_label, lv_palette_lighten(LV_PALETTE_GREY, 2), 0);
    lv_obj_align(lock_date_label, LV_ALIGN_TOP_MID, 0, 10);

    lock_time_label = lv_label_create(screen);
    lv_label_set_text(lock_time_label, "--:--");
    lv_obj_set_style_text_font(lock_time_label, &lv_font_montserrat_36, 0);
    lv_obj_set_style_text_color(lock_time_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_align(lock_time_label, LV_ALIGN_CENTER, 0, -8);

    lock_wifi_label = lv_label_create(screen);
    lv_label_set_text(lock_wifi_label, "WiFi: --");
    lv_obj_set_style_text_font(lock_wifi_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(lock_wifi_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_align(lock_wifi_label, LV_ALIGN_CENTER, -52, 30);

    lock_battery_label = lv_label_create(screen);
    lv_label_set_text(lock_battery_label, "Battery: --");
    lv_obj_set_style_text_font(lock_battery_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(lock_battery_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_align(lock_battery_label, LV_ALIGN_CENTER, 52, 30);

    lock_water_label = lv_label_create(screen);
    lv_label_set_text(lock_water_label, "Water: -- ml");
    lv_obj_set_style_text_font(lock_water_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(lock_water_label, lv_palette_lighten(LV_PALETTE_CYAN, 2), 0);
    lv_obj_align(lock_water_label, LV_ALIGN_CENTER, 0, 52);

    lock_pending_tasks_label = lv_label_create(screen);
    lv_label_set_text(lock_pending_tasks_label, "");
    lv_obj_set_style_text_font(lock_pending_tasks_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(lock_pending_tasks_label, lv_palette_lighten(LV_PALETTE_RED, 1), 0);
    lv_obj_align(lock_pending_tasks_label, LV_ALIGN_BOTTOM_MID, 0, -58);

    lock_current_activity_label = lv_label_create(screen);
    lv_label_set_text(lock_current_activity_label, "Now: --");
    lv_obj_set_style_text_font(lock_current_activity_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(lock_current_activity_label, lv_palette_lighten(LV_PALETTE_GREEN, 2), 0);
    lv_label_set_long_mode(lock_current_activity_label, LV_LABEL_LONG_DOT);
    lv_obj_set_width(lock_current_activity_label, 220);
    lv_obj_align(lock_current_activity_label, LV_ALIGN_BOTTOM_MID, 0, -40);

    lock_next_activity_label = lv_label_create(screen);
    lv_label_set_text(lock_next_activity_label, "Next: --");
    lv_obj_set_style_text_font(lock_next_activity_label, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(lock_next_activity_label, lv_palette_lighten(LV_PALETTE_BLUE, 2), 0);
    lv_label_set_long_mode(lock_next_activity_label, LV_LABEL_LONG_DOT);
    lv_obj_set_width(lock_next_activity_label, 220);
    lv_obj_align(lock_next_activity_label, LV_ALIGN_BOTTOM_MID, 0, -22);

    lv_obj_t *hint = lv_label_create(screen);
    lv_label_set_text(hint, "Tap to unlock");
    lv_obj_set_style_text_font(hint, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_color(hint, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_align(hint, LV_ALIGN_BOTTOM_MID, 0, -4);

    lv_obj_add_event_cb(screen, unlock_event, LV_EVENT_CLICKED, NULL);

    update_lock_status_labels();
    return screen;
}

static lv_obj_t *create_module_screen(const char *title_text) {
    lv_obj_t *screen = lv_obj_create(NULL);
    lv_obj_set_style_pad_all(screen, 12, 0);
    lv_obj_set_style_bg_color(screen, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(screen, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(screen, 0, 0);
    lv_obj_add_event_cb(screen, global_touch_event, LV_EVENT_PRESSED, NULL);

    lv_obj_t *title = lv_label_create(screen);
    lv_label_set_text(title, title_text);
    lv_obj_set_style_text_font(title, &lv_font_montserrat_20, 0);
    lv_obj_set_style_text_color(title, lv_palette_lighten(LV_PALETTE_GREY, 2), 0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);

    lv_obj_t *subtitle = lv_label_create(screen);
    lv_label_set_text(subtitle, "Empty section · ready to build");
    lv_obj_set_style_text_color(subtitle, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_align(subtitle, LV_ALIGN_TOP_MID, 0, 34);

    lv_obj_t *back_btn = lv_btn_create(screen);
    lv_obj_set_size(back_btn, 100, 42);
    lv_obj_align(back_btn, LV_ALIGN_BOTTOM_MID, 0, -8);
    lv_obj_set_style_radius(back_btn, 12, 0);
    lv_obj_set_style_bg_color(back_btn, lv_palette_darken(LV_PALETTE_GREY, 4), 0);
    lv_obj_set_style_bg_opa(back_btn, LV_OPA_70, 0);
    lv_obj_set_style_border_color(back_btn, lv_palette_darken(LV_PALETTE_GREY, 2), 0);
    lv_obj_set_style_border_width(back_btn, 1, 0);
    lv_obj_add_event_cb(back_btn, load_main_screen, LV_EVENT_CLICKED, NULL);

    lv_obj_t *back_label = lv_label_create(back_btn);
    lv_label_set_text(back_label, "Back");
    lv_obj_set_style_text_color(back_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
    lv_obj_center(back_label);

    return screen;
}

static void module_open_event(lv_event_t *e) {
    mark_user_activity();
    const char *module_title = static_cast<const char *>(lv_event_get_user_data(e));
    lv_obj_t *module_screen = nullptr;

    lv_refr_now(NULL); // Update display before potential blocking calls
    yield();

    if (module_title && strcmp(module_title, "Tasks") == 0) {
        module_screen = create_tasks_screen();
    } else if (module_title && strcmp(module_title, "Calendar") == 0) {
        module_screen = create_calendar_screen();
    } else if (module_title && strcmp(module_title, "Water") == 0) {
        module_screen = create_water_screen();
    } else if (module_title && strcmp(module_title, "Gym") == 0) {
        module_screen = create_gym_screen();
    } else if (module_title && strcmp(module_title, "Shopping List") == 0) {
        module_screen = create_shopping_screen();
    } else if (module_title && strcmp(module_title, "Pomodoro") == 0) {
        module_screen = create_pomodoro_screen();
    } else if (module_title && strcmp(module_title, "Settings") == 0) {
        module_screen = create_settings_screen();
    } else {
        module_screen = create_module_screen(module_title ? module_title : "Module");
    }

    lv_scr_load(module_screen);
}

static bool is_persistent_screen(lv_obj_t *screen) {
    return screen == nullptr || screen == main_screen || screen == lock_screen;
}

static void delete_if_temporary_screen(lv_obj_t *screen) {
    if (!is_persistent_screen(screen)) {
        lv_obj_del_async(screen);
    }
}

static void load_main_screen(lv_event_t *e) {
    lv_obj_t *previous = lv_scr_act();
    mark_user_activity();
    if (main_screen) {
        lv_scr_load(main_screen);
        delete_if_temporary_screen(previous);
    }
}

static void load_lock_screen(lv_event_t *e) {
    LV_UNUSED(e);
    lv_obj_t *previous = lv_scr_act();
    is_locked = true;
    lock_alert_visible_until_ms = 0;
    if (BATTERY_SAVER) {
        instance.setBrightness(LOCK_SCREEN_OFF_BRIGHTNESS);
    }
    if (lock_screen) {
        refresh_lock_pending_tasks(true);
        refresh_lock_water_notification(true);
        refresh_lock_calendar_notification(true);
        update_lock_status_labels();
        lv_scr_load(lock_screen);
        delete_if_temporary_screen(previous);
    }
}

void init_ui() {
    static const MenuItem MENU_ITEMS[] = {
        {"Tasks", LV_SYMBOL_OK},
        {"Calendar", LV_SYMBOL_VIDEO},
        {"Pomodoro", LV_SYMBOL_PLAY},
        {"Gym", LV_SYMBOL_EDIT},
        {"Shopping List", LV_SYMBOL_LIST},
        {"Water", LV_SYMBOL_TINT},
        {"Calories", LV_SYMBOL_BATTERY_FULL},
        {"Trading", "$"},
        {"Settings", LV_SYMBOL_SETTINGS}
    };

    main_screen = lv_obj_create(NULL);
    lv_obj_set_style_pad_all(main_screen, 8, 0);
    lv_obj_set_style_bg_color(main_screen, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(main_screen, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(main_screen, 0, 0);
    lv_obj_add_event_cb(main_screen, global_touch_event, LV_EVENT_PRESSED, NULL);

    lv_obj_t *title = lv_label_create(main_screen);
    lv_label_set_text(title, "Main Menu");
    lv_obj_set_style_text_font(title, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_color(title, lv_palette_lighten(LV_PALETTE_GREY, 2), 0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 0);

    static lv_coord_t columns[] = {LV_GRID_FR(1), LV_GRID_FR(1), LV_GRID_FR(1), LV_GRID_TEMPLATE_LAST};
    static lv_coord_t rows[] = {LV_GRID_FR(1), LV_GRID_FR(1), LV_GRID_FR(1), LV_GRID_TEMPLATE_LAST};

    lv_obj_t *grid = lv_obj_create(main_screen);
    lv_obj_set_size(grid, lv_pct(100), 196);
    lv_obj_align(grid, LV_ALIGN_TOP_MID, 0, 28);
    lv_obj_set_layout(grid, LV_LAYOUT_GRID);
    lv_obj_set_grid_dsc_array(grid, columns, rows);
    lv_obj_set_style_pad_all(grid, 4, 0);
    lv_obj_set_style_pad_row(grid, 6, 0);
    lv_obj_set_style_pad_column(grid, 6, 0);
    lv_obj_set_style_bg_color(grid, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(grid, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(grid, 0, 0);

    for (size_t index = 0; index < (sizeof(MENU_ITEMS) / sizeof(MENU_ITEMS[0])); ++index) {
        lv_obj_t *tile = lv_btn_create(grid);
        lv_obj_set_style_radius(tile, 12, 0);
        lv_obj_set_style_bg_color(tile, lv_palette_darken(LV_PALETTE_GREY, 4), 0);
        lv_obj_set_style_bg_opa(tile, LV_OPA_70, 0);
        lv_obj_set_style_border_color(tile, lv_palette_darken(LV_PALETTE_GREY, 2), 0);
        lv_obj_set_style_border_width(tile, 1, 0);
        lv_obj_set_grid_cell(tile,
                             LV_GRID_ALIGN_STRETCH, static_cast<lv_coord_t>(index % 3), 1,
                             LV_GRID_ALIGN_STRETCH, static_cast<lv_coord_t>(index / 3), 1);
        lv_obj_add_event_cb(tile, module_open_event, LV_EVENT_CLICKED, const_cast<char *>(MENU_ITEMS[index].title));

        lv_obj_t *icon_label = lv_label_create(tile);
        lv_label_set_text(icon_label, MENU_ITEMS[index].icon);
        lv_obj_set_style_text_font(icon_label, &lv_font_montserrat_24, 0);
        lv_obj_set_style_text_color(icon_label, lv_palette_lighten(LV_PALETTE_GREY, 1), 0);
        lv_obj_center(icon_label);
    }

    lock_screen = create_lock_screen();
    lv_scr_load(lock_screen);

    if (BATTERY_SAVER) {
        instance.setBrightness(LOCK_SCREEN_OFF_BRIGHTNESS);
    }

    mark_user_activity();
    if (!auto_lock_timer) {
        auto_lock_timer = lv_timer_create(auto_lock_check_cb, 1000, NULL);
    }
}

void ui_lock() {
    if (!is_locked) {
        load_lock_screen(NULL);
    }
}

bool ui_is_locked() {
    return is_locked;
}

uint32_t ui_get_last_interaction_ms() {
    return last_interaction_ms;
}

void ui_mark_interaction() {
    mark_user_activity();
}

static void update_lock_brightness() {
    if (!BATTERY_SAVER || !is_locked) return;

    const uint32_t now_ms = millis();
    const bool show_alert = lock_alert_visible_until_ms > now_ms;
    instance.setBrightness(show_alert ? LOCK_BRIGHTNESS_LEVEL : LOCK_SCREEN_OFF_BRIGHTNESS);
}