-- Add new AI analysis display fields for lead score result table.
-- Compatible with MySQL versions that do not support `ADD COLUMN IF NOT EXISTS`.

SET NAMES utf8mb4;

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
