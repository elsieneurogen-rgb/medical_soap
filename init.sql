-- ═══════════════════════════════════════════════════════════════
--  Inicialização do schema PostgreSQL — SOAP Psiquiatria
-- ═══════════════════════════════════════════════════════════════
--
-- Este script é copiado para /docker-entrypoint-initdb.d/ e roda
-- automaticamente na primeira inicialização do container postgres.
-- Não precisa ser chamado manualmente.
--
-- Tabele s:
--   patients   — cadastro de pacientes
--   soap_notes — consultas SOAP com campos em JSONB
-- ═══════════════════════════════════════════════════════════════

-- Criar tenant/seção (se necessário — comentar se usar schema default)
-- CREATE SCHEMA IF NOT EXISTS soap;

CREATE TABLE IF NOT EXISTS patients (
    id          SERIAL      PRIMARY KEY,
    name        TEXT        NOT NULL,
    dob         TEXT,
    sex         TEXT,
    identifier  TEXT,
    allergies   TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS soap_notes (
    id               SERIAL      PRIMARY KEY,
    patient_id       INTEGER     NOT NULL REFERENCES patients(id) ON DELETE CASCADE,

    session_date     TEXT        NOT NULL,

    psychiatrist     TEXT,

    -- Campos estruturados como JSONB para flexibilidade
    subjective       JSONB,
    objective        JSONB,
    objective_vitals TEXT,

    assessment       JSONB,
    plan             JSONB,
    signature        JSONB,

    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Índice para consultas por paciente
CREATE INDEX IF NOT EXISTS idx_notes_patient
    ON soap_notes(patient_id);

-- Índice para busca por data (útil para relatórios)
CREATE INDEX IF NOT EXISTS idx_notes_date
    ON soap_notes(session_date);

-- Comentários para documentação
COMMENT ON TABLE patients IS 'Cadastro de pacientes do prontuário SOAP';
COMMENT ON TABLE soap_notes IS 'ConsultasSOAP (Subjetivo, Objetivo, Avaliação, Plano)';
COMMENT ON COLUMN soap_notes.subjective IS 'JSON: {complaint, history, prev_illness, family, current_meds, substances, extra, phq9, gad7, cssrs}';
COMMENT ON COLUMN soap_notes.objective IS 'JSON: {presentation, affect, thinking, thought_content, perception, orientation, cognition}';
COMMENT ON COLUMN soap_notes.assessment IS 'JSON: {diagnosis, secondary, global, risk, evolution}';
COMMENT ON COLUMN soap_notes.plan IS 'JSON: {medications:[{name,dose,via,freq,obs}], psychotherapy, referrals, next_session, others}';
COMMENT ON COLUMN soap_notes.signature IS 'JSON: {name, crm, date}';
