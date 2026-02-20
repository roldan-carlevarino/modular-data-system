import psycopg2
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Import all routers
from routers import rss, tasks, pomodoro, intel, logs, shopping, plaza, gym, projects

load_dotenv()

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers






app.include_router(rss_router)
app.include_router(tasks_router)
app.include_router(pomodoro_router)
app.include_router(intel_router)
app.include_router(logs_router)
app.include_router(shopping_router)
app.include_router(plaza_router)
app.include_router(gym_router)
app.include_router(projects_router)

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