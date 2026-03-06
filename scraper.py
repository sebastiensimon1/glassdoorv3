"""
Standalone Glassdoor Job Scraper using SeleniumBase CDP Mode (Multithreaded)
- Bypasses bot detection via SeleniumBase's UC + CDP mode
- No dependency on jobspy; all models and parsing are self-contained
- Multithreaded job description fetching for significant speedup
- Exports results to CSV
"""

from __future__ import annotations

import re
import csv
import json
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from seleniumbase import SB

# ─── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
log = logging.getLogger("GlassdoorScraper")

# ─── Standalone Models ──────────────────────────────────────────────────────


class CompensationInterval(Enum):
    YEARLY = "yearly"
    MONTHLY = "monthly"
    WEEKLY = "weekly"
    DAILY = "daily"
    HOURLY = "hourly"

    @classmethod
    def get_interval(cls, pay_period: str) -> Optional["CompensationInterval"]:
        mapping = {
            "ANNUAL": cls.YEARLY,
            "MONTHLY": cls.MONTHLY,
            "WEEKLY": cls.WEEKLY,
            "DAILY": cls.DAILY,
            "HOURLY": cls.HOURLY,
        }
        return mapping.get(pay_period.upper())


class JobType(Enum):
    FULL_TIME = ("fulltime", "Full-time")
    PART_TIME = ("parttime", "Part-time")
    CONTRACT = ("contract", "Contract")
    TEMPORARY = ("temporary", "Temporary")
    INTERNSHIP = ("internship", "Internship")


@dataclass
class Location:
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None

    def __str__(self):
        parts = [p for p in (self.city, self.state, self.country) if p]
        return ", ".join(parts)


@dataclass
class Compensation:
    interval: Optional[CompensationInterval] = None
    min_amount: Optional[int] = None
    max_amount: Optional[int] = None
    currency: str = "USD"

    def __str__(self):
        if self.min_amount and self.max_amount:
            return f"${self.min_amount:,} - ${self.max_amount:,} {self.currency} ({self.interval.value if self.interval else 'N/A'})"
        return "N/A"


@dataclass
class JobPost:
    id: str
    title: str
    company_name: str
    job_url: str
    location: Optional[Location] = None
    compensation: Optional[Compensation] = None
    date_posted: Optional[str] = None
    is_remote: bool = False
    description: Optional[str] = None
    company_url: Optional[str] = None
    company_logo: Optional[str] = None
    listing_type: Optional[str] = None
    emails: list[str] = field(default_factory=list)


# ─── Parsing Utilities ──────────────────────────────────────────────────────


