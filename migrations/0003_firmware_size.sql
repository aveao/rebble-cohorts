-- Adds `size`, the .pbz's length in bytes, for the `file_size` field of the
-- eng-dash-shaped /api/ota/latest response. The poller has it in hand at
-- upload time and was throwing it away.
--
-- Nullable, because rows that predate this column cannot be backfilled without
-- fetching every blob back out of R2. The endpoint sends 0 for those, which is
-- the field the client documents as ignored either way.

ALTER TABLE firmwares ADD COLUMN size INTEGER;
