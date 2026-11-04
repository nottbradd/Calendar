-- No content, keys or provider messages. Legacy attempts keep NULL diagnostics.
ALTER TABLE calls ADD COLUMN upstream_http INTEGER;
-- next
ALTER TABLE calls ADD COLUMN upstream_code TEXT;
-- next
ALTER TABLE calls ADD COLUMN upstream_type TEXT;
-- next
-- A singleton lock and crash-recovery snapshot, private to the owner's D1 session.
CREATE TABLE owner_probe (
  id INTEGER PRIMARY KEY CHECK(id=1), member TEXT NOT NULL, invite_hash TEXT NOT NULL,
  expires INTEGER NOT NULL, previous_policy TEXT NOT NULL CHECK(json_valid(previous_policy)),
  phase TEXT NOT NULL DEFAULT 'staged' CHECK(phase IN ('staged','running','closing'))
);
-- next
CREATE TRIGGER probe_stage AFTER INSERT ON owner_probe BEGIN
  SELECT CASE WHEN NEW.phase!='staged' OR (SELECT paused FROM policy WHERE id=1)!=1
    OR EXISTS(SELECT 1 FROM members WHERE revoked=0 OR id=NEW.member)
    OR EXISTS(SELECT 1 FROM invites WHERE expires>unixepoch())
    THEN RAISE(ABORT,'probe_not_isolated') END;
  UPDATE policy SET seats=1,turns=1,planning=0,rounds=2,seconds=5,
    day_cap=50000000,month_cap=50000000,lifetime_cap=50000000 WHERE id=1 AND paused=1;
END;
-- next
CREATE TRIGGER probe_invitation BEFORE INSERT ON invites WHEN EXISTS(SELECT 1 FROM owner_probe) BEGIN
  SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM owner_probe WHERE phase='staged' AND
    member=NEW.member AND invite_hash=NEW.hash AND NEW.expires<=expires AND NEW.expires>unixepoch())
    OR EXISTS(SELECT 1 FROM members WHERE id=NEW.member) THEN RAISE(ABORT,'probe_invite_only') END;
END;
-- next
CREATE TRIGGER probe_open AFTER UPDATE OF phase ON owner_probe WHEN NEW.phase='running' BEGIN
  SELECT CASE WHEN OLD.phase!='staged' OR NEW.expires<=unixepoch() OR
    NOT EXISTS(SELECT 1 FROM policy WHERE id=1 AND paused=1) OR
    (SELECT count(*) FROM members WHERE revoked=0)!=1 OR
    (SELECT count(*) FROM devices d JOIN members m ON d.member=m.id
      WHERE m.id=NEW.member AND m.revoked=0 AND d.revoked=0)!=1 OR
    EXISTS(SELECT 1 FROM invites WHERE expires>unixepoch()) THEN RAISE(ABORT,'probe_not_isolated') END;
  UPDATE policy SET paused=0 WHERE id=1;
END;
-- next
CREATE TRIGGER probe_policy BEFORE UPDATE ON policy WHEN EXISTS(SELECT 1 FROM owner_probe) BEGIN
  SELECT CASE WHEN NEW.seats!=1 OR NEW.turns!=1 OR NEW.planning!=0 OR NEW.rounds!=2 OR NEW.seconds!=5
    OR NEW.day_cap!=50000000 OR NEW.month_cap!=50000000 OR NEW.lifetime_cap!=50000000
    OR (NEW.paused=0 AND NOT EXISTS(SELECT 1 FROM owner_probe WHERE phase='running' AND expires>unixepoch()))
    THEN RAISE(ABORT,'probe_policy_locked') END;
END;
-- next
-- Applies even if the owner process dies or UTC midnight passes. Held failures count as attempts.
CREATE TRIGGER probe_admission BEFORE INSERT ON calls WHEN EXISTS(SELECT 1 FROM owner_probe) BEGIN
  SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM owner_probe WHERE member=NEW.member AND phase='running'
      AND expires>unixepoch()) THEN RAISE(ABORT,'service_paused')
    WHEN NEW.model NOT IN ('gpt-6-luna','gpt-transcribe') THEN RAISE(ABORT,'planning_allowance')
    WHEN NEW.seconds>0 AND (NEW.seconds>5 OR EXISTS(SELECT 1 FROM calls WHERE member=NEW.member AND seconds>0))
      THEN RAISE(ABORT,'audio_allowance')
    WHEN NEW.seconds=0 AND NEW.step=0 AND EXISTS(SELECT 1 FROM calls WHERE member=NEW.member AND seconds=0)
      THEN RAISE(ABORT,'daily_allowance') END;
END;
-- next
CREATE TRIGGER probe_close AFTER UPDATE OF phase ON owner_probe WHEN NEW.phase='closing' BEGIN
  UPDATE policy SET paused=1 WHERE id=1;
  INSERT INTO members(id,revoked) VALUES (NEW.member,1) ON CONFLICT(id) DO UPDATE SET revoked=1;
  DELETE FROM invites WHERE member=NEW.member;
END;
-- next
CREATE TRIGGER probe_before_restore BEFORE DELETE ON owner_probe BEGIN
  SELECT CASE WHEN OLD.phase!='closing' OR NOT EXISTS(SELECT 1 FROM policy WHERE id=1 AND paused=1)
    OR NOT EXISTS(SELECT 1 FROM members WHERE id=OLD.member AND revoked=1)
    OR EXISTS(SELECT 1 FROM members WHERE revoked=0) OR EXISTS(SELECT 1 FROM invites WHERE expires>unixepoch())
    THEN RAISE(ABORT,'probe_not_closed') END;
END;
-- next
CREATE TRIGGER probe_restore AFTER DELETE ON owner_probe BEGIN
  UPDATE policy SET seats=json_extract(OLD.previous_policy,'$.seats'), turns=json_extract(OLD.previous_policy,'$.turns'),
    planning=json_extract(OLD.previous_policy,'$.planning'), rounds=json_extract(OLD.previous_policy,'$.rounds'),
    seconds=json_extract(OLD.previous_policy,'$.seconds'), day_cap=json_extract(OLD.previous_policy,'$.day_cap'),
    month_cap=json_extract(OLD.previous_policy,'$.month_cap'), lifetime_cap=json_extract(OLD.previous_policy,'$.lifetime_cap')
    WHERE id=1 AND paused=1;
END;
