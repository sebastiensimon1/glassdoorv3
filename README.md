# GlassdoorV3 — SeleniumBase CDP Job Scraper

Scrapes job listings from [Glassdoor](https://www.glassdoor.com) using **SeleniumBase UC + CDP mode** to bypass bot detection.  
Multithreaded job description fetching for maximum speed.

---

## Features

- 🤖 **Bot bypass** via SeleniumBase UC + CDP mode (real Chrome, undetected)
- 🧵 **Multithreaded** description fetching (up to 15 threads)
- 🚫 **Title exclusion filter** (manager, senior, director, sales, etc.)
- 📧 **Email extraction** from job descriptions
- 💰 **Salary parsing** (min/max, currency, interval)
- 🌐 **Remote detection** from location + description text
- 💾 Exports to **CSV** or **JSON**
- 🌐 **Flask API** wrapper for Fly.io deployment

---

## Local CLI Usage

```bash
pip install -r requirements.txt
python scraper.py
```

You'll be prompted for:
- Job title to search
- Remote only? (y/n)
- Number of results
- Max posting age in hours
- Easy apply only?
- Fetch full descriptions?
- Thread count (1–15)
- Output file (CSV or JSON)

---

## API Usage (Fly.io / Docker)

```bash
docker build -t glassdoorv3 .
docker run -e SCRAPER_API_KEY=your-key -p 8080:8080 glassdoorv3
```

**POST** `/scrape`

```json
{
  "api_key": "your-secret-key",
  "keyword": "security analyst",
  "results": 30,
  "remote_only": false,
  "easy_apply": false,
  "fetch_descriptions": true,
  "threads": 8,
  "hours_old": 72
}
```

**GET** `/health` — health check  
**GET** `/` — service info

---

## Environment Variables

| Variable | Description |
|---|---|
| `SCRAPER_API_KEY` | **Required** — set via `fly secrets set SCRAPER_API_KEY=...` |
| `PORT` | HTTP port (default: `8080`) |
| `DISPLAY` | Virtual display for headless Chrome (default: `:99`) |

---

## Fly.io Deployment (glassdoorv3)

```bash
fly apps create glassdoorv3
fly secrets set SCRAPER_API_KEY=your-secret-key --app glassdoorv3
fly deploy --app glassdoorv3
```

### Why `performance-2x` + `4096 MB`?

| Component | RAM Usage |
|---|---|
| Chrome browser process (UC mode) | ~500–700 MB |
| Chrome renderer + V8 JS engine | ~300–500 MB |
| Multithreaded desc fetching (8×) | ~200 MB |
| Python + Flask + requests | ~150 MB |
| OS + headroom | ~250 MB |
| **Total recommended** | **4096 MB** |

**2 dedicated vCPUs** (`performance-2x`) because:
- Chrome JS rendering is CPU-bound
- 8 concurrent description threads benefit from 2 cores
- UC/CDP mode patches Chrome at startup — CPU-intensive burst
