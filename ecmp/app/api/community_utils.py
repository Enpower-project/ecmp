from dataclasses import dataclass
from datetime import datetime
import logging
from uuid import UUID

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models.ec as ec
from app.crud.assets import get_assets_by_sp_id_crud
from app.crud.channels import get_channels_by_meter_id_crud
from app.crud.communities import get_community_id_by_member_id_crud
from app.crud.members import get_member_by_external_ref_crud
from app.crud.meters import get_meters_by_sp_id_crud
from app.crud.subchannels import get_subchannels_by_submeter_id_crud


logger = logging.getLogger(__name__)


@dataclass
class ServicePointData:
    """Prepared optimisation inputs for a single service point.

    Args:
        production: Photovoltaic production profile for the service
            point.
        consumption: Computed consumption profile for the service
            point.
        charge_power_max: Maximum BSS charging power in watts.
        discharge_power_max: Maximum BSS discharging power in watts.
        storage_capacity_max: Maximum BSS storage capacity in watt-hours.
        grid_import: Grid import profile aligned to the expected time
            index.
        grid_export: Grid export profile aligned to the expected time
            index.
        bss_charge_power: BSS charge-power profile aligned to the
            expected time index.
        bss_discharge_power: BSS discharge-power profile aligned to the
            expected time index.

    Returns:
        ServicePointData: Structured per-service-point data ready to be
        aggregated into community-level optimisation inputs.
    """

    production: np.ndarray
    consumption: np.ndarray
    charge_power_max: float
    discharge_power_max: float
    storage_capacity_max: float
    grid_import: np.ndarray
    grid_export: np.ndarray
    bss_charge_power: np.ndarray
    bss_discharge_power: np.ndarray


@dataclass
class EnergyCommunityData:
    """Prepared optimisation inputs for the full energy community.

    Args:
        pv: Community PV production matrix with shape ``(N, T)``.
        consumption: Community consumption matrix with shape ``(N, T)``.
        grid_import: Community grid-import matrix with shape ``(N, T)``.
        grid_export: Community grid-export matrix with shape ``(N, T)``.
        bss_charge_power: Community BSS charge-power matrix with shape
            ``(N, T)``.
        bss_discharge_power: Community BSS discharge-power matrix with
            shape ``(N, T)``.
        charge_power_max: Maximum charging power per service point.
        discharge_power_max: Maximum discharging power per service point.
        storage_capacity_max: Maximum storage capacity per service
            point.
        energy_prices: Energy price time series used by the optimiser.
        expected_index: Expected timestamp index for the loaded time
            series.
        num_points: Number of timesteps in the optimisation horizon.

    Returns:
        EnergyCommunityData: Structured community-level optimisation
        inputs.
    """

    pv: np.ndarray
    consumption: np.ndarray
    grid_import: np.ndarray
    grid_export: np.ndarray
    bss_charge_power: np.ndarray
    bss_discharge_power: np.ndarray
    charge_power_max: np.ndarray
    discharge_power_max: np.ndarray
    storage_capacity_max: np.ndarray
    energy_prices: pd.DataFrame
    expected_index: pd.DatetimeIndex
    num_points: int


