from sqlalchemy import select
from app.models import ec
# import app.models.mdms as mdms
from app.db.session import SessionLocal
import pandas as pd

import logging

logger = logging.getLogger(__name__)


def get_sm_readings(service_point_code, channel_role, column_name, start_date, end_date):
    with SessionLocal() as session:
        stmt = select(ec.ServicePoint).where(ec.ServicePoint.code==service_point_code)
        sp = session.scalars(stmt).one_or_none()
        sp_id = sp.sp_id
    # get meter with service point ID
    with SessionLocal() as session:
        stmt = select(ec.Meter).where(ec.Meter.sp_id==sp_id)
        mt = session.scalars(stmt).one_or_none()
        mt_id = mt.meter_id
    # get channel id
    with SessionLocal() as session:
        stmt = select(ec.Channel).where(
            ec.Channel.meter_id==mt_id,
            ec.Channel.role==channel_role)
        ch = session.scalars(stmt).one_or_none()
        ch_id = ch.channel_id
    # get readings
    with SessionLocal() as session:
        stmt = select(ec.Reading).where(
            ec.Reading.channel_id==ch_id,
            ec.Reading.ts>=start_date,
            ec.Reading.ts<=end_date
            ).order_by(ec.Reading.ts)
        data = session.scalars(stmt).all()
    data = [
        {
            "timestamp": r.ts,
            "value": r.value 
        }
        for r in data
    ]

    data = pd.DataFrame(data).rename(columns={"value": column_name})
    data["timestamp"] = pd.to_datetime(data["timestamp"])
    data = data.set_index("timestamp").sort_index()

    return data


def get_power_data(service_point_code, asset_type, sm_hrid, role, column_name, start_date, end_date):
    with SessionLocal() as session:
        stmt = select(ec.ServicePoint).where(ec.ServicePoint.code==service_point_code)
        sp = session.scalars(stmt).one_or_none()
        sp_id = sp.sp_id
    # get asset with service point id and hrid
    with SessionLocal() as session:
        stmt = select(ec.Asset).where(
            ec.Asset.sp_id==sp_id,
            ec.Asset.type==asset_type
            )
        ast = session.scalars(stmt).one_or_none()
        ast_id = ast.id
    # get submeter id
    with SessionLocal() as session:
        stmt = select(ec.SubMeter).where(
            ec.SubMeter.asset_id==ast_id,
            ec.SubMeter.hrid==sm_hrid)
        sm = session.scalars(stmt).one_or_none()
        sm_id = sm.id
    # get subchannel id
    with SessionLocal() as session:
        stmt = select(ec.SubChannel).where(
            ec.SubChannel.submeter_id==sm_id,
            ec.SubChannel.role==role)
        subch = session.scalars(stmt).one_or_none()
        subch_id = subch.id
    # get readings
    with SessionLocal() as session:
        stmt = select(ec.SubReading).where(
            ec.SubReading.id==subch_id,
            ec.SubReading.ts>=start_date,
            ec.SubReading.ts<=end_date
            ).order_by(ec.SubReading.ts)
        data = session.scalars(stmt).all()
    data = [
        {
            "timestamp": r.ts,
            "value": r.value 
        }
        for r in data
    ]

    data = pd.DataFrame(data).rename(columns={"value": column_name})
    data["timestamp"] = pd.to_datetime(data["timestamp"])
    data = data.set_index("timestamp").sort_index()

    return data


def get_energy_prices(service_point_code, start_date, end_date):
    # get service point
    with SessionLocal() as session:
        stmt = select(ec.ServicePoint).where(ec.ServicePoint.code==service_point_code)
        sp = session.scalars(stmt).one_or_none()
        sp_id = sp.sp_id
    # service point price zone
    with SessionLocal() as session:
        stmt = select(ec.ServicePointPriceZone).where(ec.ServicePointPriceZone.sp_id==sp_id)
        sppz = session.scalars(stmt).one_or_none()
        pzid = sppz.zone_id
    # get prices
    with SessionLocal() as session:
        stmt = select(ec.EnergyPriceTimeseries).where(
            ec.EnergyPriceTimeseries.zone_id==pzid,
            ec.EnergyPriceTimeseries.ts>=start_date,
            ec.EnergyPriceTimeseries.ts<=end_date
            ).order_by(ec.EnergyPriceTimeseries.ts)
        data = session.scalars(stmt).all()
    data = [
        {
            "timestamp": r.ts,
            "value": r.value 
        }
        for r in data
    ]

    data = pd.DataFrame(data).rename(columns={"value": 'price'})
    # print(f"data: {data}")
    data["timestamp"] = pd.to_datetime(data["timestamp"])
    data = data.set_index("timestamp").sort_index()

    return data


def get_co2_emissions_data(service_point_code, start_date, end_date):
    # get service point
    with SessionLocal() as session:
        stmt = select(ec.ServicePoint).where(ec.ServicePoint.code==service_point_code)
        sp = session.scalars(stmt).one_or_none()
        sp_id = sp.sp_id
    # service point emission zone
    with SessionLocal() as session:
        stmt = select(ec.EmissionZone).where(ec.EmissionZone.sp_id==sp_id)
        ez = session.scalars(stmt).one_or_none()
        ezid = ez.zone_id
    # get emission data
    with SessionLocal() as session:
        stmt = select(ec.CO2EmissionsTimeseriess).where(
            ec.CO2EmissionsTimeseries.zone_id==ezid,
            ec.CO2EmissionsTimeseries.ts>=start_date,
            ec.CO2EmissionsTimeseries.ts<=end_date
            ).order_by(ec.CO2EmissionsTimeseries.ts)
        data = session.scalars(stmt).all()
    data = [
        {
            "timestamp": r.ts,
            "value": r.value 
        }
        for r in data
    ]

    data = pd.DataFrame(data).rename(columns={"value": 'co2_emissions'})
    # print(f"data: {data}")
    data["timestamp"] = pd.to_datetime(data["timestamp"])
    data = data.set_index("timestamp").sort_index()

    return data