"""Personal-data insights: real-time aggregate summaries of the user's own
metrics (gym, weight, water, schedule) over a time window.

These endpoints do the heavy lifting (counting, summing, comparing) in SQL so
the chat's small local LLM never has to. The Ask worker classifies a question's
intent, calls the matching summary here, and feeds the resulting text back to
the model to phrase an answer grounded in real numbers.
"""

import os
from datetime import timedelta

import psycopg2
from fastapi import APIRouter, HTTPException, Query

from routers.tz import local_now, local_today

router: APIRouter = APIRouter(prefix="/insights", tags=["Insights"])
__all__ = ["router"]

VALID_DOMAINS = {"gym", "weight", "water", "schedule"}
DEFAULT_PERIOD_DAYS = 30
MAX_PERIOD_DAYS = 365


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


_DISPATCH = {
    "gym": _gym_summary,
    "weight": _weight_summary,
    "water": _water_summary,
    "schedule": _schedule_summary,
}


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
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"insights/{domain} failed: {e}")
    finally:
        conn.close()
