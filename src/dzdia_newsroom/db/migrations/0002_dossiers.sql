-- Source dossiers of a private chat (ADR-0020, point 6): one dossier per chat, its sources in
-- arrival order. Only extracted text is stored, never HTML, drafts or evidence.
CREATE TABLE dossiers (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    channel          text        NOT NULL,
    chat_id          text        NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    -- Adding a source and writing the article are activity; the dossier expires 48 hours after
    -- it (ADR-0020, point 7).
    last_activity_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (channel, chat_id)
);

CREATE TABLE dossier_sources (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dossier_id        bigint      NOT NULL REFERENCES dossiers (id) ON DELETE CASCADE,
    -- 1 to n: the number shown to the journalist.
    position          integer     NOT NULL CHECK (position >= 1),
    kind              text        NOT NULL CHECK (kind IN ('link', 'pasted_text')),
    -- The link as sent; NULL for a pasted text.
    url               text,
    -- The extracted text after the caps, and its length before them (ADR-0020, point 5).
    text              text        NOT NULL,
    source_chars      integer     NOT NULL,
    truncated         boolean     NOT NULL,
    paywall_suspected boolean     NOT NULL,
    message_id        text        NOT NULL,
    -- Position of the link in its message; 0 for a pasted text.
    link_index        integer     NOT NULL DEFAULT 0,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK ((kind = 'link') = (url IS NOT NULL)),
    UNIQUE (dossier_id, position),
    -- A replayed job adds nothing.
    UNIQUE (dossier_id, message_id, link_index)
);
