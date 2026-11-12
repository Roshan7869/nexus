-- Additive shared-capability schema. The caller owns the transaction and receipt.
CREATE TABLE capability_catalog (
    capability_id TEXT NOT NULL PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('skill','tool','native_action')),
    publisher TEXT NOT NULL, logical_key TEXT NOT NULL, current_version TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}', UNIQUE(publisher, logical_key),
    FOREIGN KEY(capability_id,current_version) REFERENCES capability_versions(capability_id,version)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE capability_versions (
    capability_id TEXT NOT NULL REFERENCES capability_catalog(capability_id), version TEXT NOT NULL,
    content_hash TEXT NOT NULL, descriptor_json TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT (unixepoch()), PRIMARY KEY(capability_id,version)
);
CREATE TABLE capability_sources (
    source_id TEXT NOT NULL PRIMARY KEY, capability_id TEXT NOT NULL REFERENCES capability_catalog(capability_id),
    real_path TEXT, original_path TEXT, resource_id INTEGER, provenance_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE provider_generations (
    provider_id TEXT NOT NULL, generation INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('inactive','loading','active','draining','failed')),
    descriptor_json TEXT NOT NULL, lease_count INTEGER NOT NULL DEFAULT 0 CHECK(lease_count>=0),
    PRIMARY KEY(provider_id,generation)
);
CREATE TABLE capability_sessions (
    session_id TEXT NOT NULL PRIMARY KEY, principal_id TEXT NOT NULL, harness_id TEXT NOT NULL,
    project TEXT NOT NULL, context_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK(status IN ('active','closed')), expires_at REAL NOT NULL
);
CREATE TABLE catalog_revisions (
    revision INTEGER PRIMARY KEY, manifest_hash TEXT NOT NULL,
    created_at REAL NOT NULL DEFAULT (unixepoch())
);
CREATE TABLE catalog_revision_members (
    revision INTEGER NOT NULL REFERENCES catalog_revisions(revision), capability_id TEXT NOT NULL,
    version TEXT NOT NULL, availability TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(revision,capability_id),
    FOREIGN KEY(capability_id,version) REFERENCES capability_versions(capability_id,version)
);
CREATE TABLE recommendation_decisions (
    decision_id TEXT NOT NULL PRIMARY KEY, session_id TEXT NOT NULL REFERENCES capability_sessions(session_id),
    catalog_revision INTEGER NOT NULL REFERENCES catalog_revisions(revision),
    policy_version TEXT NOT NULL, niche_version TEXT NOT NULL, candidate_hash TEXT NOT NULL,
    draws_json TEXT NOT NULL, created_at REAL NOT NULL DEFAULT (unixepoch())
);
CREATE TABLE capability_invocations (
    invocation_id TEXT NOT NULL PRIMARY KEY, principal_id TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES capability_sessions(session_id), capability_id TEXT NOT NULL,
    version TEXT NOT NULL, provider_generation INTEGER, idempotency_key TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('planned','running','succeeded','failed','unknown','native_pending')),
    decision_id TEXT REFERENCES recommendation_decisions(decision_id), result_ref TEXT,
    UNIQUE(principal_id,idempotency_key),
    FOREIGN KEY(capability_id,version) REFERENCES capability_versions(capability_id,version)
);
CREATE TABLE capability_events (
    event_id TEXT NOT NULL PRIMARY KEY, invocation_id TEXT REFERENCES capability_invocations(invocation_id),
    session_id TEXT NOT NULL REFERENCES capability_sessions(session_id), type TEXT NOT NULL,
    payload_json TEXT NOT NULL, created_at REAL NOT NULL DEFAULT (unixepoch())
);
CREATE TABLE capability_rewards (
    invocation_id TEXT NOT NULL PRIMARY KEY REFERENCES capability_invocations(invocation_id),
    verdict TEXT NOT NULL CHECK(verdict IN ('correct','wrong')), evidence_ref TEXT NOT NULL,
    verified_by TEXT NOT NULL, created_at REAL NOT NULL DEFAULT (unixepoch())
);
CREATE TABLE capability_beliefs (
    capability_id TEXT NOT NULL, version TEXT NOT NULL, niche_version TEXT NOT NULL, niche TEXT NOT NULL,
    successes INTEGER NOT NULL DEFAULT 0 CHECK(typeof(successes)='integer' AND successes>=0),
    failures INTEGER NOT NULL DEFAULT 0 CHECK(typeof(failures)='integer' AND failures>=0),
    PRIMARY KEY(capability_id,version,niche_version,niche),
    FOREIGN KEY(capability_id,version) REFERENCES capability_versions(capability_id,version)
);
CREATE TABLE capability_migrations (
    version TEXT NOT NULL PRIMARY KEY, checksum TEXT NOT NULL, applied_at REAL NOT NULL DEFAULT (unixepoch())
);
CREATE INDEX capability_sessions_owner ON capability_sessions(principal_id,status);
CREATE INDEX capability_invocations_session ON capability_invocations(session_id,state);
CREATE INDEX capability_events_session ON capability_events(session_id,created_at);
CREATE INDEX capability_sources_capability ON capability_sources(capability_id);
CREATE INDEX capability_beliefs_niche ON capability_beliefs(niche_version,niche);
CREATE TRIGGER capability_versions_no_update BEFORE UPDATE ON capability_versions
BEGIN SELECT RAISE(ABORT,'immutable capability version'); END;
CREATE TRIGGER capability_versions_no_delete BEFORE DELETE ON capability_versions
BEGIN SELECT RAISE(ABORT,'immutable capability version'); END;
CREATE TRIGGER capability_events_no_update BEFORE UPDATE ON capability_events
BEGIN SELECT RAISE(ABORT,'append-only capability events'); END;
CREATE TRIGGER capability_events_no_delete BEFORE DELETE ON capability_events
BEGIN SELECT RAISE(ABORT,'append-only capability events'); END;
CREATE TRIGGER capability_rewards_no_update BEFORE UPDATE ON capability_rewards
BEGIN SELECT RAISE(ABORT,'immutable capability reward'); END;
CREATE TRIGGER capability_rewards_no_delete BEFORE DELETE ON capability_rewards
BEGIN SELECT RAISE(ABORT,'immutable capability reward'); END;
CREATE TRIGGER catalog_members_no_update BEFORE UPDATE ON catalog_revision_members
BEGIN SELECT RAISE(ABORT,'immutable catalog snapshot'); END;
CREATE TRIGGER catalog_revisions_no_update BEFORE UPDATE ON catalog_revisions
BEGIN SELECT RAISE(ABORT,'immutable catalog revision'); END;
