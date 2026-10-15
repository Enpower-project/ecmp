"""Simulation API for community energy-flow reporting.

This module exposes the ``/simulation`` endpoint used to run lightweight
what-if simulations on top of the already prepared service-point and
community data structures from :mod:`app.api.community_utils`.

Currently supported strategies
------------------------------
- ``no-optimization``: reports the loaded historical/derived energy
  flows without changing battery behaviour.
- ``self-consumption``: re-simulates battery behaviour to maximize
  local use of PV production.
- ``collective-self-consumption``: reserved for future work and still
  returns ``501 Not Implemented``.

Battery discharge scope
-----------------------
- ``local``: default and current behaviour. Batteries discharge only
  against the logical service point they belong to.
- ``community``: optional behaviour for self-consumption simulation.
  Batteries may discharge against whole-community demand while
  preserving the existing local-only mode for current callers.

Why the split-asset fallback exists
-----------------------------------
The legacy self-consumption optimizer in :class:`app.optimisation.entity.Entity`
assumes the battery and PV belong to the same logical entity. That works
for communities where each service point has its own PV and storage, but
it underperforms when PV is installed on one service point and storage on
another.

To support those communities without rewriting the optimizer, this module
detects the split-asset layout and temporarily aggregates the community
into one virtual optimization entity. The resulting optimized battery
schedule is then redistributed back to the service points that actually
own storage.
"""

from datetime import datetime, timedelta
import logging

from fastapi import APIRouter, Depends, HTTPException
import numpy as np
import pandas as pd
from sqlalchemy.orm import selectinload
from sqlalchemy.orm import Session

from app.api import community_utils as cu
from app.api.status import (
    _get_active_link_coefficient,
    apply_csc_allocations,
    build_status_summary_metrics,
    build_status_timeseries_output,
)
from app.auth.dependencies import require_token
from app.crud.service_points import get_service_points_by_ec_id_crud
from app.db.session import SessionLocal
import app.models.ec as ec
from app.optimisation.entity import Entity
from app.schemas.energy_report import (
    EnergySeries,
    EnergySummary,
    ReportWindow,
    SimulationDetails,
    SimulationReport,
    SimulationResponse,
    TimeSeries,
)
from app.schemas.simulation import SimulationParameters


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/simulation", tags=["simulation"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _build_series(values: np.ndarray, index: pd.DatetimeIndex) -> pd.Series:
    """Convert array-like values into a float series on the given index."""
    return pd.Series(np.asarray(values, dtype=float), index=index)


