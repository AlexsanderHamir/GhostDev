from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Request, HTTPException, UploadFile
from authlib.integrations.starlette_client import OAuth
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.config import Config
import os
import shutil
import uuid
from starlette.middleware.sessions import SessionMiddleware
from db import get_db_connection
from scheduler import setup_scheduler

from typing import Optional
from dataclasses import dataclass


def upsert_github_user(github_id: int) -> bool:
    """
    Check if a user exists and insert if they don't.
    Returns True if the operation was successful, False otherwise.
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # First check if user exists
        cur.execute(
            """
            SELECT user_id FROM "User" WHERE user_id = %s
            """, (github_id, ))

        if cur.fetchone() is None:
            # User doesn't exist, insert them
            cur.execute(
                """
                INSERT INTO "User" (user_id)
                VALUES (%s)
                """, (github_id, ))
            conn.commit()

        cur.close()
        conn.close()
        return True
    except Exception as db_error:
        print(f"Database error in upsert_github_user: {db_error}")
        return False


async def get_current_user(request: Request) -> Optional[dict]:
    return request.session.get('github_user')


def format_date(date_str: str) -> str:
    """Format a date string for display."""
    date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    return date.strftime("%Y-%m-%d %H:%M")


def get_language_color(lang: str) -> str:
    return {
        "JavaScript": "#f1e05a",
        "Python": "#3572A5",
        "Java": "#b07219",
        "TypeScript": "#2b7489",
        "Go": "#00ADD8",
        "Ruby": "#701516",
        "PHP": "#4F5D95",
        "C++": "#f34b7d",
        "C#": "#178600",
        "Rust": "#dea584",
        "Swift": "#ffac45",
        "Kotlin": "#F18E33",
        "HTML": "#e34c26",
        "CSS": "#563d7c",
        "Shell": "#89e051",
        "Dockerfile": "#384d54",
        "Makefile": "#427819",
        "Vue": "#2c3e50",
        "React": "#61dafb",
        "Angular": "#dd0031",
    }.get(lang, "#8256d0")


async def fetch_github_repositories(
        oauth: OAuth,
        token: dict,
        visibility: str = "all") -> List[Dict[str, Any]]:
    """Fetch and format GitHub repositories based on visibility."""
    resp = await oauth.github.get('user/repos', token=token)
    if not resp or resp.status_code != 200:
        raise HTTPException(status_code=400,
                            detail="Failed to fetch repositories from GitHub")

    repos = resp.json()

    # Filter repositories based on visibility
    if visibility == "public":
        repos = [repo for repo in repos if not repo['private']]
    elif visibility == "private":
        repos = [repo for repo in repos if repo['private']]

    # Format the repository information
    return [{
        'id': repo['id'],
        'full_name': repo['full_name'],
        'name': repo['name'],
        'description': repo['description'],
        'url': repo['html_url'],
        'stars': repo['stargazers_count'],
        'forks': repo['forks_count'],
        'language': repo['language'],
        'private': repo['private'],
        'created_at': repo['created_at'],
        'updated_at': repo['updated_at']
    } for repo in repos]


async def handle_github_callback(request: Request, oauth: OAuth) -> None:
    """Handle GitHub OAuth callback and store user data."""
    token = await oauth.github.authorize_access_token(request)
    if not token:
        raise HTTPException(status_code=400,
                            detail="Failed to obtain access token from GitHub")

    # Store the token in the session
    request.session['github_token'] = token

    resp = await oauth.github.get('user', token=token)
    if not resp or resp.status_code != 200:
        raise HTTPException(status_code=400,
                            detail="Failed to fetch user profile from GitHub")

    profile = resp.json()
    if not profile or 'id' not in profile or 'login' not in profile:
        raise HTTPException(
            status_code=400,
            detail="Invalid user profile data received from GitHub")

    # Store full user info in session for immediate use
    user_data = {
        'id': profile['id'],
        'login': profile['login'],
        'name': profile.get('name'),
        'email': profile.get('email'),
        'avatar_url': profile.get('avatar_url')
    }

    request.session['github_user'] = user_data

    # Store user in database using helper function
    upsert_github_user(user_data['id'])


def save_pdf_file(file: UploadFile, user_id: int) -> str:
    # Create uploads directory if it doesn't exist
    upload_dir = os.path.join("uploads", str(user_id))
    os.makedirs(upload_dir, exist_ok=True)

    # Generate unique filename
    file_extension = os.path.splitext(file.filename)[1]
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    file_path = os.path.join(upload_dir, unique_filename)

    # Save file
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return file_path


def create_repository(repo_id: int, user_id: int) -> None:
    """Create a new repository in the database if it doesn't exist."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Check if repository exists
        cur.execute(
            """
            SELECT repo_id FROM "Repo" WHERE repo_id = %s
            """, (repo_id, ))

        if cur.fetchone() is None:
            # Repository doesn't exist, create it
            cur.execute(
                """
                INSERT INTO "Repo" (repo_id, user_id)
                VALUES (%s, %s)
                """, (repo_id, user_id))
            conn.commit()

        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error creating repository: {e}")
        raise HTTPException(status_code=500,
                            detail="Failed to create repository")


