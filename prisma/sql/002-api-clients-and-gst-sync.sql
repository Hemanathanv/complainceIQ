-- Reviewed additive diff from prisma migrate diff. Existing rows stay intact.
BEGIN;
ALTER TABLE "Automation"."gstFetch"
  ADD COLUMN "is_active" BOOLEAN NOT NULL DEFAULT true,
  ADD COLUMN "source_key" TEXT,
  ADD COLUMN "source_name" TEXT,
  ADD COLUMN "synced_at" TIMESTAMPTZ(6);

CREATE TABLE "public"."api_clients" (
  "id" UUID NOT NULL DEFAULT gen_random_uuid(),
  "name" TEXT NOT NULL,
  "key_prefix" TEXT NOT NULL,
  "key_hash" TEXT NOT NULL,
  "allowed_origins" TEXT[] DEFAULT ARRAY[]::TEXT[],
  "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "last_used_at" TIMESTAMPTZ(6),
  "revoked_at" TIMESTAMPTZ(6),
  CONSTRAINT "api_clients_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "public"."api_sessions" (
  "id" UUID NOT NULL DEFAULT gen_random_uuid(),
  "client_id" UUID NOT NULL,
  "refresh_hash" TEXT NOT NULL,
  "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "expires_at" TIMESTAMPTZ(6) NOT NULL,
  "revoked_at" TIMESTAMPTZ(6),
  CONSTRAINT "api_sessions_pkey" PRIMARY KEY ("id")
);

CREATE TABLE "public"."api_request_audit" (
  "id" BIGSERIAL NOT NULL,
  "client_id" UUID NOT NULL,
  "session_id" UUID,
  "method" TEXT NOT NULL,
  "path" TEXT NOT NULL,
  "status_code" INTEGER NOT NULL,
  "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "api_request_audit_pkey" PRIMARY KEY ("id")
);

CREATE UNIQUE INDEX "api_clients_name_key" ON "public"."api_clients"("name");
CREATE UNIQUE INDEX "api_clients_key_hash_key" ON "public"."api_clients"("key_hash");
CREATE UNIQUE INDEX "api_sessions_refresh_hash_key" ON "public"."api_sessions"("refresh_hash");
CREATE INDEX "api_sessions_client_id_idx" ON "public"."api_sessions"("client_id");
CREATE INDEX "api_request_audit_client_id_created_at_idx" ON "public"."api_request_audit"("client_id", "created_at");
CREATE UNIQUE INDEX "gstFetch_source_key_key" ON "Automation"."gstFetch"("source_key");

ALTER TABLE "public"."api_sessions"
  ADD CONSTRAINT "api_sessions_client_id_fkey" FOREIGN KEY ("client_id")
  REFERENCES "public"."api_clients"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "public"."api_request_audit"
  ADD CONSTRAINT "api_request_audit_client_id_fkey" FOREIGN KEY ("client_id")
  REFERENCES "public"."api_clients"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "public"."api_request_audit"
  ADD CONSTRAINT "api_request_audit_session_id_fkey" FOREIGN KEY ("session_id")
  REFERENCES "public"."api_sessions"("id") ON DELETE SET NULL ON UPDATE CASCADE;
COMMIT;
