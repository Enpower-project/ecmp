from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi import APIRouter, Depends, Query, status
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from jobs.eds_dingle_publisher.eds_publisher import publish_flexibility_daily_data
from fastapi.responses import JSONResponse
from app.db.session import SessionLocal
from app.auth.dependencies import require_token
from app.api import community_utils as cu
from app.schemas.flexibility import (
    FlexProvisionParameters, FlexibilityRequestData, FlexibilityRequestCreate,
    FlexibilityRequestListResponse, FlexibilityRequestResponse
)
from app.schemas.energy_report import (
    EnergySeries,
    EnergySummary,
    FlexibilityDetails,
    FlexibilityMarketOfferChunk,
    FlexibilityReport,
    FlexibilityResponse,
    ReportWindow,
    ServicePointFlexibilityOffer,
    TimeSeries,
)
import app.crud.flexibility as flex_crud
from app.crud.flexibility import ConflictError, PersistenceError
from app.crud.service_points import get_service_points_by_ec_id_crud
from decimal import Decimal
from datetime import datetime
import json
from uuid import UUID
import pandas as pd
import numpy as np
import cvxpy as cp


import logging

from app.keycloak_instance import kc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/flexibility", tags=["flexibility"])


def build_market_offer_chunks(
    *,
    timestamps: pd.DatetimeIndex,
    power_profile_w: np.ndarray,
    direction: str,
    price: float,
    price_uom: str,
    start: datetime,
    end: datetime,
    power_tolerance_w: float = 100.0,
) -> list[FlexibilityMarketOfferChunk]:
    """Group adjacent, equal 15-minute offers into market offer chunks."""
    if power_tolerance_w <= 0:
        raise ValueError("power_tolerance_w must be greater than zero.")

    start_timestamp = pd.Timestamp(start)
    end_timestamp = pd.Timestamp(end)
    if start_timestamp.tzinfo is None:
        start_timestamp = start_timestamp.tz_localize("UTC")
    else:
        start_timestamp = start_timestamp.tz_convert("UTC")
    if end_timestamp.tzinfo is None:
        end_timestamp = end_timestamp.tz_localize("UTC")
    else:
        end_timestamp = end_timestamp.tz_convert("UTC")

    chunks: list[FlexibilityMarketOfferChunk] = []
    current_start: pd.Timestamp | None = None
    current_power_w: float | None = None
    previous_timestamp: pd.Timestamp | None = None

    def append_current_chunk() -> None:
        if current_start is None or current_power_w is None or previous_timestamp is None:
            return
        chunks.append(
            FlexibilityMarketOfferChunk(
                direction=direction,
                start_datetime=current_start.to_pydatetime(),
                end_datetime=(previous_timestamp + pd.Timedelta(minutes=15)).to_pydatetime(),
                power_w=current_power_w,
                price=price,
                price_uom=price_uom,
            )
        )

    for timestamp, power in zip(timestamps, np.asarray(power_profile_w, dtype=float)):
        if timestamp < start_timestamp or timestamp >= end_timestamp:
            continue

        power_w = np.floor(float(power) / power_tolerance_w) * power_tolerance_w
        if power_w <= 0:
            append_current_chunk()
            current_start = current_power_w = previous_timestamp = None
            continue

        is_same_power = current_power_w == power_w
        if current_start is None:
            current_start = timestamp
            current_power_w = power_w
        elif not is_same_power:
            append_current_chunk()
            current_start = timestamp
            current_power_w = power_w
        previous_timestamp = timestamp

    append_current_chunk()
    return chunks


