import psycopg2
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Import all routers
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

load_dotenv()

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)




app.include_router(rss_router)
app.include_router(tasks_router)
app.include_router(pomodoro_router)
app.include_router(intel_router)
app.include_router(logs_router)
app.include_router(shopping_router)
app.include_router(plaza_router)
app.include_router(gym_router)
app.include_router(projects_router)
app.include_router(media_router)
app.include_router(calendar_router)

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