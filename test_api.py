import argparse
import json
import os
from typing import Optional

import requests
from dotenv import load_dotenv


DUMMY_RESUME = (
    "Douglas Allen is a data strategy leader with experience in AI, Python, "
    "and analytics. Led teams delivering benchmarking, certification, and "
    "partnership programs. Hands-on with LLMs, FastAPI, prompt engineering, "
    "and product analytics. MBA, strong communication skills, and stakeholder management."
)

# You can change this to a different careers page for testing if desired.
DUMMY_JOBS_PAGE_URL = "https://openai.com/careers/search"


def start_server(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run("app:app", host=host, port=port, reload=False)


def call_recommend(
    base_url: str,
    resume: str,
    jobs_page_url: Optional[str],
    max_jobs: int,
    top_n: int,
) -> None:
    # Ensure proper URL format - only add https if no protocol specified
    if not base_url.startswith(('http://', 'https://')):
        base_url = f"https://{base_url}"
    
    # Remove any trailing slashes and add /recommend
    url = f"{base_url.rstrip('/')}/recommend"
    payload = {
        "resume_data": resume,
        "max_jobs": max_jobs,
        "top_n": top_n,
    }
    if jobs_page_url:
        payload["jobs_page_url"] = jobs_page_url

    try:
        print(f"Calling: {url}")
        r = requests.post(url, json=payload, timeout=120, verify=True)
        print(f"Status: {r.status_code}")
        try:
            print(json.dumps(r.json(), indent=2))
        except Exception:
            print(r.text)
    except requests.exceptions.ConnectionError as e:
        print(f"Connection error: {e}")
        print("Make sure your Railway URL is correct and the service is running")
    except requests.exceptions.Timeout as e:
        print(f"Timeout error: {e}")
    except Exception as e:
        print(f"Error: {e}")


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Run or call the job recommend API")
    parser.add_argument("--serve", action="store_true", help="Start the FastAPI server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind or call")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind or call")
    parser.add_argument("--call", action="store_true", help="Call the /recommend endpoint")
    parser.add_argument("--dummy", action="store_true", help="Use built-in dummy data for request")
    parser.add_argument("--resume", default=os.getenv("TEST_RESUME", DUMMY_RESUME))
    parser.add_argument("--jobs_page_url", default=os.getenv("TEST_JOBS_PAGE_URL", DUMMY_JOBS_PAGE_URL))
    parser.add_argument("--max_jobs", type=int, default=int(os.getenv("TEST_MAX_JOBS", "5")))
    parser.add_argument("--top_n", type=int, default=int(os.getenv("TEST_TOP_N", "3")))

    args = parser.parse_args()

    if args.serve and args.call:
        parser.error("Use --serve or --call, not both")

    if args.serve:
        start_server(args.host, args.port)
        return

    if args.call:
        # For API calls, use the host as-is (it might already be a full URL)
        base_url = args.host
        resume = DUMMY_RESUME if args.dummy else args.resume
        jobs_url = DUMMY_JOBS_PAGE_URL if args.dummy else args.jobs_page_url
        call_recommend(base_url, resume, jobs_url, args.max_jobs, args.top_n)
        return

    parser.print_help()


if __name__ == "__main__":
    main()


