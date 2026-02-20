from fastapi import APIRouter, Body, HTTPException
import psycopg2
import os

router = APIRouter(prefix="/shopping", tags=["Shopping"]) 


@router.get("/items")
def get_all_items():
    conn = None
    cur = None
    
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        cur.execute("""
            SELECT item FROM shopping_food
                    WHERE active = true
            UNION ALL
            SELECT item FROM shopping_others
                    WHERE active = true
        """)
        rows = cur.fetchall()

        return [r[0] for r in rows]
        
    except Exception as e:
        raise HTTPException(500, f"Failed to get items: {str(e)}")
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@router.get("/list")
def get_shopping_list():
    conn = None
    cur = None
    
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        cur.execute("""
            SELECT item 
            FROM shopping_list
            """)
        rows = cur.fetchall()

        return [r[0] for r in rows]
        
    except Exception as e:
        raise HTTPException(500, f"Failed to get shopping list: {str(e)}")
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@router.post("/insert_list")
def insert_shopping_list(payload = Body(...)):
    conn = None
    cur = None
    
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        if isinstance(payload, dict):
            items = payload.get("items", [])
        else:
            items = payload

        if not isinstance(items, list):
            raise HTTPException(400, "Invalid items payload")

        for item in items:
            cur.execute("""
                INSERT INTO shopping_list (item)
                SELECT %s
                WHERE NOT EXISTS (
                    SELECT 1 FROM shopping_list WHERE item = %s
                );
            """, (item, item))

        conn.commit()
        return {"ok": True}
        
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to insert items: {str(e)}")
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@router.post("/delete_list")
def delete_shopping_list(payload = Body(...)):
    conn = None
    cur = None
    
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()

        if isinstance(payload, dict):
            items = payload.get("items", [])
        else:
            items = payload

        if not isinstance(items, list):
            raise HTTPException(400, "Invalid items payload")

        for item in items:
            cur.execute("""
                DELETE FROM shopping_list
                WHERE item = %s
            """, (item,))

        conn.commit()
        return {"ok": True}
        
    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(500, f"Failed to delete items: {str(e)}")
        
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()