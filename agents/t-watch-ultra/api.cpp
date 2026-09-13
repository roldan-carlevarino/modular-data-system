#include "api.h"
#include "secrets.h"

#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>

// Fallbacks so the firmware still compiles if secrets.h predates the auth work.
// Fill real values in secrets.h (API_USERNAME / API_PASSWORD) to authenticate.
#ifndef API_USERNAME
#define API_USERNAME ""
#endif
#ifndef API_PASSWORD
#define API_PASSWORD ""
#endif

String base_url = "https://api-dashboard-production-fc05.up.railway.app";
static String last_api_error = "";

// ---- JWT auth state ----
// The backend protects every endpoint with a bearer token (see /auth/login).
// We log in once, cache the token, attach it to every request, and re-login
// automatically if a request comes back 401 (e.g. token expired after 7 days).
static String auth_token = "";
static String api_username = API_USERNAME;
static String api_password = API_PASSWORD;

void set_api_base_url(const String &url) {
    base_url = url;
}

void set_api_credentials(const String &username, const String &password) {
    api_username = username;
    api_password = password;
}

String get_last_api_error() {
    return last_api_error;
}

static bool begin_http(HTTPClient &http, const String &url) {
    if (url.startsWith("https://")) {
        static WiFiClientSecure secure_client;
        secure_client.setInsecure();
        return http.begin(secure_client, url);
    }
    return http.begin(url);
}

// x-www-form-urlencode a single field value (login uses OAuth2 form encoding).
static String url_encode(const String &value) {
    String out;
    const char *hex = "0123456789ABCDEF";
    for (size_t i = 0; i < value.length(); ++i) {
        const char c = value[i];
        if (isalnum(static_cast<unsigned char>(c)) || c == '-' || c == '_' || c == '.' || c == '~') {
            out += c;
        } else {
            out += '%';
            out += hex[(c >> 4) & 0xF];
            out += hex[c & 0xF];
        }
    }
    return out;
}

// Log in against /auth/login and cache the returned JWT. Returns true on success.
bool api_login() {
    if (WiFi.status() != WL_CONNECTED) {
        last_api_error = "WiFi disconnected";
        return false;
    }
    if (api_username.length() == 0) {
        last_api_error = "No API credentials";
        Serial.println("[API] No credentials set; cannot login");
        return false;
    }

    yield();

    HTTPClient http;
    http.setTimeout(6000);

    const String url = base_url + "/auth/login";
    Serial.print("[API] LOGIN ");
    Serial.println(url);

    if (!begin_http(http, url)) {
        last_api_error = "HTTP begin failed";
        return false;
    }

    const String body = "username=" + url_encode(api_username) +
                        "&password=" + url_encode(api_password);

    http.addHeader("Content-Type", "application/x-www-form-urlencoded");
    const int http_code = http.POST(body);
    String payload = (http_code > 0) ? http.getString() : "";
    http.end();
    yield();

    if (http_code == 200) {
        const int key = payload.indexOf("\"access_token\":\"");
        if (key >= 0) {
            const int start = key + 16;
            const int end = payload.indexOf('"', start);
            if (end > start) {
                auth_token = payload.substring(start, end);
                last_api_error = "";
                Serial.println("[API] Login OK");
                return true;
            }
        }
        last_api_error = "Login: no token in response";
        Serial.println("[API] Login response missing token");
        return false;
    }

    last_api_error = "Login failed: " + String(http_code);
    Serial.print("[API] Login failed code: ");
    Serial.println(http_code);
    return false;
}

// Attach the bearer token (logging in first if we don't have one yet).
static void attach_auth(HTTPClient &http) {
    if (auth_token.length() == 0) {
        api_login();
    }
    if (auth_token.length() > 0) {
        http.addHeader("Authorization", "Bearer " + auth_token);
    }
}

static bool post_json(const String &path, const String &json_body) {
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("[API] WiFi is not connected");
        last_api_error = "WiFi disconnected";
        return false;
    }

    const String url = base_url + path;

    for (int attempt = 0; attempt < 2; ++attempt) {
        yield(); // Feed watchdog before blocking call

        HTTPClient http;
        http.setTimeout(4000); // Reduced timeout

        Serial.print("[API] POST ");
        Serial.println(url);

        if (!begin_http(http, url)) {
            Serial.println("[API] Failed to initialize HTTP request");
            last_api_error = "HTTP begin failed";
            return false;
        }

        http.addHeader("Content-Type", "application/json");
        attach_auth(http);
        const int http_code = http.POST(json_body);
        http.end();

        yield(); // Feed watchdog after blocking call

        if (http_code > 0 && http_code < 400) {
            last_api_error = "";
            return true;
        }

        // Token expired/invalid: re-login once and retry.
        if (http_code == 401 && attempt == 0) {
            Serial.println("[API] 401 -> re-login and retry");
            auth_token = "";
            if (!api_login()) return false;
            continue;
        }

        last_api_error = "POST failed: " + String(http_code);
        Serial.print("[API] Request failed code: ");
        Serial.println(http_code);
        return false;
    }
    return false;
}

