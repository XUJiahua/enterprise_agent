-- Database-enforced procurement invariants. They hold for every write path,
-- including SQL that bypasses the operation code; the operations still check
-- first so clients get a uniform Denied instead of an internal error.
--
-- purchase status: submitted -> approved -> closed only;
--   approve: the actor bound by the runner (app.actor) is not the creator;
--   close:   receipts sum to at least the amount; closed is final.
-- receipt insert: locks the parent purchase and requires it to be approved,
--   so a receipt cannot land on a closed purchase however it is written.
-- Triggers run as the invoker, under the same RLS and grants.
CREATE FUNCTION procurement.purchase_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE received bigint;
BEGIN
  IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR NEW.id IS DISTINCT FROM OLD.id
     OR NEW.created_by IS DISTINCT FROM OLD.created_by OR NEW.amount IS DISTINCT FROM OLD.amount THEN
    RAISE EXCEPTION 'purchase identity and amount are immutable' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status = OLD.status THEN
    RETURN NEW;
  END IF;
  IF OLD.status = 'submitted' AND NEW.status = 'approved' THEN
    IF coalesce(current_setting('app.actor', true), '') IN ('', OLD.created_by) THEN
      RAISE EXCEPTION 'purchase % cannot be approved by its creator or without a bound actor', OLD.id USING ERRCODE = 'check_violation';
    END IF;
  ELSIF OLD.status = 'approved' AND NEW.status = 'closed' THEN
    SELECT coalesce(sum(amount), 0) INTO received FROM procurement.receipt
     WHERE tenant_id = OLD.tenant_id AND purchase_id = OLD.id;
    IF received < OLD.amount THEN
      RAISE EXCEPTION 'purchase % receipts % below amount %', OLD.id, received, OLD.amount USING ERRCODE = 'check_violation';
    END IF;
  ELSE
    RAISE EXCEPTION 'purchase % cannot move from % to %', OLD.id, OLD.status, NEW.status USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER purchase_guard BEFORE UPDATE ON procurement.purchase FOR EACH ROW EXECUTE FUNCTION procurement.purchase_guard();

CREATE FUNCTION procurement.receipt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent text;
BEGIN
  SELECT status INTO parent FROM procurement.purchase
   WHERE tenant_id = NEW.tenant_id AND id = NEW.purchase_id FOR UPDATE;
  IF parent IS DISTINCT FROM 'approved' THEN
    RAISE EXCEPTION 'receipt requires an approved purchase' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER receipt_guard BEFORE INSERT ON procurement.receipt FOR EACH ROW EXECUTE FUNCTION procurement.receipt_guard();
