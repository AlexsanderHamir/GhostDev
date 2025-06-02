from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from db import get_db_connection


async def execute_due_tasks():
    """Check for and execute tasks that are due."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT t.task_id, t.task_name, t.repo_id, r.user_id, t.pdf_file_path
            FROM "Task" t
            JOIN "Repo" r ON t.repo_id = r.repo_id
            WHERE t.scheduled_time <= NOW()
            AND t.task_completed = false
        """)

        due_tasks = cur.fetchall()

        for task in due_tasks:
            task_id, task_name, repo_id, user_id, pdf_file_path = task
            print(
                f"Executing task {task_id}: {task_name} for PDF: {pdf_file_path}"
            )

            execute_task_in_container(repo_id, pdf_file_path)

            cur.execute(
                """
                UPDATE "Task"
                SET task_completed = true
                WHERE task_id = %s
            """, (task_id, ))

            # Update repository task counts
            cur.execute(
                """
                UPDATE "Repo"
                SET pending_tasks = pending_tasks - 1,
                    completed_tasks = completed_tasks + 1
                WHERE repo_id = %s AND user_id = %s
            """, (repo_id, user_id))

        conn.commit()
        cur.close()
        conn.close()

    except Exception as e:
        print(f"Error executing due tasks: {e}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()


def setup_scheduler():
    """Set up the scheduler to check for due tasks every minute."""
    scheduler = AsyncIOScheduler()

    # Add the job to check for due tasks every minute
    scheduler.add_job(execute_due_tasks,
                      trigger=IntervalTrigger(seconds=10),
                      id='check_due_tasks',
                      replace_existing=True)

    return scheduler


def execute_task_in_container(repo_url: str, pdf_text: str) -> None:
    """Execute the task in a Docker container."""
    pass
