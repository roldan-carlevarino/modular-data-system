"""Personal-data insights: real-time aggregate summaries of the user's own
metrics (gym, weight, water, schedule) over a time window.

These endpoints do the heavy lifting (counting, summing, comparing) in SQL so
the chat's small local LLM never has to. The Ask worker classifies a question's
intent, calls the matching summary here, and feeds the resulting text back to
the model to phrase an answer grounded in real numbers.
"""

import os
import re
from datetime import timedelta

import psycopg2
from fastapi import APIRouter, HTTPException, Query

from routers.tz import local_now, local_today

router: APIRouter = APIRouter(prefix="/insights", tags=["Insights"])
__all__ = ["router"]

VALID_DOMAINS = {"gym", "weight", "water", "schedule", "focus", "math", "mental",
                 "menu", "careers", "rss"}
DEFAULT_PERIOD_DAYS = 30
MAX_PERIOD_DAYS = 365

# Rule-based router: maps free text to a domain without an LLM. Order matters —
# the first matching pattern wins. Lets tiny clients (e.g. the T-Watch) hit
# /insights/ask?q=... with a raw phrase and get an answer, no Mac/LLM involved.
_DOMAIN_PATTERNS = [
    ("gym", r"entren|gimnas|\bgym\b|pesas|rutina|ejercic|muscula"),
    ("water", r"\bagua\b|beber|hidrat|\bml\b|vasos?"),
    ("weight", r"\bpeso\b|kilos?|\bkg\b|b[aá]scula|adelgaz|engord"),
    ("schedule", r"tarea|pendient|agenda|calendario|evento|cita|reuni|to.?do"),
    ("focus", r"pomodoro|foco|enfoc|concentra|estudi|productiv"),
    ("math", r"\bmate|matem|c[aá]lculo|n[uú]meros|aritm|c[aá]lcul"),
    ("mental", r"bienestar|[aá]nimo|sue[nñ]o|dormi|estr[eé]s|humor|descans"),
    ("menu", r"men[uú]|comer|comida|cena|desayun|almuerz|\bplato\b|dieta|receta"),
    ("careers", r"carrera|solicitud|aplicaci|empleo|\btrabajo\b|beca|entrevista|oferta|puesto|vacante|deadline|internship|\bphd\b"),
    ("rss", r"noticia|\brss\b|art[ií]culo|\bpaper|feed|novedad|actualidad|prensa|\bleer\b"),
]

# Temporal expressions -> period in days. First match wins; default 30.
_PERIOD_PATTERNS = [
    (1, r"\bhoy\b|\bdia\b|\bd[ií]a\b"),
    (7, r"semana"),
    (30, r"\bmes\b|mensual"),
    (365, r"\ba[ñn]o\b|anual"),
]


def _route(q):
    """Map a free-text question to (domain|None, period_days) using keywords."""
    t = (q or "").lower()
    domain = None
    for name, pattern in _DOMAIN_PATTERNS:
        if re.search(pattern, t):
            domain = name
            break
    period = DEFAULT_PERIOD_DAYS
    for days, pattern in _PERIOD_PATTERNS:
        if re.search(pattern, t):
            period = days
            break
    return domain, period


def _get_conn():
    return psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")


def _fmt_num(n, decimals=0):
    """Format a number without trailing noise (48500.0 -> '48500')."""
    if n is None:
        return "0"
    if decimals == 0:
        return f"{round(float(n)):,}".replace(",", ".")
    return f"{float(n):.{decimals}f}"


# --------------------------------------------------------------------------- #
# Gym                                                                         #
# --------------------------------------------------------------------------- #