static String get_json(const String &path) {
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("[API] WiFi is not connected");
        last_api_error = "WiFi disconnected";
        return "";
    }

    const String url = base_url + path;

    for (int attempt = 0; attempt < 2; ++attempt) {
        yield(); // Feed watchdog before blocking call

        HTTPClient http;
        http.setTimeout(4000); // Reduced timeout

        Serial.print("[API] GET ");
        Serial.println(url);

        if (!begin_http(http, url)) {
            Serial.println("[API] Failed to initialize HTTP request");
            last_api_error = "HTTP begin failed";
            return "";
        }

        attach_auth(http);
        const int http_code = http.GET();
        String payload = "";
        if (http_code > 0 && http_code < 400) {
            payload = http.getString();
            last_api_error = "";
            http.end();
            yield();
            return payload;
        }

        http.end();
        yield(); // Feed watchdog after blocking call

        // Token expired/invalid: re-login once and retry.
        if (http_code == 401 && attempt == 0) {
            Serial.println("[API] 401 -> re-login and retry");
            auth_token = "";
            if (!api_login()) return "";
            continue;
        }

        Serial.print("[API] Request failed code: ");
        Serial.println(http_code);
        last_api_error = "GET failed: " + String(http_code);
        return "";
    }
    return "";
}

static bool post_action(const String &path) {
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("[API] WiFi is not connected");
        last_api_error = "WiFi disconnected";
        return false;
    }

    const String url = base_url + path;

    for (int attempt = 0; attempt < 2; ++attempt) {
        yield(); // Feed watchdog before blocking call

        HTTPClient http;
        http.setTimeout(4000); // Reduced timeout

        Serial.print("[API] POST ");
        Serial.println(url);

        if (!begin_http(http, url)) {
            Serial.println("[API] Failed to initialize HTTP request");
            last_api_error = "HTTP begin failed";
            return false;
        }

        attach_auth(http);
        const int http_code = http.POST("");
        http.end();

        yield(); // Feed watchdog after blocking call

        if (http_code > 0 && http_code < 400) {
            Serial.print("[API] OK code: ");
            Serial.println(http_code);
            last_api_error = "";
            return true;
        }

        // Token expired/invalid: re-login once and retry.
        if (http_code == 401 && attempt == 0) {
            Serial.println("[API] 401 -> re-login and retry");
            auth_token = "";
            if (!api_login()) return false;
            continue;
        }

        Serial.print("[API] Request failed code: ");
        Serial.println(http_code);
        last_api_error = "POST failed: " + String(http_code);
        return false;
    }
    return false;
}

// ========== POMODORO API ==========

String get_pomodoro_current_json() {
    return get_json("/pomodoro/current");
}

bool start_pomodoro_session() {
    // Start with minimal payload for watch
    const String body = "{\"initial_focus\":{\"ref_type\":\"manual\",\"ref_id\":0},\"expectations\":[]}";
    return post_json("/pomodoro/start", body);
}

bool change_pomodoro_state() {
    return post_json("/pomodoro/change_state", "{}");
}

bool end_pomodoro_session() {
    return post_json("/pomodoro/end", "{}");
}

String get_current_occurrence_tasks_json() {
    return get_json("/tasks/today/current-occurrence");
}

String get_today_calendar_json() {
    return get_json("/calendar/day");
}

String get_today_water_json() {
    return get_json("/water/today");
}

bool add_water_intake(int water_increase, const String &water_event) {
    if (water_increase <= 0) {
        last_api_error = "Invalid water increase";
        return false;
    }

    const String body = String("{\"water_increase\":") + String(water_increase)
                        + String(",\"water_event\":\"") + water_event + String("\"}");
    return post_json("/water/drink", body);
}

bool set_task_occurrence_completed(int occurrences_id, bool completed) {
    const String body = String("{\"occurrences_id\":") + String(occurrences_id)
                        + String(",\"completed\":") + (completed ? "true" : "false")
                        + String("}");
    return post_json("/tasks/today/checkbox", body);
}

// ========== GYM API ==========

String get_today_gym_session_json(int routine_id) {
    String path = "/gym/sessions/today";
    if (routine_id > 0) {
        path += "?routine_id=" + String(routine_id);
    }
    return get_json(path);
}

bool add_gym_exercise(int routine_exercise_id) {
    const String body = String("{\"routine_exercise_id\":") + String(routine_exercise_id) + String("}");
    return post_json("/gym/sessions/today/exercises", body);
}

String get_exercise_sets_json(int exercise_log_id) {
    return get_json("/gym/log-exercises/" + String(exercise_log_id) + "/sets");
}

bool save_gym_set(int exercise_log_id, int set_number, float weight, int reps, int rir) {
    String body = String("{\"set_number\":") + String(set_number);
    
    if (weight > 0) {
        body += String(",\"weight\":") + String(weight, 1);
    }
    if (reps > 0) {
        body += String(",\"reps\":") + String(reps);
    }
    if (rir >= 0) {
        body += String(",\"rir\":") + String(rir);
    }
    body += "}";
    
    return post_json("/gym/log-exercises/" + String(exercise_log_id) + "/sets", body);
}

// ========== SHOPPING LIST API ==========

String get_shopping_list_json() {
    return get_json("/shopping/list");
}

bool delete_shopping_item(const String &item) {
    const String body = String("{\"items\":[\"") + item + String("\"]}");
    return post_json("/shopping/delete_list", body);
}