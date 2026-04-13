"""
scrapper.py
-----------
Calls ONPE's JSON API directly.

Three data sources:
  - Diputados regional (27 districts, idEleccion=13)
  - Senadores regional (27 districts, idEleccion=14)
  - Senadores nacional (1 circumscription, idEleccion=15)

Writes:
  - data/resultados.csv
  - data/config.json  (weighted % contabilizado)
  - data/historico.csv (appends snapshot only if pct changed)

Usage:
  python3 scrapping/scrapper.py
"""

import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRAPPING_CSV = Path("scrapping/resultados_scrapping.csv")
OUTPUT_CSV    = Path("data/resultados.csv")
OUTPUT_JSON   = Path("data/config.json")
HISTORICO     = Path("data/historico.csv")

BASE = "https://resultadoelectoral.onpe.gob.pe/presentacion-backend"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://resultadoelectoral.onpe.gob.pe/main/diputados",
    "Origin": "https://resultadoelectoral.onpe.gob.pe",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}
DELAY = 0.5

ID_DIPUTADOS       = 13
ID_SEN_REGIONAL    = 14
ID_SEN_NACIONAL    = 15


# ── HTTP helper ───────────────────────────────────────────────────────────────

# Use a session to persist cookies across requests
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def get(url, retries=3):
    for i in range(retries):
        try:
            r = SESSION.get(url, timeout=20)
            r.raise_for_status()
            if not r.content:
                raise ValueError("Empty response")
            return r.json()
        except Exception as e:
            print(f"  [WARN] Attempt {i+1} failed for {url.split('?')[0]}: {e}")
            time.sleep(2)
    print(f"  [ERROR] Failed: {url}")
    return None

def warm_up_session():
    """Visit the main page first to get cookies."""
    try:
        SESSION.get("https://resultadoelectoral.onpe.gob.pe/main/diputados", timeout=20)
        time.sleep(1)
        print("  Session warmed up")
    except Exception as e:
        print(f"  [WARN] Session warmup failed: {e}")


# ── District list ─────────────────────────────────────────────────────────────

def fetch_districts():
    url = f"{BASE}/eleccion-diputado/distritos?idEleccion={ID_DIPUTADOS}&tipoFiltro=distrito_electoral"
    data = get(url)
    if data and data.get("success"):
        return [(d["codigo"], d["nombre"]) for d in data["data"]]
    # Hardcoded fallback from DevTools
    return [
        (1,"AMAZONAS"),(2,"ÁNCASH"),(3,"APURÍMAC"),(4,"AREQUIPA"),
        (5,"AYACUCHO"),(6,"CAJAMARCA"),(7,"CALLAO"),(8,"CUSCO"),
        (9,"HUANCAVELICA"),(10,"HUÁNUCO"),(11,"ICA"),(12,"JUNÍN"),
        (13,"LA LIBERTAD"),(14,"LAMBAYEQUE"),(15,"LIMA METROPOLITANA"),
        (16,"LIMA PROVINCIAS"),(17,"LORETO"),(18,"MADRE DE DIOS"),
        (19,"MOQUEGUA"),(20,"PASCO"),(21,"PIURA"),(22,"PUNO"),
        (23,"SAN MARTÍN"),(24,"TACNA"),(25,"TUMBES"),(26,"UCAYALI"),
        (27,"PERUANOS RESIDENTES EN EL EXTRANJERO"),
    ]


# ── Dept name normalizer ──────────────────────────────────────────────────────

DEPT_MAP = {
    "LIMA METROPOLITANA": "Lima",
    "LIMA PROVINCIAS": "Lima Provincias",
    "PERUANOS RESIDENTES EN EL EXTRANJERO": "PEX",
    "ÁNCASH": "Áncash", "APURÍMAC": "Apurímac", "AREQUIPA": "Arequipa",
    "AYACUCHO": "Ayacucho", "CAJAMARCA": "Cajamarca", "CALLAO": "Callao",
    "CUSCO": "Cusco", "HUANCAVELICA": "Huancavelica", "HUÁNUCO": "Huánuco",
    "ICA": "Ica", "JUNÍN": "Junín", "LA LIBERTAD": "La Libertad",
    "LAMBAYEQUE": "Lambayeque", "LORETO": "Loreto", "MADRE DE DIOS": "Madre de Dios",
    "MOQUEGUA": "Moquegua", "PASCO": "Pasco", "PIURA": "Piura", "PUNO": "Puno",
    "SAN MARTÍN": "San Martín", "TACNA": "Tacna", "TUMBES": "Tumbes",
    "UCAYALI": "Ucayali", "AMAZONAS": "Amazonas",
}

