-- Configurable URL list for manual and scheduled data fetching.
-- MySQL 8.0+

CREATE TABLE IF NOT EXISTS crawl_target (
  target_id BIGINT PRIMARY KEY AUTO_INCREMENT,
  name VARCHAR(128) NOT NULL,
  target_url VARCHAR(2048) NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_crawl_target_url (target_url(255)),
  INDEX idx_crawl_target_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
