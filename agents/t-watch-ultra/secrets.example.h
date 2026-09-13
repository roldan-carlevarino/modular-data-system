#pragma once

// -----------------------------------------------------------------------------
// Template for local secrets. Copy this file to secrets.h and fill in your
// own values. secrets.h is git-ignored so your credentials stay out of the repo.
// -----------------------------------------------------------------------------

#define WIFI_SSID       "your-wifi-name"
#define WIFI_PASSWORD   "your-wifi-password"

// Backend login (JWT). Use your admin account; the demo user is read-only.
#define API_USERNAME    "your-username"
#define API_PASSWORD    "your-password"

// Optional: override the backend API base URL (leave empty to use the default
// compiled into api.cpp).
#define API_BASE_URL    ""