def _gym_summary(cur, since, period_days):
    cur.execute(
        "SELECT COUNT(*), MAX(date) FROM gym_log_session WHERE date >= %s",
        (since,),
    )
    n_sessions, last_date = cur.fetchone()
    n_sessions = n_sessions or 0

    cur.execute(
        """
        SELECT COUNT(*), COALESCE(SUM(ls.weight * ls.reps), 0)
        FROM gym_log_set ls
        JOIN gym_log_exercise le ON le.id = ls.exercise_log_id
        JOIN gym_log_session s   ON s.id = le.log_session_id
        WHERE s.date >= %s AND ls.weight IS NOT NULL AND ls.reps IS NOT NULL
        """,
        (since,),
    )
    n_sets, volume = cur.fetchone()

    cur.execute(
        """
        SELECT re.exercise, COUNT(*) AS sets
        FROM gym_log_set ls
        JOIN gym_log_exercise le      ON le.id = ls.exercise_log_id
        JOIN gym_routine_exercise re  ON re.id = le.routine_exercise_id
        JOIN gym_log_session s        ON s.id = le.log_session_id
        WHERE s.date >= %s
        GROUP BY re.exercise
        ORDER BY sets DESC
        LIMIT 5
        """,
        (since,),
    )
    top = cur.fetchall()

    data = {
        "sessions": n_sessions,
        "sets": n_sets or 0,
        "volume": float(volume or 0),
        "last_session": last_date.isoformat() if last_date else None,
        "top_exercises": [{"exercise": r[0], "sets": r[1]} for r in top],
    }

    if n_sessions == 0:
        return "No hay entrenamientos registrados en el periodo.", data

    per_week = n_sessions / (period_days / 7.0) if period_days else n_sessions
    days_since = (local_today() - last_date).days if last_date else None
    parts = [
        f"En los últimos {period_days} días has entrenado {n_sessions} "
        f"{'vez' if n_sessions == 1 else 'veces'} "
        f"({_fmt_num(per_week, 1)}/semana)."
    ]
    if last_date:
        if days_since == 0:
            parts.append("Última sesión: hoy.")
        elif days_since == 1:
            parts.append("Última sesión: ayer.")
        else:
            parts.append(f"Última sesión: hace {days_since} días ({last_date.isoformat()}).")
    parts.append(
        f"Volumen total: {_fmt_num(data['volume'])} (peso·reps) en {data['sets']} series."
    )
    if top:
        ex = ", ".join(f"{r[0]} ({r[1]})" for r in top)
        parts.append(f"Ejercicios más frecuentes (series): {ex}.")
    return " ".join(parts), data


# --------------------------------------------------------------------------- #
# Weight                                                                      #
# --------------------------------------------------------------------------- #

def _weight_summary(cur, since, period_days):
    cur.execute("SELECT weight, date FROM weight_log ORDER BY date DESC LIMIT 1")
    latest = cur.fetchone()

    cur.execute(
        "SELECT MIN(weight), MAX(weight), AVG(weight), COUNT(*) "
        "FROM weight_log WHERE date >= %s",
        (since,),
    )
    w_min, w_max, w_avg, n = cur.fetchone()

    cur.execute(
        "SELECT weight, date FROM weight_log WHERE date >= %s ORDER BY date ASC LIMIT 1",
        (since,),
    )
    earliest = cur.fetchone()

    data = {
        "current": int(latest[0]) if latest else None,
        "current_date": latest[1].isoformat() if latest else None,
        "min": int(w_min) if w_min is not None else None,
        "max": int(w_max) if w_max is not None else None,
        "avg": round(float(w_avg), 1) if w_avg is not None else None,
        "measurements": n or 0,
        "start": int(earliest[0]) if earliest else None,
    }

    if not latest:
        return "No hay registros de peso.", data

    parts = [f"Peso actual: {data['current']} kg (medido {data['current_date']})."]
    if earliest and earliest[0] is not None:
        delta = data["current"] - int(earliest[0])
        sign = "+" if delta > 0 else ""
        parts.append(
            f"Hace {period_days} días: {int(earliest[0])} kg "
            f"({sign}{delta} kg en el periodo)."
        )
    if w_min is not None and n:
        parts.append(
            f"Rango: {int(w_min)}–{int(w_max)} kg, media {_fmt_num(w_avg, 1)} kg, "
            f"{n} {'medición' if n == 1 else 'mediciones'}."
        )
    return " ".join(parts), data


# --------------------------------------------------------------------------- #
# Water                                                                       #
# --------------------------------------------------------------------------- #