def format_output_series(series):
    """Convert a pandas series into the frontend timestamp/value format.

    Args:
        series: Time-indexed pandas series to serialise.

    Returns:
        list[list]: List of ``[timestamp_ms, value]`` pairs.
    """
    time = (series.index.astype("int64") // 1_000_000).tolist()
    data = series.values.tolist()
    return [[t, d] for t, d in zip(time, data)]


def _power_to_watts(value, unit):
    """Convert a power value to watts.

    Args:
        value: Numeric power value to convert.
        unit: Unit associated with ``value``.

    Returns:
        float: Converted power value in watts.
    """
    if value is None:
        return 0.0

    normalized = (unit or "W").strip().lower()
    factor = {
        "w": 1.0,
        "kw": 1000.0,
        "va": 1.0,
        "kva": 1000.0,
    }.get(normalized)
    if factor is None:
        logger.warning("Unknown power unit '%s', assuming watts.", unit)
        factor = 1.0

    return float(value) * factor


def _capacity_to_wh(value, unit):
    """Convert an energy-storage capacity value to watt-hours.

    Args:
        value: Numeric capacity value to convert.
        unit: Unit associated with ``value``.

    Returns:
        float: Converted capacity value in watt-hours.
    """
    if value is None:
        return 0.0

    normalized = (unit or "Wh").strip().lower()
    factor = {
        "wh": 1.0,
        "kwh": 1000.0,
    }.get(normalized)
    if factor is None:
        logger.warning("Unknown capacity unit '%s', assuming watt-hours.", unit)
        factor = 1.0

    return float(value) * factor


def build_energy_pricing(
    energy_prices,
    num_points: int | None = None,
    *,
    buying_penalty: float = 0.15,
    selling_penalty: float = 0.15,
    tariff_kwh: float = 0.1,
) -> tuple[np.ndarray, np.ndarray]:
    """Build import and export price arrays for optimisation.

    Args:
        energy_prices: Raw energy price series in a pandas or
            NumPy-compatible structure.
        num_points: Optional number of timesteps to keep from the start
            of the price series.
        buying_penalty: Relative markup applied to imported energy.
        selling_penalty: Relative markdown applied to exported energy.
        tariff_kwh: Fixed tariff component added to imports.

    Returns:
        tuple[np.ndarray, np.ndarray]: Import and export energy price
        arrays aligned to the optimisation horizon.
    """
    energy_cost = np.asarray(energy_prices, dtype=float).reshape(-1) * 0.001
    if num_points is not None:
        energy_cost = energy_cost[:num_points]

    tariff = tariff_kwh * 0.001
    energy_cost_import = energy_cost * (1 + buying_penalty) + tariff
    energy_cost_export = energy_cost * (1 - selling_penalty)

    return energy_cost_import, energy_cost_export


def get_energy_community_id_from_token(db: Session, token_data) -> UUID:
    """Resolve the energy community id for the authenticated user.

    Args:
        db (Session): Active database session used to resolve member and
            membership data.
        token_data: Decoded authentication token containing the user
            subject identifier under ``sub``.

    Returns:
        UUID: Energy community identifier associated with the
        authenticated user.
    """
    print(f"token_data: {token_data}")
    user_id = token_data["sub"]
    member = get_member_by_external_ref_crud(db=db, external_ref=user_id)
    print(f"member: {member}")
    member_id = member.id
    print(f"member_id: {member_id}")
    membership = get_community_id_by_member_id_crud(db=db, member_id=member_id)
    print(f"membership: {membership}")
    ec_id = membership.ec_id
    print(f"ec_id: {ec_id}")
    return ec_id


def get_sm_readings(db: Session, service_point_id, channel_role, column_name, start_date, end_date):
    """Load smart meter readings for a service point channel.

    Args:
        db (Session): Active database session used to resolve the
            corresponding meter, channel, and readings.
        service_point_id: Service point identifier used to resolve the
            corresponding meter and channel.
        channel_role: Channel role to query, for example ``import`` or
            ``export``.
        column_name: Output column name used for the readings value
            column in the returned dataframe.
        start_date: Inclusive lower timestamp bound for the query.
        end_date: Inclusive upper timestamp bound for the query.

    Returns:
        pandas.DataFrame: Time-indexed dataframe sorted by timestamp and
        containing the requested reading values under ``column_name``.
    """
    sp_id = service_point_id
    stmt = select(ec.Meter).where(ec.Meter.sp_id == sp_id)
    mt = db.scalars(stmt).one_or_none()
    if mt is None:
        logger.warning(
            "No meter found while loading smart meter readings for service_point_id=%s, channel_role=%s, start_date=%s, end_date=%s.",
            sp_id,
            channel_role,
            start_date,
            end_date,
        )
        return pd.DataFrame(columns=[column_name], index=pd.DatetimeIndex([], name="timestamp"))

    mt_id = mt.meter_id
    stmt = select(ec.Channel).where(
        ec.Channel.meter_id == mt_id,
        ec.Channel.role == channel_role,
    )
    ch = db.scalars(stmt).one_or_none()
    if ch is None:
        logger.warning(
            "No channel found while loading smart meter readings for service_point_id=%s, meter_id=%s, channel_role=%s, start_date=%s, end_date=%s.",
            sp_id,
            mt_id,
            channel_role,
            start_date,
            end_date,
        )
        return pd.DataFrame(columns=[column_name], index=pd.DatetimeIndex([], name="timestamp"))

    ch_id = ch.channel_id
    logger.info(
        "Loading smart meter readings for service_point_id=%s, meter_id=%s, channel_id=%s, channel_role=%s, start_date=%s, end_date=%s.",
        sp_id,
        mt_id,
        ch_id,
        channel_role,
        start_date,
        end_date,
    )
    stmt = select(ec.Reading).where(
        ec.Reading.channel_id == ch_id,
        ec.Reading.ts >= start_date,
        ec.Reading.ts <= end_date,
    ).order_by(ec.Reading.ts)
    data = db.scalars(stmt).all()
    if not data:
        logger.warning(
            "No smart meter readings found for service_point_id=%s, meter_id=%s, channel_id=%s, channel_role=%s, start_date=%s, end_date=%s.",
            sp_id,
            mt_id,
            ch_id,
            channel_role,
            start_date,
            end_date,
        )
        return pd.DataFrame(columns=[column_name], index=pd.DatetimeIndex([], name="timestamp"))

    data = [{"timestamp": r.ts, "value": r.value} for r in data]
    data = pd.DataFrame(data).rename(columns={"value": column_name})
    data["timestamp"] = pd.to_datetime(data["timestamp"])
    logger.info(
        "Loaded %s smart meter readings for service_point_id=%s, meter_id=%s, channel_id=%s, channel_role=%s.",
        len(data),
        sp_id,
        mt_id,
        ch_id,
        channel_role,
    )
    return data.set_index("timestamp").sort_index()


def get_power_data(db: Session, service_point_id, asset_type, role, column_name, start_date, end_date):
    """Load aggregated submeter power data for service point assets.

    Args:
        db (Session): Active database session used to resolve the
            target assets, submeters, subchannels, and readings.
        service_point_id: Service point identifier used to resolve the
            target assets.
        asset_type: Asset type to select for the service point, for
            example ``PV``, ``PM``, or ``BSS``.
        role: Subchannel role to query from the selected assets.
        column_name: Output column name used for the readings value
            column in the returned dataframe.
        start_date: Inclusive lower timestamp bound for the query.
        end_date: Inclusive upper timestamp bound for the query.

    Returns:
        pandas.DataFrame: Time-indexed dataframe sorted by timestamp and
        containing the requested submeter readings under ``column_name``.
        When multiple matching assets or subchannels exist for the same
        service point, their values are summed per timestamp.
    """
    sp_id = service_point_id
    stmt = select(ec.Asset).where(
        ec.Asset.sp_id == sp_id,
        ec.Asset.type == asset_type,
    )
    assets = db.scalars(stmt).all()
    if not assets:
        logger.warning(
            "No assets found while loading power data for service_point_id=%s, asset_type=%s, role=%s, start_date=%s, end_date=%s.",
            sp_id,
            asset_type,
            role,
            start_date,
            end_date,
        )
        return pd.DataFrame(columns=[column_name], index=pd.DatetimeIndex([], name="timestamp"))

    asset_ids = [asset.id for asset in assets]
    if len(asset_ids) > 1:
        logger.info(
            "Aggregating power data for %s assets of type %s at service point %s.",
            len(asset_ids),
            asset_type,
            sp_id,
        )

    stmt = select(ec.SubMeter).where(ec.SubMeter.asset_id.in_(asset_ids))
    submeters = db.scalars(stmt).all()
    if not submeters:
        logger.warning(
            "No submeters found while loading power data for service_point_id=%s, asset_type=%s, role=%s, asset_ids=%s.",
            sp_id,
            asset_type,
            role,
            asset_ids,
        )
        return pd.DataFrame(columns=[column_name], index=pd.DatetimeIndex([], name="timestamp"))

    submeter_ids = [submeter.id for submeter in submeters]
    stmt = select(ec.SubChannel).where(
        ec.SubChannel.submeter_id.in_(submeter_ids),
        ec.SubChannel.role == role,
    )
    subchannels = db.scalars(stmt).all()
    if not subchannels:
        logger.warning(
            "No subchannels found while loading power data for service_point_id=%s, asset_type=%s, role=%s, submeter_ids=%s.",
            sp_id,
            asset_type,
            role,
            submeter_ids,
        )
        return pd.DataFrame(columns=[column_name], index=pd.DatetimeIndex([], name="timestamp"))

    subchannel_ids = [subchannel.id for subchannel in subchannels]
    logger.info(
        "Loading power data for service_point_id=%s, asset_type=%s, role=%s, asset_ids=%s, subchannel_ids=%s, start_date=%s, end_date=%s.",
        sp_id,
        asset_type,
        role,
        asset_ids,
        subchannel_ids,
        start_date,
        end_date,
    )
    stmt = select(ec.SubReading).where(
        ec.SubReading.id.in_(subchannel_ids),
        ec.SubReading.ts >= start_date,
        ec.SubReading.ts <= end_date,
    ).order_by(ec.SubReading.ts)
    data = db.scalars(stmt).all()
    if not data:
        logger.warning(
            "No subreadings found for service_point_id=%s, asset_type=%s, role=%s, subchannel_ids=%s, start_date=%s, end_date=%s.",
            sp_id,
            asset_type,
            role,
            subchannel_ids,
            start_date,
            end_date,
        )
        return pd.DataFrame(columns=[column_name], index=pd.DatetimeIndex([], name="timestamp"))

    data = [{"timestamp": r.ts, "value": r.value} for r in data]
    data = pd.DataFrame(data).groupby("timestamp", as_index=False)["value"].sum()
    data = data.rename(columns={"value": column_name})
    data["timestamp"] = pd.to_datetime(data["timestamp"])
    logger.info(
        "Loaded %s aggregated power rows for service_point_id=%s, asset_type=%s, role=%s, subchannel_ids=%s.",
        len(data),
        sp_id,
        asset_type,
        role,
        subchannel_ids,
    )
    return data.set_index("timestamp").sort_index()


def get_energy_prices(db: Session, service_point_id, start_date, end_date):
    """Load energy price time series for a service point.

    Args:
        db (Session): Active database session used to resolve the
            assigned price zone and price timeseries.
        service_point_id: Service point identifier used to resolve the
            assigned price zone.
        start_date: Inclusive lower timestamp bound for the query.
        end_date: Inclusive upper timestamp bound for the query.

    Returns:
        pandas.DataFrame: Time-indexed dataframe sorted by timestamp and
        containing the price values under the ``price`` column.
    """
    sp_id = service_point_id
    stmt = select(ec.ServicePointPriceZone).where(ec.ServicePointPriceZone.sp_id == sp_id)
    sppz = db.scalars(stmt).one_or_none()
    pzid = sppz.zone_id
    stmt = select(ec.EnergyPriceTimeseries).where(
        ec.EnergyPriceTimeseries.zone_id == pzid,
        ec.EnergyPriceTimeseries.ts >= start_date,
        ec.EnergyPriceTimeseries.ts <= end_date,
    ).order_by(ec.EnergyPriceTimeseries.ts)
    data = db.scalars(stmt).all()
    data = [{"timestamp": r.ts, "value": r.value} for r in data]
    data = pd.DataFrame(data).rename(columns={"value": "price"})
    data["timestamp"] = pd.to_datetime(data["timestamp"])
    return data.set_index("timestamp").sort_index()


def build_community_relations(db: Session, ec_id, service_points):
    """Build a nested community-to-service-point relation structure.

    Args:
        db (Session): Active database session used to load related
            meters, channels, assets, and subchannels.
        ec_id: Energy community identifier for which the relation
            structure is assembled.
        service_points: Iterable of service point objects belonging to
            the energy community.

    Returns:
        dict: Dictionary containing the energy community id under
        ``ec_id`` and a ``service_points`` list with nested service
        point, asset, meter, submeter, channel, and subchannel data.
    """
    community = db.get(ec.EnergyCommunity, ec_id)
    community_relations = {
        "ec_id": str(ec_id),
        "type": community.type if community is not None else None,
        "service_points": [],
    }
    for service_point in service_points:
        meters = get_meters_by_sp_id_crud(db=db, sp_id=service_point.sp_id)
        assets = get_assets_by_sp_id_crud(db=db, sp_id=service_point.sp_id)
        community_relations["service_points"].append(
            {
                "sp_id": str(service_point.sp_id),
                "code": service_point.code,
                "role": service_point.role,
                "assets": [
                    {
                        "asset_id": str(asset.id),
                        "hrid": asset.hrid,
                        "type": asset.type,
                        "installed_power": float(asset.installed_power) if asset.installed_power is not None else None,
                        "uom": asset.uom,
                        "description": asset.description,
                        "properties": asset.properties,
                        "submeters": [
                            {
                                "submeter_id": str(submeter.id),
                                "serial_no": submeter.serial_no,
                                "hrid": submeter.hrid,
                                "type": submeter.type,
                                "description": submeter.description,
                                "subchannels": [
                                    {
                                        "subchannel_id": str(subchannel.id),
                                        "hrid": subchannel.hrid,
                                        "role": subchannel.role,
                                        "uom": subchannel.uom,
                                        "interval_minutes": subchannel.interval_minutes,
                                        "scaling_factor": float(subchannel.scaling_factor),
                                    }
                                    for subchannel in get_subchannels_by_submeter_id_crud(
                                        db=db,
                                        submeter_id=submeter.id,
                                    )
                                ],
                            }
                            for submeter in asset.submeters
                        ],
                    }
                    for asset in assets
                ],
                "meters": [
                    {
                        "meter_id": str(meter.meter_id),
                        "serial_no": meter.serial_no,
                        "model": meter.model,
                        "channels": [
                            {
                                "channel_id": str(channel.channel_id),
                                "role": channel.role,
                                "uom": channel.uom,
                                "interval_minutes": channel.interval_minutes,
                                "scaling_factor": float(channel.scaling_factor),
                            }
                            for channel in get_channels_by_meter_id_crud(db=db, meter_id=meter.meter_id)
                        ],
                    }
                    for meter in meters
                ],
            }
        )
    return community_relations


def load_service_point_data(db: Session, service_point, start_date, end_date, timestep_minutes=15):
    """Load and normalise data for a single service point.

    Args:
        db (Session): Active database session used to load smart meter,
            submeter, and pricing data for the service point.
        service_point: Service point relation dictionary containing
            asset and meter metadata.
        start_date: Inclusive lower timestamp bound for the query.
        end_date: Inclusive upper timestamp bound for the query.
        timestep_minutes: Resolution of the expected time index in
            minutes.
    Returns:
        ServicePointData: Structured service point production, consumption,
        and storage capability data aligned to the expected time index.
    """
    logger.info(f"sp_id: {service_point['sp_id']}")
    logger.info("assets:")

    def _get_meter_channel_uom(channel_role: str) -> str:
        for meter in service_point["meters"]:
            for channel in meter.get("channels", []):
                if channel.get("role") == channel_role:
                    return channel.get("uom")
        return "W"

    def _get_asset_subchannel_uom(asset_type: str, role: str) -> str:
        for asset in service_point["assets"]:
            if asset.get("type") != asset_type:
                continue
            for submeter in asset.get("submeters", []):
                for subchannel in submeter.get("subchannels", []):
                    if subchannel.get("role") == role:
                        return subchannel.get("uom")
        return "W"

    pv_presence = False
    bss_presence = False
    charge_power_max = 0.0
    discharge_power_max = 0.0
    storage_capacity_max = 0.0

    for asset in service_point["assets"]:
        logger.info(f"asset_id: {asset['asset_id']}, asset_type: {asset['type']}")
        if asset["type"] == "PV":
            pv_presence = True
        elif asset["type"] == "BSS":
            bss_presence = True
            properties = asset.get("properties") or {}
            charge_power_max += _power_to_watts(asset.get("installed_power"), asset.get("uom"))
            discharge_power_max += _power_to_watts(asset.get("installed_power"), asset.get("uom"))
            storage_capacity_max += _capacity_to_wh(properties.get("capacity"), properties.get("uom"))

    logger.info("meters:")
    for meter in service_point["meters"]:
        logger.info(f"meter_id: {meter['meter_id']}, serial_no: {meter['serial_no']}")

    logger.info(f"PV_presence: {pv_presence}")

    grid_import_uom = _get_meter_channel_uom("import")
    grid_export_uom = _get_meter_channel_uom("export")
    production_power_uom = _get_asset_subchannel_uom("PV", "export_power")

    grid_import = get_sm_readings(
        db=db,
        service_point_id=service_point["sp_id"],
        channel_role="import",
        column_name="data",
        start_date=start_date,
        end_date=end_date,
    ).astype(float).apply(lambda column: column.map(lambda value: _power_to_watts(value, grid_import_uom)))
    grid_export = get_sm_readings(
        db=db,
        service_point_id=service_point["sp_id"],
        channel_role="export",
        column_name="data",
        start_date=start_date,
        end_date=end_date,
    ).astype(float).apply(lambda column: column.map(lambda value: _power_to_watts(value, grid_export_uom)))

    if pv_presence:
        production_power = get_power_data(
            db=db,
            service_point_id=service_point["sp_id"],
            asset_type="PV",
            role="export_power",
            column_name="data",
            start_date=start_date,
            end_date=end_date,
        ).astype(float).apply(lambda column: column.map(lambda value: _power_to_watts(value, production_power_uom)))
    else:
        production_index = grid_import.index.union(grid_export.index).sort_values()
        production_power = pd.DataFrame(0.0, index=production_index, columns=["data"])

    if bss_presence:
        bss_charge_power = get_power_data(
            db=db,
            service_point_id=service_point["sp_id"],
            asset_type="BSS",
            role="charge_power",
            column_name="data",
            start_date=start_date,
            end_date=end_date,
        ).astype(float)
        bss_discharge_power = get_power_data(
            db=db,
            service_point_id=service_point["sp_id"],
            asset_type="BSS",
            role="discharge_power",
            column_name="data",
            start_date=start_date,
            end_date=end_date,
        ).astype(float)
    else:
        bss_index = grid_import.index.union(grid_export.index).sort_values()
        bss_charge_power = pd.DataFrame(0.0, index=bss_index, columns=["data"])
        bss_discharge_power = pd.DataFrame(0.0, index=bss_index, columns=["data"])

    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    expected_index = pd.date_range(
        start=start,
        end=end,
        freq=f"{timestep_minutes}min",
        inclusive="left",
    )
    # make it UTC-aware
    expected_index = pd.to_datetime(expected_index, utc=True)

    grid_import_series = grid_import["data"].reindex(expected_index, fill_value=0.0)
    grid_export_series = grid_export["data"].reindex(expected_index, fill_value=0.0)
    production_series = production_power["data"].reindex(expected_index, fill_value=0.0)
    bss_charge_series = bss_charge_power["data"].reindex(expected_index, fill_value=0.0)
    bss_discharge_series = bss_discharge_power["data"].reindex(expected_index, fill_value=0.0)

    consumption = (
        grid_import_series
        - grid_export_series
        + production_series
        - bss_charge_series
        + bss_discharge_series
    )

    return ServicePointData(
        production=production_series.to_numpy(),
        consumption=consumption.to_numpy(),
        charge_power_max=charge_power_max,
        discharge_power_max=discharge_power_max,
        storage_capacity_max=storage_capacity_max,
        grid_import=grid_import_series.to_numpy(),
        grid_export=grid_export_series.to_numpy(),
        bss_charge_power=bss_charge_series.to_numpy(),
        bss_discharge_power=bss_discharge_series.to_numpy(),
    )


def load_energy_community_data(db: Session, community_relations, start_date, end_date, timestep_minutes=15):
    """Load and prepare optimisation inputs for the full community.

    Args:
        db (Session): Active database session shared across all data
            loading steps for the community.
        community_relations: Nested community relation dictionary with
            service point metadata.
        start_date: Inclusive lower timestamp bound for the query.
        end_date: Inclusive upper timestamp bound for the query.
        timestep_minutes: Resolution of the optimisation horizon in
            minutes.

    Returns:
        EnergyCommunityData: Structured community-level arrays and time
        series ready for optimisation.
    """
    service_points = community_relations["service_points"]
    num_service_points = len(service_points)

    energy_prices = get_energy_prices(
        db=db,
        service_point_id=service_points[0]["sp_id"],
        start_date=start_date,
        end_date=end_date,
    ).astype(float)
    logger.info(f"energy prices: {energy_prices}")

    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date)
    duration_minutes = (end - start).total_seconds() / 60
    num_points = int(duration_minutes / timestep_minutes)
    logger.info(f"Number of data points: {num_points}")

    pv = np.zeros((num_service_points, num_points))
    consumption = np.zeros((num_service_points, num_points))
    grid_import = np.zeros((num_service_points, num_points))
    grid_export = np.zeros((num_service_points, num_points))
    bss_charge_power = np.zeros((num_service_points, num_points))
    bss_discharge_power = np.zeros((num_service_points, num_points))
    charge_power_max = np.zeros(num_service_points)
    discharge_power_max = np.zeros(num_service_points)
    storage_capacity_max = np.zeros(num_service_points)
    expected_index = None

    for sp_idx, service_point in enumerate(service_points):
        service_point_data = load_service_point_data(
            db=db,
            service_point=service_point,
            start_date=start_date,
            end_date=end_date,
            timestep_minutes=timestep_minutes,
        )
        pv[sp_idx, :] = service_point_data.production
        consumption[sp_idx, :] = service_point_data.consumption
        grid_import[sp_idx, :] = service_point_data.grid_import
        grid_export[sp_idx, :] = service_point_data.grid_export
        bss_charge_power[sp_idx, :] = service_point_data.bss_charge_power
        bss_discharge_power[sp_idx, :] = service_point_data.bss_discharge_power
        charge_power_max[sp_idx] = service_point_data.charge_power_max
        discharge_power_max[sp_idx] = service_point_data.discharge_power_max
        storage_capacity_max[sp_idx] = service_point_data.storage_capacity_max

        if expected_index is None:
            expected_index = pd.date_range(
                start=pd.to_datetime(start_date),
                periods=num_points,
                freq=f"{timestep_minutes}min",
            )

    return EnergyCommunityData(
        pv=pv,
        consumption=consumption,
        grid_import=grid_import,
        grid_export=grid_export,
        bss_charge_power=bss_charge_power,
        bss_discharge_power=bss_discharge_power,
        charge_power_max=charge_power_max,
        discharge_power_max=discharge_power_max,
        storage_capacity_max=storage_capacity_max,
        energy_prices=energy_prices,
        expected_index=expected_index,
        num_points=num_points,
    )
