from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import Request, HTTPException, UploadFile
from authlib.integrations.starlette_client import OAuth
import psycopg2
import os
import shutil
import uuid


# Database connection
def get_db_connection():
    try:
        connection = psycopg2.connect(user=os.getenv("SUPABASE_USER"),
                                      password=os.getenv("SUPABASE_PASSWORD"),
                                      host=os.getenv("SUPABASE_HOST"),
                                      port=os.getenv("SUPABASE_PORT"),
                                      dbname=os.getenv("SUPABASE_DB_NAME"))
        print("Connection successful!")
        return connection
    except Exception as e:
        print(f"Failed to connect: {e}")
        raise


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
    """Get the current user from the session."""
    return request.session.get('github_user')


def format_date(date_str: str) -> str:
    """Format a date string for display."""
    date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    now = datetime.now(date.tzinfo)
    diff = now - date

    if diff.days == 0:
        return "today"
    elif diff.days == 1:
        return "yesterday"
    elif diff.days < 7:
        return f"{diff.days} days ago"
    else:
        return date.strftime("%Y-%m-%d")


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


def create_task(repo_id: int, task_name: str, pdf_file_path: str,
                user_id: int) -> Dict[str, Any]:
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # First ensure the repository exists
        create_repository(repo_id, user_id)

        # Create the task
        cur.execute(
            """
            INSERT INTO "Task" (repo_id, task_name, pdf_file_path)
            VALUES (%s, %s, %s)
            RETURNING task_id, created_at
            """, (repo_id, task_name, pdf_file_path))

        task_id, created_at = cur.fetchone()

        # Update pending tasks count
        cur.execute(
            """
            UPDATE "Repo"
            SET pending_tasks = pending_tasks + 1
            WHERE repo_id = %s
            """, (repo_id, ))

        conn.commit()

        task = {
            "task_id": task_id,
            "repo_id": repo_id,
            "task_name": task_name,
            "pdf_file_path": pdf_file_path,
            "user_id": user_id,
            "created_at": created_at.isoformat()
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
            SELECT *
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
                "created_at": row[5].isoformat()
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
            SELECT t.*
            FROM "Task" t
            JOIN "Repo" r ON t.repo_id = r.repo_id
            WHERE t.repo_id = %s AND r.user_id = %s
            ORDER BY t.created_at DESC
            """, (repo_id, user_id))

        tasks = []
        for row in cur.fetchall():
            print(row)
            created_at = row[1].isoformat() if hasattr(row[1],
                                                       'isoformat') else row[1]

            task = {
                "task_id": row[0],
                "repo_id": row[3],
                "task_name": row[2],
                "pdf_file_path": row[4],
                "created_at": created_at
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
            SELECT t.*, r.repo_id
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