def create_task(repo_id: int, task_name: str, pdf_file_path: str, user_id: int,
                scheduled_time: datetime) -> Dict[str, Any]:
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # First ensure the repository exists
        create_repository(repo_id, user_id)

        # Create the task
        cur.execute(
            """
            INSERT INTO "Task" (repo_id, task_name, pdf_file_path, scheduled_time)
            VALUES (%s, %s, %s, %s)
            RETURNING task_id, created_at, scheduled_time
            """, (repo_id, task_name, pdf_file_path, scheduled_time))

        task_id, created_at, scheduled_time = cur.fetchone()

        # Update pending tasks count
        cur.execute(
            """
            UPDATE "Repo"
            SET pending_tasks = pending_tasks + 1
            WHERE repo_id = %s
            """, (repo_id, ))

        conn.commit()

        task = {
            "task_id":
            task_id,
            "repo_id":
            repo_id,
            "task_name":
            task_name,
            "pdf_file_path":
            pdf_file_path,
            "user_id":
            user_id,
            "created_at":
            created_at.isoformat(),
            "scheduled_time":
            scheduled_time.isoformat() if scheduled_time else None
        }

        cur.close()
        conn.close()
        return task
    except Exception as e:
        print(f"Error creating task: {e}")
        raise HTTPException(status_code=500, detail="Failed to create task")


def get_user_tasks(user_id: int) -> List[Dict[str, Any]]:
    """Get all tasks for a user."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT task_id, user_id, repo_id, task_name, pdf_file_path, created_at, scheduled_time
            FROM "Task"
            WHERE user_id = %s
            ORDER BY created_at DESC
            """, (user_id, ))

        tasks = []
        for row in cur.fetchall():
            task = {
                "task_id": row[0],
                "user_id": row[1],
                "repo_id": row[2],
                "task_name": row[3],
                "pdf_file_path": row[4],
                "created_at": row[5].isoformat(),
                "scheduled_time": row[6].isoformat() if row[6] else None
            }
            tasks.append(task)

        cur.close()
        conn.close()
        return tasks
    except Exception as e:
        print(f"Error fetching tasks: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch tasks")


