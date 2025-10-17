import os
import json
import re
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from openai import OpenAI


load_dotenv()

DEFAULT_JOBS_PAGE_URL = "https://www.google.com/about/careers/applications/jobs/results"
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

openai_client = OpenAI(api_key=OPENAI_API_KEY)


class RecommendRequest(BaseModel):
    resume_data: str = Field(..., description="Raw resume text")
    jobs_page_url: Optional[str] = Field(None, description="Careers/jobs listing page URL")
    max_jobs: int = Field(5, ge=1, le=30, description="Max number of jobs to fetch")
    top_n: int = Field(3, ge=1, le=10, description="Number of recommendations to return")


class RecommendResponse(BaseModel):
    apply_links: List[str]
    extracted_jobs: List[dict]
    recommendations: List[dict]


def fetch_markdown_via_firecrawl(url: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        response = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {FIRECRAWL_API_KEY}" if FIRECRAWL_API_KEY else "",
            },
            json={
                "url": url,
                "formats": ["markdown"],
            },
            timeout=45,
        )
        if response.status_code == 402:
            return None, "Firecrawl credits insufficient (402)"
        if response.status_code != 200:
            return None, f"Firecrawl error {response.status_code}: {response.text[:200]}"
        data = response.json()
        if not data.get("success"):
            return None, data.get("message", "Firecrawl reported failure")
        return data["data"].get("markdown", ""), None
    except Exception as exc:
        return None, f"Firecrawl exception: {exc}"


def fallback_fetch_html(url: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.text, None
    except Exception as exc:
        return None, f"Fallback fetch exception: {exc}"


def normalize_url(href: str, base_url: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        scheme = "https:" if base_url.startswith("https") else "http:"
        return f"{scheme}{href}"
    if href.startswith("/"):
        # crude base join
        m = re.match(r"^(https?://[^/]+)", base_url)
        return f"{m.group(1)}{href}" if m else href
    return href


def extract_apply_links_from_html(html: str, base_url: str, max_links: int) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()
        if not href:
            continue
        href_abs = normalize_url(href, base_url)
        txt = (a.get_text() or "").lower()
        hlow = href_abs.lower()
        if any(k in hlow for k in ["job", "careers", "apply"]) or any(k in txt for k in ["apply", "job", "careers"]):
            links.append(href_abs)
            if len(links) >= max_links:
                break
    return links


def extract_jobs_via_firecrawl(link: str) -> Tuple[Optional[dict], Optional[str]]:
    try:
        response = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {FIRECRAWL_API_KEY}" if FIRECRAWL_API_KEY else "",
            },
            json={
                "url": link,
                "formats": ["extract"],
                "extract": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "job_title": {"type": "string"},
                            "sub_division_of_organization": {"type": "string"},
                            "key_skills": {"type": "array", "items": {"type": "string"}},
                            "compensation": {"type": "string"},
                            "location": {"type": "string"},
                            "apply_link": {"type": "string"},
                        },
                        "required": [
                            "job_title",
                            "sub_division_of_organization",
                            "key_skills",
                            "compensation",
                            "location",
                            "apply_link",
                        ],
                    }
                },
            },
            timeout=60,
        )
        if response.status_code == 402:
            return None, "Firecrawl credits insufficient (402)"
        if response.status_code != 200:
            return None, f"Firecrawl error {response.status_code}: {response.text[:200]}"
        data = response.json()
        if not data.get("success"):
            return None, data.get("message", "Firecrawl extract failed")
        return data["data"].get("extract", {}), None
    except Exception as exc:
        return None, f"Firecrawl extract exception: {exc}"


def recommend_with_openai(resume_text: str, extracted_jobs: List[dict], top_n: int) -> List[dict]:
    prompt = (
        "Please analyze the resume and job listings, and return a JSON object with a 'recommendations' "
        f"array of the top {top_n} roles that best fit the candidate's experience and skills. Include only the "
        "job title, compensation (empty string if not available), and apply link."
    )
    payload = {
        "resume": resume_text,
        "jobs": extracted_jobs,
    }
    completion = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload)},
        ],
    )
    content = completion.choices[0].message.content if completion.choices else "{}"
    try:
        obj = json.loads(content)
        recs = obj.get("recommendations", [])
        # ensure shape
        cleaned = []
        for r in recs[:top_n]:
            cleaned.append(
                {
                    "job_title": r.get("job_title", ""),
                    "compensation": r.get("compensation", ""),
                    "apply_link": r.get("apply_link", ""),
                }
            )
        return cleaned
    except Exception:
        return []


app = FastAPI()


@app.post("/recommend", response_model=RecommendResponse)
def recommend(req: RecommendRequest) -> RecommendResponse:
    jobs_page_url = req.jobs_page_url or DEFAULT_JOBS_PAGE_URL

    markdown, md_err = fetch_markdown_via_firecrawl(jobs_page_url)
    html_source: Optional[str] = None
    links: List[str] = []

    if markdown:
        # Try to find links in markdown by simple URL regex fallback, then constrain with keywords
        url_pattern = re.compile(r"https?://[^\s)\]]+")
        candidates = url_pattern.findall(markdown)
        filtered = []
        for u in candidates:
            low = u.lower()
            if any(k in low for k in ["job", "careers", "apply", "positions", "opening"]):
                filtered.append(u)
            if len(filtered) >= req.max_jobs:
                break
        links = filtered

    if not links:
        # Fallback to fetching the actual HTML and parsing anchors
        html_source, _ = fallback_fetch_html(jobs_page_url)
        if html_source:
            links = extract_apply_links_from_html(html_source, jobs_page_url, req.max_jobs)
        # If still empty, respond gracefully with empty lists
        if not links:
            return RecommendResponse(apply_links=[], extracted_jobs=[], recommendations=[])

    extracted_jobs: List[dict] = []
    for link in links:
        job, _ = extract_jobs_via_firecrawl(link)
        if job:
            extracted_jobs.append(job)

    recommendations = recommend_with_openai(req.resume_data, extracted_jobs, req.top_n)

    return RecommendResponse(
        apply_links=links,
        extracted_jobs=extracted_jobs,
        recommendations=recommendations,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