def get_db():
    """Provide a database session for FastAPI dependencies.

    Yields:
        Session: SQLAlchemy session scoped to the current request.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def build_flexibility_timeseries_output(
    energy_prices,
    num_points: int,
    num_service_points: int,
    load: np.ndarray,
    pv_agg: np.ndarray,
    activation_results: dict,
    trailing_steps: int = 96,
) -> dict:
    """Build timestamped timeseries outputs from solver results.

    Args:
        energy_prices: Energy price dataframe for the optimisation
            horizon.
        num_points: Number of timesteps in the active horizon.
        num_service_points: Number of service points in the community.
        load: Community load matrix with shape ``(N, T)``.
        pv_agg: Aggregated PV production profile with shape ``(T,)``.
        activation_results: Solver outputs from
            :func:`solve_activation_dispatch`.
        trailing_steps: Number of trailing timesteps to expose in the
            output series.

    Returns:
        dict: Timestamped ``[timestamp_ms, value]`` output series used by
        the API response and KPI calculations.
    """
    def _series_to_output(ts_ms: np.ndarray, values: np.ndarray) -> list[list]:
        return np.column_stack((ts_ms, values)).tolist()

    def _matrix_to_output(ts_ms: np.ndarray, values: np.ndarray) -> list[list[list]]:
        return [_series_to_output(ts_ms, values[idx]) for idx in range(num_service_points)]

    ts_index = pd.to_datetime(energy_prices.index[:num_points], utc=True, errors="raise")
    # unix timestamp in ms
    ts_ms = (ts_index.view("int64") // 10**3).astype(np.int64)
    output_window = slice(max(0, num_points - trailing_steps), num_points)
    ts_ms = ts_ms[output_window]

    charge_matrix = np.asarray(activation_results["p_ch"])[:, output_window]
    discharge_matrix = np.asarray(activation_results["p_dis"])[:, output_window]
    soc_matrix = np.asarray(activation_results["SoC"][:, :-1])[:, output_window]
    aggregated_load = np.sum(load, axis=0)[output_window]
    aggregated_bss_power = (
        np.round(np.sum(charge_matrix, axis=0), 3)
        - np.round(np.sum(discharge_matrix, axis=0), 3)
    )
    aggregated_production = np.asarray(pv_agg)[output_window]
    aggregated_power = np.asarray(activation_results["p_agg_activated"])[output_window]
    baseline_power = np.asarray(activation_results["p_agg_baseline"])[output_window]
    energy_price_values = energy_prices["price"].to_numpy()[:num_points][output_window]
    grid_import = np.asarray(activation_results["p_import"])[output_window]
    grid_export = np.asarray(activation_results["p_export"])[output_window]

    return {
        "ts_ms": ts_ms,
        "aggregated_load": _series_to_output(ts_ms, aggregated_load),
        "aggregated_production": _series_to_output(ts_ms, aggregated_production),
        "aggregated_bss_power": _series_to_output(ts_ms, aggregated_bss_power),
        "grid_import": _series_to_output(ts_ms, grid_import),
        "grid_export": _series_to_output(ts_ms, grid_export),
        "charge_power": _matrix_to_output(ts_ms, charge_matrix),
        "discharge_power": _matrix_to_output(ts_ms, discharge_matrix),
        "soc_out": _matrix_to_output(ts_ms, soc_matrix),
        "soc_agg": _series_to_output(ts_ms, np.sum(soc_matrix, axis=0)),
        "aggregated_power": _series_to_output(ts_ms, aggregated_power),
        "aggregated_production_list": _series_to_output(ts_ms, aggregated_production),
        "aggregated_bss_power_list": _series_to_output(ts_ms, aggregated_bss_power),
        "energy_prices_out": [[int(t), float(v)] for t, v in zip(ts_ms, energy_price_values)],
        "consumption": _series_to_output(ts_ms, aggregated_load),
        "baseline_agg": _series_to_output(ts_ms, baseline_power),
    }


def build_flexibility_summary_metrics(
    aggregated_load: np.ndarray,
    aggregated_production: np.ndarray,
    aggregated_bss_power: np.ndarray,
    grid_import: np.ndarray,
    grid_export: np.ndarray,
    *,
    timestep_hours: float = 0.25,
    trailing_steps: int = 96,
    co2_factor: float = 0.150,
) -> dict:
    """Build summary KPIs for the flexibility response.

    Args:
        aggregated_load: Aggregated community load profile.
        aggregated_production: Aggregated community PV production
            profile.
        aggregated_bss_power: Net aggregated BSS power profile.
        grid_import: Grid import profile.
        grid_export: Grid export profile.
        timestep_hours: Duration of each timestep in hours.
        trailing_steps: Number of trailing timesteps used for summary
            totals.
        co2_factor: CO2 emissions factor applied to imported energy.

    Returns:
        dict: Summary energy, self-consumption, and emissions metrics.
    """
    consumption_with_bss = aggregated_load + aggregated_bss_power
    self_consumed_power = np.minimum(aggregated_production, consumption_with_bss)

    consumption_wh = aggregated_load * timestep_hours
    production_wh = aggregated_production * timestep_hours
    grid_import_wh = grid_import * timestep_hours
    grid_export_wh = grid_export * timestep_hours
    self_consumed_wh = self_consumed_power * timestep_hours

    total_energy_demanded = consumption_wh[-trailing_steps:].sum()
    total_energy_produced = production_wh[-trailing_steps:].sum()
    total_grid_import = grid_import_wh[-trailing_steps:].sum()
    total_grid_export = grid_export_wh[-trailing_steps:].sum()
    self_consumed_energy = self_consumed_wh[-trailing_steps:].sum()
    self_sufficiency_pct = (self_consumed_energy / total_energy_demanded) * 100.0
    self_consumption_pct = (self_consumed_energy / total_energy_produced) * 100.0
    co2_emissions = (grid_import_wh[-trailing_steps:] * co2_factor).sum()

    return {
        "self_consumed_power": self_consumed_power,
        "self_sufficiency": self_sufficiency_pct,
        "self_consumption": self_consumption_pct,
        "co2_emissions": co2_emissions,
        "imported_energy": total_grid_import,
        "exported_energy": total_grid_export,
        "produced_energy": total_energy_produced,
        "consumed_energy": total_energy_demanded,
    }



def build_common_constraints(
    pv, load, dt,
    Pch_max, Pdis_max, SoC_min, SoC_max, SoC0, eta_ch, eta_dis,
    p_ch, p_dis, SoC, p_agg
):
    """Build the common optimisation constraints for battery dispatch.

    Args:
        pv: Array of photovoltaic production values with shape
            ``(N, T)``.
        load: Array of consumption values with shape ``(N, T)``.
        dt: Optimisation timestep in hours.
        Pch_max: Maximum charging power per entity.
        Pdis_max: Maximum discharging power per entity.
        SoC_min: Minimum state-of-charge bounds per entity.
        SoC_max: Maximum state-of-charge bounds per entity.
        SoC0: Initial state-of-charge per entity.
        eta_ch: Charging efficiency per entity.
        eta_dis: Discharging efficiency per entity.
        p_ch: CVXPY charging-power decision variable.
        p_dis: CVXPY discharging-power decision variable.
        SoC: CVXPY state-of-charge decision variable.
        p_agg: CVXPY aggregated point-of-common-coupling power
            variable.

    Returns:
        tuple: Two-element tuple containing the list of CVXPY
        constraints and the per-user PCC power expression ``p_user``.
    """
    N, T = pv.shape
    constraints = []

    # SoC dynamics
    constraints += [SoC[:, 0] == SoC0]
    for i in range(N):
        constraints += [
            SoC[i, 1:] == SoC[i, :-1]
            + eta_ch[i] * p_ch[i, :] * dt
            - (1.0 / eta_dis[i]) * p_dis[i, :] * dt
        ]
        constraints += [SoC[i, :] >= SoC_min[i], SoC[i, :] <= SoC_max[i]]
        constraints += [p_ch[i, :] <= Pch_max[i], p_dis[i, :] <= Pdis_max[i]]

    # PCC power from assets
    p_user = pv - load + p_dis - p_ch   # (N,T)
    constraints += [p_agg == cp.sum(p_user, axis=0)]

    return constraints, p_user


def solve_baseline_and_offer(
    pv, load, dt, L,
    energy_cost_import, energy_cost_export,
    flex_up_req, flex_down_req,
    Pch_max, Pdis_max, SoC_min, SoC_max, SoC0, eta_ch, eta_dis,
    c_thr=0.01, eps_energy=1e-2
):
    """Solve the baseline schedule and feasible flexibility offers.

    Args:
        pv: Array of photovoltaic production values with shape
            ``(N, T)``.
        load: Array of consumption values with shape ``(N, T)``.
        dt: Optimisation timestep in hours.
        L: Number of timesteps for which an offer must remain feasible.
        energy_cost_import: Import energy price series for each
            timestep.
        energy_cost_export: Export energy price series for each
            timestep.
        flex_up_req: Requested upward flexibility caps per timestep.
        flex_down_req: Requested downward flexibility caps per
            timestep.
        Pch_max: Maximum charging power per entity.
        Pdis_max: Maximum discharging power per entity.
        SoC_min: Minimum state-of-charge bounds per entity.
        SoC_max: Maximum state-of-charge bounds per entity.
        SoC0: Initial state-of-charge per entity.
        eta_ch: Charging efficiency per entity.
        eta_dis: Discharging efficiency per entity.
        c_thr: Throughput penalty in EUR/kWh used to discourage
            simultaneous charging and discharging.
        eps_energy: Relative tolerance used when maximizing flexibility
            offer subject to near-optimal baseline energy performance.

    Returns:
        dict: Solver status values and baseline optimisation outputs,
        including aggregated baseline power, upward and downward offers,
        charging power, discharging power, state of charge, and the
        optimal baseline energy objective.
    """
    N, T = pv.shape

    # Variables
    p_ch = cp.Variable((N, T), nonneg=True)
    p_dis = cp.Variable((N, T), nonneg=True)
    SoC = cp.Variable((N, T+1))

    p_agg = cp.Variable(T)                  # baseline PCC power
    p_import = cp.Variable(T, nonneg=True)
    p_export = cp.Variable(T, nonneg=True)

    offer_up = cp.Variable(T, nonneg=True)
    offer_dn = cp.Variable(T, nonneg=True)

    # Common constraints
    constraints, _ = build_common_constraints(
        pv, load, dt,
        Pch_max, Pdis_max, SoC_min, SoC_max, SoC0, eta_ch, eta_dis,
        p_ch, p_dis, SoC, p_agg
    )

    # Import/export split for baseline settlement
    constraints += [p_agg == p_export - p_import]

    # Offer caps (DSO request)
    constraints += [offer_up <= flex_up_req]
    constraints += [offer_dn <= flex_down_req]

    # Offer feasibility (power headroom)
    sum_dis = cp.sum(p_dis, axis=0)
    sum_ch  = cp.sum(p_ch, axis=0)
    dis_headroom = np.sum(Pdis_max) - sum_dis 
    ch_headroom  = np.sum(Pch_max)  - sum_ch
    constraints += [offer_dn <= ch_headroom + sum_dis] # ↓p_agg via more charge OR less discharge
    constraints += [offer_up <= dis_headroom + sum_ch] # ↑p_agg via more discharge OR less charge

    # Offer feasibility (SOC headroom) 
    soc_total = cp.sum(SoC[:, :-1], axis=0)
    constraints += [
        # To sustain UP (increase p_agg) for L steps -> need energy above SOC_min to discharge
        soc_total >= np.sum(SoC_min) + (L*dt) * (offer_up / np.mean(eta_dis)),
        # To sustain DOWN (decrease p_agg) for L steps -> need room below SOC_max to charge
        soc_total <= np.sum(SoC_max) - (L*dt) * (np.mean(eta_ch) * offer_dn),
    ]

    # Baseline energy profit
    energy_profit = cp.sum(cp.multiply(energy_cost_export, p_export) -
                           cp.multiply(energy_cost_import, p_import)) * dt

    # Throughput penalty (discourage simultaneous ch/dis)
    throughput_pen = (c_thr / 1000.0) * cp.sum(p_ch + p_dis) * dt

    J_energy = energy_profit - throughput_pen

    # ---------- Solve to maximize baseline energy objective ----------
    prob_energy = cp.Problem(cp.Maximize(J_energy), constraints)
    prob_energy.solve(solver=cp.ECOS, verbose=False)
    J_energy_star = prob_energy.value

    # ---------- Solve to maximize offer subject to near-optimal energy ----------
    # maximise both directions
    offer_obj = cp.sum(offer_up + offer_dn) * dt

    # constraints2 = constraints + [J_energy >= J_energy_star - eps_energy]
    # constraints2 = constraints + [J_energy >= (1 - eps_energy) * J_energy_star]
    constraints2 = constraints + [J_energy >= J_energy_star - eps_energy * max(1.0, abs(J_energy_star))]
    prob_offer = cp.Problem(cp.Maximize(offer_obj), constraints2)
    prob_offer.solve(solver=cp.ECOS, verbose=False)
    if prob_offer.status not in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
        raise RuntimeError(f"Offer optimisation failed: {prob_offer.status}")

    # print(f"offer_up: {offer_up.value}")
    # print(f"offer_down: {offer_dn.value}")

    return {
        "status_energy": prob_energy.status,
        "status_offer": prob_offer.status,
        "p_agg_baseline": p_agg.value.copy(),
        "offer_up": offer_up.value.copy(),
        "offer_down": offer_dn.value.copy(),
        "p_ch_baseline": p_ch.value.copy(),
        "p_dis_baseline": p_dis.value.copy(),
        "SoC_baseline": SoC.value.copy(),
        "J_energy_star": J_energy_star,
    }


def solve_baseline_and_offer_per_service_point(
    pv, load, dt, L,
    energy_cost_import, energy_cost_export,
    flex_up_req, flex_down_req,
    Pch_max, Pdis_max, SoC_min, SoC_max, SoC0, eta_ch, eta_dis,
    c_thr=0.01, eps_energy=1e-2,
):
    """Solve baseline dispatch with flexibility offers allocated per service point.

    The returned community offers are exactly the sums of their individual
    service-point offers.  Each individual offer is constrained by that
    service point's power and state-of-charge headroom.
    ``c_thr`` is in EUR/kWh; battery throughput is converted from Wh to kWh.
    """
    N, T = pv.shape

    p_ch = cp.Variable((N, T), nonneg=True)
    p_dis = cp.Variable((N, T), nonneg=True)
    SoC = cp.Variable((N, T + 1))
    p_agg = cp.Variable(T)
    p_import = cp.Variable(T, nonneg=True)
    p_export = cp.Variable(T, nonneg=True)
    offer_up_by_service_point = cp.Variable((N, T), nonneg=True)
    offer_down_by_service_point = cp.Variable((N, T), nonneg=True)

    constraints, _ = build_common_constraints(
        pv, load, dt,
        Pch_max, Pdis_max, SoC_min, SoC_max, SoC0, eta_ch, eta_dis,
        p_ch, p_dis, SoC, p_agg,
    )
    constraints += [p_agg == p_export - p_import]

    offer_up = cp.sum(offer_up_by_service_point, axis=0)
    offer_down = cp.sum(offer_down_by_service_point, axis=0)
    constraints += [offer_up <= flex_up_req]
    constraints += [offer_down <= flex_down_req]

    # Each service point can increase PCC power by discharging more or by
    # reducing charge; it can decrease PCC power by charging more or by
    # reducing discharge.
    # Market offers must also respect each battery's directional rating,
    # even when reversing baseline dispatch provides a larger PCC change.
    constraints += [
        offer_up_by_service_point <= Pdis_max[:, np.newaxis],
        offer_down_by_service_point <= Pch_max[:, np.newaxis],
        offer_up_by_service_point <= Pdis_max[:, np.newaxis] - p_dis + p_ch,
        offer_down_by_service_point <= Pch_max[:, np.newaxis] - p_ch + p_dis,
    ]

    # Keep each offered direction feasible for the requested activation
    # duration, using the individual battery's state of charge and efficiency.
    constraints += [
        SoC[:, :-1] >= SoC_min[:, np.newaxis]
        + (L * dt) * cp.multiply(1.0 / eta_dis[:, np.newaxis], offer_up_by_service_point),
        SoC[:, :-1] <= SoC_max[:, np.newaxis]
        - (L * dt) * cp.multiply(eta_ch[:, np.newaxis], offer_down_by_service_point),
    ]

    energy_profit = cp.sum(
        cp.multiply(energy_cost_export, p_export)
        - cp.multiply(energy_cost_import, p_import)
    ) * dt
    throughput_pen = (c_thr / 1000.0) * cp.sum(p_ch + p_dis) * dt
    energy_objective = energy_profit - throughput_pen

    baseline_problem = cp.Problem(cp.Maximize(energy_objective), constraints)
    baseline_problem.solve(solver=cp.ECOS, verbose=False)
    energy_objective_star = baseline_problem.value

    offer_objective = cp.sum(offer_up + offer_down) * dt
    offer_constraints = constraints + [
        energy_objective >= energy_objective_star
        - eps_energy * max(1.0, abs(energy_objective_star))
    ]
    offer_problem = cp.Problem(cp.Maximize(offer_objective), offer_constraints)
    offer_problem.solve(solver=cp.ECOS, verbose=False)
    if offer_problem.status not in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
        raise RuntimeError(f"Per-service-point offer optimisation failed: {offer_problem.status}")

    return {
        "status_energy": baseline_problem.status,
        "status_offer": offer_problem.status,
        "p_agg_baseline": p_agg.value.copy(),
        "offer_up": np.sum(offer_up_by_service_point.value, axis=0),
        "offer_down": np.sum(offer_down_by_service_point.value, axis=0),
        "offer_up_by_service_point": offer_up_by_service_point.value.copy(),
        "offer_down_by_service_point": offer_down_by_service_point.value.copy(),
        "p_ch_baseline": p_ch.value.copy(),
        "p_dis_baseline": p_dis.value.copy(),
        "SoC_baseline": SoC.value.copy(),
        "J_energy_star": energy_objective_star,
    }


def solve_activation_dispatch(
    pv, load, dt, L, W,
    energy_cost_import, energy_cost_export,
    flex_price_up, flex_price_down,
    Pch_max, Pdis_max, SoC_min, SoC_max, SoC0, eta_ch, eta_dis,
    p_agg_baseline, offer_up, offer_dn,
    act_up_req, act_down_req,
    c_thr=1e-6
):
    """Solve the activated dispatch for an accepted flexibility request.

    Args:
        pv: Array of photovoltaic production values with shape
            ``(N, T)``.
        load: Array of consumption values with shape ``(N, T)``.
        dt: Optimisation timestep in hours.
        L: Number of timesteps in the activation horizon.
        W: Slice or index range defining the active flexibility window.
        energy_cost_import: Import energy price series for each
            timestep.
        energy_cost_export: Export energy price series for each
            timestep.
        flex_price_up: Activation remuneration for upward flexibility
            per timestep.
        flex_price_down: Activation remuneration for downward
            flexibility per timestep.
        Pch_max: Maximum charging power per entity.
        Pdis_max: Maximum discharging power per entity.
        SoC_min: Minimum state-of-charge bounds per entity.
        SoC_max: Maximum state-of-charge bounds per entity.
        SoC0: Initial state-of-charge per entity.
        eta_ch: Charging efficiency per entity.
        eta_dis: Discharging efficiency per entity.
        p_agg_baseline: Baseline aggregated PCC power profile.
        offer_up: Available upward flexibility offer profile.
        offer_dn: Available downward flexibility offer profile.
        act_up_req: Accepted upward activation request per timestep.
        act_down_req: Accepted downward activation request per
            timestep.
        c_thr: Reserved penalty parameter for dispatch regularisation.

    Returns:
        dict: Solver status and activated dispatch outputs, including
        baseline and activated aggregated power, charging power,
        discharging power, state of charge, import power, export power,
        and the total optimisation objective.
    """
    N, T = pv.shape

    p_ch  = cp.Variable((N, T), nonneg=True)
    p_dis = cp.Variable((N, T), nonneg=True)
    p_abs = cp.Variable((N, T), nonneg=True)
    SoC   = cp.Variable((N, T+1))

    p_agg    = cp.Variable(T)
    p_import = cp.Variable(T, nonneg=True)
    p_export = cp.Variable(T, nonneg=True)

    constraints, _ = build_common_constraints(
        pv, load, dt,
        Pch_max, Pdis_max, SoC_min, SoC_max, SoC0, eta_ch, eta_dis,
        p_ch, p_dis, SoC, p_agg
    )

    constraints += [
        p_abs >= p_ch, 
        p_abs >= p_dis,
        p_abs <= p_ch + p_dis
        ]

    # Activated PCC must follow baseline + activation (Up increases p_agg, Down decreases p_agg)
    p_target = p_agg_baseline + act_up_req - act_down_req
    constraints += [p_agg[W] == p_target[W]]

    # Settlement split
    constraints += [p_agg == p_export - p_import]

    # Economics
    energy_profit = cp.sum(cp.multiply(energy_cost_export, p_export) -
                           cp.multiply(energy_cost_import, p_import)) * dt

    activation_profit = cp.sum(cp.multiply(flex_price_up, act_up_req) +
                               cp.multiply(flex_price_down, act_down_req)) * dt

    # throughput_pen = c_thr * cp.sum(p_ch + p_dis) * dt

    c_sim = 1e-3  # start small; increase until overlap disappears
    simult_pen = c_sim * cp.sum(p_ch + p_dis - p_abs) * dt

    prob = cp.Problem(cp.Maximize(energy_profit + activation_profit - simult_pen), constraints)
    prob.solve(solver=cp.CLARABEL, verbose=True)

    # print("flex_price_up min/max:", np.min(flex_price_up[W]), np.max(flex_price_up[W]))
    # print("flex_price_down min/max:", np.min(flex_price_down[W]), np.max(flex_price_down[W]))
    # print("activation term (down) approx:", np.sum(flex_price_down[W] * act_down_req[W]) * dt)

    # print("energy_profit:", energy_profit.value)
    # print("activation_profit:", activation_profit.value)
    # print("throughput_pen:", simult_pen.value)
    # print("J_total:", prob.value)

    return {
        "status": prob.status,
        "J_total": prob.value,
        "p_agg_baseline": p_agg_baseline.copy(),
        "p_agg_activated": p_agg.value.copy(),
        "p_ch": p_ch.value.copy(),
        "p_dis": p_dis.value.copy(),
        "SoC": SoC.value.copy(),
        "p_import": p_import.value.copy(),
        "p_export": p_export.value.copy(),
    }


@router.post("", response_model=FlexibilityResponse)
async def flexibility(
    params: FlexProvisionParameters,
    db: Session = Depends(get_db),
    token=Depends(require_token("admin")),
) -> FlexibilityResponse:
    """Compute flexibility offers and activated dispatch for a request.

    Args:
        params: Flexibility provisioning parameters including horizon
            and request identifier.
        db: SQLAlchemy session.
        token: Authenticated admin token payload.

    Returns:
        A validated common energy-community report with flexibility
        optimisation details.
    """
    date1 = params.start_datetime
    date2 = params.end_datetime
    reqid = params.request_id

    # get the energy community from the token: user_id, tenant
    ec_id = cu.get_energy_community_id_from_token(db=db, token_data=token)

    # get service points over community_service_points: ec_id --> sp_id
    service_points = get_service_points_by_ec_id_crud(db=db, ec_id=ec_id)
    print(f"service_points: {[service_point.code for service_point in service_points]}")

    # get meters/assets/subchannels for the community service points
    community_relations = cu.build_community_relations(
        db=db,
        ec_id=ec_id,
        service_points=service_points,
    )

    print(f"community_relations: {community_relations}")


    # move start date for more realistic battery optimization
    # date1 = (datetime.strptime(date1, "%Y-%m-%dT%H:%M:%S") - timedelta(days=0)).strftime("%Y-%m-%dT%H:%M:%S")
    print(f"start: {date1}, end: {date2}")
    print(f"request_id: {reqid}")

    # number of service points
    N = len(service_points)
    print(f"N - number of service points: {N}")
    dt = 0.25

    community_data = cu.load_energy_community_data(
        db=db,
        community_relations=community_relations,
        start_date=date1,
        end_date=date2,
    )
    Nt = community_data.num_points
    pv = community_data.pv
    load = community_data.consumption
    Pch_max = community_data.charge_power_max
    Pdis_max = community_data.discharge_power_max
    SoC_max = community_data.storage_capacity_max
    energy_prices = community_data.energy_prices
    
    # aggregated PV production
    pv_agg = np.sum(pv, axis=0)

    # electricity prices definition
    energy_cost_import, energy_cost_export = cu.build_energy_pricing(
        energy_prices=energy_prices,
        num_points=Nt,
    )

    # Prices (€/kWh), shape (T,)
    # print(f"energy_cost_import: {energy_cost_import}")
    # print(f"energy_cost_export: {energy_cost_export}")

    # flexibility request caps
    flex_up_req = np.zeros(Nt)
    flex_down_req = np.zeros(Nt)
    # activation payment
    flex_price_up = np.zeros(Nt)
    flex_price_down = np.zeros(Nt)

    # get the full flexibility request object
    req = flex_crud.get_flex_request_by_id(db=db, request_id=UUID(reqid))
    print(f"Fetched flexibility request: {req}")

    horizon_index = pd.DatetimeIndex(
        pd.to_datetime(energy_prices.index[:Nt], utc=True, errors="raise")
    )
    request_start = pd.Timestamp(req.start)
    request_end = pd.Timestamp(req.end)
    if request_start.tzinfo is None:
        request_start = request_start.tz_localize("UTC")
    else:
        request_start = request_start.tz_convert("UTC")
    if request_end.tzinfo is None:
        request_end = request_end.tz_localize("UTC")
    else:
        request_end = request_end.tz_convert("UTC")

    # Locate the request in the actual optimisation horizon.  Do not derive
    # offsets from the clock time: the horizon may start before midnight.
    idx_start = int(horizon_index.searchsorted(request_start, side="left"))
    idx_stop = int(horizon_index.searchsorted(request_end, side="left"))
    if idx_start >= idx_stop:
        raise ValueError("Flexibility request window does not overlap the optimisation horizon.")
    logger.info(
        "Flexibility request horizon indexes: start=%d, stop=%d, window=%s to %s",
        idx_start,
        idx_stop,
        request_start,
        request_end,
    )

    if 'REDUCE' == req.type:
        flex_down_req[idx_start:idx_stop] = float(req.amount) * 1000.0
        flex_price_down[idx_start:idx_stop] = float(req.price) * 0.001
    elif 'INCREASE' == req.type:
        flex_up_req[idx_start:idx_stop] = float(req.amount) * 1000.0
        flex_price_up[idx_start:idx_stop] = float(req.price) * 0.001

    # print(f"flex_down_req: {flex_down_req}")
    # print(f"flex_price_down: {flex_price_down}")
    # print(f"flex_up_req: {flex_up_req}")
    # print(f"flex_price_up: {flex_price_up}")

    # length of activation
    L = idx_stop - idx_start
    # print(f"L: {L}")

    # define the flexibility window
    # W = ((flex_down_req > 0) | (flex_up_req > 0)).astype(int)

    # BSS parameters
    SoC_min = np.zeros(N)
    SoC0 = np.zeros(N)
    eta_ch = np.full(N, 0.95)
    eta_dis = np.full(N, 0.95)

    # Throughput penalty in EUR/kWh, discouraging simultaneous charge/discharge.
    c_thr = 0.01

    logger.info("Start preparing baseline and offer")

    res1 = solve_baseline_and_offer_per_service_point(
        pv, load, dt, L,
        energy_cost_import, energy_cost_export,
        flex_up_req, flex_down_req,
        Pch_max, Pdis_max, SoC_min, SoC_max, SoC0, eta_ch, eta_dis,
        c_thr
    )

    # print(f"res1: {res1}")

    p_agg_baseline = res1["p_agg_baseline"]
    offer_up = res1["offer_up"]
    offer_down = res1["offer_down"]
    offer_up_by_service_point = res1["offer_up_by_service_point"]
    offer_down_by_service_point = res1["offer_down_by_service_point"]

    logger.info(f"offer_up: {offer_up}")
    logger.info(f"offer_down: {offer_down}")

    logger.info(
        "Offer window: request=%s to %s; non-zero down timestamps=%s",
        req.start,
        req.end,
        horizon_index[np.asarray(offer_down) > 0.1].tolist(),
    )
    market_offer_chunks = [
        *build_market_offer_chunks(
            timestamps=horizon_index,
            power_profile_w=offer_up,
            direction="up",
            price=float(req.price),
            price_uom=req.price_uom,
            start=req.start,
            end=req.end,
        ),
        *build_market_offer_chunks(
            timestamps=horizon_index,
            power_profile_w=offer_down,
            direction="down",
            price=float(req.price),
            price_uom=req.price_uom,
            start=req.start,
            end=req.end,
        ),
    ]

    service_point_market_offer_chunks = [
        [
            *build_market_offer_chunks(
                timestamps=horizon_index,
                power_profile_w=service_point_offer_up,
                direction="up",
                price=float(req.price),
                price_uom=req.price_uom,
                start=req.start,
                end=req.end,
            ),
            *build_market_offer_chunks(
                timestamps=horizon_index,
                power_profile_w=service_point_offer_down,
                direction="down",
                price=float(req.price),
                price_uom=req.price_uom,
                start=req.start,
                end=req.end,
            ),
        ]
        for service_point_offer_up, service_point_offer_down in zip(
            offer_up_by_service_point,
            offer_down_by_service_point,
        )
    ]

    logger.info(f"market_offer_chunks: {market_offer_chunks}")

    # 2) Contract arrives: build activation schedules (must be <= offer)
    act_up_req = np.zeros(Nt)
    act_down_req = np.zeros(Nt)
    act_up_req = np.minimum(np.sum(Pdis_max), offer_up)
    act_down_req = np.minimum(np.sum(Pch_max), offer_down)

    # define the flexibility activation window
    W = slice(idx_start, idx_stop)

    logger.info("Solve for activation dispatch")

    # 3) Activation dispatch
    res2 = solve_activation_dispatch(
        pv, load, dt, L, W,
        energy_cost_import, energy_cost_export,
        flex_price_up, flex_price_down,
        Pch_max, Pdis_max, SoC_min, SoC_max, SoC0, eta_ch, eta_dis,
        p_agg_baseline, offer_up, offer_down,
        act_up_req, act_down_req,
        c_thr
    )

    logger.info("Baseline energy-optimal objective: %s", res1["J_energy_star"])
    logger.info("Activation run objective: %s", res2["J_total"])

    # use just the last 96 steps for the target day
    trailing_steps = 96

    timeseries_output = build_flexibility_timeseries_output(
        energy_prices=energy_prices,
        num_points=Nt,
        num_service_points=N,
        load=load,
        pv_agg=pv_agg,
        activation_results=res2,
        trailing_steps=trailing_steps,
    )

    def series_values(points: list[list[float]]) -> np.ndarray:
        """Extract numeric values from a timestamped output series."""
        return np.asarray([point[1] for point in points], dtype=float)

    summary_metrics = build_flexibility_summary_metrics(
        aggregated_load=series_values(timeseries_output["aggregated_load"]),
        aggregated_production=series_values(timeseries_output["aggregated_production"]),
        aggregated_bss_power=series_values(timeseries_output["aggregated_bss_power"]),
        grid_import=series_values(timeseries_output["grid_import"]),
        grid_export=series_values(timeseries_output["grid_export"]),
        trailing_steps=trailing_steps,
    )

    report_steps = len(timeseries_output["ts_ms"])
    timestamps = np.asarray(timeseries_output["ts_ms"], dtype=np.int64)

    def make_series(points: list[list[float]], unit: str) -> TimeSeries:
        return TimeSeries(
            points=[(int(timestamp), float(value)) for timestamp, value in points],
            unit=unit,
        )

    def timestamped(values: np.ndarray | list) -> list[list[float]]:
        """Pair a scalar report array with the report timestamps."""
        values_array = np.asarray(values, dtype=float)
        if values_array.ndim != 1 or len(values_array) != report_steps:
            raise ValueError("Report series length does not match its timestamp series.")
        return [[int(timestamp), float(value)] for timestamp, value in zip(timestamps, values_array)]

    report_start = pd.to_datetime(timestamps[0], unit="ms", utc=True).to_pydatetime()
    report_end = (
        pd.to_datetime(timestamps[-1], unit="ms", utc=True)
        + pd.Timedelta(minutes=15)
    ).to_pydatetime()

    charge_power = np.sum(res2["p_ch"], axis=0)[-report_steps:]
    discharge_power = np.sum(res2["p_dis"], axis=0)[-report_steps:]
    stored_energy = np.sum(res2["SoC"][:, :-1], axis=0)[-report_steps:]
    import_energy_cost = np.asarray(energy_cost_import[-report_steps:], dtype=float)
    export_energy_cost = np.asarray(energy_cost_export[-report_steps:], dtype=float)
    expenses_eur = float(
        np.sum(series_values(timeseries_output["grid_import"]) * dt * import_energy_cost)
        - np.sum(series_values(timeseries_output["grid_export"]) * dt * export_energy_cost)
    )

    report = FlexibilityReport(
        window=ReportWindow(
            start_datetime=report_start,
            end_datetime=report_end,
            timestep_minutes=15,
        ),
        community_type=community_relations.get("type"),
        summary=EnergySummary(
            imported_energy_wh=float(summary_metrics["imported_energy"]),
            exported_energy_wh=float(summary_metrics["exported_energy"]),
            produced_energy_wh=float(summary_metrics["produced_energy"]),
            consumed_energy_wh=float(summary_metrics["consumed_energy"]),
            self_consumed_energy_wh=float(np.sum(summary_metrics["self_consumed_power"]) * dt),
            self_consumption_pct=float(summary_metrics["self_consumption"]),
            self_sufficiency_pct=float(summary_metrics["self_sufficiency"]),
            expenses_eur=expenses_eur,
            co2_emissions_kg=float(summary_metrics["co2_emissions"]),
        ),
        series=EnergySeries(
            energy_price=make_series(timeseries_output["energy_prices_out"], "EUR/kWh"),
            consumption_power=make_series(timeseries_output["consumption"], "W"),
            production_power=make_series(timeseries_output["aggregated_production"], "W"),
            grid_import_power=make_series(timeseries_output["grid_import"], "W"),
            grid_export_power=make_series(timeseries_output["grid_export"], "W"),
            battery_charge_power=make_series(timestamped(charge_power), "W"),
            battery_discharge_power=make_series(timestamped(discharge_power), "W"),
            stored_energy=make_series(timestamped(stored_energy), "Wh"),
        ),
    )
    flexibility_details = FlexibilityDetails(
        request_id=reqid,
        solver_status=res2["status"],
        baseline_power=make_series(timeseries_output["baseline_agg"], "W"),
        activated_power=make_series(timeseries_output["aggregated_power"], "W"),
        offer_up_power=make_series(timestamped(res1["offer_up"][-report_steps:]), "W"),
        offer_down_power=make_series(timestamped(res1["offer_down"][-report_steps:]), "W"),
        market_offer_chunks=market_offer_chunks,
        service_point_offers=[
            ServicePointFlexibilityOffer(
                service_point_id=service_point["sp_id"],
                code=service_point["code"],
                offer_up_power=make_series(
                    timestamped(service_point_offer_up[-report_steps:]), "W"
                ),
                offer_down_power=make_series(
                    timestamped(service_point_offer_down[-report_steps:]), "W"
                ),
                market_offer_chunks=chunks,
            )
            for service_point, service_point_offer_up, service_point_offer_down, chunks in zip(
                community_relations["service_points"],
                offer_up_by_service_point,
                offer_down_by_service_point,
                service_point_market_offer_chunks,
            )
        ],
        objective_eur=float(res2["J_total"]),
    )

    response = FlexibilityResponse(report=report, flexibility=flexibility_details)
    try:
        published_files = await run_in_threadpool(
            publish_flexibility_daily_data,
            timestamps=horizon_index[-report_steps:],
            service_point_codes=[sp["code"] for sp in community_relations["service_points"]],
            consumption=load[:, -report_steps:],
            pv_production=pv[:, -report_steps:],
            charge_power=res2["p_ch"][:, -report_steps:],
            discharge_power=res2["p_dis"][:, -report_steps:],
            baseline_charge_power=res1["p_ch_baseline"][:, -report_steps:],
            baseline_discharge_power=res1["p_dis_baseline"][:, -report_steps:],
            meter_import=community_data.grid_import[:, -report_steps:],
            meter_export=community_data.grid_export[:, -report_steps:],
            pv_export_power=community_data.pv[:, -report_steps:],
        )
    except Exception as error:
        logger.exception("Failed to publish flexibility daily report for request %s", reqid)
        raise HTTPException(
            status_code=502,
            detail="Flexibility computed, but daily report publishing failed; some files may already have been published.",
        ) from error
    logger.info("Published flexibility daily reports: %s", published_files)
    return response



@router.get(
        "/request",
        summary="Get a list of flexibility requests",
        description="Get all registered flexibility requests on the platform",
        response_description="JSON string with a list of flexibility requests",
        response_model=FlexibilityRequestListResponse)
def list_flexibility_requests(db: Session = Depends(get_db), token=Depends(require_token("admin"))):
    """
    Retrieve flexibility requests.

    Returns:
        FlexibilityRequestListResponse: Response containing a list of requests and success flag.
    """
    try:
        flreqs = flex_crud.get_flex_requests(db=db)
        logger.debug("Fetched %d flexibility requests", len(flreqs))
        return FlexibilityRequestListResponse(data=flreqs, success=True)
    except PersistenceError as e:
        logger.exception("Database error while listing flexibility requests")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Database error occurred."},
        )

    except Exception as e:
        logger.exception("Unexpected error while listing flexibility requests")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred"},
        )


@router.get(
        "/request/selection",
        summary="Get flexibility requests for a selected energy community",
        description="Get registered flexibility requests for all low-voltage substations inside the selected energy community",
        response_description="JSON string with a list of filtered flexibility requests",
        response_model=FlexibilityRequestListResponse)
def list_community_flexibility_requests(
    ec_id: UUID = Query(..., description="Energy community identifier"),
    start_datetime: datetime | None = Query(
        default=None,
        description="Inclusive lower bound of the datetime window used to filter flexibility requests",
    ),
    end_datetime: datetime | None = Query(
        default=None,
        description="Exclusive upper bound of the datetime window used to filter flexibility requests",
    ),
    db: Session = Depends(get_db),
    token=Depends(require_token("admin")),
):
    """
    Retrieve flexibility requests for all low-voltage substations
    inside a selected energy community.

    Returns:
        FlexibilityRequestListResponse: Response containing the filtered
        list of requests and success flag.
    """
    try:
        if (start_datetime is None) != (end_datetime is None):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "start_datetime and end_datetime must be provided together."},
            )

        flreqs = flex_crud.get_flex_requests_for_community(
            db=db,
            ec_id=ec_id,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        )
        logger.debug("Fetched %d filtered flexibility requests", len(flreqs))
        return FlexibilityRequestListResponse(data=flreqs, success=True)
    except PersistenceError:
        logger.exception("Database error while listing filtered flexibility requests")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Database error occurred."},
        )
    except Exception:
        logger.exception("Unexpected error while listing filtered flexibility requests")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred"},
        )


@router.get(
        "/request/community",
        summary="Get flexibility requests for the authenticated energy community",
        description="Get registered flexibility requests for all low-voltage substations inside the authenticated user's energy community",
        response_description="JSON string with a list of filtered flexibility requests",
        response_model=FlexibilityRequestListResponse)
def list_token_community_flexibility_requests(
    start_datetime: datetime | None = Query(
        default=None,
        description="Inclusive lower bound of the datetime window used to filter flexibility requests",
    ),
    end_datetime: datetime | None = Query(
        default=None,
        description="Exclusive upper bound of the datetime window used to filter flexibility requests",
    ),
    db: Session = Depends(get_db),
    token=Depends(require_token("admin")),
):
    """
    Retrieve flexibility requests for all low-voltage substations
    inside the authenticated user's energy community.

    Returns:
        FlexibilityRequestListResponse: Response containing the filtered
        list of requests and success flag.
    """
    try:
        if (start_datetime is None) != (end_datetime is None):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"message": "start_datetime and end_datetime must be provided together."},
            )

        ec_id = cu.get_energy_community_id_from_token(db=db, token_data=token)
        flreqs = flex_crud.get_flex_requests_for_community(
            db=db,
            ec_id=ec_id,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        )
        logger.debug(
            "Fetched %d filtered flexibility requests for token community %s",
            len(flreqs),
            ec_id,
        )
        return FlexibilityRequestListResponse(data=flreqs, success=True)
    except PersistenceError:
        logger.exception("Database error while listing token community flexibility requests")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Database error occurred."},
        )
    except Exception:
        logger.exception("Unexpected error while listing token community flexibility requests")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Unexpected error occurred"},
        )


@router.post(
        "/request",
        summary="Register the flexibility request",
        description="Create new flexibility request posted by the DSO using the provided parameters",
        response_description="JSON string containing the results",
        response_model=FlexibilityRequestResponse)
def register_flexibility_request(params: FlexibilityRequestCreate, db: Session = Depends(get_db), token=Depends(require_token("admin"))):
    """
    Register a new flexibility request from the DSO.

    Args:
        params: Flexibility request creation payload.
        db: SQLAlchemy session.
        token: Auth dependency (admin).

    Returns:
        FlexibilityRequestResponse: The created request and success flag.
    """
    logger.debug(f"Register Flexibility Request: {params}")
    
    try:
        flrq = flex_crud.write_flex_request(db=db, flexreq=params)
        logger.debug("Created Flexibility Request: %s", flrq)
        return FlexibilityRequestResponse(data=flrq, success=True) 
    
    except ConflictError:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"message": "Flexibility request already exists or violates constraints."},
        )

    except PersistenceError:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"message": "Database error occurred while creating flexibility request."},
        )






