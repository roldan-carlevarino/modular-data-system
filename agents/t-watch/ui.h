#pragma once

#include <Arduino.h>

void init_ui();
void ui_lock();
bool ui_is_locked();
uint32_t ui_get_last_interaction_ms();
void ui_mark_interaction();