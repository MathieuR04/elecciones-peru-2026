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
  - data/candidatos.json (elected candidates per qualifying party)

Usage:
  python3 scrapping/scrapper.py
"""

import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from collections import defaultdict

import pandas as pd
import requests

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRAPPING_CSV = Path("scrapping/resultados_scrapping.csv")
OUTPUT_CSV    = Path("data/resultados.csv")
OUTPUT_JSON   = Path("data/config.json")
HISTORICO     = Path("data/historico.csv")
CANDIDATOS    = Path("data/candidatos.json")

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
    url = (f"{BASE}/resumen-general/totales"
           f"?idEleccion={id_eleccion}&tipoFiltro=distrito_electoral&idDistritoElectoral={dist_id}")
    data = get(url)
    if not data or not data.get("success"):
        return 0, 0
    d = data.get("data", {})
    return int(d.get("contabilizadas", 0) or 0), int(d.get("totalActas", 0) or 0)

def fetch_actas_nacional():
    url = (f"{BASE}/resumen-general/totales"
           f"?idEleccion={ID_SEN_NACIONAL}&tipoFiltro=eleccion")
    data = get(url)
    if not data or not data.get("success"):
        return 0, 0
    d = data.get("data", {})
    return int(d.get("contabilizadas", 0) or 0), int(d.get("totalActas", 0) or 0)


# ── D'Hondt (for determining qualifying parties) ──────────────────────────────

EXCL = {"VOTOS EN BLANCO", "VOTOS NULOS"}

def dhondt(vm, seats):
    if not seats: return {}
    ps = [p for p in vm if vm[p] > 0]
    if not ps: return {}
    qs = [(vm[p]/d, p) for p in ps for d in range(1, seats+1)]
    qs.sort(reverse=True)
    res = {p: 0 for p in ps}
    for _, p in qs[:seats]:
        res[p] += 1
    return res

def compute_qualifying_parties(all_rows):
    """Run D'Hondt to find which parties pass threshold for each cargo."""
    result = {}
    for cargo, seat_thr in [("diputado", 7), ("senador", 3)]:
        valid = [r for r in all_rows if r["cargo"] == cargo and r["party"] not in EXCL]
        pv = defaultdict(int)
        for r in valid:
            pv[r["party"]] += int(r["votes"] or 0)
        grand = sum(pv.values())
        if not grand:
            result[cargo] = set()
            continue
        pct5 = {p for p in pv if pv[p]/grand >= 0.05}

        # Group by dept
        cs = defaultdict(lambda: {"seats": 0, "votes": defaultdict(int)})
        for r in valid:
            d = r["dept"]
            cs[d]["seats"] = int(r["seats"])
            cs[d]["votes"][r["party"]] += int(r["votes"] or 0)

        first = defaultdict(int)
        for d, data in cs.items():
            res = dhondt(data["votes"], data["seats"])
            for p, s in res.items():
                first[p] += s

        seat_ok = {p for p in first if first[p] >= seat_thr}
        qual = pct5 & seat_ok
        result[cargo] = qual
        print(f"  Qualifying {cargo}: {', '.join(sorted(qual))}")
    return result


# ── Party code fetcher ────────────────────────────────────────────────────────

# Hardcoded party codes (from DevTools inspection) — used as fallback
PARTY_CODES_FALLBACK = {
    "FUERZA POPULAR": 8,
    "RENOVACIÓN POPULAR": 35,
    "AHORA NACIÓN - AN": 2,
    "PARTIDO DEL BUEN GOBIERNO": 16,
    "PARTIDO CÍVICO OBRAS": 14,
    "PARTIDO PAÍS PARA TODOS": 23,
    "JUNTOS POR EL PERÚ": 10,
    "ALIANZA PARA EL PROGRESO": 4,
    "PODEMOS PERÚ": 32,
    "PARTIDO DEMOCRÁTICO SOMOS PERÚ": 20,
    "PARTIDO POLÍTICO NACIONAL PERÚ LIBRE": 13,
    "PARTIDO APRISTA PERUANO": 12,
    "AVANZA PAÍS - PARTIDO DE INTEGRACIÓN SOCIAL": 7,
}