def _water_summary(cur, since, period_days):
    today = local_today()
    cur.execute(
        "SELECT AVG(water), COUNT(*), MAX(water) "
        "FROM water_day WHERE date >= %s AND water > 0",
        (since,),
    )
    avg, days, mx = cur.fetchone()

    cur.execute("SELECT COALESCE(water, 0) FROM water_day WHERE date = %s", (today,))
    row = cur.fetchone()
    today_val = int(row[0]) if row else 0

    data = {
        "avg_per_day": round(float(avg), 0) if avg is not None else 0,
        "days_logged": days or 0,
        "max": int(mx) if mx is not None else 0,
        "today": today_val,
    }

    if not days:
        return f"No hay registros de agua en el periodo. Hoy: {today_val}.", data

    parts = [
        f"Agua: media de {_fmt_num(avg)} por día en los últimos {period_days} días "
        f"({days} {'día' if days == 1 else 'días'} registrados)."
    ]
    parts.append(f"Hoy llevas {today_val}. Máximo del periodo: {int(mx)}.")
    return " ".join(parts), data


# --------------------------------------------------------------------------- #
# Schedule (tasks + calendar)                                                 #
# --------------------------------------------------------------------------- #

def _schedule_summary(cur, since, period_days):
    today = local_today()

    # Today's task occurrences: done vs pending.
    cur.execute(
        "SELECT COALESCE(SUM(CASE WHEN completed THEN 1 ELSE 0 END), 0), COUNT(*) "
        "FROM task_occurrences WHERE date = %s",
        (today,),
    )
    today_done, today_total = cur.fetchone()

    # Completion over the period.
    cur.execute(
        "SELECT COALESCE(SUM(CASE WHEN completed THEN 1 ELSE 0 END), 0), COUNT(*) "
        "FROM task_occurrences WHERE date >= %s AND date <= %s",
        (since, today),
    )
    period_done, period_total = cur.fetchone()

    # Overdue: incomplete occurrences dated before today.
    cur.execute(
        "SELECT COUNT(*) FROM task_occurrences WHERE completed = FALSE AND date < %s",
        (today,),
    )
    overdue = cur.fetchone()[0] or 0

    # Names of today's pending tasks (grouped by occurrence, in order).
    cur.execute(
        """
        SELECT t.name, o.occurrence
        FROM task_occurrences o
        JOIN task t ON t.id = o.task_id
        WHERE o.date = %s AND o.completed = FALSE
        ORDER BY o.position
        """,
        (today,),
    )
    pending_today = cur.fetchall()

    # Names of overdue (incomplete, past) tasks, most recent first.
    cur.execute(
        """
        SELECT t.name, o.date
        FROM task_occurrences o
        JOIN task t ON t.id = o.task_id
        WHERE o.completed = FALSE AND o.date < %s
        ORDER BY o.date DESC, o.position
        LIMIT 10
        """,
        (today,),
    )
    overdue_tasks = cur.fetchall()

    # Upcoming calendar events within the window (bounded to a sensible horizon).
    horizon_days = min(period_days, 30)
    win_start = local_now().replace(tzinfo=None)
    win_end = win_start + timedelta(days=horizon_days)
    cur.execute(
        """
        SELECT
            ci.title,
            COALESCE(
                ci.start_time,
                cs.start_time + make_interval(mins => COALESCE(ci.start_minute, 0))
            ) AS ev_start
        FROM calendar_item ci
        LEFT JOIN calendar_slot cs ON cs.id = ci.calendar_slot_id
        WHERE COALESCE(
                ci.start_time,
                cs.start_time + make_interval(mins => COALESCE(ci.start_minute, 0))
              ) >= %s
          AND COALESCE(
                ci.start_time,
                cs.start_time + make_interval(mins => COALESCE(ci.start_minute, 0))
              ) < %s
        ORDER BY ev_start ASC
        """,
        (win_start, win_end),
    )
    events = cur.fetchall()

    rate = round(100.0 * period_done / period_total) if period_total else None
    data = {
        "today_done": int(today_done),
        "today_total": int(today_total),
        "today_pending": int(today_total) - int(today_done),
        "period_done": int(period_done),
        "period_total": int(period_total),
        "completion_rate": rate,
        "overdue": int(overdue),
        "pending_today": [
            {"title": (t[0] or "(sin título)"), "occurrence": t[1]} for t in pending_today
        ],
        "overdue_tasks": [
            {"title": (t[0] or "(sin título)"), "date": t[1].isoformat() if t[1] else None}
            for t in overdue_tasks
        ],
        "upcoming_events": len(events),
        "horizon_days": horizon_days,
        "next_events": [
            {"title": (e[0] or "(sin título)"), "start": e[1].isoformat() if e[1] else None}
            for e in events[:5]
        ],
    }

    parts = []
    if today_total:
        parts.append(
            f"Hoy: {today_done} de {today_total} tareas hechas "
            f"({data['today_pending']} pendientes)."
        )
        if pending_today:
            names = "; ".join(t[0] or "(sin título)" for t in pending_today)
            parts.append(f"Pendientes hoy: {names}.")
    else:
        parts.append("Hoy no tienes tareas programadas.")
    if period_total:
        parts.append(
            f"En los últimos {period_days} días has completado {period_done} de "
            f"{period_total} tareas ({rate}%)."
        )
    if overdue:
        parts.append(
            f"Tienes {overdue} {'tarea atrasada' if overdue == 1 else 'tareas atrasadas'} "
            "sin completar de días anteriores."
        )
        if overdue_tasks:
            names = "; ".join(
                f"{t[0] or '(sin título)'} ({t[1].strftime('%d/%m') if t[1] else '?'})"
                for t in overdue_tasks
            )
            parts.append(f"Atrasadas: {names}.")
    if events:
        nxt = "; ".join(
            f"{(e[0] or '(sin título)')} ({e[1].strftime('%d/%m %H:%M') if e[1] else '?'})"
            for e in events[:5]
        )
        parts.append(
            f"Próximos {len(events)} eventos ({horizon_days} días): {nxt}."
        )
    else:
        parts.append(f"No hay eventos en los próximos {horizon_days} días.")
    return " ".join(parts), data


