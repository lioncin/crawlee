-- AI lead analysis result storage
-- MySQL 8.0+

SET NAMES utf8mb4;

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
