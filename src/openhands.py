import os
import subprocess
import logging
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv
import pdfplumber
import time

# Configure logging to suppress pdfplumber warnings
logging.getLogger('pdfminer').setLevel(logging.ERROR)

# Load environment variables from .env file
load_dotenv()


def get_repo_name(repo_url: str) -> str:
    # Extract repository name from URL
    parsed_url = urlparse(repo_url)
    repo_name = os.path.splitext(os.path.basename(parsed_url.path))[0]
    return repo_name


def setup_repo_directory(repo_url: str) -> Path:
    script_dir = Path(__file__).parent.absolute()
    repos_dir = script_dir / "repos"
    repo_name = get_repo_name(repo_url)
    repo_dir = repos_dir / repo_name

    # Create repos directory if it doesn't exist
    repos_dir.mkdir(parents=True, exist_ok=True)

    return repo_dir


def clone_repository(repo_url: str, repo_dir: Path) -> None:
    try:
        subprocess.run(
            ["git", "clone", repo_url, str(repo_dir)],
            check=True,
            capture_output=True,
            text=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to clone repository: {e.stderr}")


def setup_environment(repo_dir: Path) -> None:
    # Ensure we have an absolute path for Docker
    absolute_repo_path = str(repo_dir.resolve())
    os.environ["SANDBOX_VOLUMES"] = f"{absolute_repo_path}:/workspace:rw"
    os.environ["LLM_MODEL"] = "openai/gpt-4o"

    llm_api_key = os.getenv("LLM_API_KEY")
    if not llm_api_key:
        raise ValueError("LLM_API_KEY not found in environment variables")
    os.environ["LLM_API_KEY"] = llm_api_key


def extract_pdf_text(pdf_location: str) -> str:
    if not pdf_location:
        return ""

    try:
        with pdfplumber.open(pdf_location) as pdf:
            if len(pdf.pages) == 0:
                raise ValueError("PDF file is empty")

            text = ""
            for page in pdf.pages:
                # Get the page dimensions
                page_height = page.height

                # Extract text with layout information
                words = page.extract_words(keep_blank_chars=True,
                                           x_tolerance=3,
                                           y_tolerance=3)

                # Filter out text that's likely to be header (top 10% of page) or footer (bottom 10% of page)
                header_threshold = page_height * 0.1
                footer_threshold = page_height * 0.9

                filtered_words = [
                    word for word in words
                    if not (word['top'] < header_threshold
                            or word['bottom'] > footer_threshold)
                ]

                # Sort words by their position (top to bottom, left to right)
                filtered_words.sort(key=lambda w: (w['top'], w['x0']))

                # Join the words with appropriate spacing
                page_text = ' '.join(word['text'] for word in filtered_words)
                if page_text:
                    text += page_text + "\n"

            return text.strip()

    except FileNotFoundError:
        raise FileNotFoundError(f"PDF file not found at: {pdf_location}")
    except Exception as e:
        raise ValueError(f"Error extracting text from PDF: {str(e)}")


def run_docker_command(pdf_text: str) -> None:
    """Run the docker command with the PDF text as the task."""
    docker_cmd = [
        "docker", "run", "-it", "--pull=always", "-e",
        f"SANDBOX_RUNTIME_CONTAINER_IMAGE=docker.all-hands.dev/all-hands-ai/runtime:0.39-nikolaik",
        "-e", f"SANDBOX_USER_ID={os.getuid()}", "-e",
        f"SANDBOX_VOLUMES={os.environ['SANDBOX_VOLUMES']}", "-e",
        f"LLM_API_KEY={os.environ['LLM_API_KEY']}", "-e",
        f"LLM_MODEL={os.environ['LLM_MODEL']}", "-e", "LOG_ALL_EVENTS=true",
        "-v", "/var/run/docker.sock:/var/run/docker.sock", "-v",
        f"{os.path.expanduser('~')}/.openhands-state:/.openhands-state",
        "--add-host", "host.docker.internal:host-gateway", "--name",
        f"openhands-app-{int(time.time())}",
        "docker.all-hands.dev/all-hands-ai/openhands:0.39", "python", "-m",
        "openhands.core.main", "-t", pdf_text
    ]

    try:
        subprocess.run(docker_cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to run docker command: {str(e)}")


def run_openhands(repo_url: str, pdf_location: str):
    repo_dir = setup_repo_directory(repo_url)
    clone_repository(repo_url, repo_dir)
    setup_environment(repo_dir)

    # Extract the text from the pdf
    pdf_text = extract_pdf_text(pdf_location)

    # Run the docker command with the PDF text
    if pdf_text:
        run_docker_command(pdf_text)


def main():
    run_openhands("https://github.com/AlexsanderHamir/prof.git",
                  "uploads/98370540/721dbd93-32a1-4d1e-b6c3-15922910a978.pdf")


if __name__ == "__main__":
    main()
