# Show the home page from template on the browser

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from authlib.integrations.starlette_client import OAuth
from starlette.middleware.sessions import SessionMiddleware
from starlette.config import Config
import uvicorn
import os
from dotenv import load_dotenv
from typing import Optional
import psycopg2

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
    client_kwargs={'scope': 'user:email'},
)

templates = Jinja2Templates(directory="templates")


async def get_current_user(request: Request) -> Optional[dict]:
    """Get the current user from the session."""
    return request.session.get('github_user')


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
async def dash(request: Request):
    """Handle GitHub OAuth callback and dashboard."""
    # Check if this is an OAuth callback
    if 'code' in request.query_params:
        try:
            token = await oauth.github.authorize_access_token(request)
            if not token:
                raise HTTPException(
                    status_code=400,
                    detail="Failed to obtain access token from GitHub")

            resp = await oauth.github.get('user', token=token)
            if not resp or resp.status_code != 200:
                raise HTTPException(
                    status_code=400,
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

            # Only store the GitHub ID in the database
            try:
                conn = get_db_connection()
                cur = conn.cursor()

                # Simple insert of just the GitHub ID
                cur.execute(
                    """
                    INSERT INTO "User" (user_id)
                    VALUES (%s)
                    ON CONFLICT (user_id) DO NOTHING
                """, (user_data['id'], ))

                conn.commit()
                cur.close()
                conn.close()
            except Exception as db_error:
                print(f"Database error: {db_error}")
                # Don't raise the error to the user, just log it
                # The user can still use the app even if DB insert fails

            return RedirectResponse(url='/dash')
        except Exception as e:
            # Log the error here if you have logging set up
            request.session.clear()  # Clear any partial session data
            raise HTTPException(status_code=400,
                                detail=f"Authentication failed: {str(e)}")

    # If not a callback, show the dashboard
    user = await get_current_user(request)
    if not user:
        return RedirectResponse(url='/')

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user
    })


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


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
