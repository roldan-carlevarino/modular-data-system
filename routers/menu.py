from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import psycopg2
import os
from datetime import date

router: APIRouter = APIRouter(prefix="/menu", tags=["Menu"])
__all__ = ["router"]


# ---------- Pydantic Models ----------

class MenuItemCreate(BaseModel):
    name: str
    occurrence: str
    weekday: int


class MenuItemUpdate(BaseModel):
    name: Optional[str] = None
    occurrence: Optional[str] = None
    weekday: Optional[int] = None


# ---------- Helper ----------

def _get_conn():
    return psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")


WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
OCCURRENCE_ORDER = ["morning", "afternoon", "evening"]


def _order_occurrence(occurrence: str) -> int:
    """Return sort order for occurrence (meal time)"""
    try:
        return OCCURRENCE_ORDER.index(occurrence.lower())
    except ValueError:
        return 999


# ==========================================================
#                    MENU ENDPOINTS
# ==========================================================

# GET /menu/all - Get complete weekly menu
@router.get("/all")
def get_all_menu():
    """Get the complete weekly menu organized by weekday"""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, occurrence, weekday
        FROM calories_menu
        ORDER BY weekday, occurrence
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    # Organize by weekday
    menu_by_day = {i: [] for i in range(7)}
    for r in rows:
        menu_by_day[r[3]].append({
            "id": r[0],
            "name": r[1],
            "occurrence": r[2],
            "weekday": r[3]
        })
    
    # Sort each day's meals by occurrence order
    for day in menu_by_day:
        menu_by_day[day].sort(key=lambda x: _order_occurrence(x["occurrence"]))
    
    return {
        "menu": [
            {
                "weekday": i,
                "weekday_name": WEEKDAY_NAMES[i],
                "meals": menu_by_day[i]
            }
            for i in range(7)
        ]
    }