def get_repo_tasks(repo_id: int, user_id: int) -> List[Dict[str, Any]]:
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT t.task_id, t.created_at, t.task_name, t.repo_id, t.pdf_file_path, t.scheduled_time, t.task_completed
            FROM "Task" t
            JOIN "Repo" r ON t.repo_id = r.repo_id
            WHERE t.repo_id = %s AND r.user_id = %s
            ORDER BY t.created_at DESC
            """, (repo_id, user_id))

        tasks = []
        for row in cur.fetchall():
            created_at = row[1].isoformat() if hasattr(row[1],
                                                       'isoformat') else row[1]
            scheduled_time = row[5].isoformat() if row[5] and hasattr(
                row[5], 'isoformat') else row[5]

            task = {
                "task_id": row[0],
                "repo_id": row[3],
                "task_name": row[2],
                "pdf_file_path": row[4],
                "created_at": created_at,
                "scheduled_time": scheduled_time,
                "task_completed": row[6]
            }
            tasks.append(task)

        cur.close()
        conn.close()
        return tasks
    except Exception as e:
        print(f"Error fetching repository tasks: {e}")
        raise HTTPException(status_code=500,
                            detail="Failed to fetch repository tasks")


async def get_task_details(task_id: int, user_id: int, oauth: OAuth,
                           token: dict) -> Dict[str, Any]:
    """Get details for a specific task."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # First get the task and repo_id from the database
        cur.execute(
            """
            SELECT t.task_id, t.created_at, t.task_name, t.repo_id, t.pdf_file_path, t.scheduled_time, t.task_completed, r.repo_id
            FROM "Task" t
            JOIN "Repo" r ON t.repo_id = r.repo_id
            WHERE t.task_id = %s AND r.user_id = %s
            """, (task_id, user_id))

        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")

        # Get repository details from GitHub API
        repos = await fetch_github_repositories(oauth, token, "all")
        repo_info = next((r for r in repos if r['id'] == row[3]), None)
        if not repo_info:
            raise HTTPException(status_code=404, detail="Repository not found")

        task = {
            "task_id":
            row[0],
            "created_at":
            row[1].isoformat() if hasattr(row[1], 'isoformat') else row[1],
            "task_name":
            row[2],
            "repo_id":
            row[3],
            "pdf_file_path":
            row[4],
            "scheduled_time":
            row[5].isoformat()
            if row[5] and hasattr(row[5], 'isoformat') else row[5],
            "task_completed":
            row[6],
            "repo_name":
            repo_info['name'],
            "repo_url":
            repo_info['url']
        }

        cur.close()
        conn.close()
        return task
    except Exception as e:
        print(f"Error fetching task details: {e}")
        raise HTTPException(status_code=500,
                            detail="Failed to fetch task details")


async def get_repo_url(repo_id: int, oauth: OAuth, token: dict) -> str:
    """Get the URL for a specific repository by its ID."""
    try:
        repos = await fetch_github_repositories(oauth, token, "all")
        repo = next((r for r in repos if r['id'] == repo_id), None)
        if not repo:
            raise HTTPException(status_code=404, detail="Repository not found")
        return repo['url']
    except Exception as e:
        print(f"Error fetching repository URL: {e}")
        raise HTTPException(status_code=500,
                            detail="Failed to fetch repository URL")


def oauth_config() -> OAuth:
    config = Config('.env')
    oauth = OAuth(config)

    oauth.register(
        name='github',
        client_id=os.getenv('GITHUB_CLIENT_ID'),
        client_secret=os.getenv('GITHUB_CLIENT_SECRECT'),
        access_token_url='https://github.com/login/oauth/access_token',
        access_token_params=None,
        authorize_url='https://github.com/login/oauth/authorize',
        authorize_params=None,
        api_base_url='https://api.github.com/',
        client_kwargs={'scope': 'user:email repo'},
    )

    return oauth


@dataclass
class AppState:
    scheduler: Optional[Any] = None

    def initialize_scheduler(self) -> None:
        try:
            self.scheduler = setup_scheduler()
            self.scheduler.start()
        except Exception as e:
            raise RuntimeError(f"Failed to initialize scheduler: {e}")

    def shutdown_scheduler(self) -> None:
        if self.scheduler is not None:
            try:
                self.scheduler.shutdown()
            except Exception as e:
                print(f"Error during scheduler shutdown: {e}")


def create_app() -> FastAPI:
    title = os.getenv("APP_TITLE")
    session_cookie = os.getenv("SESSION_COOKIE_NAME")
    middleware_secret = os.getenv("MIDDLEWARE_SECRET_KEY")

    if not middleware_secret:
        raise ValueError("Session middleware secret key not provided")
    if not title:
        raise ValueError("App title not provided")
    if not session_cookie:
        raise ValueError("Session cookie name not provided")

    app_state = AppState()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            # Startup: Initialize scheduler
            app_state.initialize_scheduler()
            yield
        finally:
            # Shutdown: Cleanup resources
            app_state.shutdown_scheduler()

    app = FastAPI(title=title, lifespan=lifespan)

    app.add_middleware(SessionMiddleware,
                       secret_key=middleware_secret,
                       session_cookie=session_cookie)

    return app