# --------------------------------------------------------------------------- #
# Focus / pomodoro                                                            #
# --------------------------------------------------------------------------- #

def _focus_summary(cur, since, period_days):
    cur.execute(
        """
        SELECT
            COUNT(*),
            COALESCE(SUM(EXTRACT(EPOCH FROM (end_time - start_time)) / 60.0), 0),
            MAX(start_time)
        FROM pomodoro_log
        WHERE status = 'ended' AND start_time >= %s
        """,
        (since,),
    )
    n_sessions, total_min, last_start = cur.fetchone()
    n_sessions = n_sessions or 0

    cur.execute(
        """
        SELECT COUNT(*)
        FROM pomodoro_event
        WHERE type = 'study' AND started >= %s
        """,
        (since,),
    )
    study_blocks = cur.fetchone()[0] or 0

    total_min = float(total_min or 0)
    data = {
        "sessions": int(n_sessions),
        "total_minutes": round(total_min),
        "study_blocks": int(study_blocks),
        "last_session": last_start.isoformat() if last_start else None,
    }

    if n_sessions == 0:
        return "No hay sesiones de pomodoro registradas en el periodo.", data

    per_week = n_sessions / (period_days / 7.0) if period_days else n_sessions
    hours = total_min / 60.0
    parts = [
        f"En los últimos {period_days} días has hecho {n_sessions} "
        f"{'sesión' if n_sessions == 1 else 'sesiones'} de pomodoro "
        f"({_fmt_num(per_week, 1)}/semana)."
    ]
    parts.append(
        f"Tiempo total enfocado: {_fmt_num(hours, 1)} h ({round(total_min)} min) "
        f"en {study_blocks} bloques de estudio."
    )
    if last_start:
        days_since = (local_today() - last_start.date()).days
        if days_since == 0:
            parts.append("Última sesión: hoy.")
        elif days_since == 1:
            parts.append("Última sesión: ayer.")
        else:
            parts.append(f"Última sesión: hace {days_since} días.")
    return " ".join(parts), data


# --------------------------------------------------------------------------- #
# Math trainer                                                                #
# --------------------------------------------------------------------------- #