def fetch_party_codes(id_eleccion, tipo_filtro):
    """Fetch the list of parties with their codigoAgrupacionPolitica."""
    if id_eleccion == ID_SEN_NACIONAL:
        url = f"{BASE}/senadores-distrito-unico/organizacion-politica?idEleccion={id_eleccion}&tipoFiltro={tipo_filtro}"
    elif id_eleccion == ID_SEN_REGIONAL:
        url = f"{BASE}/senadores-distrital-multiple/organizacion-politica?idEleccion={id_eleccion}&tipoFiltro={tipo_filtro}"
    else:
        url = f"{BASE}/eleccion-diputado/organizacion-politica?idEleccion={id_eleccion}&tipoFiltro={tipo_filtro}"
    data = get(url)
    if data and data.get("success"):
        return {
            p.get("nombreAgrupacionPolitica","").strip(): p.get("codigoAgrupacionPolitica")
            for p in data.get("data", [])
            if p.get("nombreAgrupacionPolitica","").strip()
        }
    print(f"  [INFO] Using hardcoded party codes as fallback")
    return PARTY_CODES_FALLBACK


# ── Candidate fetchers ────────────────────────────────────────────────────────

def get_silent(url):
    """Single attempt fetch, returns None silently on failure."""
    try:
        r = SESSION.get(url, timeout=15)
        r.raise_for_status()
        if not r.content:
            return None
        return r.json()
    except Exception:
        return None

# Cache all candidates per diputados district
def fetch_candidates_dip(dist_id, party_code):
    """Fetch top candidates for a party in a diputados district."""
    url = (f"{BASE}/eleccion-diputado/participantes-por-candidato-nombre"
           f"?idAgrupacionPolitica={party_code}&idDistritoElectoral={dist_id}"
           f"&idEleccion={ID_DIPUTADOS}&tipoFiltro=distrito_electoral")
    data = get_silent(url)
    if not data or not data.get("success") or not data.get("data"):
        return []
    return [
        {"name": c.get("nombreCandidato","").strip(),
         "votes": int(c.get("totalVotosEmitidos", 0) or 0),
         "lista": c.get("lista", "")}
        for c in data.get("data", [])
        if c.get("nombreCandidato","").strip()
    ]

def fetch_candidates_sen_regional(dist_id, party_code):
    """Fetch top candidates for a party in a senadores regional district."""
    url = (f"{BASE}/senadores-distrital-multiple/participantes-candidato-organizacion"
           f"?idAgrupacionPolitica={party_code}&idDistritoElectoral={dist_id}"
           f"&idEleccion={ID_SEN_REGIONAL}&tipoFiltro=distrito_electoral")
    data = get_silent(url)
    if not data or not data.get("success") or not data.get("data"):
        return []
    return [
        {"name": c.get("nombreCandidato","").strip(),
         "votes": int(c.get("totalVotosValidos", 0) or c.get("totalVotosEmitidos", 0) or 0),
         "lista": c.get("lista", "")}
        for c in data.get("data", [])
        if c.get("nombreCandidato","").strip()
    ]

def fetch_candidates_sen_nacional(party_code):
    """Fetch all candidates for a party in senadores nacional."""
    url = (f"{BASE}/senadores-distrito-unico/participantes-realizar-busqueda"
           f"?idEleccion={ID_SEN_NACIONAL}&tipoFiltro=eleccion"
           f"&idAgrupacionPolitica={party_code}")
    data = get(url)
    if not data or not data.get("success"):
        return []
    return [
        {"name": c.get("nombreCandidato","").strip(),
         "votes": int(c.get("totalVotosValidos", 0) or 0),
         "lista": c.get("lista", "")}
        for c in data.get("data", [])
        if c.get("nombreCandidato","").strip()
    ]


