-- Existing policies retain their normal activation path. Credits require an explicit cutoff.
ALTER TABLE policy ADD COLUMN funding TEXT NOT NULL DEFAULT 'normal' CHECK(funding IN ('normal','credit'));
-- next
ALTER TABLE policy ADD COLUMN credit_expires INTEGER CHECK(
  (funding='normal' AND credit_expires IS NULL) OR
  (funding='credit' AND credit_expires IS NOT NULL AND credit_expires>0
    AND seats<=5 AND day_cap<=250000000 AND month_cap<=1000000000 AND lifetime_cap<=1000000000)
);
-- next
-- A mode or grant-window change must start paused. The owner enables all limits atomically.
CREATE TRIGGER credit_configuration BEFORE UPDATE ON policy BEGIN
  SELECT CASE WHEN (NEW.funding!=OLD.funding OR NEW.credit_expires IS NOT OLD.credit_expires) AND OLD.paused!=1
    THEN RAISE(ABORT,'pause_before_funding_change')
    WHEN NEW.funding='credit' AND NEW.paused=0 AND NEW.credit_expires<=unixepoch()
    THEN RAISE(ABORT,'service_paused') END;
END;
-- next
-- D1 checks the server clock in the same transaction as the quota and dollar reservations.
CREATE TRIGGER credit_admission BEFORE INSERT ON calls BEGIN
  SELECT CASE WHEN EXISTS(SELECT 1 FROM policy WHERE id=1 AND funding='credit' AND credit_expires<=unixepoch())
    THEN RAISE(ABORT,'service_paused') END;
END;
-- next
CREATE TRIGGER credit_enrollment BEFORE INSERT ON devices BEGIN
  SELECT CASE WHEN EXISTS(SELECT 1 FROM policy WHERE id=1 AND funding='credit' AND credit_expires<=unixepoch())
    THEN RAISE(ABORT,'service_paused') END;
END;
