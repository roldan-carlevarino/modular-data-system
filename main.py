import psycopg2
import os
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

# Import all routers (AFTER load_dotenv so env vars are available)
from routers.auth import router as auth_router, get_current_user
from routers.rss import router as rss_router
from routers.tasks import router as tasks_router
from routers.pomodoro import router as pomodoro_router
from routers.intel import router as intel_router
from routers.logs import router as logs_router
from routers.shopping import router as shopping_router
from routers.plaza import router as plaza_router
from routers.gym import router as gym_router
from routers.projects import router as projects_router
from routers.media import router as media_router
from routers.calendar import router as calendar_router
from routers.calendar_template import router as calendar_template_router
from routers.water import router as water_router
from routers.weight import router as weight_router
from routers.menu import router as menu_router
from routers.welfare import router as welfare_router


def _run_migrations():
    """Idempotent schema migrations executed once at startup."""
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()
        # Add featured boolean to calendar_item (default false)
        cur.execute("""
            ALTER TABLE calendar_item
            ADD COLUMN IF NOT EXISTS featured BOOLEAN NOT NULL DEFAULT FALSE
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS calendar_weekly_template (
                id SERIAL PRIMARY KEY,
                day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
                start_hour INTEGER NOT NULL CHECK (start_hour BETWEEN 0 AND 23),
                start_minute INTEGER NOT NULL DEFAULT 0 CHECK (start_minute BETWEEN 0 AND 59),
                duration_minutes INTEGER NOT NULL DEFAULT 60 CHECK (duration_minutes > 0),
                title TEXT NOT NULL,
                item_kind TEXT NOT NULL DEFAULT 'note',
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[migration] warning: {e}")


_run_migrations()

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth router (public - no token required)
app.include_router(auth_router)

# All other routers require authentication
_auth = [Depends(get_current_user)]

app.include_router(rss_router, dependencies=_auth)
app.include_router(tasks_router, dependencies=_auth)
app.include_router(pomodoro_router, dependencies=_auth)
app.include_router(intel_router, dependencies=_auth)
app.include_router(logs_router, dependencies=_auth)
app.include_router(shopping_router, dependencies=_auth)
app.include_router(plaza_router, dependencies=_auth)
app.include_router(gym_router, dependencies=_auth)
app.include_router(projects_router, dependencies=_auth)
app.include_router(media_router, dependencies=_auth)
app.include_router(calendar_router, dependencies=_auth)
app.include_router(calendar_template_router, dependencies=_auth)
app.include_router(water_router, dependencies=_auth)
app.include_router(weight_router, dependencies=_auth)
app.include_router(menu_router, dependencies=_auth)
app.include_router(welfare_router, dependencies=_auth)

@app.get("/")
def root():
    return {"status": "ok", "version": "2.0"}

@app.get("/health")
def health():
    try:
        conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}