from django.db import migrations


FORWARD_SQL = """
CREATE FUNCTION spawns_validate_combat_member_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p spawns_combatparticipant; target spawns_combatparticipant; side_enc bigint; live_status text;
BEGIN
    IF TG_OP = 'UPDATE' AND (NEW.encounter_id, NEW.side_id, NEW.current_target_id, NEW.is_active, NEW.player_id, NEW.mob_id)
        IS NOT DISTINCT FROM (OLD.encounter_id, OLD.side_id, OLD.current_target_id, OLD.is_active, OLD.player_id, OLD.mob_id) THEN
        RETURN NULL;
    END IF;
    SELECT * INTO p FROM spawns_combatparticipant WHERE id = NEW.id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF p.side_id IS NOT NULL THEN
        SELECT encounter_id INTO side_enc FROM spawns_combatside WHERE id = p.side_id;
        IF side_enc IS DISTINCT FROM p.encounter_id THEN
            RAISE EXCEPTION 'Participant side belongs to another encounter' USING ERRCODE = '23514';
        END IF;
    END IF;
    IF p.is_active THEN
        SELECT status INTO live_status FROM spawns_combatencounter WHERE id = p.encounter_id;
        IF live_status NOT IN ('active', 'paused') THEN
            RAISE EXCEPTION 'Active participant belongs to a finished encounter' USING ERRCODE = '23514';
        END IF;
        IF p.current_target_id IS NOT NULL THEN
            SELECT * INTO target FROM spawns_combatparticipant WHERE id = p.current_target_id;
            IF NOT FOUND OR NOT target.is_active OR target.encounter_id <> p.encounter_id OR target.side_id = p.side_id THEN
                RAISE EXCEPTION 'Current target is not an active opponent in this encounter' USING ERRCODE = '23514';
            END IF;
        END IF;
    END IF;
    IF EXISTS (SELECT 1 FROM spawns_combatparticipant owner WHERE owner.current_target_id = p.id AND owner.is_active
               AND (NOT p.is_active OR owner.encounter_id <> p.encounter_id OR owner.side_id = p.side_id)) THEN
        RAISE EXCEPTION 'Participant change invalidates an active target' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER combat_member_scope
AFTER INSERT OR UPDATE ON spawns_combatparticipant
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION spawns_validate_combat_member_scope();

CREATE FUNCTION spawns_validate_combat_relation_scope() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE relation spawns_combatsiderelation; lower_enc bigint; higher_enc bigint;
BEGIN
    SELECT * INTO relation FROM spawns_combatsiderelation WHERE id = NEW.id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    SELECT encounter_id INTO lower_enc FROM spawns_combatside WHERE id = relation.lower_side_id;
    SELECT encounter_id INTO higher_enc FROM spawns_combatside WHERE id = relation.higher_side_id;
    IF lower_enc IS DISTINCT FROM higher_enc THEN
        RAISE EXCEPTION 'Combat relation crosses encounters' USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER combat_relation_scope
AFTER INSERT OR UPDATE ON spawns_combatsiderelation
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION spawns_validate_combat_relation_scope();

CREATE FUNCTION spawns_validate_combat_parent_scope() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'spawns_combatencounter' THEN
        IF EXISTS (SELECT 1 FROM spawns_combatencounter e JOIN spawns_combatparticipant p ON p.encounter_id = e.id
                   WHERE e.id = NEW.id AND e.status = 'finished' AND p.is_active) THEN
            RAISE EXCEPTION 'Finished encounter still has active participants' USING ERRCODE = '23514';
        END IF;
    ELSE
        IF EXISTS (SELECT 1 FROM spawns_combatside s JOIN spawns_combatparticipant p ON p.side_id = s.id
                   WHERE s.id = NEW.id AND p.encounter_id <> s.encounter_id)
           OR EXISTS (SELECT 1 FROM spawns_combatsiderelation r
                      JOIN spawns_combatside a ON a.id = r.lower_side_id
                      JOIN spawns_combatside b ON b.id = r.higher_side_id
                      WHERE (a.id = NEW.id OR b.id = NEW.id) AND a.encounter_id <> b.encounter_id) THEN
            RAISE EXCEPTION 'Side change crosses encounter scopes' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER combat_encounter_scope AFTER UPDATE ON spawns_combatencounter
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.status = 'finished')
EXECUTE FUNCTION spawns_validate_combat_parent_scope();
CREATE CONSTRAINT TRIGGER combat_side_scope AFTER UPDATE ON spawns_combatside
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION spawns_validate_combat_parent_scope();
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS combat_member_scope ON spawns_combatparticipant;
DROP TRIGGER IF EXISTS combat_relation_scope ON spawns_combatsiderelation;
DROP FUNCTION IF EXISTS spawns_validate_combat_member_scope();
DROP FUNCTION IF EXISTS spawns_validate_combat_relation_scope();
DROP TRIGGER IF EXISTS combat_encounter_scope ON spawns_combatencounter;
DROP TRIGGER IF EXISTS combat_side_scope ON spawns_combatside;
DROP FUNCTION IF EXISTS spawns_validate_combat_parent_scope();
"""


class Migration(migrations.Migration):
    dependencies = [('spawns', '0161_participant_combat_integrity')]
    operations = [migrations.RunSQL(FORWARD_SQL, REVERSE_SQL)]
