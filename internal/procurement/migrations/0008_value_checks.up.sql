-- Value checks that procurement.create@1 and procurement.receive@1 apply in
-- Normalize, enforced for every write path (raw SQL as the application role
-- included). Normalize rejects these values first, so the API is unchanged.
--
-- procurement.space_trim(s) strips the characters Go's strings.TrimSpace
-- strips (unicode.IsSpace: \t \n \v \f \r, space, U+0085, U+00A0, and the
-- other Unicode White_Space characters U+1680, U+2000-U+200A, U+2028, U+2029,
-- U+202F, U+205F, U+3000). TestSpaceTrimMatchesGo compares it with Go over
-- every code point.
CREATE FUNCTION procurement.space_trim(s text) RETURNS text LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
  RETURN btrim(s, E'\t\n\u000B\f\r \u0085\u00A0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200A\u2028\u2029\u202F\u205F\u3000');

ALTER TABLE procurement.purchase
  ADD CONSTRAINT purchase_title_not_blank CHECK (procurement.space_trim(title) <> ''),
  ADD CONSTRAINT purchase_id_charset CHECK (id ~ '^[A-Za-z0-9_-]+$'),
  ADD CONSTRAINT purchase_amount_max CHECK (amount <= 1000000000000);

ALTER TABLE procurement.receipt
  ADD CONSTRAINT receipt_amount_max CHECK (amount <= 1000000000000);