def parse_compensation(data: dict) -> Optional[Compensation]:
    pay_period = data.get("payPeriod")
    adjusted_pay = data.get("payPeriodAdjustedPay")
    currency = data.get("payCurrency", "USD")
    if not pay_period or not adjusted_pay:
        return None
    if pay_period == "ANNUAL":
        interval = CompensationInterval.YEARLY
    else:
        interval = CompensationInterval.get_interval(pay_period)
    min_amount = int(adjusted_pay.get("p10", 0) // 1)
    max_amount = int(adjusted_pay.get("p90", 0) // 1)
    return Compensation(
        interval=interval,
        min_amount=min_amount,
        max_amount=max_amount,
        currency=currency,
    )


def parse_location(location_name: str) -> Optional[Location]:
    if not location_name or location_name == "Remote":
        return None
    city, _, state = location_name.partition(", ")
    return Location(city=city, state=state)


def get_cursor_for_page(pagination_cursors: list[dict], page_num: int) -> Optional[str]:
    for cursor_data in pagination_cursors:
        if cursor_data["pageNumber"] == page_num:
            return cursor_data["cursor"]
    return None


def extract_emails_from_text(text: str) -> list[str]:
    if not text:
        return []
    return re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)


def is_title_excluded(title: str) -> bool:
    """Check if a job title contains any excluded terms."""
    title_lower = title.lower()
    for term in EXCLUDE_TERMS:
        if term in title_lower:
            return True
    return False


def detect_remote_from_text(text: str) -> bool:
    """Check if text contains remote indicators via regex."""
    if not text:
        return False
    if REMOTE_LOCATION_RE.search(text):
        return True
    if REMOTE_LI_TAG_RE.search(text):
        return True
    return False


# ─── CSV Export ─────────────────────────────────────────────────────────────


def save_jobs_to_csv(jobs: list[JobPost], filepath: str) -> None:
    """Export a list of JobPost objects to a CSV file."""
    CSV_COLUMNS = [
        "id",
        "title",
        "company_name",
        "company_url",
        "job_url",
        "location",
        "is_remote",
        "date_posted",
        "pay_min",
        "pay_max",
        "pay_currency",
        "pay_interval",
        "listing_type",
        "emails",
        "description",
    ]

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()

        for job in jobs:
            comp = job.compensation
            writer.writerow(
                {
                    "id": job.id,
                    "title": job.title,
                    "company_name": job.company_name,
                    "company_url": job.company_url or "",
                    "job_url": job.job_url,
                    "location": str(job.location) if job.location else ("Remote" if job.is_remote else ""),
                    "is_remote": job.is_remote,
                    "date_posted": job.date_posted or "",
                    "pay_min": comp.min_amount if comp else "",
                    "pay_max": comp.max_amount if comp else "",
                    "pay_currency": comp.currency if comp else "",
                    "pay_interval": comp.interval.value if comp and comp.interval else "",
                    "listing_type": job.listing_type or "",
                    "emails": "; ".join(job.emails) if job.emails else "",
                    "description": (job.description or "").replace("\n", " ").strip(),
                }
            )

    log.info(f"Saved {len(jobs)} jobs to {filepath}")


# ─── Constants ──────────────────────────────────────────────────────────────

FALLBACK_TOKEN = "Ft6oHEWlRZrxDww95Cpazw:0pGUrkb2y3TyOpAIqF2vbPmUXoXVkD3oEGDVkvfeCerceQ5-n8mBg3BovySUIjmCPHCaW0H2nQVdqzbtsYqf4Q:wcqRqeegRUa9MVLJGyujVXB7vWFPjdaS1CtrrzJq-ok"

MAX_DESCRIPTION_WORKERS = 8  # threads for parallel description fetching

EXCLUDE_TERMS = {
    'lead', 'manager', 'senior', 'principal', 'director', 'vp', 'vice president',
    'sr ', 'ciso', 'chief', 'level 2', 'tier 3', 'associate director', 'l3',
    'architecture', 'sme', 'architect', 'field', 'software developer',
    'data scientist', 'scientist', 'federal account executive',
    'full stack developer', 'traveling aircraft mechanic', 'software engineer',
    'human resources operations', 'ii', 'regional technical development specialist',
    'stock plan administrator', 'commissioning authority', 'salesforce', 'dir',
    'consultant', 'adjunct faculty', 'subject matter expert', 'staff',
    'intern', 'internship',
}

# Regex patterns for detecting remote jobs
REMOTE_LOCATION_RE = re.compile(r'(Remote(?:\s*\([^)]+\))?)', re.IGNORECASE)
REMOTE_LI_TAG_RE = re.compile(r'#LI-Remote', re.IGNORECASE)

HEADERS = {
    "authority": "www.glassdoor.com",
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "apollographql-client-name": "job-search-next",
    "apollographql-client-version": "4.65.5",
    "content-type": "application/json",
    "origin": "https://www.glassdoor.com",
    "referer": "https://www.glassdoor.com/",
    "sec-ch-ua": '"Chromium";v="118", "Google Chrome";v="118", "Not=A?Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
}

QUERY_TEMPLATE = """
    query JobSearchResultsQuery(
        $excludeJobListingIds: [Long!],
        $keyword: String,
        $locationId: Int,
        $locationType: LocationTypeEnum,
        $numJobsToShow: Int!,
        $pageCursor: String,
        $pageNumber: Int,
        $filterParams: [FilterParams],
        $originalPageUrl: String,
        $seoFriendlyUrlInput: String,
        $parameterUrlInput: String,
        $seoUrl: Boolean
    ) {
        jobListings(
            contextHolder: {
                searchParams: {
                    excludeJobListingIds: $excludeJobListingIds,
                    keyword: $keyword,
                    locationId: $locationId,
                    locationType: $locationType,
                    numPerPage: $numJobsToShow,
                    pageCursor: $pageCursor,
                    pageNumber: $pageNumber,
                    filterParams: $filterParams,
                    originalPageUrl: $originalPageUrl,
                    seoFriendlyUrlInput: $seoFriendlyUrlInput,
                    parameterUrlInput: $parameterUrlInput,
                    seoUrl: $seoUrl,
                    searchType: SR
                }
            }
        ) {
            jobListings {
                jobview {
                    header {
                        adOrderSponsorshipLevel
                        ageInDays
                        employer { id name shortName __typename }
                        employerNameFromSearch
                        jobTitleText
                        locationName
                        locationType
                        payCurrency
                        payPeriod
                        payPeriodAdjustedPay { p10 p50 p90 __typename }
                        __typename
                    }
                    job {
                        description
                        jobTitleText
                        listingId
                        __typename
                    }
                    overview {
                        squareLogoUrl
                        __typename
                    }
                    __typename
                }
                __typename
            }
            paginationCursors {
                cursor
                pageNumber
                __typename
            }
            totalJobsCount
            __typename
        }
    }
"""

JOB_DETAIL_QUERY = """
    query JobDetailQuery($jl: Long!, $queryString: String, $pageTypeEnum: PageTypeEnum) {
        jobview: jobView(
            listingId: $jl
            contextHolder: {queryString: $queryString, pageTypeEnum: $pageTypeEnum}
        ) {
            job {
                description
                __typename
            }
            __typename
        }
    }
"""


# ─── Scraper ────────────────────────────────────────────────────────────────


class GlassdoorScraper:
    """
    Standalone Glassdoor scraper using SeleniumBase CDP Mode.
    Multithreaded description fetching for faster scraping.
    """

    BASE_URL = "https://www.glassdoor.com"
    JOBS_PER_PAGE = 30
    MAX_PAGES = 30

    def __init__(
        self,
        proxy: Optional[str] = None,
        headless: bool = True,
        user_agent: Optional[str] = None,
        description_workers: int = MAX_DESCRIPTION_WORKERS,
    ):
        self.proxy = proxy
        self.headless = headless
        self.user_agent = user_agent
        self.description_workers = max(1, min(description_workers, 15))
        self.seen_urls: set[str] = set()
        self.csrf_token: Optional[str] = None
        self.cookies: dict = {}

    def scrape(
        self,
        search_term: str,
        location: str = "",
        results_wanted: int = 30,
        hours_old: Optional[int] = None,
        is_remote: bool = False,
        easy_apply: bool = False,
        job_type: Optional[str] = None,
        offset: int = 0,
        fetch_descriptions: bool = True,
    ) -> list[JobPost]:
        results_wanted = min(900, results_wanted)
        self.seen_urls.clear()

        log.info("Launching SeleniumBase browser to obtain session credentials...")
        self._init_browser_session()

        location_id, location_type = self._get_location(location, is_remote)
        if location_type is None:
            log.error("Could not resolve location on Glassdoor.")
            return []

        job_list: list[JobPost] = []
        cursor = None
        page = 1 + (offset // self.JOBS_PER_PAGE)
        total_excluded = 0

        while len(job_list) < results_wanted and page <= self.MAX_PAGES:
            log.info(f"Fetching page {page} (collected {len(job_list)}/{results_wanted}, excluded {total_excluded})...")
            try:
                jobs, new_cursor, excluded_count = self._fetch_jobs_page(
                    search_term=search_term,
                    location_id=location_id,
                    location_type=location_type,
                    page_num=page,
                    cursor=cursor,
                    hours_old=hours_old,
                    easy_apply=easy_apply,
                    job_type=job_type,
                    fetch_descriptions=fetch_descriptions,
                )
                cursor = new_cursor
                total_excluded += excluded_count

                if not jobs and cursor is None:
                    log.info("No more results available.")
                    break

                job_list.extend(jobs)
                page += 1
            except Exception as e:
                log.error(f"Error on page {page}: {e}")
                break

        job_list = job_list[:results_wanted]
        log.info(f"Scraped {len(job_list)} jobs total ({total_excluded} excluded by title filter).")
        return job_list

    def _init_browser_session(self):
        sb_kwargs = {
            "uc": True,
            "test": True,
            "headless2": self.headless,
        }
        if self.proxy:
            sb_kwargs["proxy"] = self.proxy
        if self.user_agent:
            sb_kwargs["agent"] = self.user_agent

        with SB(**sb_kwargs) as sb:
            url = f"{self.BASE_URL}/Job/computer-science-jobs.htm"
            sb.activate_cdp_mode(url)
            sb.sleep(3)

            try:
                sb.solve_captcha()
            except Exception:
                pass

            sb.sleep(2)

            page_source = sb.get_page_source()
            token_match = re.findall(r'"token":\s*"([^"]+)"', page_source)
            if token_match:
                self.csrf_token = token_match[0]
                log.info("Obtained CSRF token from page.")
            else:
                self.csrf_token = FALLBACK_TOKEN
                log.warning("Using fallback CSRF token.")

            try:
                all_cookies = sb.driver.get_cookies()
                self.cookies = {c["name"]: c["value"] for c in all_cookies}
                log.info(f"Captured {len(self.cookies)} cookies from browser session.")
            except Exception:
                self.cookies = {}
                log.warning("Could not extract cookies from browser.")

    def _make_api_request(self, payload: str) -> dict:
        import requests

        headers = HEADERS.copy()
        headers["gd-csrf-token"] = self.csrf_token or FALLBACK_TOKEN
        if self.user_agent:
            headers["user-agent"] = self.user_agent

        proxies = None
        if self.proxy:
            proxies = {"http": self.proxy, "https": self.proxy}

        response = requests.post(
            f"{self.BASE_URL}/graph",
            headers=headers,
            cookies=self.cookies,
            data=payload,
            proxies=proxies,
            timeout=15,
        )

        if response.status_code != 200:
            raise Exception(f"Glassdoor API returned status {response.status_code}")

        return response.json()

    def _get_location(self, location: str, is_remote: bool) -> tuple[Optional[str], Optional[str]]:
        if not location or is_remote:
            return "11047", "STATE"

        import requests

        url = f"{self.BASE_URL}/findPopularLocationAjax.htm?maxLocationsToReturn=10&term={location}"
        headers = HEADERS.copy()
        headers["gd-csrf-token"] = self.csrf_token or FALLBACK_TOKEN

        proxies = None
        if self.proxy:
            proxies = {"http": self.proxy, "https": self.proxy}

        res = requests.get(
            url, headers=headers, cookies=self.cookies, proxies=proxies, timeout=10
        )

        if res.status_code == 429:
            log.error("429 - Rate limited by Glassdoor on location lookup.")
            return None, None
        if res.status_code != 200:
            log.error(f"Location lookup returned status {res.status_code}")
            return None, None

        items = res.json()
        if not items:
            log.error(f"Location '{location}' not found on Glassdoor.")
            return None, None

        loc_type = items[0]["locationType"]
        type_map = {"C": "CITY", "S": "STATE", "N": "COUNTRY"}
        location_type = type_map.get(loc_type, loc_type)

        return int(items[0]["locationId"]), location_type

    def _fetch_jobs_page(
        self,
        search_term: str,
        location_id: int,
        location_type: str,
        page_num: int,
        cursor: Optional[str],
        hours_old: Optional[int],
        easy_apply: bool,
        job_type: Optional[str],
        fetch_descriptions: bool,
    ) -> tuple[list[JobPost], Optional[str], int]:
        fromage = None
        if hours_old:
            fromage = max(hours_old // 24, 1)

        filter_params = []
        if easy_apply:
            filter_params.append({"filterKey": "applicationType", "values": "1"})
        if fromage:
            filter_params.append({"filterKey": "fromAge", "values": str(fromage)})

        payload = {
            "operationName": "JobSearchResultsQuery",
            "variables": {
                "excludeJobListingIds": [],
                "filterParams": filter_params,
                "keyword": search_term,
                "numJobsToShow": 30,
                "locationType": location_type,
                "locationId": int(location_id),
                "parameterUrlInput": f"IL.0,12_I{location_type}{location_id}",
                "pageNumber": page_num,
                "pageCursor": cursor,
                "fromage": fromage,
                "sort": "date",
            },
            "query": QUERY_TEMPLATE,
        }

        if job_type:
            payload["variables"]["filterParams"].append(
                {"filterKey": "jobType", "values": job_type}
            )

        try:
            res_json = self._make_api_request(json.dumps([payload]))
            data = res_json[0]
            if "errors" in data:
                log.error(f"GraphQL errors: {data['errors']}")
                return [], None, 0
        except Exception as e:
            log.error(f"API request failed: {e}")
            return [], None, 0

        jobs_data = data["data"]["jobListings"]["jobListings"]
        log.info(f"  Got {len(jobs_data)} listings on page {page_num}")

        partial_jobs: list[tuple[JobPost, int]] = []
        excluded_count = 0

        for job_data in jobs_data:
            try:
                result = self._process_job_metadata(job_data)
                if result is None:
                    excluded_count += 1
                    continue
                job_post, listing_id = result
                partial_jobs.append((job_post, listing_id))
            except Exception as e:
                log.warning(f"  Failed to process a job: {e}")

        if fetch_descriptions and partial_jobs:
            self._fetch_descriptions_parallel(partial_jobs)

        final_jobs: list[JobPost] = []
        for job_post, _ in partial_jobs:
            if not job_post.is_remote and job_post.description:
                if detect_remote_from_text(job_post.description):
                    job_post.is_remote = True
            if job_post.description:
                job_post.emails = extract_emails_from_text(job_post.description)
            final_jobs.append(job_post)

        next_cursor = get_cursor_for_page(
            data["data"]["jobListings"]["paginationCursors"], page_num + 1
        )
        return final_jobs, next_cursor, excluded_count

    def _fetch_descriptions_parallel(self, partial_jobs: list[tuple[JobPost, int]]) -> None:
        workers = min(self.description_workers, len(partial_jobs))
        log.info(f"  Fetching {len(partial_jobs)} descriptions with {workers} threads...")
        start = time.time()

        def _fetch_one(job_and_id: tuple[JobPost, int]) -> None:
            job_post, listing_id = job_and_id
            try:
                desc = self._fetch_job_description(listing_id)
                job_post.description = desc
            except Exception as e:
                log.debug(f"  Description fetch failed for {listing_id}: {e}")

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_fetch_one, item) for item in partial_jobs]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    pass

        elapsed = time.time() - start
        fetched = sum(1 for j, _ in partial_jobs if j.description)
        log.info(f"  Fetched {fetched}/{len(partial_jobs)} descriptions in {elapsed:.1f}s")

    def _process_job_metadata(self, job_data: dict) -> Optional[tuple[JobPost, int]]:
        job = job_data["jobview"]
        job_id = job["job"]["listingId"]
        job_url = f"{self.BASE_URL}/job-listing/j?jl={job_id}"

        if job_url in self.seen_urls:
            return None
        self.seen_urls.add(job_url)

        title = job["job"]["jobTitleText"]

        if is_title_excluded(title):
            log.info(f"  Excluded: {title}")
            return None

        company_name = job["header"]["employerNameFromSearch"]
        company_id = job["header"]["employer"]["id"]
        location_name = job["header"].get("locationName", "")
        loc_type = job["header"].get("locationType", "")
        age_in_days = job["header"].get("ageInDays")

        is_remote = False
        location = None
        date_posted = None

        if age_in_days is not None:
            date_posted = (datetime.now() - timedelta(days=age_in_days)).date().isoformat()

        if loc_type == "S":
            is_remote = True
        elif detect_remote_from_text(location_name):
            is_remote = True
        else:
            location = parse_location(location_name)

        compensation = parse_compensation(job["header"])

        company_url = f"{self.BASE_URL}/Overview/W-EI_IE{company_id}.htm" if company_id else None
        company_logo = job.get("overview", {}).get("squareLogoUrl") if job.get("overview") else None
        listing_type = job.get("header", {}).get("adOrderSponsorshipLevel", "").lower()

        job_post = JobPost(
            id=f"gd-{job_id}",
            title=title,
            company_name=company_name,
            company_url=company_url,
            job_url=job_url,
            location=location,
            compensation=compensation,
            date_posted=date_posted,
            is_remote=is_remote,
            description=None,
            company_logo=company_logo,
            listing_type=listing_type,
            emails=[],
        )

        return job_post, job_id

    def _fetch_job_description(self, job_id: int) -> Optional[str]:
        body = [
            {
                "operationName": "JobDetailQuery",
                "variables": {
                    "jl": job_id,
                    "queryString": "q",
                    "pageTypeEnum": "SERP",
                },
                "query": JOB_DETAIL_QUERY,
            }
        ]

        try:
            data = self._make_api_request(json.dumps(body))
            return data[0]["data"]["jobview"]["job"]["description"]
        except Exception:
            return None


# ─── Interactive Entry Point ────────────────────────────────────────────────


def prompt_yes_no(question: str, default: bool = False) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = input(question + suffix).strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def prompt_input(question: str, default: str = "") -> str:
    if default:
        answer = input(f"{question} [{default}]: ").strip()
        return answer if answer else default
    return input(f"{question}: ").strip()


def prompt_int(question: str, default: int = 30) -> int:
    answer = input(f"{question} [{default}]: ").strip()
    if not answer:
        return default
    try:
        return int(answer)
    except ValueError:
        print(f"  Invalid number, using default: {default}")
        return default


def interactive_menu():
    print("\n" + "=" * 60)
    print("  Glassdoor Job Scraper (SeleniumBase CDP + Multithreaded)")
    print("=" * 60)

    search_term = ""
    while not search_term:
        search_term = prompt_input("\n Job search term (e.g. 'security analyst')")
        if not search_term:
            print("  Search term is required.")

    is_remote = prompt_yes_no("Remote jobs only?", default=False)
    results_wanted = prompt_int("Number of results wanted", default=30)

    hours_old_str = prompt_input("Max posting age in hours (leave blank for any)", "")
    hours_old = int(hours_old_str) if hours_old_str.isdigit() else None

    easy_apply = prompt_yes_no("Easy apply only?", default=False)
    fetch_descriptions = prompt_yes_no("Fetch full job descriptions?", default=True)

    desc_workers = prompt_int("Description fetch threads (1-15)", default=MAX_DESCRIPTION_WORKERS)
    desc_workers = max(1, min(15, desc_workers))

    output_file = prompt_input("Save results to file? (e.g. jobs.csv or jobs.json, blank to skip)", "")
    output_file = output_file if output_file else None

    print("\n" + "-" * 60)
    print("  Configuration Summary:")
    print(f"    Search:        {search_term}")
    print(f"    Remote only:   {is_remote}")
    print(f"    Results:       {results_wanted}")
    print(f"    Max age:       {f'{hours_old}h' if hours_old else 'Any'}")
    print(f"    Easy apply:    {easy_apply}")
    print(f"    Descriptions:  {fetch_descriptions}")
    print(f"    Threads:       {desc_workers}")
    print(f"    Output file:   {output_file or 'None'}")
    print("-" * 60)

    if not prompt_yes_no("\nStart scraping?", default=True):
        print("Cancelled.")
        return

    scraper = GlassdoorScraper(headless=True, description_workers=desc_workers)
    start_time = time.time()

    jobs = scraper.scrape(
        search_term=search_term,
        location="",
        results_wanted=results_wanted,
        hours_old=hours_old,
        is_remote=is_remote,
        easy_apply=easy_apply,
        fetch_descriptions=fetch_descriptions,
    )

    elapsed = time.time() - start_time

    if not jobs:
        print("\nNo jobs found.")
        return

    print(f"\nFound {len(jobs)} jobs (after filtering) in {elapsed:.1f}s:\n")
    for i, job in enumerate(jobs, 1):
        remote_tag = " [REMOTE]" if job.is_remote else ""
        print(f"{'=' * 60}")
        print(f"  [{i}] {job.title}{remote_tag}")
        print(f"      Company:  {job.company_name}")
        print(f"      Location: {job.location or ('Remote' if job.is_remote else 'N/A')}")
        print(f"      Pay:      {job.compensation or 'N/A'}")
        print(f"      Posted:   {job.date_posted or 'N/A'}")
        print(f"      URL:      {job.job_url}")

    if output_file:
        if output_file.lower().endswith(".csv"):
            save_jobs_to_csv(jobs, output_file)
            print(f"\nResults saved to {output_file} (CSV)")
        else:
            import dataclasses
            with open(output_file, "w") as f:
                json.dump([dataclasses.asdict(j) for j in jobs], f, indent=2, default=str)
            print(f"\nResults saved to {output_file} (JSON)")

    print(f"\nTotal jobs scraped: {len(jobs)} in {elapsed:.1f}s")


if __name__ == "__main__":
    interactive_menu()