def norm(nombre):
    return DEPT_MAP.get(nombre, nombre.title())


# ── Seats loader ──────────────────────────────────────────────────────────────

def load_seats():
    df = pd.read_csv(SCRAPPING_CSV)
    url_col = next((c for c in df.columns if c.lower()=="url" or c.startswith("Unnamed")), None)
    if url_col:
        df = df.drop(columns=[url_col])
    return {(str(r["cargo"]).strip(), str(r["dept"]).strip()): int(r["seats"])
            for _, r in df.iterrows()}


# ── Vote fetchers ─────────────────────────────────────────────────────────────

def fetch_votes(url):
    data = get(url)
    if not data or not data.get("success"):
        return []
    return [
        {"party": p.get("nombreAgrupacionPolitica","").strip(),
         "votes": int(p.get("totalVotosValidos", 0) or 0)}
        for p in data.get("data", [])
        if p.get("nombreAgrupacionPolitica","").strip()
    ]


def fetch_diputados_votes(dist_id):
    return fetch_votes(
        f"{BASE}/eleccion-diputado/participantes-ubicacion-geografica-nombre"
        f"?idEleccion={ID_DIPUTADOS}&tipoFiltro=distrito_electoral&idDistritoElectoral={dist_id}"
    )


def fetch_sen_regional_votes(dist_id):
    return fetch_votes(
        f"{BASE}/senadores-distrital-multiple/participantes-ubicacion-geografica"
        f"?idDistritoElectoral={dist_id}&idEleccion={ID_SEN_REGIONAL}&tipoFiltro=distrito_electoral"
    )


def fetch_sen_nacional_votes():
    return fetch_votes(
        f"{BASE}/senadores-distrito-unico/participantes-ubicacion-geografica-nombre"
        f"?idEleccion={ID_SEN_NACIONAL}&tipoFiltro=eleccion"
    )


# ── Actas fetchers ────────────────────────────────────────────────────────────

def fetch_actas_regional(dist_id, id_eleccion):
    """Returns (contabilizadas, totalActas) for a regional district."""
    url = (f"{BASE}/resumen-general/totales"
           f"?idEleccion={id_eleccion}&tipoFiltro=distrito_electoral&idDistritoElectoral={dist_id}")
    data = get(url)
    if not data or not data.get("success"):
        return 0, 0
    d = data.get("data", {})
    return int(d.get("contabilizadas", 0) or 0), int(d.get("totalActas", 0) or 0)


def fetch_actas_nacional():
    """Returns (contabilizadas, totalActas) for nacional senadores."""
    url = (f"{BASE}/resumen-general/totales"
           f"?idEleccion={ID_SEN_NACIONAL}&tipoFiltro=eleccion")
    data = get(url)
    if not data or not data.get("success"):
        return 0, 0
    d = data.get("data", {})
    return int(d.get("contabilizadas", 0) or 0), int(d.get("totalActas", 0) or 0)


# ── Process 1: resultados.csv ─────────────────────────────────────────────────

def process_resultados(districts, seats):
    print("=" * 60)
    print("PROCESS 1: Scraping votes")
    print("=" * 60)
    all_rows = []

    # Diputados regional
    for dist_id, dist_name in districts:
        dept = norm(dist_name)
        seat_count = seats.get(("diputado", dept), 0)
        print(f"  diputado / {dept} (id={dist_id}, seats={seat_count})")
        for v in fetch_diputados_votes(dist_id):
            all_rows.append({"cargo":"diputado","dept":dept,"seats":seat_count,
                             "party":v["party"],"votes":v["votes"]})
        time.sleep(DELAY)

    # Senadores regional
    for dist_id, dist_name in districts:
        dept = norm(dist_name)
        seat_count = seats.get(("senador", dept), 0)
        if seat_count == 0:
            continue
        print(f"  senador  / {dept} (id={dist_id}, seats={seat_count})")
        for v in fetch_sen_regional_votes(dist_id):
            all_rows.append({"cargo":"senador","dept":dept,"seats":seat_count,
                             "party":v["party"],"votes":v["votes"]})
        time.sleep(DELAY)

    # Senadores nacional
    seat_count = seats.get(("senador", "Nacional"), 30)
    print(f"  senador  / Nacional (seats={seat_count})")
    for v in fetch_sen_nacional_votes():
        all_rows.append({"cargo":"senador","dept":"Nacional","seats":seat_count,
                         "party":v["party"],"votes":v["votes"]})

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["cargo","dept","seats","party","votes"])
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"\n✓ Wrote {len(all_rows)} rows -> {OUTPUT_CSV}")
    return all_rows


