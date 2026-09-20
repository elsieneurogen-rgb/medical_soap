#!/usr/bin/env python3
"""
pgx_soap/server.py — Servidor Flask (produção) integrando PubMed + PharmGKB + ClinPGx/CPIC.
Backend: PostgreSQL via psycopg2. Frontend estático servido pelo mesmo (ou via nginx).
"""
import os
import json
import time
import re
from flask import Flask, request, jsonify, send_from_directory, g
from flask_cors import CORS
from Bio import Entrez
import requests
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)

# ── Configuração ────────────────────────────────────────────────
ENTREZ_EMAIL = os.getenv("ENTREZ_EMAIL", "medico@hospital.br")
ENTREZ_API_KEY = os.getenv("ENTREZ_API_KEY", "")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "soap_psiquiatria")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "")

DB_DSN = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASS}"

print(f"""
╔══════════════════════════════════════════════════════════╗
║  SOAP Psiquiatria — Servidor PGx (Produção)              ║
║  Backend: PostgreSQL {DB_HOST}:{DB_PORT}/{DB_NAME}       ║
║  PubMed + PharmGKB + ClinPGx/CPIC                        ║
╚══════════════════════════════════════════════════════════╝
""")

# ── PostgreSQL helpers ─────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(DB_DSN)
        g.db.autocommit = True
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def query_db(query, params=None, one=False):
    conn = get_db()
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
        if one:
            return dict(rows[0]) if rows else None
        return [dict(r) for r in rows]

def run_db(query, params=None):
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(query, params)

# ── Inicialização do schema ────────────────────────────────────

