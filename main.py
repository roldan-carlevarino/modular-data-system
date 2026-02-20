import psycopg2
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Import all routers
from routers import rss, tasks, pomodoro, knowledge, logs, shopping, pando, gym, projects

load_dotenv()

app = FastAPI(title="Dashboard API", version="2.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(rss.router)
app.include_router(tasks.router)
app.include_router(pomodoro.router)
app.include_router(knowledge.router)
app.include_router(logs.router)
app.include_router(shopping.router)
app.include_router(pando.router)
app.include_router(gym.router)
app.include_router(projects.router)

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