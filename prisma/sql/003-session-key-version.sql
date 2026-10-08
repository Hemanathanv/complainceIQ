-- Bind each session to the frontend key that issued it.
-- The registry is new; there are no existing sessions to backfill.
ALTER TABLE "public"."api_sessions"
  ADD COLUMN "key_hash_at_issue" TEXT NOT NULL;
