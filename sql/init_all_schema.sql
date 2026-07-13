-- One-shot schema initialization and upgrade script
-- This script is idempotent and can be re-run safely.
-- MySQL 8.0+

SET NAMES utf8mb4;

-- 1) Flexible storage tables

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
  item_date DATE NULL,
  extra JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_biz_key (biz_key),
  INDEX idx_record_id (record_id),
  INDEX idx_issuer_name (issuer_full_name),
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

-- 2) AI analysis tables

CREATE TABLE IF NOT EXISTS ai_analysis_run (
  run_id BIGINT PRIMARY KEY AUTO_INCREMENT,
  total_count INT NOT NULL DEFAULT 0,
  chunk_size INT NOT NULL DEFAULT 0,
  chunk_count INT NOT NULL DEFAULT 0,
  generated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  summary_payload JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_generated_at (generated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS ai_analysis_result (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  run_id BIGINT NOT NULL,
  grade CHAR(1) NOT NULL,
  company_name VARCHAR(256) NULL,
  contact_name VARCHAR(128) NULL,
  phone VARCHAR(128) NULL,
  email VARCHAR(256) NULL,
  employee_count VARCHAR(128) NULL,
  operating_revenue VARCHAR(256) NULL,
  insured_count VARCHAR(128) NULL,
  reason TEXT NULL,
  title VARCHAR(512) NULL,
  item_date DATE NULL,
  sort_order INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_run_grade (run_id, grade),
  INDEX idx_run_sort (run_id, sort_order),
  CONSTRAINT fk_ai_result_run FOREIGN KEY (run_id) REFERENCES ai_analysis_run(run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3) Backward-compatible column upgrades for existing ai_analysis_result tables
-- Compatible with MySQL versions that do not support `ADD COLUMN IF NOT EXISTS`.

SET @db = DATABASE();

SET @sql = (
  SELECT IF(
    EXISTS(
      SELECT 1
      FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = @db
        AND TABLE_NAME = 'ai_analysis_result'
        AND COLUMN_NAME = 'employee_count'
    ),
    'SELECT "employee_count exists" AS msg',
    'ALTER TABLE ai_analysis_result ADD COLUMN employee_count VARCHAR(128) NULL AFTER email'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = (
  SELECT IF(
    EXISTS(
      SELECT 1
      FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = @db
        AND TABLE_NAME = 'ai_analysis_result'
        AND COLUMN_NAME = 'operating_revenue'
    ),
    'SELECT "operating_revenue exists" AS msg',
    'ALTER TABLE ai_analysis_result ADD COLUMN operating_revenue VARCHAR(256) NULL AFTER employee_count'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = (
  SELECT IF(
    EXISTS(
      SELECT 1
      FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA = @db
        AND TABLE_NAME = 'ai_analysis_result'
        AND COLUMN_NAME = 'insured_count'
    ),
    'SELECT "insured_count exists" AS msg',
    'ALTER TABLE ai_analysis_result ADD COLUMN insured_count VARCHAR(128) NULL AFTER operating_revenue'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