def _math_summary(cur, since, period_days):
    cur.execute(
        """
        SELECT
            COUNT(*),
            COALESCE(SUM(correct), 0),
            COALESCE(SUM(wrong), 0),
            COALESCE(SUM(duration_s), 0),
            AVG(NULLIF(score_per_min, 0)),
            AVG(avg_latency_ms),
            MAX(started_at)
        FROM math_session
        WHERE started_at >= %s
        """,
        (since,),
    )
    n_sessions, correct, wrong, dur_s, avg_spm, avg_lat, last_at = cur.fetchone()
    n_sessions = n_sessions or 0
    correct = int(correct or 0)
    wrong = int(wrong or 0)
    total = correct + wrong
    accuracy = round(100.0 * correct / total) if total else None

    data = {
        "sessions": int(n_sessions),
        "correct": correct,
        "wrong": wrong,
        "accuracy": accuracy,
        "practice_minutes": round(float(dur_s or 0) / 60.0),
        "avg_score_per_min": round(float(avg_spm), 1) if avg_spm is not None else None,
        "avg_latency_ms": int(avg_lat) if avg_lat is not None else None,
        "last_session": last_at.isoformat() if last_at else None,
    }

    if n_sessions == 0:
        return "No hay sesiones de entrenamiento mental (mates) en el periodo.", data

    per_week = n_sessions / (period_days / 7.0) if period_days else n_sessions
    parts = [
        f"En los últimos {period_days} días has hecho {n_sessions} "
        f"{'sesión' if n_sessions == 1 else 'sesiones'} de mates "
        f"({_fmt_num(per_week, 1)}/semana, {data['practice_minutes']} min en total)."
    ]
    if total:
        parts.append(
            f"Aciertos: {correct} de {total} ({accuracy}% de acierto)."
        )
    if avg_spm is not None:
        parts.append(f"Ritmo medio: {_fmt_num(avg_spm, 1)} puntos/min.")
    if avg_lat is not None:
        parts.append(f"Latencia media por respuesta: {int(avg_lat)} ms.")
    return " ".join(parts), data


# --------------------------------------------------------------------------- #
# Mental / wellbeing                                                          #
# --------------------------------------------------------------------------- #

def _mental_summary(cur, since, period_days):
    cur.execute(
        """
        SELECT
            AVG(sleep_hours), MIN(sleep_hours), MAX(sleep_hours),
            AVG(stress), COUNT(*)
        FROM mental_log
        WHERE date >= %s
        """,
        (since,),
    )
    avg_sleep, min_sleep, max_sleep, avg_stress, n = cur.fetchone()
    n = n or 0

    cur.execute(
        "SELECT date, sleep_hours, stress FROM mental_log ORDER BY date DESC LIMIT 1"
    )
    latest = cur.fetchone()

    data = {
        "entries": int(n),
        "avg_sleep": round(float(avg_sleep), 1) if avg_sleep is not None else None,
        "min_sleep": float(min_sleep) if min_sleep is not None else None,
        "max_sleep": float(max_sleep) if max_sleep is not None else None,
        "avg_stress": round(float(avg_stress), 1) if avg_stress is not None else None,
        "latest_date": latest[0].isoformat() if latest else None,
        "latest_sleep": float(latest[1]) if latest and latest[1] is not None else None,
        "latest_stress": int(latest[2]) if latest and latest[2] is not None else None,
    }

    if n == 0:
        return "No hay registros de bienestar (sueño/estrés) en el periodo.", data

    parts = [f"Bienestar en los últimos {period_days} días ({n} {'registro' if n == 1 else 'registros'})."]
    if avg_sleep is not None:
        parts.append(
            f"Sueño medio: {_fmt_num(avg_sleep, 1)} h "
            f"(rango {_fmt_num(min_sleep, 1)}–{_fmt_num(max_sleep, 1)} h)."
        )
    if avg_stress is not None:
        parts.append(f"Estrés medio: {_fmt_num(avg_stress, 1)}/5.")
    if latest:
        extras = []
        if latest[1] is not None:
            extras.append(f"{_fmt_num(latest[1], 1)} h de sueño")
        if latest[2] is not None:
            extras.append(f"estrés {int(latest[2])}/5")
        if extras:
            parts.append(f"Último registro ({latest[0].isoformat()}): {', '.join(extras)}.")
    return " ".join(parts), data


# --------------------------------------------------------------------------- #
# Menu / meals                                                                #
# --------------------------------------------------------------------------- #

