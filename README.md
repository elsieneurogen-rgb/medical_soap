# ═══════════════════════════════════════════════════════════════
#  SOAP Psiquiatria — Prontuário Eletrônico com PGx
# ═══════════════════════════════════════════════════════════════
#
# Interface web para registro de consultas psiquiátricas no formato
# SOAP (Subjetivo · Objetivo · Avaliação · Plano), com integração a
# bases de farmacogenômica: PubMed, PharmGKB e diretrizes CPIC.
#
# Arquitetura de produção (VPS com EasyPanel):
#
#   Nginx ( porta 80/443 )
#     ├── /          → serve index.html (frontend estático)
#     └── /api/*     → reverse proxy → Flask/Gunicorn (porta 5000)
#
#   PostgreSQL (porta 5432) — banco de dados do prontuário
#
#   Backend Flask (porta 5000, Gunicorn) — APIs REST + psycopg2
#
# ────────────────────────────────────────────────────────────────
#
# Pré-requisitos:
#   - Docker + Docker Compose no VPS
#   - Domínio apontando para o IP do VPS (para Let's Encrypt)
#   - Conta NCBI com email registrado (ENTREZ_EMAIL)
#
# Como subir:
#   1. Copiar .env.example → .env e preencher
#   2. docker compose up -d
#   3. Acessar http://<ip-ou-dominio>/
#
# Integrações externas:
#   - PubMed (NCBI E-utilities):  https://eutils.ncbi.nlm.nih.gov
#   - PharmGKB REST API:          https://api.pharmgkb.org/v1
#   - ClinPGx/CPIC (scrape):      https://www.clinpgx.org
#
# ═══════════════════════════════════════════════════════════════
