#!/bin/bash
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    CREATE DATABASE $KEYCLOAK_DB_NAME;
    CREATE DATABASE $ECMP_DB_NAME;
EOSQL

# Create the user with a password
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';
    CREATE USER $KC_DB_USER WITH PASSWORD '$KC_DB_PASSWORD';
EOSQL

# Grant all privileges on the databases to the user
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    GRANT ALL PRIVILEGES ON DATABASE $KEYCLOAK_DB_NAME TO $KC_DB_USER;
    GRANT ALL PRIVILEGES ON DATABASE $ECMP_DB_NAME TO $DB_USER;
EOSQL

# Grant the user the ability to create tables in the public schema
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -d "$KEYCLOAK_DB_NAME" <<-EOSQL
    GRANT CREATE ON SCHEMA public TO  $KC_DB_USER;
EOSQL

# Grant the user the ability to create tables in the public schema
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -d "$ECMP_DB_NAME" <<-EOSQL
    GRANT CREATE ON SCHEMA public TO  $DB_USER;
EOSQL

# Enable TimescaleDB extension in the PAP database
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" -d "$ECMP_DB_NAME" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS timescaledb;
EOSQL
