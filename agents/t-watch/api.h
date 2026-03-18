#pragma once

#include <Arduino.h>

// Pomodoro API
String get_pomodoro_current_json();
bool start_pomodoro_session();
bool change_pomodoro_state();
bool end_pomodoro_session();

String get_current_occurrence_tasks_json();
String get_today_calendar_json();
String get_today_water_json();
bool add_water_intake(int water_increase, const String &water_event = "watch_quick_add");
bool set_task_occurrence_completed(int occurrences_id, bool completed);
String get_last_api_error();
void set_api_base_url(const String &url);

// Gym API
String get_today_gym_session_json(int routine_id = 0);
bool add_gym_exercise(int routine_exercise_id);
String get_exercise_sets_json(int exercise_log_id);
bool save_gym_set(int exercise_log_id, int set_number, float weight, int reps, int rir = -1);

// Shopping List API
String get_shopping_list_json();
bool delete_shopping_item(const String &item);