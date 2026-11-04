-- The Worker has no binding to the calendar sync database.
CREATE TABLE policy (
  id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL CHECK(paused IN (0,1)),
  seats INTEGER NOT NULL CHECK(seats BETWEEN 1 AND 100),
  turns INTEGER NOT NULL CHECK(turns BETWEEN 1 AND 1000),
  planning INTEGER NOT NULL CHECK(planning BETWEEN 0 AND turns),
  rounds INTEGER NOT NULL CHECK(rounds BETWEEN 1 AND 4),
  seconds INTEGER NOT NULL CHECK(seconds BETWEEN 1 AND 3600),
  day_cap INTEGER NOT NULL CHECK(day_cap BETWEEN 1 AND 1000000000000),
  month_cap INTEGER NOT NULL CHECK(month_cap BETWEEN 1 AND 1000000000000),
  lifetime_cap INTEGER NOT NULL CHECK(lifetime_cap BETWEEN 1 AND 1000000000000)
);
-- next
CREATE TABLE members(id TEXT PRIMARY KEY, revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)));
-- next
CREATE TABLE invites(hash TEXT PRIMARY KEY, member TEXT NOT NULL, expires INTEGER NOT NULL);
-- next
CREATE INDEX invites_expiry ON invites(expires);
-- next
CREATE TABLE devices(id TEXT PRIMARY KEY, member TEXT NOT NULL REFERENCES members(id),
  verifier TEXT NOT NULL, invite_hash TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)));
-- next
CREATE INDEX devices_member ON devices(member);
-- next
CREATE TRIGGER enroll BEFORE INSERT ON devices BEGIN
  SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM policy WHERE id=1) THEN RAISE(ABORT,'service_unavailable') END;
  SELECT CASE WHEN EXISTS(SELECT 1 FROM members WHERE id=NEW.member AND revoked=1)
    THEN RAISE(ABORT,'revoked') END;
  SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM members WHERE id=NEW.member)
    AND (SELECT count(*) FROM members WHERE revoked=0)>=(SELECT seats FROM policy WHERE id=1)
    THEN RAISE(ABORT,'seats_full') END;
  INSERT OR IGNORE INTO members(id) VALUES (NEW.member);
  DELETE FROM invites WHERE hash=NEW.invite_hash;
END;
-- next
CREATE TABLE daily(member TEXT NOT NULL, day TEXT NOT NULL, turns INTEGER NOT NULL,
  planning INTEGER NOT NULL, seconds INTEGER NOT NULL, PRIMARY KEY(member,day));
-- next
CREATE INDEX daily_expiry ON daily(day);
-- next
CREATE TABLE totals(bucket TEXT PRIMARY KEY, amount INTEGER NOT NULL CHECK(amount>=0));
-- next
CREATE TABLE calls(member TEXT NOT NULL, turn TEXT NOT NULL, step INTEGER NOT NULL, device TEXT NOT NULL,
  day TEXT NOT NULL, model TEXT NOT NULL, seconds INTEGER NOT NULL, is_plan INTEGER NOT NULL,
  charge INTEGER NOT NULL CHECK(charge>=0), state TEXT NOT NULL DEFAULT 'held',
  prior TEXT, continuation TEXT, input_tokens INTEGER, output_tokens INTEGER, audio_seconds INTEGER,
  PRIMARY KEY(member,turn,step));
-- next
CREATE INDEX calls_expiry ON calls(day);
-- next
CREATE TRIGGER reserve BEFORE INSERT ON calls BEGIN
  SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM policy WHERE id=1 AND paused=0)
    THEN RAISE(ABORT,'service_paused') END;
  SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM devices d JOIN members m ON d.member=m.id
    WHERE d.id=NEW.device AND m.id=NEW.member AND d.revoked=0 AND m.revoked=0)
    THEN RAISE(ABORT,'revoked') END;
  SELECT CASE WHEN EXISTS(SELECT 1 FROM calls WHERE member=NEW.member AND turn=NEW.turn AND step=NEW.step)
    THEN RAISE(ABORT,'already_submitted') END;
  SELECT CASE WHEN NEW.step<0 OR NEW.step>=(SELECT rounds FROM policy WHERE id=1)
    OR (NEW.step>0 AND NOT EXISTS(SELECT 1 FROM calls WHERE member=NEW.member AND turn=NEW.turn
      AND step=NEW.step-1 AND device=NEW.device AND model=NEW.model AND day=NEW.day
      AND continuation=NEW.prior AND state IN ('measured','conservative')))
    THEN RAISE(ABORT,'invalid_round') END;
  SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM totals WHERE bucket='lifetime')
    THEN RAISE(ABORT,'service_unavailable') END;
  INSERT OR IGNORE INTO totals VALUES (substr(NEW.day,1,7),0),(NEW.day,0);
  SELECT CASE WHEN EXISTS(SELECT 1 FROM totals,policy WHERE
    (bucket='lifetime' AND amount+NEW.charge>lifetime_cap) OR
    (bucket=substr(NEW.day,1,7) AND amount+NEW.charge>month_cap) OR
    (bucket=NEW.day AND amount+NEW.charge>day_cap)) THEN RAISE(ABORT,'shared_budget') END;
  INSERT OR IGNORE INTO daily VALUES (NEW.member,NEW.day,0,0,0);
  SELECT CASE WHEN NEW.seconds>0 AND daily.seconds+NEW.seconds>policy.seconds THEN RAISE(ABORT,'audio_allowance')
    WHEN NEW.step=0 AND NEW.seconds=0 AND daily.turns>=policy.turns THEN RAISE(ABORT,'daily_allowance')
    WHEN NEW.step=0 AND NEW.is_plan=1 AND daily.planning>=policy.planning THEN RAISE(ABORT,'planning_allowance')
    END FROM daily,policy WHERE member=NEW.member AND day=NEW.day;
  UPDATE totals SET amount=amount+NEW.charge WHERE bucket IN ('lifetime',substr(NEW.day,1,7),NEW.day);
  UPDATE daily SET turns=turns+(NEW.step=0 AND NEW.seconds=0),
    planning=planning+(NEW.step=0 AND NEW.is_plan=1), seconds=seconds+NEW.seconds
    WHERE member=NEW.member AND day=NEW.day;
END;
-- next
CREATE TRIGGER reconcile AFTER UPDATE OF state ON calls WHEN OLD.state='held' BEGIN
  UPDATE totals SET amount=amount+NEW.charge-OLD.charge
    WHERE bucket IN ('lifetime',substr(NEW.day,1,7),NEW.day);
  UPDATE daily SET seconds=seconds+NEW.audio_seconds-OLD.seconds
    WHERE member=NEW.member AND day=NEW.day AND NEW.audio_seconds>OLD.seconds;
  -- Stop admissions if upstream metering ever exceeds our conservative bound.
  UPDATE policy SET paused=1 WHERE id=1 AND (NEW.charge>OLD.charge OR NEW.audio_seconds>OLD.seconds);
END;
-- next
INSERT INTO totals VALUES ('lifetime',0);
-- next
INSERT INTO policy VALUES (1,1,100,20,1,4,120,1000000000,10000000000,80000000000);
