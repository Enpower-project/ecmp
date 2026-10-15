import os
from sqlalchemy.orm import Session
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta, timezone
from app.models.ec import (
    Base, EnergyCommunity, CommunityMembership, Member, GridElement, ServicePoint, 
    Meter, Channel, Reading, WeatherLocation, WeatherTimeseries, ServicePointWeather, 
    EmissionZone, CO2EmissionsTimeseries, ServicePointEmissionZone, PriceZone, 
    EnergyPriceTimeseries, ServicePointPriceZone)
from app.models.flex import (FlexibilityRequest)
from app.models.enums import (
    quality_flag, grid_element_type, meter_role, submeter_role, unit_of_measure, community_role,
    weather_variable, price_category, price_unit, co2_emissions_unit, asset_type, flexibility_type)
import uuid
from dotenv import load_dotenv

load_dotenv()

# --- Configure DB URL ---
DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_engine(DATABASE_URL, echo=True, future=True)

# Base.metadata.drop_all(engine)

# drop everything inside the schema dynamically
with engine.begin() as conn:
    # Drop all tables
    conn.execute(text("""
        DO $$ DECLARE
            r RECORD;
        BEGIN
            FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public')
            LOOP
                EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
            END LOOP;
        END $$;
    """))

    # Drop all enums
    conn.execute(text("""
        DO $$ DECLARE
            r RECORD;
        BEGIN
            FOR r IN (
                SELECT t.typname
                FROM pg_type t
                JOIN pg_enum e ON t.oid = e.enumtypid
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname = 'public'
                GROUP BY t.typname
            )
            LOOP
                EXECUTE 'DROP TYPE IF EXISTS public.' || quote_ident(r.typname) || ' CASCADE';
            END LOOP;
        END $$;
    """))