def _simulate_self_consumption_service_point(
    point_data: cu.ServicePointData,
    expected_index: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Run the legacy self-consumption optimizer for one logical entity.

    Args:
        point_data: Service-point level energy-flow and storage-capability
            inputs.
        expected_index: Simulation timestamps aligned to the prepared
            input arrays.

    Returns:
        pandas.DataFrame: Optimized timeseries returned by
        :meth:`Entity.self_consumption_optimization`.
    """
    bss_max_power = max(float(point_data.charge_power_max), float(point_data.discharge_power_max))
    entity = Entity(
        bss_capacity=float(point_data.storage_capacity_max),
        bss_max_power=bss_max_power,
    )
    zero_soc = pd.Series(0.0, index=expected_index)
    entity.set_entity_data(
        import_power=_build_series(point_data.grid_import, expected_index),
        export_power=_build_series(point_data.grid_export, expected_index),
        production_power=_build_series(point_data.production, expected_index),
        consumption_power=_build_series(point_data.consumption, expected_index),
        bss_charge_power=_build_series(point_data.bss_charge_power, expected_index),
        bss_discharge_power=_build_series(point_data.bss_discharge_power, expected_index),
        bss_state_of_charge=zero_soc,
    )
    return entity.self_consumption_optimization()


def _point_has_storage(point_data: cu.ServicePointData) -> bool:
    """Return whether a service point exposes usable battery capability."""
    return (
        float(point_data.storage_capacity_max) > 0.0
        and max(float(point_data.charge_power_max), float(point_data.discharge_power_max)) > 0.0
    )


def _point_has_production(point_data: cu.ServicePointData) -> bool:
    """Return whether a service point contains non-zero PV production."""
    return bool(np.any(np.asarray(point_data.production, dtype=float) > 0.0))


def _requires_community_self_consumption_fallback(
    service_point_data: list[cu.ServicePointData],
) -> bool:
    """Detect communities where PV-only and battery-only points coexist.

    The fallback is intentionally narrow: it activates only when at
    least one service point has storage without PV and at least one
    other service point has PV without storage. That preserves the
    original per-service-point optimization path for colocated setups.
    """
    has_storage_only = any(
        _point_has_storage(point_data) and not _point_has_production(point_data)
        for point_data in service_point_data
    )
    has_pv_only = any(
        _point_has_production(point_data) and not _point_has_storage(point_data)
        for point_data in service_point_data
    )
    return has_storage_only and has_pv_only


def _build_service_point_data_with_bss_schedule(
    point_data: cu.ServicePointData,
    *,
    bss_charge_power: np.ndarray,
    bss_discharge_power: np.ndarray,
) -> cu.ServicePointData:
    """Rebuild one service point after replacing its battery schedule.

    This helper keeps the original production and consumption series and
    recomputes import/export from the new charge and discharge powers.
    """
    production = np.asarray(point_data.production, dtype=float)
    consumption = np.asarray(point_data.consumption, dtype=float)
    charge = np.asarray(bss_charge_power, dtype=float)
    discharge = np.asarray(bss_discharge_power, dtype=float)
    net_grid_power = consumption - production + charge - discharge
    grid_import = np.clip(net_grid_power, a_min=0.0, a_max=None)
    grid_export = np.clip(-net_grid_power, a_min=0.0, a_max=None)

    return cu.ServicePointData(
        production=production,
        consumption=consumption,
        charge_power_max=point_data.charge_power_max,
        discharge_power_max=point_data.discharge_power_max,
        storage_capacity_max=point_data.storage_capacity_max,
        grid_import=grid_import,
        grid_export=grid_export,
        bss_charge_power=charge,
        bss_discharge_power=discharge,
    )


def _build_community_aggregate_service_point_data(
    service_point_data: list[cu.ServicePointData],
) -> cu.ServicePointData:
    """Aggregate service points into one virtual optimization entity.

    The returned object is shaped like normal ``ServicePointData`` so it
    can be passed into the existing entity optimizer without changing
    that implementation.
    """
    return cu.ServicePointData(
        production=np.sum([point_data.production for point_data in service_point_data], axis=0),
        consumption=np.sum([point_data.consumption for point_data in service_point_data], axis=0),
        charge_power_max=float(np.sum([point_data.charge_power_max for point_data in service_point_data])),
        discharge_power_max=float(np.sum([point_data.discharge_power_max for point_data in service_point_data])),
        storage_capacity_max=float(np.sum([point_data.storage_capacity_max for point_data in service_point_data])),
        grid_import=np.sum([point_data.grid_import for point_data in service_point_data], axis=0),
        grid_export=np.sum([point_data.grid_export for point_data in service_point_data], axis=0),
        bss_charge_power=np.sum([point_data.bss_charge_power for point_data in service_point_data], axis=0),
        bss_discharge_power=np.sum([point_data.bss_discharge_power for point_data in service_point_data], axis=0),
    )


def _build_storage_distribution_weights(
    service_point_data: list[cu.ServicePointData],
    storage_point_indexes: list[int],
) -> np.ndarray:
    """Build normalized weights for redistributing community battery power.

    Battery power is distributed in proportion to each storage-bearing
    service point's configured max battery power.
    """
    weights = np.array(
        [
            max(
                float(service_point_data[idx].charge_power_max),
                float(service_point_data[idx].discharge_power_max),
                0.0,
            )
            for idx in storage_point_indexes
        ],
        dtype=float,
    )
    if np.all(weights <= 0.0):
        weights = np.ones(len(storage_point_indexes), dtype=float)
    return weights / weights.sum()


def _build_storage_aggregate_service_point_data(
    service_point_data: list[cu.ServicePointData],
    storage_point_indexes: list[int],
    *,
    production: np.ndarray,
    consumption: np.ndarray,
) -> cu.ServicePointData:
    """Build one virtual storage fleet entity for community discharge."""
    zero_profile = np.zeros_like(np.asarray(production, dtype=float), dtype=float)
    production_values = np.asarray(production, dtype=float)
    consumption_values = np.asarray(consumption, dtype=float)
    net_grid_power = consumption_values - production_values

    return cu.ServicePointData(
        production=production_values,
        consumption=consumption_values,
        charge_power_max=float(
            np.sum([service_point_data[idx].charge_power_max for idx in storage_point_indexes])
        ),
        discharge_power_max=float(
            np.sum([service_point_data[idx].discharge_power_max for idx in storage_point_indexes])
        ),
        storage_capacity_max=float(
            np.sum([service_point_data[idx].storage_capacity_max for idx in storage_point_indexes])
        ),
        grid_import=np.clip(net_grid_power, a_min=0.0, a_max=None),
        grid_export=np.clip(-net_grid_power, a_min=0.0, a_max=None),
        bss_charge_power=zero_profile,
        bss_discharge_power=zero_profile,
    )


def _build_virtual_point_data_for_optimization(
    point_data: cu.ServicePointData,
    *,
    production: np.ndarray,
) -> cu.ServicePointData:
    """Build a virtual optimization input with explicit production and load.

    The legacy entity optimizer infers consumption from grid and battery
    timeseries. To preserve the original local consumption while giving a
    service point a different effective PV profile, this helper rebuilds
    a self-consistent pre-optimization view with zero battery power and
    grid flows derived from ``consumption - production``.
    """
    production_values = np.asarray(production, dtype=float)
    consumption_values = np.asarray(point_data.consumption, dtype=float)
    net_grid_power = consumption_values - production_values
    grid_import = np.clip(net_grid_power, a_min=0.0, a_max=None)
    grid_export = np.clip(-net_grid_power, a_min=0.0, a_max=None)
    zero_profile = np.zeros_like(production_values, dtype=float)

    return cu.ServicePointData(
        production=production_values,
        consumption=consumption_values,
        charge_power_max=point_data.charge_power_max,
        discharge_power_max=point_data.discharge_power_max,
        storage_capacity_max=point_data.storage_capacity_max,
        grid_import=grid_import,
        grid_export=grid_export,
        bss_charge_power=zero_profile,
        bss_discharge_power=zero_profile,
    )


def _build_csc_allocated_production_profiles(
    db: Session,
    ec_id,
    community_relations: dict,
    service_point_data: list[cu.ServicePointData],
    expected_index: pd.DatetimeIndex,
) -> np.ndarray:
    """Build allocation-aware remote PV profiles for CSC recipients.

    For each producer service point, this applies the configured
    allocation coefficients to that producer's raw exported surplus and
    distributes the resulting allocated PV power to recipients without
    limiting it by the recipients' current import. That allows the
    recipient battery optimizer to use any residual allocated PV for
    charging after its own local demand is covered.
    """
    service_points = community_relations["service_points"]
    sp_index_by_id = {service_point["sp_id"]: idx for idx, service_point in enumerate(service_points)}
    allocated_production = np.zeros(
        (len(service_point_data), len(expected_index)),
        dtype=float,
    )

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

    links_by_producer: dict[str, list] = {}
    for link in allocation_links:
        producer_sp_id = str(link.producer_sp_id)
        recipient_sp_id = str(link.recipient_sp_id)
        if producer_sp_id not in sp_index_by_id or recipient_sp_id not in sp_index_by_id:
            continue
        links_by_producer.setdefault(producer_sp_id, []).append(link)

    for producer_sp_id, links in links_by_producer.items():
        producer_idx = sp_index_by_id[producer_sp_id]
        producer_export = np.asarray(service_point_data[producer_idx].grid_export, dtype=float)
        for ts_idx, timestamp in enumerate(expected_index):
            producer_remaining_export = producer_export[ts_idx]
            if producer_remaining_export <= 0.0:
                continue

            for link in links:
                coefficient = _get_active_link_coefficient(link.coefficients, timestamp)
                if coefficient <= 0.0:
                    continue

                recipient_idx = sp_index_by_id[str(link.recipient_sp_id)]
                requested_allocation = producer_export[ts_idx] * coefficient
                applied_allocation = min(producer_remaining_export, requested_allocation)
                if applied_allocation <= 0.0:
                    continue

                allocated_production[recipient_idx, ts_idx] += applied_allocation
                producer_remaining_export -= applied_allocation

                if producer_remaining_export <= 0.0:
                    break

    return allocated_production


def _simulate_csc_split_asset_self_consumption(
    db: Session,
    ec_id,
    community_relations: dict,
    service_point_data: list[cu.ServicePointData],
    expected_index: pd.DatetimeIndex,
    *,
    discharge_scope: str = "local",
) -> tuple[list[cu.ServicePointData], np.ndarray]:
    """Optimize only the storage points using their allocated CSC PV surplus.

    Returns:
        tuple[list[ServicePointData], np.ndarray]: Updated service-point
        data and the aggregated stored-energy profile across all
        optimized storage points.
    """
    allocated_production = _build_csc_allocated_production_profiles(
        db=db,
        ec_id=ec_id,
        community_relations=community_relations,
        service_point_data=service_point_data,
        expected_index=expected_index,
    )

    effective_service_point_data = list(service_point_data)
    storage_point_indexes = [
        idx for idx, point_data in enumerate(service_point_data) if _point_has_storage(point_data)
    ]
    if not storage_point_indexes:
        return effective_service_point_data, np.zeros(len(expected_index), dtype=float)

    if discharge_scope == "community":
        weights = _build_storage_distribution_weights(service_point_data, storage_point_indexes)
        virtual_storage_entity = _build_storage_aggregate_service_point_data(
            service_point_data,
            storage_point_indexes,
            production=np.sum(allocated_production[storage_point_indexes], axis=0),
            consumption=np.sum([point_data.consumption for point_data in service_point_data], axis=0),
        )
        simulation_frame = _simulate_self_consumption_service_point(
            point_data=virtual_storage_entity,
            expected_index=expected_index,
        )
        community_charge = simulation_frame["bss_charge_power"].to_numpy(dtype=float)
        community_discharge = simulation_frame["bss_discharge_power"].to_numpy(dtype=float)

        for storage_weight, point_idx in zip(weights, storage_point_indexes):
            effective_service_point_data[point_idx] = _build_service_point_data_with_bss_schedule(
                service_point_data[point_idx],
                bss_charge_power=community_charge * storage_weight,
                bss_discharge_power=community_discharge * storage_weight,
            )

        return effective_service_point_data, simulation_frame["bss_stored_energy"].to_numpy(dtype=float)

    stored_energy_profiles: list[np.ndarray] = []
    for idx in storage_point_indexes:
        point_data = service_point_data[idx]

        optimization_input = _build_virtual_point_data_for_optimization(
            point_data,
            production=np.asarray(point_data.production, dtype=float) + allocated_production[idx],
        )
        simulation_frame = _simulate_self_consumption_service_point(
            point_data=optimization_input,
            expected_index=expected_index,
        )
        stored_energy_profiles.append(simulation_frame["bss_stored_energy"].to_numpy(dtype=float))
        effective_service_point_data[idx] = _build_service_point_data_with_bss_schedule(
            point_data,
            bss_charge_power=simulation_frame["bss_charge_power"].to_numpy(dtype=float),
            bss_discharge_power=simulation_frame["bss_discharge_power"].to_numpy(dtype=float),
        )

    if stored_energy_profiles:
        stored_energy = np.sum(stored_energy_profiles, axis=0)
    else:
        stored_energy = np.zeros(len(expected_index), dtype=float)

    return effective_service_point_data, stored_energy


def _simulate_split_asset_community_self_consumption(
    service_point_data: list[cu.ServicePointData],
    expected_index: pd.DatetimeIndex,
) -> tuple[list[cu.ServicePointData], pd.DataFrame]:
    """Optimize a split-asset community through one virtual entity.

    Steps:
    1. Aggregate all service-point data into one virtual community
       entity.
    2. Run the existing self-consumption optimization once on that
       aggregate.
    3. Redistribute the resulting battery charge/discharge schedule to
       service points that actually have storage.

    Returns:
        tuple[list[ServicePointData], pandas.DataFrame]: The first item
        contains updated service-point data with redistributed battery
        schedules. The second item contains the aggregate optimized
        dataframe, which is used directly for non-CSC reporting.
    """
    community_point_data = _build_community_aggregate_service_point_data(service_point_data)
    community_simulation = _simulate_self_consumption_service_point(
        point_data=community_point_data,
        expected_index=expected_index,
    )

    storage_point_indexes = [
        idx for idx, point_data in enumerate(service_point_data) if _point_has_storage(point_data)
    ]
    if not storage_point_indexes:
        return service_point_data, community_simulation

    weights = _build_storage_distribution_weights(service_point_data, storage_point_indexes)
    community_charge = community_simulation["bss_charge_power"].to_numpy(dtype=float)
    community_discharge = community_simulation["bss_discharge_power"].to_numpy(dtype=float)

    effective_service_point_data = list(service_point_data)
    for storage_weight, point_idx in zip(weights, storage_point_indexes):
        effective_service_point_data[point_idx] = _build_service_point_data_with_bss_schedule(
            service_point_data[point_idx],
            bss_charge_power=community_charge * storage_weight,
            bss_discharge_power=community_discharge * storage_weight,
        )

    return effective_service_point_data, community_simulation


def _slice_report_window(
    values: np.ndarray,
    report_mask: np.ndarray,
) -> np.ndarray:
    """Remove warmup timesteps and keep only the user-requested window."""
    return np.asarray(values, dtype=float)[report_mask]


def _build_service_point_data_from_simulation(
    point_data: cu.ServicePointData,
    simulation_frame: pd.DataFrame,
) -> cu.ServicePointData:
    """Convert optimizer output back into ``ServicePointData``."""
    return cu.ServicePointData(
        production=simulation_frame["pv_production_power"].to_numpy(dtype=float),
        consumption=simulation_frame["consumption"].to_numpy(dtype=float),
        charge_power_max=point_data.charge_power_max,
        discharge_power_max=point_data.discharge_power_max,
        storage_capacity_max=point_data.storage_capacity_max,
        grid_import=simulation_frame["grid_import_power"].to_numpy(dtype=float),
        grid_export=simulation_frame["grid_export_power"].to_numpy(dtype=float),
        bss_charge_power=simulation_frame["bss_charge_power"].to_numpy(dtype=float),
        bss_discharge_power=simulation_frame["bss_discharge_power"].to_numpy(dtype=float),
    )


def _build_simulation_output(
    *,
    start_datetime: str,
    end_datetime: str,
    strategy: str,
    battery_discharge_scope: str,
    community_type: str | None,
    energy_prices: pd.DataFrame,
    production: np.ndarray,
    consumption: np.ndarray,
    grid_import: np.ndarray,
    grid_export: np.ndarray,
    bss_charge_power: np.ndarray,
    bss_discharge_power: np.ndarray,
    stored_energy: np.ndarray | None = None,
) -> SimulationResponse:
    """Build the common simulation response for the simulation endpoint.

    Summary metrics and timeseries formatting are delegated to the
    shared status helpers so the simulation output stays aligned with
    the status endpoint calculations.
    """
    energy_cost_import, energy_cost_export = cu.build_energy_pricing(energy_prices, len(production))
    summary_metrics = build_status_summary_metrics(
        production=production,
        consumption=consumption,
        grid_import=grid_import,
        grid_export=grid_export,
        bss_charge_power=bss_charge_power,
        energy_cost_import=energy_cost_import,
        energy_cost_export=energy_cost_export,
        trailing_steps=len(production),
    )
    timeseries_output = build_status_timeseries_output(
        energy_prices=energy_prices,
        production=production,
        consumption=consumption,
        grid_import=grid_import,
        grid_export=grid_export,
        bss_charge_power=bss_charge_power,
        bss_discharge_power=bss_discharge_power,
    )
    if stored_energy is not None:
        ts_index = pd.to_datetime(energy_prices.index[: len(stored_energy)], utc=True, errors="raise")
        ts_ms = (ts_index.view("int64") // 10**3).astype(np.int64)
        timeseries_output["stored_energy_profile"] = [
            [int(timestamp), float(value)]
            for timestamp, value in zip(ts_ms, np.clip(np.asarray(stored_energy, dtype=float), a_min=0.0, a_max=None))
        ]
    else:
        timeseries_output["stored_energy_profile"] = [
            [int(timestamp), float(value)]
            for timestamp, value in timeseries_output["stored_energy_profile"]
            if True
        ]
        for point in timeseries_output["stored_energy_profile"]:
            point[1] = max(0.0, point[1])

    def make_series(points: list[list[float]], unit: str) -> TimeSeries:
        return TimeSeries(
            points=[(int(timestamp), float(value)) for timestamp, value in points],
            unit=unit,
        )

    timestamps = timeseries_output["energy_prices_out"]
    report_start = pd.to_datetime(timestamps[0][0], unit="ms", utc=True).to_pydatetime()
    report_end = (
        pd.to_datetime(timestamps[-1][0], unit="ms", utc=True)
        + pd.Timedelta(minutes=15)
    ).to_pydatetime()

    report = SimulationReport(
        window=ReportWindow(
            start_datetime=report_start,
            end_datetime=report_end,
            timestep_minutes=15,
        ),
        community_type=community_type,
        summary=EnergySummary(
            imported_energy_wh=float(summary_metrics["imported_energy"]),
            exported_energy_wh=float(summary_metrics["exported_energy"]),
            produced_energy_wh=float(summary_metrics["produced_energy"]),
            consumed_energy_wh=float(summary_metrics["consumed_energy"]),
            self_consumed_energy_wh=float(summary_metrics["self_consumed_energy"]),
            self_consumption_pct=float(summary_metrics["self_consumption"]),
            self_sufficiency_pct=float(summary_metrics["self_sufficiency"]),
            expenses_eur=float(summary_metrics["expenses"]),
            co2_emissions_kg=float(summary_metrics["co2_emissions"]),
        ),
        series=EnergySeries(
            energy_price=make_series(timeseries_output["energy_prices_out"], "EUR/kWh"),
            consumption_power=make_series(timeseries_output["consumption_profile"], "W"),
            production_power=make_series(timeseries_output["production_profile"], "W"),
            grid_import_power=make_series(timeseries_output["import_profile"], "W"),
            grid_export_power=make_series(timeseries_output["export_profile"], "W"),
            battery_charge_power=TimeSeries(
                points=[
                    (int(timestamp), float(value))
                    for timestamp, value in zip(
                        [point[0] for point in timestamps], bss_charge_power
                    )
                ],
                unit="W",
            ),
            battery_discharge_power=TimeSeries(
                points=[
                    (int(timestamp), float(value))
                    for timestamp, value in zip(
                        [point[0] for point in timestamps], bss_discharge_power
                    )
                ],
                unit="W",
            ),
            stored_energy=make_series(timeseries_output["stored_energy_profile"], "Wh"),
        ),
    )

    return SimulationResponse(
        report=report,
        simulation=SimulationDetails(
            strategy=strategy,
            battery_discharge_scope=battery_discharge_scope,
        ),
    )

    # Legacy response retained in place for reference during the API-v2 migration.
    # output = {
    #     "start_datetime": start_datetime,
    #     "end_datetime": end_datetime,
    #     "optimization_strategy": strategy,
    #     "results": {
    #         "self_consumption": summary_metrics["self_consumption"],
    #         "self_sufficiency": summary_metrics["self_sufficiency"],
    #         "co2_emissions": summary_metrics["co2_emissions"],
    #         "expenses": summary_metrics["expenses"],
    #         "imported_energy": summary_metrics["imported_energy"],
    #         "exported_energy": summary_metrics["exported_energy"],
    #         "produced_energy": summary_metrics["produced_energy"],
    #         "consumed_energy": summary_metrics["consumed_energy"],
    #         "self_consumed_energy": summary_metrics["self_consumed_energy"],
    #         "total_energy_demanded": summary_metrics["total_energy_demanded"],
    #         "energy_prices": timeseries_output["energy_prices_out"],
    #         "consumption_profile": timeseries_output["consumption_profile"],
    #         "production_profile": timeseries_output["production_profile"],
    #         "stored_energy_profile": timeseries_output["stored_energy_profile"],
    #         "import_profile": timeseries_output["import_profile"],
    #         "export_profile": timeseries_output["export_profile"],
    #     },
    # }
    # return json.dumps(output)


@router.post("", response_model=SimulationResponse)
async def simulate(
    params: SimulationParameters,
    db: Session = Depends(get_db),
    token=Depends(require_token("admin")),
) -> SimulationResponse:
    """Run a simulation for the authenticated user's energy community.

    Workflow:
    1. Validate the requested strategy and build a warmup window.
    2. Load community metadata and per-service-point timeseries.
    3. For ``self-consumption``, either optimize each service point
       independently or use the split-asset community fallback with the
       requested battery discharge scope.
    4. Apply CSC allocations when required by the community type.
    5. Trim the warmup horizon and return aggregate metrics and
       timeseries as JSON.
    """
    requested_start = datetime.strptime(params.start_datetime, "%Y-%m-%dT%H:%M:%S")
    requested_end = datetime.strptime(params.end_datetime, "%Y-%m-%dT%H:%M:%S")
    strategy = params.optimization_strategy
    discharge_scope = params.battery_discharge_scope

    if strategy not in {"no-optimization", "self-consumption", "collective-self-consumption"}:
        raise HTTPException(status_code=400, detail=f"Unsupported optimization strategy: {strategy}")
    if discharge_scope not in {"local", "community"}:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported battery_discharge_scope: {discharge_scope}",
        )

    if strategy == "collective-self-consumption":
        raise HTTPException(
            status_code=501,
            detail="collective-self-consumption is not implemented yet.",
        )

    warmup_start = requested_start - timedelta(days=3)
    logger.info(f"warmup_start: {warmup_start}, requested_start: {requested_start}, requested_end: {requested_end}")

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
        start_date=warmup_start.strftime("%Y-%m-%dT%H:%M:%S"),
        end_date=requested_end.strftime("%Y-%m-%dT%H:%M:%S"),
    )

    service_point_data = [
        cu.load_service_point_data(
            db=db,
            service_point=service_point,
            start_date=warmup_start.strftime("%Y-%m-%dT%H:%M:%S"),
            end_date=requested_end.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        for service_point in community_relations["service_points"]
    ]

    report_index = pd.DatetimeIndex(community_data.expected_index)
    report_mask = (report_index >= pd.Timestamp(requested_start)) & (
        report_index < pd.Timestamp(requested_end)
    )

    if not np.any(report_mask):
        raise HTTPException(status_code=400, detail="Requested simulation window contains no timesteps.")

    price_window = community_data.energy_prices.iloc[: community_data.num_points].copy()
    report_energy_prices = pd.DataFrame(
        {
            "price": price_window["price"].to_numpy(dtype=float)[report_mask]
        },
        index=report_index[report_mask],
    )
    community_type = community_relations.get("type")

    aggregated_override = None
    stored_energy = None

    if strategy == "no-optimization":
        effective_service_point_data = service_point_data
    else:
        if _requires_community_self_consumption_fallback(service_point_data):
            if community_type == "CSC_00":
                logger.info(
                    "Using CSC allocation-aware self-consumption fallback because PV and storage are split across service points."
                )
                effective_service_point_data, stored_energy = (
                    _simulate_csc_split_asset_self_consumption(
                        db=db,
                        ec_id=ec_id,
                        community_relations=community_relations,
                        service_point_data=service_point_data,
                        expected_index=report_index,
                        discharge_scope=discharge_scope,
                    )
                )
            else:
                logger.info(
                    "Using community-level self-consumption fallback because PV and storage are split across service points."
                )
                effective_service_point_data, aggregated_override = (
                    _simulate_split_asset_community_self_consumption(
                        service_point_data=service_point_data,
                        expected_index=report_index,
                    )
                )
                stored_energy = aggregated_override["bss_stored_energy"].to_numpy(dtype=float)
        else:
            simulated_frames = [
                _simulate_self_consumption_service_point(point_data, report_index)
                for point_data in service_point_data
            ]
            effective_service_point_data = [
                _build_service_point_data_from_simulation(point_data, frame)
                for point_data, frame in zip(service_point_data, simulated_frames)
            ]
            stored_energy = np.sum(
                [frame["bss_stored_energy"].to_numpy(dtype=float) for frame in simulated_frames],
                axis=0,
            )

    if community_type == "CSC_00":
        allocated_flows = apply_csc_allocations(
            db=db,
            ec_id=ec_id,
            community_relations=community_relations,
            service_point_data=effective_service_point_data,
            expected_index=report_index,
        )
        grid_import_source = allocated_flows["grid_import"]
        grid_export_source = allocated_flows["grid_export"]
    else:
        grid_import_source = np.array([point_data.grid_import for point_data in effective_service_point_data])
        grid_export_source = np.array([point_data.grid_export for point_data in effective_service_point_data])

    if aggregated_override is not None and community_type != "CSC_00":
        production = _slice_report_window(
            aggregated_override["pv_production_power"].to_numpy(dtype=float),
            report_mask,
        )
        consumption = _slice_report_window(
            aggregated_override["consumption"].to_numpy(dtype=float),
            report_mask,
        )
        grid_import = _slice_report_window(
            aggregated_override["grid_import_power"].to_numpy(dtype=float),
            report_mask,
        )
        grid_export = _slice_report_window(
            aggregated_override["grid_export_power"].to_numpy(dtype=float),
            report_mask,
        )
        bss_charge_power = _slice_report_window(
            aggregated_override["bss_charge_power"].to_numpy(dtype=float),
            report_mask,
        )
        bss_discharge_power = _slice_report_window(
            aggregated_override["bss_discharge_power"].to_numpy(dtype=float),
            report_mask,
        )
        stored_energy = _slice_report_window(stored_energy, report_mask) if stored_energy is not None else None
    else:
        production = _slice_report_window(
            np.sum([point_data.production for point_data in effective_service_point_data], axis=0),
            report_mask,
        )
        consumption = _slice_report_window(
            np.sum([point_data.consumption for point_data in effective_service_point_data], axis=0),
            report_mask,
        )
        grid_import = _slice_report_window(np.sum(grid_import_source, axis=0), report_mask)
        grid_export = _slice_report_window(np.sum(grid_export_source, axis=0), report_mask)
        bss_charge_power = _slice_report_window(
            np.sum([point_data.bss_charge_power for point_data in effective_service_point_data], axis=0),
            report_mask,
        )
        bss_discharge_power = _slice_report_window(
            np.sum([point_data.bss_discharge_power for point_data in effective_service_point_data], axis=0),
            report_mask,
        )
        if stored_energy is not None:
            stored_energy = _slice_report_window(stored_energy, report_mask)

    return _build_simulation_output(
        start_datetime=params.start_datetime,
        end_datetime=params.end_datetime,
        strategy=strategy,
        battery_discharge_scope=discharge_scope,
        community_type=community_type,
        energy_prices=report_energy_prices,
        production=production,
        consumption=consumption,
        grid_import=grid_import,
        grid_export=grid_export,
        bss_charge_power=bss_charge_power,
        bss_discharge_power=bss_discharge_power,
        stored_energy=stored_energy,
    )
