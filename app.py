# app.py - GlassdoorV3 Flask API wrapper for Fly.io
# Exposes the SeleniumBase CDP scraper as an HTTP API.

from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import time
import dataclasses
from datetime import datetime

from scraper import GlassdoorScraper, save_jobs_to_csv

app = Flask(__name__)
CORS(app)

SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY")
if not SCRAPER_API_KEY:
    raise RuntimeError("SCRAPER_API_KEY env var is not set — add it via: fly secrets set SCRAPER_API_KEY=...")


# ---------------------------------------------------------------------------
# Health / info routes
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "online",
        "service": "GlassdoorV3 Scraper API",
        "version": "3.0.0 (SeleniumBase CDP + Multithreaded)",
        "description": "Scrapes Glassdoor jobs via SeleniumBase UC/CDP bot bypass",
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()}), 200


# ---------------------------------------------------------------------------
# Main scrape endpoint
# ---------------------------------------------------------------------------

@app.route("/scrape", methods=["POST"])
def scrape():
    try:
        # Auth
        api_key = request.headers.get("X-API-Key") or (request.json or {}).get("api_key")
        if api_key != SCRAPER_API_KEY:
            return jsonify({"success": False, "error": "Invalid API key"}), 401

        data = request.json or {}
        search_term       = data.get("keyword", "").strip()
        results_wanted    = min(int(data.get("results", 20)), 200)
        is_remote         = bool(data.get("remote_only", False))
        easy_apply        = bool(data.get("easy_apply", False))
        fetch_desc        = bool(data.get("fetch_descriptions", True))
        desc_workers      = min(int(data.get("threads", 8)), 15)
        hours_old         = data.get("hours_old")
        if hours_old is not None:
            hours_old = int(hours_old)

        if not search_term:
            return jsonify({"success": False, "error": "keyword is required"}), 400

        print(f"[API] /scrape — keyword='{search_term}', results={results_wanted}, "
              f"remote_only={is_remote}, threads={desc_workers}")

        scraper = GlassdoorScraper(headless=True, description_workers=desc_workers)

        start = time.time()
        raw_jobs = scraper.scrape(
            search_term=search_term,
            location="",
            results_wanted=results_wanted,
            hours_old=hours_old,
            is_remote=is_remote,
            easy_apply=easy_apply,
            fetch_descriptions=fetch_desc,
        )
        elapsed = round(time.time() - start, 2)

        # Serialize dataclass objects to plain dicts
        jobs = [dataclasses.asdict(j) for j in raw_jobs]

        return jsonify({
            "success": True,
            "count": len(jobs),
            "elapsed_seconds": elapsed,
            "timestamp": datetime.now().isoformat(),
            "jobs": jobs,
        })

    except Exception as exc:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"[STARTUP] GlassdoorV3 API listening on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
