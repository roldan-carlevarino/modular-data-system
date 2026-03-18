# Modular Data System

A personal operating system built from scratch to manage daily-powered data. This project implements a modular architecture for tracking and managing various aspects of daily life.

## Stack

- **Frontend**: Vanilla JS
- **Backend**: FastAPI (Python)
- **Database**: PostgreSQL
- **Auth**: JWT
- **Deployment**: Railway

## Project Structure

```
modular-data-system/
├── agents/                 # Hardware device integrations
│   └── t-watch/            # LilyGo T-Watch smartwatch agent
├── backend/
│   ├── app/api/            # FastAPI REST API
│   │   ├── main.py         # Application entry point
│   │   └── routers/        # API route modules
│   └── crons/              # Scheduled background tasks
├── clients/
│   └── dashboard-web/      # Web dashboard frontend
├── docs/                   # Documentation
└── infra/                  # Infrastructure configuration
```

## Features

### API Modules

| Module | Description |
|--------|-------------|
| **Auth** | JWT-based authentication |
| **Tasks** | Task management and tracking |
| **Projects** | Project organization |
| **Calendar** | Calendar and scheduling |
| **Pomodoro** | Focus/productivity timer |
| **RSS** | RSS feed aggregation |
| **Intel** | Intelligence/notes system |
| **Logs** | Activity logging |
| **Shopping** | Shopping list management |
| **Gym** | Workout tracking |
| **Water** | Water intake tracking |
| **Weight** | Weight tracking |
| **Menu** | Meal planning |
| **Media** | Media management |
| **Plaza** | Social/community features |
| **Welfare** | Wellness tracking |

### Scheduled Jobs (Crons)

- **Daily Tasks**: Auto-generates recurring tasks
- **Calendar Templates**: Creates daily calendar entries
- **RSS Feeds**: Processes and updates RSS feeds
- **Gym**: Gym-related scheduled tasks
- **Water**: Water intake reminders

### Hardware Agents

- **T-Watch**: LilyGo T-Watch smartwatch integration with WiFi sync and battery saver mode

## Getting Started

### Prerequisites

- Python 3.10+
- PostgreSQL
- Node.js (for frontend development)

### Backend Setup

1. Navigate to the API directory:
   ```bash
   cd backend/app/api
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Set up environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

4. Run the API:
   ```bash
   uvicorn main:app --reload
   ```

### Frontend Setup

1. Navigate to the dashboard:
   ```bash
   cd clients/dashboard-web
   ```

2. Open `index.html` in a browser or serve with a local server.

## API Documentation

Once the backend is running, visit:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `JWT_SECRET` | Secret key for JWT tokens |
| `B2_KEY_ID` | Backblaze B2 key ID (for media storage) |
| `B2_APPLICATION_KEY` | Backblaze B2 application key |
| `OPENAI_API_KEY` | OpenAI API key (optional, for AI features) |

## Deployment

The project is configured for Railway deployment. See `Procfile` in the API directory for the production start command.

## License

Private project.
