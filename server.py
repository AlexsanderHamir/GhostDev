from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from pydantic import BaseModel
from typing import Optional
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
import shutil
import uuid

# Store scheduled tasks (in a real app, this would be in a database)
scheduled_tasks = {}


def create_app() -> FastAPI:
    app = FastAPI(title="PDF Processing Server")

    # Enable CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Create uploads directory if it doesn't exist
    UPLOAD_DIR = "uploads"
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # Initialize scheduler
    scheduler = AsyncIOScheduler()
    scheduler.start()

    class ScheduleRequest(BaseModel):
        scheduled_time: datetime
        description: Optional[str] = None

    async def process_pdf(file_path: str, task_id: str):
        """Process the PDF file at the scheduled time"""
        try:
            # TODO: Implement your PDF processing logic here
            print(f"Processing file {file_path} for task {task_id}")

            # Clean up the file after processing
            os.remove(file_path)
            del scheduled_tasks[task_id]
        except Exception as e:
            print(f"Error processing file: {str(e)}")

    @app.post("/upload")
    async def upload_pdf(
            file: UploadFile = File(...), schedule: ScheduleRequest = None):
        if not file.filename.endswith('.pdf'):
            raise HTTPException(status_code=400,
                                detail="Only PDF files are allowed")

        if schedule and schedule.scheduled_time < datetime.now():
            raise HTTPException(status_code=400,
                                detail="Scheduled time must be in the future")

        # Generate unique filename
        file_extension = os.path.splitext(file.filename)[1]
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        file_path = os.path.join(UPLOAD_DIR, unique_filename)

        # Save the file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        task_id = str(uuid.uuid4())

        if schedule:
            scheduler.add_job(
                process_pdf,
                trigger=DateTrigger(run_date=schedule.scheduled_time),
                args=[file_path, task_id],
                id=task_id)
            scheduled_tasks[task_id] = {
                "scheduled_time": schedule.scheduled_time,
                "filename": file.filename,
                "description": schedule.description
            }
            return {
                "message": "File uploaded and scheduled for processing",
                "task_id": task_id,
                "scheduled_time": schedule.scheduled_time
            }

    @app.get("/tasks")
    async def list_tasks():
        """List all scheduled tasks"""
        return scheduled_tasks

    @app.delete("/tasks/{task_id}")
    async def cancel_task(task_id: str):
        """Cancel a scheduled task"""
        if task_id not in scheduled_tasks:
            raise HTTPException(status_code=404, detail="Task not found")

        scheduler.remove_job(task_id)
        task_info = scheduled_tasks.pop(task_id)

        # Clean up the file if it exists
        file_path = os.path.join(UPLOAD_DIR, task_info["filename"])
        if os.path.exists(file_path):
            os.remove(file_path)

        return {"message": "Task cancelled successfully"}

    return app