async def handle_auth_callback(request: Request, oauth) -> RedirectResponse:
    """Handle GitHub OAuth callback and return redirect response."""
    try:
        await handle_github_callback(request, oauth)
        return RedirectResponse(url='/dash')
    except Exception as e:
        request.session.clear()
        raise HTTPException(status_code=400,
                            detail=f"Authentication failed: {str(e)}")


async def validate_user_session(request: Request) -> tuple[dict, str]:
    """Validate user session and return user info and token."""
    user = await get_current_user(request)
    if not user:
        raise RedirectResponse(url='/')

    token = request.session.get('github_token')
    if not token:
        raise HTTPException(status_code=401, detail="No GitHub token found")

    return user, token


async def get_dashboard_data(oauth, token: str, visibility: str) -> dict:
    """Fetch and format repository data for dashboard."""
    try:
        formatted_repos = await fetch_github_repositories(
            oauth, token, visibility)
        return formatted_repos
    except Exception as e:
        raise HTTPException(status_code=500,
                            detail=f"Error fetching repositories: {str(e)}")


class TaskCreateResponse(BaseModel):
    task_id: int
    task_name: str
    repo_id: int
    pdf_file_path: str
    user_id: int
    created_at: str
    scheduled_time: Optional[str]


def validate_pdf_file(file: UploadFile) -> None:
    """Validate that the uploaded file is a PDF."""
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400,
                            detail="Only PDF files are allowed")


def parse_scheduled_time(time_str: str) -> datetime:
    """Parse and validate the scheduled time string."""
    try:
        return datetime.fromisoformat(time_str.replace('Z', '+00:00'))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=
            "Invalid scheduled_time format. Use ISO format (YYYY-MM-DDTHH:MM:SSZ)"
        )


def handle_task_creation(repo_id: int, task_name: str, pdf_file: UploadFile,
                         user_id: int,
                         scheduled_time: datetime) -> TaskCreateResponse:
    try:
        # Save the PDF file
        pdf_file_path = save_pdf_file(pdf_file, user_id)

        # Create task in database
        task = create_task(repo_id, task_name, pdf_file_path, user_id,
                           scheduled_time)

        return TaskCreateResponse(**task)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def get_repo_details_from_github(oauth: OAuth, token: dict,
                                       repo_id: int) -> dict:
    """Get repository details from GitHub API."""
    # First get the repository's full name from the list of repositories
    repos = await fetch_github_repositories(oauth, token, "all")
    repo_info = next((r for r in repos if r['id'] == repo_id), None)
    if not repo_info:
        raise HTTPException(status_code=404, detail="Repository not found")

    # Fetch repository details using the full name
    resp = await oauth.github.get(f'repos/{repo_info["full_name"]}',
                                  token=token)
    if not resp or resp.status_code != 200:
        raise HTTPException(status_code=404, detail="Repository not found")

    repo = resp.json()
    return {
        'id': repo['id'],
        'name': repo['name'],
        'description': repo['description'],
        'url': repo['html_url'],
        'stars': repo['stargazers_count'],
        'forks': repo['forks_count'],
        'language': repo['language'],
        'private': repo['private'],
        'created_at': repo['created_at'],
        'updated_at': repo['updated_at']
    }


def get_repo_task_counts(repo_id: int, user_id: int) -> tuple[int, int]:
    """Get pending and completed task counts for a repository."""
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT pending_tasks, completed_tasks
            FROM "Repo"
            WHERE repo_id = %s AND user_id = %s
            """, (repo_id, user_id))

        row = cur.fetchone()
        pending_tasks_count = row[0] if row else 0
        completed_tasks_count = row[1] if row else 0
        return pending_tasks_count, completed_tasks_count
    finally:
        cur.close()
        conn.close()


async def get_repository_details(repo_id: int, user_id: int, oauth: OAuth,
                                 token: dict) -> dict:
    """Get complete repository details including tasks and counts."""
    try:
        # Get repository details from GitHub
        repo = await get_repo_details_from_github(oauth, token, repo_id)

        # Get tasks for this repository
        tasks = get_repo_tasks(repo_id, user_id)

        # Get task counts
        pending_tasks_count, completed_tasks_count = get_repo_task_counts(
            repo_id, user_id)

        return {
            "repo": repo,
            "tasks": tasks,
            "pending_tasks_count": pending_tasks_count,
            "completed_tasks_count": completed_tasks_count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