def init_schema():
    run_db("""
        CREATE TABLE IF NOT EXISTS patients (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            dob TEXT,
            sex TEXT,
            identifier TEXT,
            allergies TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    run_db("""
        CREATE TABLE IF NOT EXISTS soap_notes (
            id SERIAL PRIMARY KEY,
            patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
            session_date TEXT NOT NULL,
            psychiatrist TEXT,
            subjective JSONB,
            objective JSONB,
            objective_vitals TEXT,
            assessment JSONB,
            plan JSONB,
            signature JSONB,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    run_db("CREATE INDEX IF NOT EXISTS idx_notes_patient ON soap_notes(patient_id)")

with app.app_context():
    try:
        init_schema()
        print("[OK] Schema PostgreSQL verificado/criado.")
    except Exception as e:
        print(f"[ERRO] Falha ao inicializar schema: {e}")

# ── PubMed / E-utilities ───────────────────────────────────────

def entrez_get(path: str, params: dict, sleep: float = 0.35):
    time.sleep(sleep)
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/{path}"
    if ENTREZ_API_KEY:
        params["api_key"] = ENTREZ_API_KEY
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    ct = resp.headers.get("content-type", "")
    return resp.json() if "application/json" in ct else resp.text

def search_pubmed(term: str, max_results: int = 20) -> list[dict]:
    params = {"db": "pubmed", "term": term, "retmax": max_results, "retmode": "xml"}
    data = entrez_get("esearch.fcgi", params)
    id_list = data.get("esearchresult", {}).get("idlist", [])
    if not id_list:
        return []
    results = []
    for i in range(0, len(id_list), 50):
        chunk = id_list[i:i+50]
        time.sleep(0.35)
        raw = entrez_get("efetch.fcgi", {
            "db": "pubmed", "id": ",".join(chunk),
            "rettype": "abstract", "retmode": "text"
        })
        results.extend(_parse_medline(raw))
    return results[:max_results]

def _parse_medline(text: str) -> list[dict]:
    articles = []
    current = {}
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped == "":
            if current:
                articles.append({k: v for k, v in current.items() if v})
                current = {}
            continue
        if stripped.startswith("PMID-"):
            current["pmid"] = stripped.split("-", 1)[1].strip()
        elif stripped.startswith("TI  -"):
            current["title"] = stripped.split("-", 1)[1].strip()
        elif stripped.startswith("AB  -"):
            current["abstract"] = stripped.split("-", 1)[1].strip()
        elif stripped.startswith("MHDA-") or stripped.startswith("DA  -"):
            current["date"] = stripped.split("-", 1)[1].strip()
    if current:
        articles.append({k: v for k, v in current.items() if v})
    return articles

def pubmed_summary(pmid: str) -> dict | None:
    params = {"db": "pubmed", "id": pmid, "rettype": "abstract", "retmode": "text"}
    raw = entrez_get("efetch.fcgi", params)
    arts = _parse_medline(raw)
    return arts[0] if arts else None

def pubmed_gene_link(gene: str) -> list[str]:
    try:
        params = {"dbfrom": "gene", "db": "pubmed", "id": gene, "retmode": "xml"}
        data = entrez_get("elink.fcgi", params, sleep=0.35)
        pmids = []
        for linkset in data.get("linksets", []):
            for link in linkset.get("link", []):
                pmids.append(link.get("id", ""))
        return pmids[:30]
    except Exception:
        return []

# ── PharmGKB API ───────────────────────────────────────────────

def pharmgkb_gene(gene: str) -> dict | None:
    time.sleep(0.4)
    resp = requests.get(f"https://api.pharmgkb.org/v1/genes/{gene}", timeout=15)
    return resp.json() if resp.status_code == 200 else None

def pharmgkb_drug(drug: str) -> dict | None:
    time.sleep(0.4)
    resp = requests.get(f"https://api.pharmgkb.org/v1/drugs/search?query={drug}", timeout=15)
    if resp.status_code == 200:
        data = resp.json()
        return data.get("data", [{}])[0] if data.get("data") else None
    return None

def pharmgkb_gene_drug(gene: str, drug: str) -> dict | None:
    time.sleep(0.4)
    resp = requests.get(
        f"https://api.pharmgkb.org/v1/geneDrugPairs/search?query={gene}%20{drug}",
        timeout=15
    )
    if resp.status_code == 200:
        data = resp.json()
        return data.get("data", [{}])[0] if data.get("data") else None
    return None

# ── ClinPGx / CPIC ──────────────────────────────────────────────

def _scrape_cpic_guidelines() -> list[dict]:
    try:
        time.sleep(0.5)
        resp = requests.get(
            "https://www.clinpgx.org/cpic/guidelines",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if resp.status_code != 200:
            return []
        html = resp.text
        pattern = r'href="(/cpic/guidelines/[^"]+)"[^>]*>([^<]+)</a>'
        matches = re.findall(pattern, html)
        guidelines = []
        for url_path, title in matches:
            if "all-guidelines" in url_path:
                continue
            parts = title.strip().split(" and ")
            if len(parts) >= 1:
                gene = parts[0].strip()
                drug = parts[1].strip() if len(parts) > 1 else ""
                guidelines.append({
                    "gene": gene, "drug": drug,
                    "title": title.strip(),
                    "url": f"https://www.clinpgx.org{url_path}",
                    "source": "CPIC", "level": ""
                })
        return guidelines
    except Exception as e:
        print(f"[ClinPGx] Erro: {e}")
        return []

_cpic_cache = {"data": None, "fetched_at": 0}

def get_cpic_guidelines() -> list[dict]:
    import time as _t
    now = _t.time()
    if _cpic_cache["data"] and (now - _cpic_cache["fetched_at"]) < 86400:
        return _cpic_cache["data"]
    data = _scrape_cpic_guidelines()
    _cpic_cache = {"data": data, "fetched_at": now}
    return data

def cpic_for_gene_drug(gene: str, drug: str) -> dict | None:
    guidelines = get_cpic_guidelines()
    gene_u, drug_u = gene.upper(), drug.upper()
    for g in guidelines:
        if (g["gene"].upper() == gene_u or gene_u in g["gene"].upper()) and \
           (g["drug"].upper() == drug_u or drug_u in g["drug"].upper() or g["drug"] == ""):
            return g
    return None

def cpic_for_drug(drug: str) -> list[dict]:
    guidelines = get_cpic_guidelines()
    drug_u = drug.upper()
    return [g for g in guidelines if drug_u in g["drug"].upper() or g["drug"] == ""]

def cpic_for_gene(gene: str) -> list[dict]:
    guidelines = get_cpic_guidelines()
    gene_u = gene.upper()
    return [g for g in guidelines if gene_u in g["gene"].upper()]

# ── API: Pacientes ──────────────────────────────────────────────

@app.route("/api/patients", methods=["GET"])
def api_list_patients():
    patients = query_db("SELECT id, name, dob, sex, identifier, allergies, created_at FROM patients ORDER BY name")
    return jsonify(patients)

@app.route("/api/patients", methods=["POST"])
def api_create_patient():
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "nome é obrigatório"}), 400
    run_db(
        "INSERT INTO patients (name, dob, sex, identifier, allergies) VALUES (%s,%s,%s,%s,%s) RETURNING id, name, dob, sex, identifier, allergies, created_at",
        [data.get("name"), data.get("dob"), data.get("sex"), data.get("identifier"), data.get("allergies")]
    )
    row = query_db("SELECT currval('patients_id_seq')")[0]
    return jsonify({
        "id": row["currval"],
        "name": data["name"],
        "dob": data.get("dob"),
        "sex": data.get("sex"),
        "identifier": data.get("identifier"),
        "allergies": data.get("allergies"),
        "created_at": row["currval"] and time.strftime("%Y-%m-%d %H:%M:%S")
    }), 201

@app.route("/api/patients/<int:pid>", methods=["DELETE"])
def api_delete_patient(pid):
    run_db("DELETE FROM soap_notes WHERE patient_id = %s", [pid])
    run_db("DELETE FROM patients WHERE id = %s", [pid])
    return jsonify({"deleted": pid})

# ── API: Notas SOAP ────────────────────────────────────────────

@app.route("/api/patients/<int:pid>/notes", methods=["GET"])
def api_list_notes(pid):
    notes = query_db(
        "SELECT id, patient_id, session_date, psychiatrist, subjective, objective, objective_vitals, assessment, plan, signature, created_at, updated_at FROM soap_notes WHERE patient_id = %s ORDER BY session_date DESC",
        [pid]
    )
    return jsonify(notes)

@app.route("/api/notes", methods=["POST"])
def api_create_note():
    data = request.get_json()
    if not data or not data.get("patient_id"):
        return jsonify({"error": "patient_id é obrigatório"}), 400

    run_db(
        """INSERT INTO soap_notes
           (patient_id, session_date, psychiatrist, subjective, objective, objective_vitals, assessment, plan, signature)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        [
            data["patient_id"],
            data.get("session_date"),
            data.get("psychiatrist"),
            json.dumps(data.get("subjective", {})),
            json.dumps(data.get("objective", {})),
            data.get("objective", {}).get("vitals", "") if data.get("objective") else "",
            json.dumps(data.get("assessment", {})),
            json.dumps(data.get("plan", {})),
            json.dumps(data.get("signature", {})),
        ]
    )
    row = query_db("SELECT currval('soap_notes_id_seq')")[0]
    return jsonify({"id": row["currval"], "patient_id": data["patient_id"]}), 201

@app.route("/api/notes/<int:nid>", methods=["PUT"])
def api_update_note(nid):
    data = request.get_json()
    run_db(
        """UPDATE soap_notes SET
           session_date = %s,
           psychiatrist = %s,
           subjective = %s,
           objective = %s,
           objective_vitals = %s,
           assessment = %s,
           plan = %s,
           signature = %s,
           updated_at = NOW()
           WHERE id = %s""",
        [
            data.get("session_date"),
            data.get("psychiatrist"),
            json.dumps(data.get("subjective", {})),
            json.dumps(data.get("objective", {})),
            data.get("objective", {}).get("vitals", "") if data.get("objective") else "",
            json.dumps(data.get("assessment", {})),
            json.dumps(data.get("plan", {})),
            json.dumps(data.get("signature", {})),
            nid,
        ]
    )
    return jsonify({"id": nid, "updated": True})

@app.route("/api/notes/<int:nid>", methods=["DELETE"])
def api_delete_note(nid):
    run_db("DELETE FROM soap_notes WHERE id = %s", [nid])
    return jsonify({"deleted": nid})

# ── API: PGx (PubMed + PharmGKB + CPIC) ───────────────────────

@app.route("/api/pgx/lookup", methods=["GET"])
def api_pgx_lookup():
    gene = request.args.get("gene", "").strip()
    drug = request.args.get("drug", "").strip()
    if not gene and not drug:
        return jsonify({"error": "Forneça 'gene' e/ou 'drug'"}), 400

    result = {"gene": gene, "drug": drug, "pharmgkb_gene": None,
              "pharmgkb_drug": None, "pharmgkb_pair": None,
              "cpic": None, "pubmed": []}

    try:
        if gene: result["pharmgkb_gene"] = pharmgkb_gene(gene)
        if drug: result["pharmgkb_drug"] = pharmgkb_drug(drug)
        if gene and drug: result["pharmgkb_pair"] = pharmgkb_gene_drug(gene, drug)
    except Exception as e:
        result["pharmgkb_error"] = str(e)

    try:
        if gene and drug:
            result["cpic"] = cpic_for_gene_drug(gene, drug)
        elif drug:
            gl = cpic_for_drug(drug)
            result["cpic"] = {"type": "drug", "guidelines": gl} if gl else None
        elif gene:
            gl = cpic_for_gene(gene)
            result["cpic"] = {"type": "gene", "guidelines": gl} if gl else None
    except Exception as e:
        result["cpic_error"] = str(e)

    try:
        search_term = ""
        if gene and drug:
            search_term = f"{gene} AND {drug} AND (pharmacogen* OR PGx OR metabolism)"
        elif gene:
            search_term = f"{gene} AND (pharmacogen* OR polymorphism OR genetics)"
        elif drug:
            search_term = f"{drug} AND (pharmacogen* OR PGx OR metabolism OR CYP)"
        if search_term:
            result["pubmed"] = search_pubmed(search_term, max_results=5)
    except Exception as e:
        result["pubmed_error"] = str(e)

    return jsonify(result)

@app.route("/api/search/pubmed", methods=["GET"])
def api_search_pubmed():
    q = request.args.get("q", "").strip()
    max_r = min(int(request.args.get("max", 10)), 50)
    if not q:
        return jsonify({"error": "Parâmetro 'q' é obrigatório"}), 400
    try:
        results = search_pubmed(q, max_results=max_r)
        for r in results:
            if r.get("abstract"):
                r["abstract_short"] = r["abstract"][:300] + ("…" if len(r["abstract"]) > 300 else "")
            r.pop("abstract", None)
        return jsonify({"count": len(results), "results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/pharmgkb/gene/<gene>", methods=["GET"])
def api_pharmgkb_gene(gene):
    try:
        data = pharmgkb_gene(gene)
        if data: return jsonify(data)
        return jsonify({"error": f"Gene '{gene}' não encontrado no PharmGKB"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/pharmgkb/drug/<drug>", methods=["GET"])
def api_pharmgkb_drug(drug):
    try:
        data = pharmgkb_drug(drug)
        if data: return jsonify(data)
        return jsonify({"error": f"Fármaco '{drug}' não encontrado no PharmGKB"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/cpic/guidelines", methods=["GET"])
def api_cpic_guidelines():
    try:
        data = get_cpic_guidelines()
        return jsonify({"count": len(data), "guidelines": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/cpic/gene-drug", methods=["GET"])
def api_cpic_gene_drug():
    gene = request.args.get("gene", "").strip()
    drug = request.args.get("drug", "").strip()
    if not gene and not drug:
        return jsonify({"error": "Forneça 'gene' e/ou 'drug'"}), 400
    try:
        result = None
        if gene and drug:
            result = cpic_for_gene_drug(gene, drug)
        elif drug:
            guidelines = cpic_for_drug(drug)
            result = {"type": "drug", "guidelines": guidelines} if guidelines else None
        elif gene:
            guidelines = cpic_for_gene(gene)
            result = {"type": "gene", "guidelines": guidelines} if guidelines else None
        return jsonify({"gene": gene, "drug": drug, "result": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Frontend estático ───────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(".", path)

# ── Main ────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
