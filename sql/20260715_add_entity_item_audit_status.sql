-- Add audit status as a first-class field for crawled notice items.
-- MySQL 8.0+

ALTER TABLE entity_item
  ADD COLUMN audit_status VARCHAR(64) NULL AFTER issuer_full_name,
  ADD INDEX idx_audit_status (audit_status);
