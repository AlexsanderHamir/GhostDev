# GhostDev

GhostDev is a web-based application for scheduling and executing tasks on GitHub repositories.
Users can upload PDF documents with task instructions, schedule them for execution, and monitor task status through a clean dashboard interface.

## Features
- GitHub OAuth authentication
- Browse and filter repositories (public/private/all)
- Upload PDF documents and schedule tasks
- Dashboard and detailed views of repositories and tasks
- Asynchronous task scheduler using APScheduler
- Integration with OpenHands for PDF processing and task execution

## System Architecture

See the design document for details: [desing_versions/1.0.0.md](desing_versions/1.0.0.md)

**Storage**
- PDF files: local filesystem (`src/uploads/`) (design doc: AWS S3)
- Metadata: PostgreSQL database (via Supabase)

**Authentication**
- GitHub OAuth for user login
- Sessions stored in database and server-side session middleware

**Scheduling**
- APScheduler-based scheduler checks for due tasks and updates their status

**Task Execution**
- `openhands.py` contains utilities to clone repositories, extract text from PDFs, and run the OpenHands Docker runtime

## Getting Started

### Prerequisites
- Python 3.12
- PostgreSQL database (e.g. Supabase)
- Docker (for running OpenHands tasks)

### Installation
```bash
git clone <repository-url>
cd GhostDev
python3 -m venv .venv
source .venv/bin/activate
pip install -r src/requirements.txt
```

### Configuration

Create a `.env` file in `src/` with the following variables:
```dotenv
GITHUB_CLIENT_ID=your_github_client_id
GITHUB_CLIENT_SECRECT=your_github_client_secret

APP_TITLE=GhostDev
SESSION_COOKIE_NAME=your_session_cookie_name
MIDDLEWARE_SECRET_KEY=your_session_secret_key

SUPABASE_USER=...
SUPABASE_PASSWORD=...
SUPABASE_HOST=...
SUPABASE_PORT=5432
SUPABASE_DB_NAME=...

LLM_API_KEY=your_llm_api_key
```

### Running the Application
```bash
cd src
uvicorn main:app --reload
```

Open <http://localhost:8000> in your browser to access the app.

## Demo Script

A helper script `setup.sh` is provided to quickly bootstrap and run a sample OpenHands execution.

## Project Structure

```
.
├── desing_versions/            # Versioned design documents
│   └── 1.0.0.md
├── src/                        # Source code
│   ├── db.py                   # Database connection
│   ├── helpers.py              # Core application logic and utilities
│   ├── main.py                 # FastAPI application entrypoint
│   ├── openhands.py            # OpenHands integration utilities
│   ├── scheduler.py            # Task scheduler setup
│   ├── templates/              # Jinja2 HTML templates
│   ├── uploads/                # Uploaded PDF files (created at runtime)
│   └── requirements.txt        # Python dependencies
├── setup.sh                    # Helper script to bootstrap OpenHands demo
├── .gitignore
└── README.md                   # Project overview and setup instructions
```

## Contributing

Contributions are welcome! Please open issues and pull requests.