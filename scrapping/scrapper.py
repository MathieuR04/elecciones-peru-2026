"""
scrapper.py
-----------
Two separate processes:

1. Scrape all circumscriptions from scrapping/resultados_scrapping.csv
   -> writes data/resultados.csv

2. Scrape % actas contabilizadas from scrapping/contabilizado.csv
   -> writes data/config.json  (sen_pct, dip_pct, updated_at)

Usage
-----
  # Live (production):
  python3 scrapping/scrapper.py

  # Local test (reads HTML files from a folder instead of fetching URLs):
  python3 scrapping/scrapper.py --local /path/to/onpe_capturas

  In local mode the filename is derived from the last URL segment:
    https://.../ReCng/D44001  ->  D44001.html
    https://.../GenRl         ->  GenRl.html
"""

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRAPPING_CSV    = Path("scrapping/resultados_scrapping.csv")
CONTABILIZADO_CSV = Path("scrapping/contabilizado.csv")
OUTPUT_CSV       = Path("data/resultados.csv")
OUTPUT_JSON      = Path("data/config.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
REQUEST_DELAY   = 1.5
REQUEST_TIMEOUT = 30


# ── Fetch / load ──────────────────────────────────────────────────────────────

def load_local(url, local_dir):
    filename = url.rstrip("/").split("/")[-1] + ".html"
    filepath = local_dir / filename
    if not filepath.exists():
        print(f"  [ERROR] Local file not found: {filepath}", file=sys.stderr)
        return None
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        return BeautifulSoup(f, "html.parser")


def fetch_remote(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        print(f"  [ERROR] Could not fetch {url}: {exc}", file=sys.stderr)
        return None


def get_soup(url, local_dir, delay=True):
    if local_dir:
        return load_local(url, local_dir)
    else:
        soup = fetch_remote(url)
        if delay:
            time.sleep(REQUEST_DELAY)
        return soup


# ── Vote parsing ──────────────────────────────────────────────────────────────

def parse_votes(raw):
    cleaned = raw.replace(",", "").replace(".", "").strip()
    try:
        return int(cleaned)
    except ValueError:
        return 0


# ── Scrapers: resultados ──────────────────────────────────────────────────────

def scrape_regional(soup, cargo, dept, seats):
    table = soup.find("table", id="tableRes")
    if not table:
        print(f"  [WARN] table#tableRes not found for {cargo}/{dept}", file=sys.stderr)
        return []

    SKIP = {"TOTAL DE VOTOS VÁLIDOS", "TOTAL DE VOTOS EMITIDOS"}
    RENAME = {
        "VOTOS BLANCOS": "VOTOS EN BLANCO",
        "VOTOS NULOS":   "VOTOS NULOS",
    }

    rows = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if not cells or len(cells) < 3:
            continue
        if "ORGANIZACIONES" in " ".join(cells):
            continue
        party_raw = cells[1].strip()
        if not party_raw or party_raw in SKIP:
            continue
        party = RENAME.get(party_raw, party_raw)
        votes = parse_votes(cells[2])
        rows.append({"cargo": cargo, "dept": dept, "seats": seats,
                     "party": party, "votes": votes})
    return rows


def scrape_nacional(soup, cargo, dept, seats):
    table = soup.find("table", id="table1")
    if not table:
        print(f"  [WARN] table#table1 not found for {cargo}/{dept}", file=sys.stderr)
        return []

    rows = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if not cells or len(cells) < 3:
            continue
        if "ORGANIZACIÓN" in " ".join(cells) or "VOTOS" in cells[0]:
            continue
        party = cells[1].strip()
        if not party:
            continue
        votes = parse_votes(cells[2])
        rows.append({"cargo": cargo, "dept": dept, "seats": seats,
                     "party": party, "votes": votes})
    return rows


# ── Scraper: % contabilizado ──────────────────────────────────────────────────

def extract_pct_contabilizado(soup):
    """Read % from <li>ACTAS CONTABILIZADAS: X%</li>"""
    for li in soup.find_all("li"):
        text = li.get_text(strip=True)
        m = re.match(r"ACTAS CONTABILIZADAS[:\s]+([\d.,]+)%", text, re.IGNORECASE)
        if m:
            return float(m.group(1).replace(",", "."))
    return None


# ── Process 1: resultados.csv ─────────────────────────────────────────────────

def process_resultados(df, local_dir):
    print("=" * 60)
    print("PROCESS 1: Scraping resultados")
    print("=" * 60)

    all_rows = []
    total = len(df)

    for i, row in df.iterrows():
        cargo = str(row["cargo"]).strip()
        dept  = str(row["dept"]).strip()
        seats = int(row["seats"])
        url   = str(row["url"]).strip()

        print(f"[{i+1}/{total}] {cargo:10} / {dept:20} -> {url.split('/')[-1]}")

        soup = get_soup(url, local_dir)
        if soup is None:
            print("  [SKIP]")
            continue

        is_nacional = "GenRl" in url or dept.lower() == "nacional"

        if is_nacional:
            rows = scrape_nacional(soup, cargo, dept, seats)
        else:
            rows = scrape_regional(soup, cargo, dept, seats)

        print(f"  -> {len(rows)} parties")
        all_rows.extend(rows)

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["cargo", "dept", "seats", "party", "votes"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nWrote {len(all_rows)} rows -> {OUTPUT_CSV}\n")


# ── Process 2: config.json ────────────────────────────────────────────────────

def process_contabilizado(df_cont, local_dir):
    print("=" * 60)
    print("PROCESS 2: Scraping % contabilizado")
    print("=" * 60)

    pct = {"diputado": None, "senador": None}

    for _, row in df_cont.iterrows():
        cargo = str(row["cargo"]).strip().lower()
        url   = str(row["url"]).strip()

        print(f"  {cargo:10} -> {url.split('/')[-1]}")

        soup = get_soup(url, local_dir, delay=False)
        if soup is None:
            print("  [SKIP]")
            continue

        val = extract_pct_contabilizado(soup)
        print(f"  -> % contabilizadas: {val}")

        if cargo in pct:
            pct[cargo] = val

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "sen_pct":    round(pct["senador"],  3) if pct["senador"]  is not None else None,
        "dip_pct":    round(pct["diputado"], 3) if pct["diputado"] is not None else None,
        "updated_at": datetime.now().strftime("%H:%M"),
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    print(f"\nWrote config -> {OUTPUT_JSON}: {config}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ONPE results scraper")
    parser.add_argument(
        "--local",
        metavar="DIR",
        default=None,
        help="Read HTML files from this local directory instead of fetching live URLs.",
    )
    args = parser.parse_args()

    local_dir = Path(args.local) if args.local else None
    if local_dir and not local_dir.is_dir():
        sys.exit(f"[FATAL] --local path is not a directory: {local_dir}")

    print(f"Mode: {'LOCAL (' + str(local_dir) + ')' if local_dir else 'LIVE'}\n")

    # Load resultados_scrapping.csv
    if not SCRAPPING_CSV.exists():
        sys.exit(f"[FATAL] {SCRAPPING_CSV} not found. Run from repo root.")
    df = pd.read_csv(SCRAPPING_CSV)
    url_col = next(
        (c for c in df.columns if c.lower() == "url" or c.startswith("Unnamed")), None
    )
    if url_col is None:
        sys.exit("[FATAL] No URL column found in resultados_scrapping.csv")
    df = df.rename(columns={url_col: "url"})
    df["url"] = df["url"].str.strip()

    # Load contabilizado.csv
    if not CONTABILIZADO_CSV.exists():
        sys.exit(f"[FATAL] {CONTABILIZADO_CSV} not found. Run from repo root.")
    df_cont = pd.read_csv(CONTABILIZADO_CSV)
    df_cont.columns = [c.strip().lower() for c in df_cont.columns]

    # Run both processes
    process_resultados(df, local_dir)
    process_contabilizado(df_cont, local_dir)


if __name__ == "__main__":
    main()