_OCC_ORDER = "CASE occurrence WHEN 'morning' THEN 0 WHEN 'afternoon' THEN 1 WHEN 'evening' THEN 2 ELSE 3 END"
_OCC_LABEL = {"morning": "mañana", "afternoon": "mediodía", "evening": "noche"}
_WEEKDAY_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _menu_summary(cur, since, period_days):
    today = local_today()
    weekday = today.weekday()

    cur.execute(
        f"""
        SELECT name, occurrence
        FROM calories_menu
        WHERE weekday = %s
        ORDER BY {_OCC_ORDER}
        """,
        (weekday,),
    )
    meals = cur.fetchall()

    cur.execute(
        "SELECT occurrence, completed FROM calories_mealtrack WHERE date = %s",
        (today,),
    )
    done = {occ: bool(c) for occ, c in cur.fetchall()}

    meal_list = [
        {
            "occurrence": occ,
            "label": _OCC_LABEL.get(occ, occ),
            "name": name,
            "completed": done.get(occ, False),
        }
        for name, occ in meals
    ]
    completed_count = sum(1 for m in meal_list if m["completed"])
    data = {
        "weekday": _WEEKDAY_ES[weekday],
        "meals": meal_list,
        "completed": completed_count,
        "total": len(meal_list),
    }

    if not meal_list:
        return f"No hay menú definido para hoy ({_WEEKDAY_ES[weekday]}).", data

    items = ", ".join(f"{m['label']}: {m['name']}" for m in meal_list)
    text = f"Menú de hoy ({_WEEKDAY_ES[weekday]}) — {items}."
    text += f" Llevas {completed_count}/{len(meal_list)} comidas registradas."
    return text, data


# --------------------------------------------------------------------------- #
# Careers / job pipeline                                                       #
# --------------------------------------------------------------------------- #

_CAREERS_ACTIVE = ("saved", "applied", "oa", "phone", "onsite", "offer")


def _careers_summary(cur, since, period_days):
    cur.execute(
        """
        SELECT status, COUNT(*)
        FROM career_application
        WHERE status = ANY(%s)
        GROUP BY status
        """,
        (list(_CAREERS_ACTIVE),),
    )
    by_status = {s: n for s, n in cur.fetchall()}
    active_total = sum(by_status.values())

    cur.execute(
        "SELECT COUNT(*) FROM career_application WHERE applied_at >= %s",
        (since,),
    )
    applied_period = cur.fetchone()[0] or 0

    cur.execute(
        """
        SELECT COUNT(*) FILTER (WHERE status = 'offer'),
               COUNT(*) FILTER (WHERE status = 'accepted')
        FROM career_application
        """
    )
    offers, accepted = cur.fetchone()

    horizon = min(max(period_days, 30), 90)
    cur.execute(
        """
        SELECT company, role, deadline
        FROM career_application
        WHERE status = ANY(%s)
          AND deadline IS NOT NULL
          AND deadline >= %s
          AND deadline <= %s
        ORDER BY deadline ASC
        LIMIT 10
        """,
        (list(_CAREERS_ACTIVE), local_today(), local_today() + timedelta(days=horizon)),
    )
    deadline_rows = cur.fetchall()
    deadlines = [
        {"company": c, "role": r, "deadline": d.isoformat(), "_dm": d.strftime("%d/%m")}
        for c, r, d in deadline_rows
    ]

    data = {
        "active_total": int(active_total),
        "by_status": {s: int(n) for s, n in by_status.items()},
        "applied_period": int(applied_period),
        "offers": int(offers or 0),
        "accepted": int(accepted or 0),
        "upcoming_deadlines": [
            {"company": d["company"], "role": d["role"], "deadline": d["deadline"]}
            for d in deadlines
        ],
        "horizon_days": horizon,
    }

    if active_total == 0 and applied_period == 0:
        return "No hay solicitudes activas en el pipeline de carrera.", data

    parts = [f"Tienes {active_total} solicitud{'es' if active_total != 1 else ''} activa{'s' if active_total != 1 else ''} en el pipeline."]
    if by_status:
        breakdown = ", ".join(f"{s}: {n}" for s, n in sorted(by_status.items(), key=lambda x: -x[1]))
        parts.append(f"Por estado — {breakdown}.")
    if offers or accepted:
        oa_bits = []
        if offers:
            oa_bits.append(f"{offers} oferta{'s' if offers != 1 else ''}")
        if accepted:
            oa_bits.append(f"{accepted} aceptada{'s' if accepted != 1 else ''}")
        parts.append("Tienes " + " y ".join(oa_bits) + ".")
    parts.append(f"En los últimos {period_days} días has aplicado a {applied_period}.")
    if deadlines:
        dl = "; ".join(f"{d['company']} ({d['_dm']})" for d in deadlines[:5])
        parts.append(f"Próximos deadlines: {dl}.")
    else:
        parts.append(f"Sin deadlines en los próximos {horizon} días.")
    return " ".join(parts), data


