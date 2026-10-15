from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from app.db.session import SessionLocal
from app.api import community_utils as cu
from app.schemas.energy_report import (
    EnergySeries,
    EnergySummary,
    ReportWindow,
    ServicePointReport,
    StatusReport,
    StatusResponse,
    TimeSeries,
)
from app.schemas.status import StatusParameters
from app.auth.dependencies import require_token
from app.crud.service_points import get_service_points_by_ec_id_crud
import app.models.ec as ec
from app.optimisation.entity import Entity
from datetime import datetime, timedelta
import pandas as pd
import numpy as np


import logging

from app.keycloak_instance import kc

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/status", tags=["status"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def build_status_timeseries_output(
    energy_prices: pd.DataFrame,
    production: np.ndarray,
    consumption: np.ndarray,
    grid_import: np.ndarray,
    grid_export: np.ndarray,
    bss_charge_power: np.ndarray,
    bss_discharge_power: np.ndarray,
) -> dict:
    """Build timestamped status output series from aggregate arrays.

    Args:
        energy_prices: Energy price dataframe aligned to the status horizon.
        production: Aggregate production profile in watts.
        consumption: Aggregate consumption profile in watts.
        grid_import: Aggregate grid-import profile in watts.
        grid_export: Aggregate grid-export profile in watts.
        bss_charge_power: Aggregate BSS charge-power profile in watts.
        bss_discharge_power: Aggregate BSS discharge-power profile in watts.

    Returns:
        dict: Timestamped status output series.
    """
    num_points = len(production)
    ts_index = pd.to_datetime(energy_prices.index[:num_points], utc=True, errors="raise")
    # unix timestamp in ms
    ts_ms = (ts_index.view("int64") // 10**3).astype(np.int64)
    stored_energy_profile = np.cumsum((bss_charge_power - bss_discharge_power) * 0.25)
    energy_price_values = energy_prices["price"].to_numpy()[:num_points]

    def build_series(values: np.ndarray) -> list[list[float]]:
        """Build [timestamp_ms, value] pairs with integer timestamps."""
        # return [[int(t), float(v)] for t, v in zip(ts_index, values)]
        return [[int(t), float(v)] for t, v in zip(ts_ms, values)]

    return {
        "energy_prices_out": build_series(energy_price_values),
        "consumption_profile": build_series(consumption),
        "production_profile": build_series(production),
        "stored_energy_profile": build_series(stored_energy_profile),
        "import_profile": build_series(grid_import),
        "export_profile": build_series(grid_export),
        # "import_profile": build_series(grid_import * 0.25),
        # "export_profile": build_series(grid_export * 0.25),
    }


def build_status_summary_metrics(
    production: np.ndarray,
    consumption: np.ndarray,
    grid_import: np.ndarray,
    grid_export: np.ndarray,
    bss_charge_power: np.ndarray,
    energy_cost_import: np.ndarray,
    energy_cost_export: np.ndarray,
    *,
    timestep_hours: float = 0.25,
    trailing_steps: int = 96,
    co2_factor: float = 0.150,
) -> dict:
    """Build summary status metrics from aggregate arrays.

    Args:
        production: Aggregate production profile in watts.
        consumption: Aggregate consumption profile in watts.
        grid_import: Aggregate grid-import profile in watts.
        grid_export: Aggregate grid-export profile in watts.
        bss_charge_power: Aggregate BSS charge-power profile in watts.
        energy_cost_import: Import energy price series aligned to the
            status horizon.
        energy_cost_export: Export energy price series aligned to the
            status horizon.
        timestep_hours: Duration of each timestep in hours.
        trailing_steps: Number of trailing timesteps used for totals.
        co2_factor: CO2 emissions factor applied to imported energy.

    Returns:
        dict: Summary status metrics for the supplied profiles.
    """
    self_consumed_power = np.minimum(production, consumption + bss_charge_power)

    consumption_wh = consumption * timestep_hours
    production_wh = production * timestep_hours
    grid_import_wh = grid_import * timestep_hours
    grid_export_wh = grid_export * timestep_hours
    self_consumed_wh = self_consumed_power * timestep_hours

    total_consumed_energy = consumption_wh[-trailing_steps:].sum()
    total_produced_energy = production_wh[-trailing_steps:].sum()
    total_imported_energy = grid_import_wh[-trailing_steps:].sum()
    total_exported_energy = grid_export_wh[-trailing_steps:].sum()
    total_self_consumed_energy = self_consumed_wh[-trailing_steps:].sum()

    import_cost = np.sum(grid_import_wh[-trailing_steps:] * energy_cost_import[-trailing_steps:])
    export_revenue = np.sum(grid_export_wh[-trailing_steps:] * energy_cost_export[-trailing_steps:])
    expenses = import_cost - export_revenue
    self_sufficiency = (total_self_consumed_energy / total_consumed_energy) * 100.0
    self_consumption = (total_self_consumed_energy / total_produced_energy) * 100.0
    co2_emissions = np.sum(grid_import_wh[-trailing_steps:] * co2_factor)

    return {
        "self_consumption": self_consumption,
        "self_sufficiency": self_sufficiency,
        "co2_emissions": co2_emissions,
        "expenses": expenses,
        "imported_energy": total_imported_energy,
        "exported_energy": total_exported_energy,
        "produced_energy": total_produced_energy,
        "consumed_energy": total_consumed_energy,
        "self_consumed_energy": total_self_consumed_energy,
        "total_energy_demanded": total_consumed_energy,
    }


def _get_active_link_coefficient(coefficients, timestamp) -> float:
    """Resolve the active allocation coefficient for one timestamp."""
    active_coefficients = []
    for coefficient in coefficients:
        valid_from = pd.Timestamp(coefficient.valid_from)
        valid_to = pd.Timestamp(coefficient.valid_to) if coefficient.valid_to is not None else None
        ts = pd.Timestamp(timestamp)

        if valid_from.tzinfo is None:
            valid_from = valid_from.tz_localize("UTC")
        else:
            valid_from = valid_from.tz_convert("UTC")

        if valid_to is not None:
            if valid_to.tzinfo is None:
                valid_to = valid_to.tz_localize("UTC")
            else:
                valid_to = valid_to.tz_convert("UTC")

        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")

        if valid_from <= ts and (valid_to is None or ts < valid_to):
            active_coefficients.append((valid_from, float(coefficient.coefficient)))

    if not active_coefficients:
        return 0.0

    active_coefficients.sort(key=lambda item: item[0], reverse=True)
    return active_coefficients[0][1]


def apply_csc_allocations(
    db: Session,
    ec_id,
    community_relations: dict,
    service_point_data: list,
    expected_index: pd.DatetimeIndex,
) -> dict:
    """Apply stored CSC allocation coefficients to service-point grid flows.

    This function transforms the raw per-service-point import/export
    flows loaded by ``load_service_point_data`` into CSC allocation-aware
    net grid flows. For every active producer-to-recipient allocation
    link stored in the database, it resolves the coefficient that is
    valid for each timestep and uses that coefficient to distribute a
    portion of the producer's exported surplus to the recipient's
    imported demand.

    The allocation is applied independently for each timestep and is
    constrained by three limits:
    - the producer's remaining export at that timestep
    - the recipient's remaining import at that timestep
    - the configured allocated share of the producer's original export

    The original raw arrays in ``service_point_data`` are not mutated.
    Instead, the function works on copied arrays and returns both the
    adjusted net grid flows and the separately tracked allocated energy
    transfer volumes.

    Args:
        db: Active SQLAlchemy session used to load allocation links and
            their coefficient histories.
        ec_id: Energy community identifier used to scope the allocation
            lookup.
        community_relations: Community relation dictionary produced by
            ``build_community_relations``. The service-point order in
            this structure defines the row order for the returned
            arrays.
        service_point_data: Per-service-point flow data returned by
            ``load_service_point_data``. Raw ``grid_import`` and
            ``grid_export`` values are used as the allocation baseline.
        expected_index: Timestamp index for the reporting horizon. Each
            timestamp is matched against coefficient validity windows.

    Returns:
        dict: Allocation-adjusted arrays with the following keys:
        ``grid_import``: Net grid import after local CSC allocation.
        ``grid_export``: Net grid export after local CSC allocation.
        ``allocated_import``: Amount allocated to each recipient per
        timestep.
        ``allocated_export``: Amount allocated away from each producer
        per timestep.
    """
    service_points = community_relations["service_points"]
    sp_index_by_id = {service_point["sp_id"]: idx for idx, service_point in enumerate(service_points)}
    logger.debug(f"sp_index_by_id: {sp_index_by_id}")

    adjusted_grid_import = np.array([point_data.grid_import.copy() for point_data in service_point_data])
    adjusted_grid_export = np.array([point_data.grid_export.copy() for point_data in service_point_data])
    allocated_import = np.zeros_like(adjusted_grid_import)
    allocated_export = np.zeros_like(adjusted_grid_export)

    allocation_links = (
        db.query(ec.ServicePointAllocationLink)
        .options(selectinload(ec.ServicePointAllocationLink.coefficients))
        .filter(
            ec.ServicePointAllocationLink.ec_id == ec_id,
            ec.ServicePointAllocationLink.active.is_(True),
        )
        .order_by(
            ec.ServicePointAllocationLink.producer_sp_id,
            ec.ServicePointAllocationLink.recipient_sp_id,
        )
        .all()
    )

    links_by_producer = {}
    for link in allocation_links:
        producer_sp_id = str(link.producer_sp_id)
        recipient_sp_id = str(link.recipient_sp_id)
        if producer_sp_id not in sp_index_by_id or recipient_sp_id not in sp_index_by_id:
            continue
        links_by_producer.setdefault(producer_sp_id, []).append(link)
    
    logger.debug(f"links_by_producer: {links_by_producer}")

    for producer_sp_id, links in links_by_producer.items():
        producer_idx = sp_index_by_id[producer_sp_id]
        for ts_idx, timestamp in enumerate(expected_index):
            producer_remaining_export = adjusted_grid_export[producer_idx, ts_idx]
            if producer_remaining_export <= 0.0:
                continue

            for link in links:
                recipient_sp_id = str(link.recipient_sp_id)
                recipient_idx = sp_index_by_id[recipient_sp_id]
                recipient_remaining_import = adjusted_grid_import[recipient_idx, ts_idx]
                if recipient_remaining_import <= 0.0:
                    continue

                coefficient = _get_active_link_coefficient(link.coefficients, timestamp)
                if coefficient <= 0.0:
                    continue

                requested_allocation = service_point_data[producer_idx].grid_export[ts_idx] * coefficient
                applied_allocation = min(
                    producer_remaining_export,
                    recipient_remaining_import,
                    requested_allocation,
                )
                if applied_allocation <= 0.0:
                    continue

                adjusted_grid_export[producer_idx, ts_idx] -= applied_allocation
                adjusted_grid_import[recipient_idx, ts_idx] -= applied_allocation
                allocated_export[producer_idx, ts_idx] += applied_allocation
                allocated_import[recipient_idx, ts_idx] += applied_allocation
                producer_remaining_export -= applied_allocation

                if producer_remaining_export <= 0.0:
                    break

    return {
        "grid_import": adjusted_grid_import,
        "grid_export": adjusted_grid_export,
        "allocated_import": allocated_import,
        "allocated_export": allocated_export,
    }


def build_legacy_entity_output(entity_reports: list[dict]) -> list[dict]:
    """Build the existing entity list output shape."""
    return [
        {
            "imported_energy": entity_report["imported_energy"],
            "exported_energy": entity_report["exported_energy"],
            "produced_energy": entity_report["produced_energy"],
            "self_consumption": entity_report["self_consumption"],
            "self_sufficiency": entity_report["self_sufficiency"],
            "expenses": entity_report["expenses"],
            "co2_emissions": entity_report["co2_emissions"],
        }
        for entity_report in entity_reports
    ]


def build_csc_entity_output(
    community_relations: dict,
    service_point_data: list,
    energy_cost_import: np.ndarray,
    energy_cost_export: np.ndarray,
    allocated_flows: dict,
) -> dict:
    """Build CSC_00 role-aware service-point status output."""
    service_point_reports = []
    role_groups = {"producer": [], "consumer": [], "prosumer": []}

    for idx, (service_point, point_data) in enumerate(zip(community_relations["service_points"], service_point_data)):
        summary = build_status_summary_metrics(
            production=point_data.production,
            consumption=point_data.consumption,
            grid_import=allocated_flows["grid_import"][idx],
            grid_export=allocated_flows["grid_export"][idx],
            bss_charge_power=point_data.bss_charge_power,
            energy_cost_import=energy_cost_import,
            energy_cost_export=energy_cost_export,
        )
        report = {
            "sp_id": service_point["sp_id"],
            "code": service_point["code"],
            "role": service_point.get("role"),
            "imported_energy": summary["imported_energy"],
            "exported_energy": summary["exported_energy"],
            "produced_energy": summary["produced_energy"],
            "consumed_energy": summary["consumed_energy"],
            "self_consumed_energy": summary["self_consumed_energy"],
            "self_consumption": summary["self_consumption"],
            "self_sufficiency": summary["self_sufficiency"],
            "expenses": summary["expenses"],
            "co2_emissions": summary["co2_emissions"],
            "total_energy_demanded": summary["total_energy_demanded"],
            "allocated_imported_energy": float(np.sum(allocated_flows["allocated_import"][idx] * 0.25)),
            "allocated_exported_energy": float(np.sum(allocated_flows["allocated_export"][idx] * 0.25)),
        }
        service_point_reports.append(report)

        role = service_point.get("role")
        if role in role_groups:
            role_groups[role].append(report)

    return {
        "service_points": service_point_reports,
        "role_groups": role_groups,
    }


@router.post("", response_model=StatusResponse)
async def status(
    params: StatusParameters,
    db: Session = Depends(get_db),
    token=Depends(require_token("admin")),
) -> StatusResponse:
    date = params.datetime
    # shift to the end of the selected date
    date = (datetime.strptime(date, "%Y-%m-%dT%H:%M:%S") + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    date1 = (datetime.strptime(date, "%Y-%m-%dT%H:%M:%S") - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")

    ec_id = cu.get_energy_community_id_from_token(db=db, token_data=token)
    service_points = get_service_points_by_ec_id_crud(db=db, ec_id=ec_id)
    community_relations = cu.build_community_relations(
        db=db,
        ec_id=ec_id,
        service_points=service_points,
    )
    community_data = cu.load_energy_community_data(
        db=db,
        community_relations=community_relations,
        start_date=date1,
        end_date=date,
    )
    energy_cost_import, energy_cost_export = cu.build_energy_pricing(
        energy_prices=community_data.energy_prices,
        num_points=community_data.num_points,
    )

    print(f"start: {date1}, end: {date}")

    service_point_data = [
        cu.load_service_point_data(
            db=db,
            service_point=service_point,
            start_date=date1,
            end_date=date,
        )
        for service_point in community_relations["service_points"]
    ]

    logger.info(f"sp_data: {service_point_data}")

    # allocation process for CSC EnC

    

    community_type = community_relations.get("type")
    logger.debug(f"community_type: {community_type}")

    if community_type == "CSC_00":
        allocated_flows = apply_csc_allocations(
            db=db,
            ec_id=ec_id,
            community_relations=community_relations,
            service_point_data=service_point_data,
            expected_index=community_data.expected_index,
        )

        aggregated_summary = build_status_summary_metrics(
            production=np.sum(community_data.pv, axis=0),
            consumption=np.sum(community_data.consumption, axis=0),
            grid_import=np.sum(allocated_flows["grid_import"], axis=0),
            grid_export=np.sum(allocated_flows["grid_export"], axis=0),
            bss_charge_power=np.sum(community_data.bss_charge_power, axis=0),
            energy_cost_import=energy_cost_import,
            energy_cost_export=energy_cost_export,
        )

        aggregated_timeseries = build_status_timeseries_output(
            energy_prices=community_data.energy_prices,
            production=np.sum(community_data.pv, axis=0),
            consumption=np.sum(community_data.consumption, axis=0),
            grid_import=np.sum(allocated_flows["grid_import"], axis=0),
            grid_export=np.sum(allocated_flows["grid_export"], axis=0),
            bss_charge_power=np.sum(community_data.bss_charge_power, axis=0),
            bss_discharge_power=np.sum(community_data.bss_discharge_power, axis=0),
        )

        entities_output = build_csc_entity_output(
            community_relations=community_relations,
            service_point_data=service_point_data,
            energy_cost_import=energy_cost_import,
            energy_cost_export=energy_cost_export,
            allocated_flows=allocated_flows,
        )
    else:
        aggregated_summary = build_status_summary_metrics(
            production=np.sum(community_data.pv, axis=0),
            consumption=np.sum(community_data.consumption, axis=0),
            grid_import=np.sum(community_data.grid_import, axis=0),
            grid_export=np.sum(community_data.grid_export, axis=0),
            bss_charge_power=np.sum(community_data.bss_charge_power, axis=0),
            energy_cost_import=energy_cost_import,
            energy_cost_export=energy_cost_export,
        )

        aggregated_timeseries = build_status_timeseries_output(
            energy_prices=community_data.energy_prices,
            production=np.sum(community_data.pv, axis=0),
            consumption=np.sum(community_data.consumption, axis=0),
            grid_import=np.sum(community_data.grid_import, axis=0),
            grid_export=np.sum(community_data.grid_export, axis=0),
            bss_charge_power=np.sum(community_data.bss_charge_power, axis=0),
            bss_discharge_power=np.sum(community_data.bss_discharge_power, axis=0),
        )
        
        entity_reports = [
            build_status_summary_metrics(
                production=point_data.production,
                consumption=point_data.consumption,
                grid_import=point_data.grid_import,
                grid_export=point_data.grid_export,
                bss_charge_power=point_data.bss_charge_power,
                energy_cost_import=energy_cost_import,
                energy_cost_export=energy_cost_export,
            )
            for point_data in service_point_data
        ]
        entities_output = build_legacy_entity_output(entity_reports)

    def make_summary(metrics: dict) -> EnergySummary:
        return EnergySummary(
            imported_energy_wh=float(metrics["imported_energy"]),
            exported_energy_wh=float(metrics["exported_energy"]),
            produced_energy_wh=float(metrics["produced_energy"]),
            consumed_energy_wh=float(metrics["consumed_energy"]),
            self_consumed_energy_wh=float(metrics["self_consumed_energy"]),
            self_consumption_pct=float(metrics["self_consumption"]),
            self_sufficiency_pct=float(metrics["self_sufficiency"]),
            expenses_eur=float(metrics["expenses"]),
            co2_emissions_kg=float(metrics["co2_emissions"]),
        )

    def make_series(points: list[list[float]], unit: str) -> TimeSeries:
        return TimeSeries(
            points=[(int(timestamp), float(value)) for timestamp, value in points],
            unit=unit,
        )

    timestamps = aggregated_timeseries["energy_prices_out"]
    report_start = pd.to_datetime(timestamps[0][0], unit="ms", utc=True).to_pydatetime()
    report_end = (
        pd.to_datetime(timestamps[-1][0], unit="ms", utc=True)
        + pd.Timedelta(minutes=15)
    ).to_pydatetime()
    battery_timestamps = [point[0] for point in timestamps]
    aggregated_charge_power = np.sum(community_data.bss_charge_power, axis=0)
    aggregated_discharge_power = np.sum(community_data.bss_discharge_power, axis=0)

    if community_type == "CSC_00":
        service_point_reports = [
            ServicePointReport(
                service_point_id=entity["sp_id"],
                code=entity["code"],
                role=entity["role"],
                summary=make_summary(entity),
            )
            for entity in entities_output["service_points"]
        ]
    else:
        service_point_reports = [
            ServicePointReport(
                service_point_id=str(service_point["sp_id"]),
                code=service_point["code"],
                role=service_point.get("role"),
                summary=make_summary(entity_report),
            )
            for service_point, entity_report in zip(
                community_relations["service_points"], entity_reports
            )
        ]

    report = StatusReport(
        window=ReportWindow(
            start_datetime=report_start,
            end_datetime=report_end,
            timestep_minutes=15,
        ),
        community_type=community_type,
        summary=make_summary(aggregated_summary),
        series=EnergySeries(
            energy_price=make_series(aggregated_timeseries["energy_prices_out"], "EUR/kWh"),
            consumption_power=make_series(aggregated_timeseries["consumption_profile"], "W"),
            production_power=make_series(aggregated_timeseries["production_profile"], "W"),
            grid_import_power=make_series(aggregated_timeseries["import_profile"], "W"),
            grid_export_power=make_series(aggregated_timeseries["export_profile"], "W"),
            battery_charge_power=TimeSeries(
                points=[
                    (int(timestamp), float(value))
                    for timestamp, value in zip(battery_timestamps, aggregated_charge_power)
                ],
                unit="W",
            ),
            battery_discharge_power=TimeSeries(
                points=[
                    (int(timestamp), float(value))
                    for timestamp, value in zip(battery_timestamps, aggregated_discharge_power)
                ],
                unit="W",
            ),
            stored_energy=make_series(aggregated_timeseries["stored_energy_profile"], "Wh"),
        ),
        service_points=service_point_reports,
    )

    return StatusResponse(report=report)

    # Legacy response retained in place for reference during the API-v2 migration.
    # output = {
    #     'datetime': date,
    #     'data': {
    #         'community_type': community_type,
    #         'aggregated': {
    #             'self_consumption': aggregated_summary['self_consumption'],
    #             'self_sufficiency': aggregated_summary['self_sufficiency'],
    #             'co2_emissions': aggregated_summary['co2_emissions'],
    #             'expenses': aggregated_summary['expenses'],
    #             'imported_energy': aggregated_summary['imported_energy'],
    #             'exported_energy': aggregated_summary['exported_energy'],
    #             'produced_energy': aggregated_summary['produced_energy'],
    #             'consumed_energy': aggregated_summary['consumed_energy'],
    #             'self_consumed_energy': aggregated_summary['self_consumed_energy'],
    #             'total_energy_demanded': aggregated_summary['total_energy_demanded'],
    #             'energy_prices': aggregated_timeseries['energy_prices_out'],
    #             'consumption_profile': aggregated_timeseries['consumption_profile'],
    #             'production_profile': aggregated_timeseries['production_profile'],
    #             'stored_energy_profile': aggregated_timeseries['stored_energy_profile'],
    #             'import_profile': aggregated_timeseries['import_profile'],
    #             'export_profile': aggregated_timeseries['export_profile']
    #         },
    #         'entities': entities_output
    #     }
    # }
    # json_str = json.dumps(output)
    # return json_str

