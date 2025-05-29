import os
import psycopg2


def get_db_connection():
    """Create and return a database connection."""
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