# ── Process 4: candidatos.json ────────────────────────────────────────────────

def process_candidatos(all_rows, districts, seats, qualifying):
    """
    For each qualifying party, fetch candidate preferential votes per
    circumscription. Then determine which candidates are elected based
    on how many seats their party won via D'Hondt.

    Output structure:
    {
      "diputado": {
        "Amazonas": {
          "FUERZA POPULAR": {
            "seats_won": 1,
            "candidates": [{"name": "...", "votes": 123, "elected": true}, ...]
          }
        }
      },
      "senador": { ... }
    }
    """
    print("=" * 60)
    print("PROCESS 4: Fetching candidates")
    print("=" * 60)

    output = {"diputado": {}, "senador": {}}

    # ── Diputados ──────────────────────────────────────────────────────────────
    qual_dip = qualifying.get("diputado", set())
    if qual_dip:
        dip_codes = PARTY_CODES_FALLBACK

        # Compute D'Hondt seats per dept for diputados
        dip_dept_seats = compute_dept_seats(all_rows, "diputado", qual_dip)

        for dist_id, dist_name in districts:
            dept = norm(dist_name)
            dept_result = dip_dept_seats.get(dept, {})
            if not dept_result:
                continue

            output["diputado"][dept] = {}
            for party in qual_dip:
                seats_won = dept_result.get(party, 0)
                code = dip_codes.get(party)
                if not code:
                    # try partial match
                    for k, v in dip_codes.items():
                        if party.upper() in k.upper() or k.upper() in party.upper():
                            code = v
                            break
                if not code:
                    output["diputado"][dept][party] = {"seats_won": seats_won, "candidates": []}
                    continue

                candidates = fetch_candidates_dip(dist_id, code)
                candidates.sort(key=lambda x: -x["votes"])
                for i, c in enumerate(candidates):
                    c["elected"] = seats_won > 0 and i < seats_won
                output["diputado"][dept][party] = {
                    "seats_won": seats_won,
                    "candidates": candidates
                }
            print(f"  dip / {dept} done")
    else:
        print("  No qualifying diputado parties yet")

    # ── Senadores regional ─────────────────────────────────────────────────────
    qual_sen = qualifying.get("senador", set())
    if qual_sen:
        sen_reg_codes = PARTY_CODES_FALLBACK
        sen_nac_codes = PARTY_CODES_FALLBACK

        sen_dept_seats = compute_dept_seats(all_rows, "senador", qual_sen)

        output["senador"] = {}

        for dist_id, dist_name in districts:
            dept = norm(dist_name)
            seat_count = seats.get(("senador", dept), 0)
            if seat_count == 0:
                continue
            dept_result = sen_dept_seats.get(dept, {})
            output["senador"][dept] = {}

            for party in qual_sen:
                seats_won = dept_result.get(party, 0)
                code = sen_reg_codes.get(party)
                if not code:
                    for k, v in sen_reg_codes.items():
                        if party.upper() in k.upper() or k.upper() in party.upper():
                            code = v
                            break
                if not code:
                    output["senador"][dept][party] = {"seats_won": seats_won, "candidates": []}
                    continue

                candidates = fetch_candidates_sen_regional(dist_id, code)
                candidates.sort(key=lambda x: -x["votes"])
                for i, c in enumerate(candidates):
                    c["elected"] = seats_won > 0 and i < seats_won
                output["senador"][dept][party] = {
                    "seats_won": seats_won,
                    "candidates": candidates
                }
            print(f"  sen_reg / {dept} done")

        # ── Senadores nacional ─────────────────────────────────────────────────
        nac_result = sen_dept_seats.get("Nacional", {})
        output["senador"]["Nacional"] = {}

        for party in qual_sen:
            seats_won = nac_result.get(party, 0)
            code = sen_nac_codes.get(party)
            if not code:
                for k, v in sen_nac_codes.items():
                    if party.upper() in k.upper() or k.upper() in party.upper():
                        code = v
                        break
            if not code:
                output["senador"]["Nacional"][party] = {"seats_won": seats_won, "candidates": []}
                continue

            candidates = fetch_candidates_sen_nacional(code)
            candidates.sort(key=lambda x: -x["votes"])
            for i, c in enumerate(candidates):
                c["elected"] = seats_won > 0 and i < seats_won
            output["senador"]["Nacional"][party] = {
                "seats_won": seats_won,
                "candidates": candidates
            }
            time.sleep(DELAY)
        print(f"  sen_nac / Nacional done")
    else:
        print("  No qualifying senador parties yet")

    CANDIDATOS.parent.mkdir(parents=True, exist_ok=True)
    with open(CANDIDATOS, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"✓ Wrote {CANDIDATOS}")
    return output


