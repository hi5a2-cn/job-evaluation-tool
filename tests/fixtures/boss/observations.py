import json
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent


def load_search_list_fixture() -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / "search_list.json").read_text(encoding="utf-8"))


def load_detail_fixture() -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / "detail.json").read_text(encoding="utf-8"))


def load_broken_fixture() -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / "broken.json").read_text(encoding="utf-8"))


def to_list_observation(
    sample: dict[str, Any] | None = None,
    observed_at: str = "2026-09-24T12:00:00Z",
) -> dict[str, Any]:
    """Convert search_list.json structure to /v1/observations (page_type=list) request payload."""
    if sample is None:
        sample = load_search_list_fixture()

    job_list = sample.get("vue", {}).get("jobList", [])
    jobs = []
    for item in job_list:
        salary_raw = item.get("salaryDesc")
        if salary_raw is not None and not str(salary_raw).strip():
            salary_raw = None

        area_district = item.get("areaDistrict")
        if area_district is not None and not str(area_district).strip():
            area_district = None

        company_name = item.get("brandName")
        if company_name is not None and not str(company_name).strip():
            company_name = None

        jobs.append({
            "platform_job_id": item["encryptJobId"],
            "title": item["jobName"],
            "company_name": company_name,
            "salary_raw": salary_raw,
            "city": item["cityName"],
            "district": area_district,
            "description": None,
        })

    return {
        "page_type": "list",
        "observed_at": observed_at,
        "jobs": jobs,
    }


def to_detail_observation(
    sample: dict[str, Any] | None = None,
    observed_at: str = "2026-09-24T12:00:00Z",
) -> dict[str, Any]:
    """Convert detail.json structure to /v1/observations (page_type=detail) request payload."""
    if sample is None:
        sample = load_detail_fixture()

    job_info = sample.get("vue", {}).get("jobDetail", {}).get("jobInfo", {})
    salary_raw = job_info.get("salaryDesc")
    if salary_raw is not None and not str(salary_raw).strip():
        salary_raw = None

    return {
        "page_type": "detail",
        "observed_at": observed_at,
        "jobs": [
            {
                "platform_job_id": job_info["encryptId"],
                "title": job_info["jobName"],
                "salary_raw": salary_raw,
                "city": job_info["locationName"],
                "district": None,
                "description": job_info.get("postDescription"),
            }
        ],
    }
