from helpers import (TaskCreateResponse, create_app, get_current_user,
                     get_dashboard_data, get_language_color, format_date,
                     handle_auth_callback, handle_task_creation, oauth_config,
                     parse_scheduled_time, get_task_details, validate_pdf_file,
                     validate_user_session, get_repository_details)
from fastapi import Request, HTTPException, Depends, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
import uvicorn
import os
from dotenv import load_dotenv
from authlib.integrations.starlette_client import OAuth

load_dotenv()
app = create_app()
oauth = oauth_config()
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user, _ = await get_current_user(request, oauth)
    if user:
        return RedirectResponse(url="/dash", status_code=303)
    return templates.TemplateResponse("home.html", {
        "request": request,
    })


@app.get("/auth/github")
async def github_login(request: Request):
    redirect_uri = request.url_for('dash')
    return await oauth.github.authorize_redirect(request, redirect_uri)


@app.get("/dash")
async def dash(request: Request, visibility: str = "all"):
    # When GitHub redirects back after authorization, it includes a 'code' parameter
    # This code needs to be exchanged for an access token before we can proceed
    # We handle this exchange here and then redirect back to /dash to:
    # 1. Remove the sensitive 'code' from the URL
    # 2. Ensure the session is properly set up with user data and token
    # 3. Show the dashboard with a clean URL and authenticated state
    if 'code' in request.query_params:
        return await handle_auth_callback(request, oauth)

    user, token = await validate_user_session(request, oauth)

    repositories = await get_dashboard_data(oauth, token, visibility)

    return templates.TemplateResponse(
        "dashboard.html", {
            "request": request,
            "user": user,
            "repositories": repositories,
            "visibility": visibility,
            "get_language_color": get_language_color,
            "format_date": format_date
        })


@app.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url='/')


async def get_current_user(request: Request, oauth: OAuth) -> tuple[dict, str]:
    user, token = await get_current_user(request, oauth)
    if not user or not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user, token


async def get_authenticated_user(request: Request) -> dict:
    user, _ = await get_current_user(request, oauth)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


@app.post("/api/tasks", response_model=TaskCreateResponse)
async def create_new_task(
    task_name: str = Form(...),
    repo_id: int = Form(...),
    pdf_file: UploadFile = File(...),
    scheduled_time: str = Form(...),
    user: dict = Depends(get_authenticated_user)
) -> TaskCreateResponse:
    validate_pdf_file(pdf_file)
    scheduled_datetime = parse_scheduled_time(scheduled_time)

    task = handle_task_creation(repo_id=repo_id,
                                task_name=task_name,
                                pdf_file=pdf_file,
                                user_id=user['id'],
                                scheduled_time=scheduled_datetime)

    return task


@app.get("/repo/{repo_id}")
async def repo_details(request: Request,
                       repo_id: int,
                       user_info: tuple[dict,
                                        str] = Depends(get_current_user)):
    if not user_info[0] or not user_info[1]:
        return RedirectResponse(url='/')

    user, token = user_info
    try:
        repo_data = await get_repository_details(repo_id, user['id'], oauth,
                                                 token)

        return templates.TemplateResponse(
            "repo_details.html", {
                "request": request,
                "user": user,
                "repo": repo_data["repo"],
                "tasks": repo_data["tasks"],
                "pending_tasks_count": repo_data["pending_tasks_count"],
                "completed_tasks_count": repo_data["completed_tasks_count"],
                "get_language_color": get_language_color,
                "format_date": format_date
            })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/task/{task_id}")
async def task_details(request: Request,
                       task_id: int,
                       user_info: tuple[dict,
                                        str] = Depends(get_current_user)):

    if not user_info[0] or not user_info[1]:
        return RedirectResponse(url='/')

    user, token = user_info
    try:
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
                       user_info: tuple[dict,
                                        str] = Depends(get_current_user)):

    if not user_info[0] or not user_info[1]:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user, token = user_info
    try:
        task = await get_task_details(task_id, user['id'], oauth, token)

        if not os.path.exists(task['pdf_file_path']):
            raise HTTPException(status_code=404, detail="PDF file not found")

        return FileResponse(task['pdf_file_path'],
                            media_type='application/pdf',
                            filename=f"{task['task_name']}.pdf")
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
