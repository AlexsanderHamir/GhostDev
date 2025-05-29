# Show the home page from template on the browser

from fastapi import FastAPI, Request, HTTPException, Depends, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from authlib.integrations.starlette_client import OAuth
from starlette.middleware.sessions import SessionMiddleware
from starlette.config import Config
import uvicorn
import os
from dotenv import load_dotenv
from typing import Optional, List, Dict, Any
import psycopg2
import shutil
from datetime import datetime
import uuid

from helpers import (get_current_user, handle_github_callback,
                     fetch_github_repositories, get_language_color,
                     format_date, save_pdf_file, create_task, get_user_tasks,
                     get_repo_tasks, get_task_details)

# Load environment variables
load_dotenv()

USER = os.getenv("SUPABASE_USER")
PASSWORD = os.getenv("SUPABASE_PASSWORD")
HOST = os.getenv("SUPABASE_HOST")
PORT = os.getenv("SUPABASE_PORT")
DBNAME = os.getenv("SUPABASE_DB_NAME")


def get_db_connection():
    try:
        connection = psycopg2.connect(user=USER,
                                      password=PASSWORD,
                                      host=HOST,
                                      port=PORT,
                                      dbname=DBNAME)
        print("Connection successful!")
        return connection
    except Exception as e:
        print(f"Failed to connect: {e}")
        raise  # Re-raise the exception to handle it in the calling code


app = FastAPI(title="GhostDev API")

# Add session middleware
app.add_middleware(SessionMiddleware,
                   secret_key=os.getenv("MIDDLEWARE_SECRET_KEY"),
                   session_cookie="ghostdev_session")

# OAuth setup
config = Config('.env')
oauth = OAuth(config)

# Configure GitHub OAuth
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

templates = Jinja2Templates(directory="templates")


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


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = await get_current_user(request)
    return templates.TemplateResponse("home.html", {
        "request": request,
        "user": user
    })


@app.get("/auth/github")
async def github_login(request: Request):
    redirect_uri = request.url_for('dash')
    return await oauth.github.authorize_redirect(request, redirect_uri)


@app.get("/dash")
async def dash(request: Request, visibility: str = "all"):
    if 'code' in request.query_params:
        try:
            await handle_github_callback(request, oauth)
            return RedirectResponse(url='/dash')
        except Exception as e:
            request.session.clear()
            raise HTTPException(status_code=400,
                                detail=f"Authentication failed: {str(e)}")

    # If not a callback, show the dashboard
    user = await get_current_user(request)
    if not user:
        return RedirectResponse(url='/')

    try:
        # Get the token from the session
        token = request.session.get('github_token')
        if not token:
            raise HTTPException(status_code=401,
                                detail="No GitHub token found")

        # Fetch and format repositories
        formatted_repos = await fetch_github_repositories(
            oauth, token, visibility)

        return templates.TemplateResponse(
            "dashboard.html", {
                "request": request,
                "user": user,
                "repositories": formatted_repos,
                "visibility": visibility,
                "get_language_color": get_language_color,
                "format_date": format_date
            })
    except Exception as e:
        raise HTTPException(status_code=500,
                            detail=f"Error fetching repositories: {str(e)}")


@app.get("/auth/logout")
async def logout(request: Request):
    """Logout the user by clearing the session."""
    request.session.clear()
    return RedirectResponse(url='/')


@app.get("/api/me")
async def get_me(user: dict = Depends(get_current_user)):
    """Get current user information."""
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@app.get("/api/id")
async def get_user_id(user: dict = Depends(get_current_user)):
    """Get current user's GitHub ID."""
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"id": user['id']}


# Task endpoints
@app.post("/api/tasks")
async def create_new_task(task_name: str = Form(...),
                          repo_id: int = Form(...),
                          pdf_file: UploadFile = File(...),
                          user: dict = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if not pdf_file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400,
                            detail="Only PDF files are allowed")

    try:
        # Save the PDF file
        pdf_file_path = save_pdf_file(pdf_file, user['id'])

        # Create task in database
        task = create_task(repo_id, task_name, pdf_file_path, user['id'])

        return JSONResponse(content=task, status_code=201)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tasks")
async def get_tasks(user: dict = Depends(get_current_user)):
    """Get all tasks for the current user."""
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        tasks = get_user_tasks(user['id'])
        return tasks
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/repo/{repo_id}")
async def repo_details(request: Request,
                       repo_id: int,
                       user: dict = Depends(get_current_user)):
    """Show repository details and its tasks."""
    if not user:
        return RedirectResponse(url='/')

    try:
        # Get the token from the session
        token = request.session.get('github_token')
        if not token:
            raise HTTPException(status_code=401,
                                detail="No GitHub token found")

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
        formatted_repo = {
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

        # Get tasks for this repository
        tasks = get_repo_tasks(repo_id, user['id'])

        # Get task counts from the database
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT pending_tasks, completed_tasks
            FROM "Repo"
            WHERE repo_id = %s AND user_id = %s
            """, (repo_id, user['id']))

        row = cur.fetchone()
        pending_tasks_count = row[0] if row else 0
        completed_tasks_count = row[1] if row else 0

        cur.close()
        conn.close()

        return templates.TemplateResponse(
            "repo_details.html", {
                "request": request,
                "user": user,
                "repo": formatted_repo,
                "tasks": tasks,
                "pending_tasks_count": pending_tasks_count,
                "completed_tasks_count": completed_tasks_count,
                "get_language_color": get_language_color,
                "format_date": format_date
            })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/task/{task_id}")
async def task_details(request: Request,
                       task_id: int,
                       user: dict = Depends(get_current_user)):
    """Show task details."""
    if not user:
        return RedirectResponse(url='/')

    try:
        # Get the token from the session
        token = request.session.get('github_token')
        if not token:
            raise HTTPException(status_code=401,
                                detail="No GitHub token found")

        # Get task details
        task = await get_task_details(task_id, user['id'], oauth, token)

        return templates.TemplateResponse(
            "task_details.html", {
                "request": request,
                "user": user,
                "task": task,
                "format_date": format_date
            })
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tasks/{task_id}/pdf")
async def get_task_pdf(task_id: int,
                       user: dict = Depends(get_current_user),
                       request: Request = None):
    """Get the PDF file for a task."""
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        # Get the token from the session
        token = request.session.get('github_token')
        if not token:
            raise HTTPException(status_code=401,
                                detail="No GitHub token found")

        # Get task details to verify ownership and get file path
        task = await get_task_details(task_id, user['id'], oauth, token)

        # Check if file exists
        if not os.path.exists(task['pdf_file_path']):
            raise HTTPException(status_code=404, detail="PDF file not found")

        # Return the file
        return FileResponse(task['pdf_file_path'],
                            media_type='application/pdf',
                            filename=f"{task['task_name']}.pdf")
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