# --------------------------------------------------------------------------- #
# RSS / articles to read                                                       #
# --------------------------------------------------------------------------- #

def _rss_summary(cur, since, period_days):
    cur.execute(
        "SELECT COUNT(*) FROM rss_articles WHERE created_at >= %s",
        (since,),
    )
    new_count = cur.fetchone()[0] or 0

    cur.execute(
        """
        SELECT top_category, COUNT(*)
        FROM rss_articles
        WHERE created_at >= %s AND top_category IS NOT NULL
        GROUP BY top_category
        ORDER BY COUNT(*) DESC
        """,
        (since,),
    )
    by_category = {c: int(n) for c, n in cur.fetchall()}

    cur.execute(
        """
        SELECT title, top_category
        FROM rss_articles
        WHERE global_rank IS NOT NULL AND created_at >= %s
        ORDER BY global_rank ASC
        LIMIT 6
        """,
        (since,),
    )
    top = [{"title": t, "category": c} for t, c in cur.fetchall()]

    data = {
        "new_count": int(new_count),
        "by_category": by_category,
        "top_articles": top,
    }

    if new_count == 0:
        return f"No hay artículos nuevos en los últimos {period_days} días.", data

    parts = [f"Tienes {new_count} artículo{'s' if new_count != 1 else ''} para leer de los últimos {period_days} días."]
    if by_category:
        cats = ", ".join(f"{c}: {n}" for c, n in by_category.items())
        parts.append(f"Por categoría — {cats}.")
    if top:
        titles = "; ".join(f"«{a['title']}»" + (f" ({a['category']})" if a['category'] else "") for a in top[:5])
        parts.append(f"Destacados: {titles}.")
    return " ".join(parts), data


_DISPATCH = {
    "gym": _gym_summary,
    "weight": _weight_summary,
    "water": _water_summary,
    "schedule": _schedule_summary,
    "focus": _focus_summary,
    "math": _math_summary,
    "mental": _mental_summary,
    "menu": _menu_summary,
    "careers": _careers_summary,
    "rss": _rss_summary,
}


def _build_summary(domain, period_days):
    """Run the SQL aggregate for a domain and return the response dict."""
    since = local_today() - timedelta(days=period_days)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        summary, data = _DISPATCH[domain](cur, since, period_days)
        cur.close()
        return {
            "domain": domain,
            "period_days": period_days,
            "since": since.isoformat(),
            "summary": summary,
            "data": data,
        }
    finally:
        conn.close()


@router.get("/summary")
def personal_summary(
    domain: str = Query(..., description="One of: gym, weight, water, schedule"),
    period_days: int = Query(DEFAULT_PERIOD_DAYS, ge=1, le=MAX_PERIOD_DAYS),
):
    """Return a real-time aggregate summary of a personal-data domain over the
    last `period_days`. `summary` is a ready-to-read text; `data` is structured."""
    domain = (domain or "").strip().lower()
    if domain not in VALID_DOMAINS:
        raise HTTPException(400, f"domain must be one of {sorted(VALID_DOMAINS)}")
    try:
        return _build_summary(domain, period_days)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"insights/{domain} failed: {e}")


@router.get("/ask")
def personal_ask(
    q: str = Query(..., description="Free-text question, e.g. 'cómo llevo los entrenamientos esta semana'"),
):
    """Rule-based (no-LLM) entry point for tiny clients: parse a free-text
    question into a domain + period with keywords, then return the aggregate
    summary. `matched` is False when no personal domain is recognized."""
    domain, period_days = _route(q)
    if domain is None:
        return {
            "matched": False,
            "domain": None,
            "period_days": period_days,
            "summary": "",
            "data": None,
        }
    try:
        result = _build_summary(domain, period_days)
        result["matched"] = True
        return result
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"insights/ask failed: {e}")

