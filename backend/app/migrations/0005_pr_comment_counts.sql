-- Conversation comment counts for pull requests. The /issues listing returns
-- PRs too, with their comment count, so it is recorded at no extra request
-- cost and lets comment fetching skip PRs with no discussion.
ALTER TABLE pull_requests ADD COLUMN IF NOT EXISTS comments INTEGER;