# GET /menu/today - Get today's menu
@router.get("/today")
def get_today_menu():
    """Get menu for today based on current weekday"""
    today = date.today()
    weekday = today.weekday()  # 0=Monday, 6=Sunday
    
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, occurrence, weekday
        FROM calories_menu
        WHERE weekday = %s
        ORDER BY occurrence
    """, (weekday,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    meals = [
        {"id": r[0], "name": r[1], "occurrence": r[2], "weekday": r[3]}
        for r in rows
    ]
    meals.sort(key=lambda x: _order_occurrence(x["occurrence"]))
    
    return {
        "weekday": weekday,
        "weekday_name": WEEKDAY_NAMES[weekday],
        "date": today.isoformat(),
        "meals": meals
    }


# GET /menu/weekday/{weekday} - Get menu for specific weekday
@router.get("/weekday/{weekday}")
def get_weekday_menu(weekday: int):
    """Get menu for a specific weekday (0=Monday, 6=Sunday)"""
    if weekday < 0 or weekday > 6:
        raise HTTPException(status_code=400, detail="Weekday must be 0-6")
    
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, occurrence, weekday
        FROM calories_menu
        WHERE weekday = %s
        ORDER BY occurrence
    """, (weekday,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    meals = [
        {"id": r[0], "name": r[1], "occurrence": r[2], "weekday": r[3]}
        for r in rows
    ]
    meals.sort(key=lambda x: _order_occurrence(x["occurrence"]))
    
    return {
        "weekday": weekday,
        "weekday_name": WEEKDAY_NAMES[weekday],
        "meals": meals
    }


# POST /menu - Create a new menu item
@router.post("/")
def create_menu_item(item: MenuItemCreate):
    """Create a new menu item"""
    if item.weekday < 0 or item.weekday > 6:
        raise HTTPException(status_code=400, detail="Weekday must be 0-6")
    
    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO calories_menu (name, occurrence, weekday)
            VALUES (%s, %s, %s)
            ON CONFLICT (weekday, occurrence) DO UPDATE
            SET name = EXCLUDED.name
            RETURNING id
        """, (item.name, item.occurrence, item.weekday))
        new_id = cur.fetchone()[0]
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()
    
    return {"id": new_id, "name": item.name, "occurrence": item.occurrence, "weekday": item.weekday}


# PUT /menu/{item_id} - Update a menu item
@router.put("/{item_id}")
def update_menu_item(item_id: int, item: MenuItemUpdate):
    """Update an existing menu item"""
    conn = _get_conn()
    cur = conn.cursor()
    
    # Check if item exists
    cur.execute("SELECT id FROM calories_menu WHERE id = %s", (item_id,))
    if not cur.fetchone():
        cur.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Menu item not found")
    
    # Build update query dynamically
    updates = []
    values = []
    if item.name is not None:
        updates.append("name = %s")
        values.append(item.name)
    if item.occurrence is not None:
        updates.append("occurrence = %s")
        values.append(item.occurrence)
    if item.weekday is not None:
        if item.weekday < 0 or item.weekday > 6:
            cur.close()
            conn.close()
            raise HTTPException(status_code=400, detail="Weekday must be 0-6")
        updates.append("weekday = %s")
        values.append(item.weekday)
    
    if not updates:
        cur.close()
        conn.close()
        raise HTTPException(status_code=400, detail="No fields to update")
    
    values.append(item_id)
    try:
        cur.execute(f"""
            UPDATE calories_menu
            SET {', '.join(updates)}
            WHERE id = %s
            RETURNING id, name, occurrence, weekday
        """, values)
        row = cur.fetchone()
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()
    
    return {"id": row[0], "name": row[1], "occurrence": row[2], "weekday": row[3]}


# DELETE /menu/{item_id} - Delete a menu item
@router.delete("/{item_id}")
def delete_menu_item(item_id: int):
    """Delete a menu item"""
    conn = _get_conn()
    cur = conn.cursor()
    
    cur.execute("DELETE FROM calories_menu WHERE id = %s RETURNING id", (item_id,))
    deleted = cur.fetchone()
    conn.commit()
    cur.close()
    conn.close()
    
    if not deleted:
        raise HTTPException(status_code=404, detail="Menu item not found")
    
    return {"deleted": True, "id": item_id}


# ==========================================================
#                    MEAL TRACKING
# ==========================================================

# GET /menu/tracking/today - Get today's meal completion status
@router.get("/tracking/today")
def get_today_tracking():
    """Get meal completion status for today"""
    today = date.today()
    weekday = today.weekday()
    
    conn = _get_conn()
    cur = conn.cursor()
    
    # Get today's menu
    cur.execute("""
        SELECT id, name, occurrence
        FROM calories_menu
        WHERE weekday = %s
        ORDER BY occurrence
    """, (weekday,))
    menu_rows = cur.fetchall()
    
    # Get completion status
    cur.execute("""
        SELECT occurrence, completed
        FROM calories_mealtrack
        WHERE date = %s
    """, (today,))
    tracking_rows = cur.fetchall()
    tracking_map = {r[0]: r[1] for r in tracking_rows}
    
    cur.close()
    conn.close()
    
    meals = []
    for r in menu_rows:
        meals.append({
            "id": r[0],
            "name": r[1],
            "occurrence": r[2],
            "completed": tracking_map.get(r[2], False)
        })
    meals.sort(key=lambda x: _order_occurrence(x["occurrence"]))
    
    completed_count = sum(1 for m in meals if m["completed"])
    
    return {
        "date": today.isoformat(),
        "meals": meals,
        "completed": completed_count,
        "total": len(meals)
    }


# POST /menu/tracking/toggle - Toggle meal completion
@router.post("/tracking/toggle")
def toggle_meal(occurrence: str):
    """Toggle completion status for a meal occurrence (morning/afternoon/evening)"""
    if occurrence not in OCCURRENCE_ORDER:
        raise HTTPException(status_code=400, detail="Invalid occurrence. Use: morning, afternoon, evening")
    
    today = date.today()
    
    conn = _get_conn()
    cur = conn.cursor()
    
    try:
        # Check current status
        cur.execute("""
            SELECT completed FROM calories_mealtrack
            WHERE date = %s AND occurrence = %s
        """, (today, occurrence))
        row = cur.fetchone()
        
        if row is None:
            # Insert new record as true (first toggle = completed)
            cur.execute("""
                INSERT INTO calories_mealtrack (date, occurrence, completed)
                VALUES (%s, %s, true)
                RETURNING completed
            """, (today, occurrence))
        else:
            # Toggle existing
            cur.execute("""
                UPDATE calories_mealtrack
                SET completed = NOT completed
                WHERE date = %s AND occurrence = %s
                RETURNING completed
            """, (today, occurrence))
        
        new_status = cur.fetchone()[0]
        conn.commit()
        
        return {
            "date": today.isoformat(),
            "occurrence": occurrence,
            "completed": new_status
        }
        
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cur.close()
        conn.close()
