-- Flexible schema for heterogeneous URL extraction results
-- MySQL 8.0+

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS source_config (
  source_id BIGINT PRIMARY KEY AUTO_INCREMENT,
  source_url VARCHAR(1024) NOT NULL,
  source_type VARCHAR(64) NOT NULL,
  parser_version VARCHAR(64) NOT NULL DEFAULT 'v1',
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_source_url (source_url(255)),
  INDEX idx_source_type (source_type),
  INDEX idx_is_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS crawl_record (
  record_id BIGINT PRIMARY KEY AUTO_INCREMENT,
  source_id BIGINT NOT NULL,
  task_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  status_code INT NULL,
  raw_payload JSON NULL,
  normalized_payload JSON NULL,
  error_message TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_source_task_time (source_id, task_time),
  CONSTRAINT fk_record_source FOREIGN KEY (source_id) REFERENCES source_config(source_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS entity_item (
  item_id BIGINT PRIMARY KEY AUTO_INCREMENT,
  record_id BIGINT NOT NULL,
  biz_key VARCHAR(128) NOT NULL,
  title VARCHAR(512) NULL,
  url VARCHAR(1024) NULL,
  issuer_full_name VARCHAR(256) NULL,
  audit_status VARCHAR(64) NULL,
  item_date DATE NULL,
  extra JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_biz_key (biz_key),
  INDEX idx_record_id (record_id),
  INDEX idx_issuer_name (issuer_full_name),
  INDEX idx_audit_status (audit_status),
  INDEX idx_item_date (item_date),
  CONSTRAINT fk_item_record FOREIGN KEY (record_id) REFERENCES crawl_record(record_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS entity_kv (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  item_id BIGINT NOT NULL,
  field_key VARCHAR(128) NOT NULL,
  field_value TEXT NULL,
  field_type VARCHAR(32) NOT NULL DEFAULT 'string',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_item_id (item_id),
  INDEX idx_field_key_value (field_key, field_value(100)),
  CONSTRAINT fk_kv_item FOREIGN KEY (item_id) REFERENCES entity_item(item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
