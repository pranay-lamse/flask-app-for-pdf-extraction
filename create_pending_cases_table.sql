CREATE TABLE IF NOT EXISTS pending_cases_by_head (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    report_upload_id BIGINT UNSIGNED NOT NULL,
    crime_head_id BIGINT UNSIGNED NOT NULL,
    month_0_3 INT DEFAULT 0,
    month_3_6 INT DEFAULT 0,
    month_6_12 INT DEFAULT 0,
    above_1_year INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX (report_upload_id),
    INDEX (crime_head_id)
);