def compute_dept_seats(all_rows, cargo, qual):
    """Run D'Hondt per dept with only qualifying parties, return seats dict."""
    valid = [r for r in all_rows if r["cargo"] == cargo and r["party"] not in EXCL]
    cs = defaultdict(lambda: {"seats": 0, "votes": defaultdict(int)})
    for r in valid:
        d = r["dept"]
        cs[d]["seats"] = int(r["seats"])
        cs[d]["votes"][r["party"]] += int(r["votes"] or 0)

    result = {}
    for dept, data in cs.items():
        fv = {p: v for p, v in data["votes"].items() if p in qual}
        result[dept] = dhondt(fv, data["seats"])
    return result


# ── Process 1: resultados.csv ─────────────────────────────────────────────────

def process_resultados(districts, seats):
    print("=" * 60)
    print("PROCESS 1: Scraping votes")
    print("=" * 60)
    all_rows = []

    for dist_id, dist_name in districts:
        dept = norm(dist_name)
        seat_count = seats.get(("diputado", dept), 0)
        print(f"  diputado / {dept} (id={dist_id}, seats={seat_count})")
        for v in fetch_diputados_votes(dist_id):
            all_rows.append({"cargo":"diputado","dept":dept,"seats":seat_count,
                             "party":v["party"],"votes":v["votes"]})
        time.sleep(DELAY)

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

    total_cont_dip, total_actas_dip = 0, 0
    for dist_id, dist_name in districts:
        cont, total = fetch_actas_regional(dist_id, ID_DIPUTADOS)
        total_cont_dip += cont
        total_actas_dip += total
        time.sleep(DELAY)
    dip_pct = round(total_cont_dip / total_actas_dip * 100, 3) if total_actas_dip > 0 else None
    print(f"  Diputados: {total_cont_dip}/{total_actas_dip} = {dip_pct}%")

    total_cont_sen, total_actas_sen = 0, 0
    for dist_id, dist_name in districts:
        cont, total = fetch_actas_regional(dist_id, ID_SEN_REGIONAL)
        total_cont_sen += cont
        total_actas_sen += total
        time.sleep(DELAY)

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

    cargo_rows = [r for r in all_rows if r["cargo"] == cargo]

    write_header = not HISTORICO.exists() or HISTORICO.stat().st_size == 0
    with open(HISTORICO, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["pct_escrutado", "cargo", "dept", "seats", "partido", "votes"])
        for row in cargo_rows:
            writer.writerow([pct, cargo, row["dept"], row["seats"], row["party"], row["votes"]])
    print(f"  ✓ Appended {cargo} snapshot at {pct}% ({len(cargo_rows)} rows)")


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
    print()
    print("PROCESS 3: Computing qualifying parties")
    qualifying = compute_qualifying_parties(all_rows)
    print()
    process_candidatos(all_rows, districts, seats, qualifying)


if __name__ == "__main__":
    main()
