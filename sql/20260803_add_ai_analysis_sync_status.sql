-- CRM sync status for AI analysis results.
-- MySQL 8.0+

SET @db = DATABASE();

SET @sql = (
  SELECT IF(
    EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'ai_analysis_result' AND COLUMN_NAME = 'sync_status'),
    'SELECT "sync_status exists" AS msg',
    'ALTER TABLE ai_analysis_result ADD COLUMN sync_status VARCHAR(16) NOT NULL DEFAULT ''未同步'' AFTER sort_order'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql = (
  SELECT IF(
    EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'ai_analysis_result' AND COLUMN_NAME = 'synced_at'),
    'SELECT "synced_at exists" AS msg',
    'ALTER TABLE ai_analysis_result ADD COLUMN synced_at DATETIME NULL AFTER sync_status'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;
