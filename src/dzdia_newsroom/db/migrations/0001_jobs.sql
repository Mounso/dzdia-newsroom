-- Postgres job queue (ADR-004): at-least-once delivery with a renewable lease.
CREATE TABLE jobs (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind         text        NOT NULL,
    payload      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    status       text        NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'done', 'dead')),
    -- Incremented at claim time, so a job that crashes its worker still runs out of attempts.
    attempts     integer     NOT NULL DEFAULT 0,
    max_attempts integer     NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
    run_after    timestamptz NOT NULL DEFAULT now(),
    locked_until timestamptz,
    -- Fencing token: drawn at each claim; every later update must present it.
    claim_token  uuid,
    -- e.g. 'telegram:<update_id>': a redelivered webhook never creates a second job.
    dedup_key    text UNIQUE,
    last_error   text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX jobs_queued_idx ON jobs (run_after, id) WHERE status = 'queued';
CREATE INDEX jobs_running_idx ON jobs (locked_until) WHERE status = 'running';
