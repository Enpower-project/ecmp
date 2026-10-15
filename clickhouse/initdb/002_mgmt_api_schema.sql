CREATE TABLE IF NOT EXISTS ecmp.ecmp_api_audit_log
(
    timestamp      DateTime64(3, 'UTC') DEFAULT now(),
    user_id        String,
    username       String,
    email          String,
    roles          Array(String),
    path           String,
    method         String,
    status_code    UInt16,
    client_ip      String
)
ENGINE = MergeTree()
ORDER BY (timestamp, user_id)
SETTINGS index_granularity = 8192;

