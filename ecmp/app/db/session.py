from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/dbname")
DATABASE_URL_OLD = os.getenv("DATABASE_URL_OLD", "postgresql://user:pass@localhost/dbname")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

engine_donor = create_engine(DATABASE_URL_OLD)
SessionLocalDonor = sessionmaker(autocommit=False, autoflush=False, bind=engine_donor)