# ── Process 2: config.json ────────────────────────────────────────────────────

def process_contabilizado(districts):
    print("=" * 60)
    print("PROCESS 2: Computing % contabilizado")
    print("=" * 60)

    # Diputados: weighted sum across regional districts (skip PEX=27, no mesa endpoint)
    total_cont_dip, total_actas_dip = 0, 0
    for dist_id, dist_name in districts:
        if dist_id == 27:
            continue
        cont, total = fetch_actas_regional(dist_id, ID_DIPUTADOS)
        total_cont_dip += cont
        total_actas_dip += total
        time.sleep(DELAY)
    dip_pct = round(total_cont_dip / total_actas_dip * 100, 3) if total_actas_dip > 0 else None
    print(f"  Diputados: {total_cont_dip}/{total_actas_dip} = {dip_pct}%")

    # Senadores regional: same districts (skip PEX=27)
    total_cont_sen, total_actas_sen = 0, 0
    for dist_id, dist_name in districts:
        if dist_id == 27:
            continue
        cont, total = fetch_actas_regional(dist_id, ID_SEN_REGIONAL)
        total_cont_sen += cont
        total_actas_sen += total
        time.sleep(DELAY)

    # Senadores nacional: add nacional actas
    cont_nac, total_nac = fetch_actas_nacional()
    total_cont_sen += cont_nac
    total_actas_sen += total_nac
    sen_pct = round(total_cont_sen / total_actas_sen * 100, 3) if total_actas_sen > 0 else None
    print(f"  Senadores: {total_cont_sen}/{total_actas_sen} = {sen_pct}%")

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "sen_pct": sen_pct,
        "dip_pct": dip_pct,
        "updated_at": datetime.now().strftime("%H:%M"),
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"✓ Wrote {OUTPUT_JSON}: {config}")
    return dip_pct, sen_pct


# ── Process 3: historico.csv ──────────────────────────────────────────────────

def append_historico(pct, cargo, all_rows):
    if pct is None:
        return

    HISTORICO.parent.mkdir(parents=True, exist_ok=True)

    # Check last pct for this cargo — skip if unchanged
    if HISTORICO.exists():
        last_pct = None
        with open(HISTORICO, "r") as f:
            for line in reversed(f.readlines()[1:]):
                parts = line.split(",")
                if len(parts) >= 2 and parts[1].strip() == cargo:
                    try:
                        last_pct = float(parts[0])
                    except ValueError:
                        pass
                    break
        if last_pct == pct:
            print(f"  [SKIP] {cargo} pct unchanged ({pct}%)")
            return

    # Aggregate total votes per party for this cargo
    votes_by_party = {}
    for row in all_rows:
        if row["cargo"] == cargo:
            p = row["party"]
            votes_by_party[p] = votes_by_party.get(p, 0) + int(row["votes"])

    write_header = not HISTORICO.exists() or HISTORICO.stat().st_size == 0
    with open(HISTORICO, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["pct_escrutado", "cargo", "partido", "votes"])
        for party, votes in votes_by_party.items():
            writer.writerow([pct, cargo, party, votes])
    print(f"  ✓ Appended {cargo} snapshot at {pct}%")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Mode: LIVE\n")
    print("Warming up session...")
    warm_up_session()
    districts = fetch_districts()
    print(f"Districts: {len(districts)}")
    seats = load_seats()

    all_rows = process_resultados(districts, seats)
    print()
    dip_pct, sen_pct = process_contabilizado(districts)
    print()
    append_historico(dip_pct, "diputado", all_rows)
    append_historico(sen_pct, "senador", all_rows)


if __name__ == "__main__":
    main()
