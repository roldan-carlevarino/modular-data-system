import psycopg2
import os
from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from dotenv import load_dotenv
from jose import JWTError, jwt

load_dotenv()

# Import all routers (AFTER load_dotenv so env vars are available)
from routers.auth import router as auth_router, get_current_user, DEMO_USERNAME, JWT_SECRET, JWT_ALGORITHM
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
from routers.math_trainer import router as math_trainer_router
from routers.library import router as library_router
from routers.careers import router as careers_router
from routers.graph import router as graph_router
from routers.insights import router as insights_router
from routers.knowledge_engine import router as knowledge_engine_router, migrate as knowledge_engine_migrate


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

        # ---- Library (mini-Zotero): papers, books, competitions ----
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lib_item (
                id SERIAL PRIMARY KEY,
                type TEXT NOT NULL CHECK (type IN ('paper', 'book', 'competition')),
                title TEXT NOT NULL,
                year INTEGER,
                status TEXT NOT NULL DEFAULT 'wishlist',
                authors JSONB NOT NULL DEFAULT '[]'::jsonb,
                external_id TEXT,
                primary_url TEXT,
                file_path TEXT,
                summary TEXT,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                start_date DATE,
                due_date DATE,
                added_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        # Add date columns idempotently for older deployments
        cur.execute("ALTER TABLE lib_item ADD COLUMN IF NOT EXISTS start_date DATE")
        cur.execute("ALTER TABLE lib_item ADD COLUMN IF NOT EXISTS due_date   DATE")
        cur.execute("""
            CREATE INDEX IF NOT EXISTS lib_item_type_idx ON lib_item(type);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS lib_item_status_idx ON lib_item(status);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS lib_item_added_idx ON lib_item(added_at DESC);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS lib_item_due_idx ON lib_item(due_date)
            WHERE due_date IS NOT NULL;
        """)
        # Full-text search index over title/summary/authors
        cur.execute("""
            CREATE INDEX IF NOT EXISTS lib_item_fts_idx ON lib_item
            USING GIN (to_tsvector('english',
                coalesce(title, '') || ' ' ||
                coalesce(summary, '') || ' ' ||
                coalesce(authors::text, '')
            ));
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lib_link (
                id SERIAL PRIMARY KEY,
                item_id INTEGER NOT NULL REFERENCES lib_item(id) ON DELETE CASCADE,
                label TEXT NOT NULL,
                url TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'main',
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS lib_link_item_idx ON lib_link(item_id, sort_order);
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lib_collection (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                parent_id INTEGER REFERENCES lib_collection(id) ON DELETE SET NULL,
                color TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("ALTER TABLE lib_collection ADD COLUMN IF NOT EXISTS project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL")
        cur.execute("CREATE INDEX IF NOT EXISTS lib_collection_project_idx ON lib_collection(project_id)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lib_item_collection (
                item_id INTEGER NOT NULL REFERENCES lib_item(id) ON DELETE CASCADE,
                collection_id INTEGER NOT NULL REFERENCES lib_collection(id) ON DELETE CASCADE,
                PRIMARY KEY (item_id, collection_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lib_note (
                id SERIAL PRIMARY KEY,
                item_id INTEGER NOT NULL REFERENCES lib_item(id) ON DELETE CASCADE,
                body_md TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS lib_note_item_idx ON lib_note(item_id, updated_at DESC);
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lib_highlight (
                id SERIAL PRIMARY KEY,
                item_id INTEGER NOT NULL REFERENCES lib_item(id) ON DELETE CASCADE,
                locator TEXT,
                quote TEXT NOT NULL,
                comment TEXT,
                color TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS lib_highlight_item_idx ON lib_highlight(item_id);
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lib_tag (
                item_id INTEGER NOT NULL REFERENCES lib_item(id) ON DELETE CASCADE,
                tag TEXT NOT NULL,
                PRIMARY KEY (item_id, tag)
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS lib_tag_tag_idx ON lib_tag(tag);
        """)

        # ---- Careers: internships, new-grad, research, grants ----
        cur.execute("""
            CREATE TABLE IF NOT EXISTS career_application (
                id SERIAL PRIMARY KEY,
                type TEXT NOT NULL DEFAULT 'internship',
                company TEXT NOT NULL,
                role TEXT NOT NULL,
                location TEXT,
                status TEXT NOT NULL DEFAULT 'saved',
                source TEXT,
                applied_at DATE,
                deadline DATE,
                start_date DATE,
                end_date DATE,
                salary TEXT,
                url TEXT,
                notes TEXT,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS career_status_idx ON career_application(status);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS career_deadline_idx ON career_application(deadline)
            WHERE deadline IS NOT NULL;
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS career_updated_idx ON career_application(updated_at DESC);
        """)

        # Career events: timeline of interactions (interview, OA, offer, note...)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS career_event (
                id SERIAL PRIMARY KEY,
                application_id INTEGER NOT NULL REFERENCES career_application(id) ON DELETE CASCADE,
                kind TEXT NOT NULL DEFAULT 'note',
                title TEXT,
                body TEXT,
                occurred_at TIMESTAMP NOT NULL DEFAULT NOW(),
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS career_event_app_idx
                ON career_event(application_id, occurred_at DESC);
        """)

        # Career contacts: recruiters, referrals, interviewers per application
        cur.execute("""
            CREATE TABLE IF NOT EXISTS career_contact (
                id SERIAL PRIMARY KEY,
                application_id INTEGER NOT NULL REFERENCES career_application(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                role TEXT,
                email TEXT,
                phone TEXT,
                linkedin TEXT,
                relationship TEXT NOT NULL DEFAULT 'recruiter',
                notes TEXT,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS career_contact_app_idx
                ON career_contact(application_id);
        """)

        # Career people: standalone CRM for research outreach (LinkedIn etc.)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS career_person (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                headline TEXT,
                company TEXT,
                location TEXT,
                linkedin TEXT,
                email TEXT,
                website TEXT,
                category TEXT NOT NULL DEFAULT 'other',
                outreach_status TEXT NOT NULL DEFAULT 'to_contact',
                tags TEXT[] NOT NULL DEFAULT '{}',
                interest INTEGER NOT NULL DEFAULT 2,
                last_contact_at DATE,
                notes TEXT,
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS career_person_category_idx
                ON career_person(category);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS career_person_status_idx
                ON career_person(outreach_status);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS career_person_tags_idx
                ON career_person USING GIN(tags);
        """)

        # Knowledge blocks: ensure 'name' column exists for older deployments
        cur.execute("""
            ALTER TABLE knowledge_blocks
            ADD COLUMN IF NOT EXISTS name TEXT
        """)

        # Project attachments: spreadsheets / excels attached to a project
        cur.execute("""
            CREATE TABLE IF NOT EXISTS project_attachment (
                id SERIAL PRIMARY KEY,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                kind TEXT NOT NULL DEFAULT 'excel',
                name TEXT NOT NULL DEFAULT 'Untitled',
                data JSONB NOT NULL DEFAULT '{}'::jsonb,
                position INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS project_attachment_project_idx
                ON project_attachment(project_id);
        """)

        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[migration] warning: {e}")


_run_migrations()

try:
    knowledge_engine_migrate()
except Exception as e:
    print(f"[migration] knowledge_engine warning: {e}")

app = FastAPI()

# ---- Read-only middleware for demo user ----
WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

class DemoReadOnlyMiddleware(BaseHTTPMiddleware):
    """Block write operations for the demo user."""
    async def dispatch(self, request: Request, call_next):
        if request.method in WRITE_METHODS:
            # Allow login endpoint for everyone
            if request.url.path == "/auth/login":
                return await call_next(request)
            # Extract JWT and check if it's the demo user
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                try:
                    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
                    username = payload.get("sub", "")
                    if username == DEMO_USERNAME:
                        return JSONResponse(
                            status_code=403,
                            content={"detail": "Demo account is read-only"},
                        )
                except JWTError:
                    pass  # let the auth dependency handle invalid tokens
        return await call_next(request)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(DemoReadOnlyMiddleware)

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
app.include_router(math_trainer_router, dependencies=_auth)
app.include_router(library_router, dependencies=_auth)
app.include_router(careers_router, dependencies=_auth)
app.include_router(graph_router, dependencies=_auth)
app.include_router(insights_router, dependencies=_auth)
app.include_router(knowledge_engine_router, dependencies=_auth)

@app.get("/")
def root():
    return {"status": "ok", "version": "2.0"}

@app.get("/debug/memory")
def debug_memory():
    """Returns actual RSS memory of the Python process (no extra deps needed)."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS"):
                    kb = int(line.split()[1])
                    return {"rss_mb": round(kb / 1024, 1)}
    except Exception:
        pass
    import resource
    kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"rss_mb": round(kb / 1024, 1)}

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