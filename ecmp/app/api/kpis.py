from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from app.db.session import SessionLocal
from app.auth.dependencies import require_token
from app.schemas.flexibility import (
    FlexProvisionParameters, FlexibilityRequestData, FlexibilityRequestCreate,
    FlexibilityRequestListResponse, FlexibilityRequestResponse
)
from app.schemas.kpis import (
    BaselineRequest
)
import app.crud.flexibility as flex_crud
from app.crud.flexibility import ConflictError, PersistenceError
import app.models.ec as ec
import app.models.flex as flex
from app.optimisation.entity import Entity
from datetime import datetime, timedelta
from decimal import Decimal
import json
from uuid import UUID
import pandas as pd
import numpy as np
import cvxpy as cp


import logging

from app.keycloak_instance import kc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kpis", tags=["kpis"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def format_output_series(series):
    # generate result array
    time = (series.index.astype("int64") // 1_000_000).tolist()
    data = series.values.tolist()
    res = [[t, d] for t, d in zip(time, data)]

    return res


def generate_report_no(data, energy_prices):
    co2 = 0.150
    # energy_cost = 0.00012
    # grid_tarrif = 0.0001
    # calculate self-sufficiency for individual entities
    data_out = data
    production = data_out['pv_production_power']
    consumption_bss = data_out['consumption'] + data['bss_charge_power']
    # consumption_bss = data_out['consumption'] + data['bss_discharge_power']
    consumption = data_out['consumption']
    self_consumption = production.combine(consumption_bss, func=np.minimum)
    grid_feed_in = data_out['grid_export_power']
    grid_import = data_out['grid_import_power']
    # convert power values into the Wh values (energy)
    # 15-min values
    consumption_wh = consumption * 0.25 #Decimal('0.25')
    production_wh = production * 0.25 #Decimal('0.25')
    self_consumption_wh = self_consumption * 0.25 #Decimal('0.25')
    grid_feed_in_wh = grid_feed_in * 0.25 #Decimal('0.25')
    grid_import_wh = grid_import * 0.25 #Decimal('0.25')

    # sum the energies
    total_energy_demanded = consumption_wh.tail(96).sum()
    total_energy_produced = production_wh.tail(96).sum()
    total_grid_feed_in = grid_feed_in_wh.tail(96).sum()
    total_grid_import = grid_import_wh.tail(96).sum()
    self_consumed_energy = self_consumption_wh.tail(96).sum()


    print(f"self_consumed_energy: {self_consumed_energy}")
    print(f"total_energy_demanded: {total_energy_demanded}")
    print(f"total_energy_produced: {total_energy_produced}")

    self_sufficiency_result = (self_consumed_energy / total_energy_demanded) * 100.0 #Decimal('100.0')
    self_consumption_result = (self_consumed_energy / total_energy_produced) * 100.0 #Decimal('100.0')

    # cost = (grid_import_wh * energy_prices * 1.15 + grid_import_wh * grid_tarrif).sum()
    # cost = (grid_import_wh * energy_prices * 1.15)
    # energy_prices = pd.to_numeric(energy_prices['price'], errors='coerce')
    energy_prices = energy_prices['price']/1000.0
    # grid_import_wh = pd.to_numeric(grid_import_wh, errors='coerce')
    cost = (grid_import_wh.mul(energy_prices, axis=0) * 1.15)
    cost = cost.tail(96).sum()

    co2em = grid_import_wh * co2
    co2em = co2em.tail(96).sum()

    return {
        'total_demand': total_energy_demanded,
        'total_production': total_energy_produced,
        'total_export': total_grid_feed_in,
        'total_import': total_grid_import,
        'self_consumed_energy': self_consumed_energy,
        'self_sufficiency': self_sufficiency_result,
        'self_consumption': self_consumption_result,
        'produced_energy': total_energy_produced,
        'consumed_energy': total_energy_demanded,
        'consumption': consumption.tail(96),
        'production': production.tail(96),
        'stored_energy': data_out['bss_stored_energy'].tail(96),
        'cost': cost,
        'co2_emissions': co2em
        }


def generate_report(data, energy_prices):
    co2 = 0.150
    # energy_cost = 0.00012
    # grid_tarrif = 0.0001
    # calculate self-sufficiency for individual entities
    # data_out = data.tail(96)
    data_out = data
    production = data_out['pv_production_power']
    consumption_bss = data_out['consumption'] + data['bss_charge_power']
    # consumption_bss = data_out['consumption'] + data['bss_discharge_power']
    consumption = data_out['consumption']
    self_consumption = production.combine(consumption_bss, func=np.minimum)
    grid_feed_in = data_out['grid_export_power']
    grid_import = data_out['grid_import_power']
    local_import = data_out['local_import']
    # convert power values into the Wh values (energy)
    # 15-min values
    consumption_wh = consumption * 0.25 #Decimal('0.25')
    production_wh = production * 0.25 #Decimal('0.25')
    self_consumption_wh = self_consumption * 0.25 #Decimal('0.25')
    grid_feed_in_wh = grid_feed_in * 0.25 #Decimal('0.25')
    grid_import_wh = grid_import * 0.25 #Decimal('0.25')
    local_import_wh = local_import * 0.25 #Decimal('0.25')
    # sum the energies
    total_energy_demanded = consumption_wh.tail(96).sum()
    total_energy_produced = production_wh.tail(96).sum()
    total_grid_feed_in = grid_feed_in_wh.tail(96).sum()
    total_grid_import = grid_import_wh.tail(96).sum()
    self_consumed_energy = self_consumption_wh.tail(96).sum()

    self_sufficiency_result = (self_consumed_energy / total_energy_demanded) * 100.0 #Decimal('100.0')
    self_consumption_result = (self_consumed_energy / total_energy_produced) * 100.0 #Decimal('100.0')

    # cost = ((grid_import_wh * energy_prices * 1.15 + grid_import_wh * grid_tarrif) + (local_import_wh * energy_cost)).sum()
    energy_prices = energy_prices['price']/1000.0
    cost = (grid_import_wh.mul(energy_prices, axis=0) * 1.15) + (local_import_wh * energy_prices)
    cost = cost.tail(96).to_numpy().sum()

    co2em = (grid_import_wh.tail(96) * co2).sum()


    return {
        'total_demand': total_energy_demanded,
        'total_production': total_energy_produced,
        'total_export': total_grid_feed_in,
        'total_import': total_grid_import,
        'self_consumed_energy': self_consumed_energy,
        'self_sufficiency': self_sufficiency_result,
        'self_consumption': self_consumption_result,
        'produced_energy': total_energy_produced,
        'consumed_energy': total_energy_demanded,
        'export': grid_feed_in_wh,
        'import': grid_import_wh,
        'consumption': consumption,
        'production': production,
        'stored_energy': data_out['bss_stored_energy'],
        'cost': cost,
        'co2_emissions': co2em
        }


def surplus_allocation(data1, data2, data3):#, pricing):
    """This function provides allocation and allocation coefficients for the local surplus sharing to the energy community participants
    """
    allocation_coeff = [[], [], []]
    allocation = [[], [], []]

    for i in range(len(data1)):
        state = [data1.iloc[i], data2.iloc[i], data3.iloc[i]]

        for mp in range(len(state)):
            # calculate allocation coefficients for each metering point's injected surplus
            # customer c's local surplus
            # grid_export_power
            surplus = state[mp]['grid_export_power']
            # all customers' supplies/imports
            supplies = [state[customer]['grid_import_power'] for customer in range(len(state))]
            total_supply = sum(supplies) - supplies[mp]

            coeff = []
            alloc = []
            if 0.0 == surplus:
                coeff = [0.0 for item in state]
            else:
                for c in range(len(state)):
                    # calculate allocation coefficients per customer
                    if c == mp:
                        alcf = 0.0
                    elif 0.0 == total_supply:
                        alcf = 0.0
                    else:
                        alcf = supplies[c] / total_supply
                    coeff.append(alcf)
            alloc = [surplus*cf for cf in coeff]
            # alloc = [surplus*Decimal(str(cf)) for cf in coeff]   

            allocation_coeff[mp].append(coeff)
            allocation[mp].append(alloc)

    return allocation_coeff, allocation
            

def aggregate_local_import(allocation):
    local_import = [[], [], []]

    for i in range(3):
        for j in range(len(allocation[i])):
            mp_sum = (allocation[0][j][i] + allocation[1][j][i] + allocation[2][j][i])#*0.25
            local_import[i].append(mp_sum)

    return local_import


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


def build_common_constraints(
    pv, load, dt,
    Pch_max, Pdis_max, SoC_min, SoC_max, SoC0, eta_ch, eta_dis,
    p_ch, p_dis, SoC, p_agg
):
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


def solve_baseline(
    pv, load, dt, L,
    energy_cost_import, energy_cost_export,
    Pch_max, Pdis_max, SoC_min, SoC_max, SoC0, eta_ch, eta_dis,
    c_thr=0.01, eps_energy=1e-2
):
    N, T = pv.shape

    # Variables
    p_ch = cp.Variable((N, T), nonneg=True)
    p_dis = cp.Variable((N, T), nonneg=True)
    SoC = cp.Variable((N, T+1))

    p_agg = cp.Variable(T)                  # baseline PCC power
    p_import = cp.Variable(T, nonneg=True)
    p_export = cp.Variable(T, nonneg=True)

    # Common constraints
    constraints, _ = build_common_constraints(
        pv, load, dt,
        Pch_max, Pdis_max, SoC_min, SoC_max, SoC0, eta_ch, eta_dis,
        p_ch, p_dis, SoC, p_agg
    )

    # Import/export split for baseline settlement
    constraints += [p_agg == p_export - p_import]

    # Offer feasibility (power headroom)
    sum_dis = cp.sum(p_dis, axis=0)
    sum_ch  = cp.sum(p_ch, axis=0)

    # Baseline energy profit
    energy_profit = cp.sum(cp.multiply(energy_cost_export, p_export) -
                           cp.multiply(energy_cost_import, p_import)) * dt

    # Throughput penalty (discourage simultaneous ch/dis)
    throughput_pen = c_thr * cp.sum(p_ch + p_dis) * dt

    J_energy = energy_profit - throughput_pen

    # ---------- Solve to maximize baseline energy objective ----------
    prob_energy = cp.Problem(cp.Maximize(J_energy), constraints)
    prob_energy.solve(solver=cp.ECOS, verbose=False)
    J_energy_baseline = prob_energy.value

    return {
        "status_energy": prob_energy.status,
        "p_agg_baseline": p_agg.value.copy(),
        "p_ch_baseline": p_ch.value.copy(),
        "p_dis_baseline": p_dis.value.copy(),
        "SoC_baseline": SoC.value.copy(),
        "baseline_revenue": J_energy_baseline,
    }


def prepare_flex_offer(
    pv, load, p_agg_baseline, baseline_revenue, dt, L, W,
    energy_cost_import, energy_cost_export,
    flex_up_req, flex_down_req, flex_price_up, flex_price_down,
    Pch_max, Pdis_max, SoC_min, SoC_max, SoC0, eta_ch, eta_dis,
    c_thr=0.01, eps_energy=1e-2
):
    N, T = pv.shape

    # Variables
    p_ch = cp.Variable((N, T), nonneg=True)
    p_dis = cp.Variable((N, T), nonneg=True)
    SoC = cp.Variable((N, T+1))

    p_agg = cp.Variable(T)  # baseline PCC power
    p_abs = cp.Variable((N, T), nonneg=True)
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

    p_grid_abs = cp.Variable(T, nonneg=True)

    constraints += [
        p_grid_abs >= p_import,
        p_grid_abs >= p_export,
        p_grid_abs <= p_import + p_export,
]

    # Offer caps (DSO request)
    constraints += [offer_up <= flex_up_req]
    constraints += [offer_dn <= flex_down_req]

    # Offer feasibility (power headroom)
    sum_dis = cp.sum(p_dis, axis=0)
    sum_ch  = cp.sum(p_ch, axis=0)
    dis_headroom = np.sum(Pdis_max) - sum_dis 
    ch_headroom  = np.sum(Pch_max) - sum_ch
    constraints += [offer_dn <= ch_headroom + sum_dis] # reduce p_agg via more charge OR less discharge
    constraints += [offer_up <= dis_headroom + sum_ch] # increase p_agg via more discharge OR less charge

    constraints += [
        p_abs >= p_ch, 
        p_abs >= p_dis,
        p_abs <= p_ch + p_dis
        ]
    
    # Activated PCC must follow baseline + activation (Up increases p_agg, Down decreases p_agg)
    p_target = p_agg_baseline + offer_up - offer_dn
    constraints += [p_agg[W] == p_target[W]]

    # Offer feasibility (SOC headroom) 
    # soc_total = cp.sum(SoC[:, :-1], axis=0)
    # constraints += [
    #     # To sustain UP (increase p_agg) for L steps -> need energy above SOC_min to discharge
    #     soc_total >= np.sum(SoC_min) + (L*dt) * (offer_up / np.mean(eta_dis)),
    #     # To sustain DOWN (decrease p_agg) for L steps -> need room below SOC_max to charge
    #     soc_total <= np.sum(SoC_max) - (L*dt) * (np.mean(eta_ch) * offer_dn),
    # ]

    # Baseline energy profit
    energy_profit = cp.sum(cp.multiply(energy_cost_export, p_export) -
                           cp.multiply(energy_cost_import, p_import)) * dt

    activation_profit = cp.sum(cp.multiply(flex_price_up, offer_up) +
                               cp.multiply(flex_price_down, offer_dn)) * dt
    
    c_sim = 1e-3  # start small; increase until overlap disappears
    simult_pen = c_sim * cp.sum(p_ch + p_dis - p_abs) * dt
    c_grid_sim = 1e-3  
    grid_simult_pen = c_grid_sim * cp.sum(p_import + p_export - p_grid_abs) * dt

    J_energy = energy_profit + activation_profit - simult_pen - grid_simult_pen

    constraints += [J_energy >= (1 - eps_energy) * baseline_revenue]
    prob = cp.Problem(cp.Maximize(J_energy), constraints)
    prob.solve(solver=cp.CLARABEL, verbose=False)

    up_rhs = dis_headroom.value + sum_ch.value
    dn_rhs = ch_headroom.value + sum_dis.value

    print("offer_up[W]:", offer_up.value[W])
    print("up_rhs[W]:", up_rhs[W])

    print("offer_dn[W]:", offer_dn.value[W])
    print("dn_rhs[W]:", dn_rhs[W])

    # J_energy_star = prob.value
    # offer_obj = cp.sum(offer_up + offer_dn) * dt

    # constraints2 = constraints + [J_energy >= (1 - eps_energy) * J_energy_star]
    # prob_offer = cp.Problem(cp.Maximize(offer_obj), constraints2)
    # prob_offer.solve(solver=cp.ECOS, verbose=False)

    # print(f"offer_up: {offer_up[W]}")
    # print(f"offer_dn: {offer_dn[W]}")

    return {
        "status_energy": prob.status,
        "p_agg": p_agg.value.copy(),
        "offer_up": offer_up.value.copy(),
        "offer_down": offer_dn.value.copy(),
        "p_ch_baseline": p_ch.value.copy(),
        "p_dis_baseline": p_dis.value.copy(),
        "SoC_baseline": SoC.value.copy(),
        "flex_revenue": prob.value,
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
    N, T = pv.shape

    # act_up_req[W] = 32000.0
    print(f"act_up_req: {act_up_req[W]}")
    print(f"act_down_req: {act_down_req[W]}")

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

    # print(f"p_agg_baseline: {p_agg_baseline}")
    # print(f"p_target: {p_target}")
    

    # Settlement split
    constraints += [p_agg == p_export - p_import]

    # Economics
    energy_profit = cp.sum(cp.multiply(energy_cost_export, p_export) -
                           cp.multiply(energy_cost_import, p_import)) * dt

    activation_profit = cp.sum(cp.multiply(flex_price_up, act_up_req) +
                               cp.multiply(flex_price_down, act_down_req)) * dt

    c_sim = 1e-3  # start small; increase until overlap disappears
    simult_pen = c_sim * cp.sum(p_ch + p_dis - p_abs) * dt

    prob = cp.Problem(cp.Maximize(energy_profit + activation_profit - simult_pen), constraints)
    prob.solve(solver=cp.CLARABEL, verbose=False)

    print(f"act_up_req: {act_up_req[W]}")
    print(f"act_down_req: {act_down_req[W]}")
    print(f"sum_up: {np.sum(act_up_req[W])/L}")
    print(f"sum_down: {np.sum(act_down_req[W])/L}")

    print("flex_price_up min/max:", np.min(flex_price_up[W]), np.max(flex_price_up[W]))
    print("flex_price_down min/max:", np.min(flex_price_down[W]), np.max(flex_price_down[W]))
    print("activation term (down) approx:", np.sum(flex_price_down[W] * act_down_req[W]) * dt)
    print("activation term (up) approx:", np.sum(flex_price_up[W] * act_up_req[W]) * dt)

    print("energy_profit:", energy_profit.value)
    print("activation_profit:", activation_profit.value)
    print("throughput_pen:", simult_pen.value)
    print("J_total:", prob.value)

    return {
        "status": prob.status,
        "activation_revenue": prob.value,
        "shifted_load": np.sum(act_up_req[W])/L + np.sum(act_down_req[W])/L,
        "p_agg_baseline": p_agg_baseline.copy(),
        "p_agg_activated": p_agg.value.copy(),
        "p_ch": p_ch.value.copy(),
        "p_dis": p_dis.value.copy(),
        "SoC": SoC.value.copy(),
        "p_import": p_import.value.copy(),
        "p_export": p_export.value.copy(),
    }


@router.post("/baseline")
async def baseline_report(params: BaselineRequest, db: Session = Depends(get_db)): #, token=Depends(require_token("admin"))):
    '''
    This endpoint loads EC data and simulates the situation without BSS

    '''
    date1 = params.start_datetime
    date2 = params.end_datetime
    print(f"start: {date1}, end: {date2}")

    # metering point name E003
    # BSS 139188  capacity: 8kW/55kWh 
    # pv1
    entity1 = Entity(bss_capacity=55000.0, bss_max_power=8000.0)
    import_power = get_sm_readings(service_point_code='E003', channel_role='import', column_name='import', start_date=date1, end_date=date2).astype(float)*1000.0
    export_power = get_sm_readings(service_point_code='E003', channel_role='export', column_name='export', start_date=date1, end_date=date2).astype(float)*1000.0
    production_power = get_power_data(service_point_code='E003', asset_type='PV', sm_hrid='pv1', role='export_power', column_name='production', start_date=date1, end_date=date2).astype(float)
    consumption_power = get_power_data(service_point_code='E003', asset_type='PM', sm_hrid='pm1', role='consumption', column_name='consumption', start_date=date1, end_date=date2).astype(float)
    bss_charge_power = get_power_data(service_point_code='E003', asset_type='BSS', sm_hrid='BSS 139188', role='charge_power', column_name='bss_charge_power', start_date=date1, end_date=date2).astype(float)
    bss_discharge_power = get_power_data(service_point_code='E003', asset_type='BSS', sm_hrid='BSS 139188', role='discharge_power', column_name='bss_discharge_power', start_date=date1, end_date=date2).astype(float)
    bss_soc = get_power_data(service_point_code='E003', asset_type='BSS', sm_hrid='BSS 139188', role='state_of_charge', column_name='bss_state_of_charge', start_date=date1, end_date=date2).astype(float)
    entity1.set_entity_data(import_power=import_power, export_power=export_power, production_power=production_power, 
                            consumption_power=consumption_power, bss_charge_power=bss_charge_power, bss_discharge_power=bss_discharge_power,
                            bss_state_of_charge=bss_soc)

    # # metering point name E004
    # # BSS 136739  capacity: 8kW/55kWh 
    # # pv2
    entity2 = Entity(bss_capacity=55000.0, bss_max_power=8000.0)
    import_power = get_sm_readings(service_point_code='E004', channel_role='import', column_name='import', start_date=date1, end_date=date2).astype(float)*1000.0
    export_power = get_sm_readings(service_point_code='E004', channel_role='export', column_name='export', start_date=date1, end_date=date2).astype(float)*1000.0
    production_power = get_power_data(service_point_code='E004', asset_type='PV', sm_hrid='pv2', role='export_power', column_name='production', start_date=date1, end_date=date2).astype(float)
    consumption_power = get_power_data(service_point_code='E004', asset_type='PM', sm_hrid='pm2', role='consumption', column_name='consumption', start_date=date1, end_date=date2).astype(float)
    bss_charge_power = get_power_data(service_point_code='E004', asset_type='BSS', sm_hrid='BSS 136739', role='charge_power', column_name='bss_charge_power', start_date=date1, end_date=date2).astype(float)
    bss_discharge_power = get_power_data(service_point_code='E004', asset_type='BSS', sm_hrid='BSS 136739', role='discharge_power', column_name='bss_discharge_power', start_date=date1, end_date=date2).astype(float)
    bss_soc = get_power_data(service_point_code='E004', asset_type='BSS', sm_hrid='BSS 136739', role='state_of_charge', column_name='bss_state_of_charge', start_date=date1, end_date=date2).astype(float)
    entity2.set_entity_data(import_power=import_power, export_power=export_power, production_power=production_power, 
                            consumption_power=consumption_power, bss_charge_power=bss_charge_power, bss_discharge_power=bss_discharge_power,
                            bss_state_of_charge=bss_soc)

    # # metering point name E007
    # # BSS 137085  capacity: 16kW/110kWh 
    # # pv3
    entity3 = Entity(bss_capacity=110000.0, bss_max_power=16000.0)
    import_power = get_sm_readings(service_point_code='E007', channel_role='import', column_name='import', start_date=date1, end_date=date2).astype(float)*1000.0
    export_power = get_sm_readings(service_point_code='E007', channel_role='export', column_name='export', start_date=date1, end_date=date2).astype(float)*1000.0
    production_power = get_power_data(service_point_code='E007', asset_type='PV', sm_hrid='pv3', role='export_power', column_name='production', start_date=date1, end_date=date2).astype(float)
    consumption_power = get_power_data(service_point_code='E007', asset_type='PM', sm_hrid='pm3', role='consumption', column_name='consumption', start_date=date1, end_date=date2).astype(float)
    bss_charge_power = get_power_data(service_point_code='E007', asset_type='BSS', sm_hrid='BSS 137085', role='charge_power', column_name='bss_charge_power', start_date=date1, end_date=date2).astype(float)
    bss_discharge_power = get_power_data(service_point_code='E007', asset_type='BSS', sm_hrid='BSS 137085', role='discharge_power', column_name='bss_discharge_power', start_date=date1, end_date=date2).astype(float)
    bss_soc = get_power_data(service_point_code='E007', asset_type='BSS', sm_hrid='BSS 137085', role='state_of_charge', column_name='bss_state_of_charge', start_date=date1, end_date=date2).astype(float)
    entity3.set_entity_data(import_power=import_power, export_power=export_power, production_power=production_power, 
                            consumption_power=consumption_power, bss_charge_power=bss_charge_power, bss_discharge_power=bss_discharge_power,
                            bss_state_of_charge=bss_soc)


    data1 = entity1.get_entity_data().copy()
    data2 = entity2.get_entity_data().copy()
    data3 = entity3.get_entity_data().copy()

    # get number of steps in data
    data_length = len(data1)
    T = data_length 

    dt = 0.25
    N = 3 #number of users

    # data placeholders
    pv = np.zeros((N,T))
    load = np.zeros((N,T))
    import_power = np.zeros((N,T))
    export_power = np.zeros((N,T))
    bss_charge = np.zeros((N,T))
    bss_discharge = np.zeros((N,T))


    # load data
    pv[0,:] = data1['pv_production_power']
    pv[1,:] = data2['pv_production_power']
    pv[2,:] = data3['pv_production_power']
    load[0,:] = data1['consumption']
    load[1,:] = data2['consumption']
    load[2,:] = data3['consumption']
    import_power[0,:] = data1['grid_import_power']
    import_power[1,:] = data2['grid_import_power']
    import_power[2,:] = data3['grid_import_power']
    export_power[0,:] = data1['grid_export_power']
    export_power[1,:] = data2['grid_export_power']
    export_power[2,:] = data3['grid_export_power']
    bss_charge[0,:] = data1['bss_charge_power']
    bss_charge[1,:] = data2['bss_charge_power']
    bss_charge[2,:] = data3['bss_charge_power']
    bss_discharge[0,:] = data1['bss_discharge_power']
    bss_discharge[1,:] = data2['bss_discharge_power']
    bss_discharge[2,:] = data3['bss_discharge_power']

    
    # aggregated PV
    pv_agg = np.sum(pv, axis=0)

    # €/kWh
    tariff_kwh = 0.02296
    # €/Wh
    tariff = tariff_kwh * 0.001

    op_grid_feed_in = 0.0567
    op_consumption = 0.3239


    aggregated_production = np.asarray(pv_agg)
    aggregated_bss_power = np.sum((np.asarray(bss_charge) - np.asarray(bss_discharge)), axis=0)
    aggregated_load = np.sum(load, axis=0)
    grid_import = np.sum(np.asarray(import_power), axis=0)
    grid_export = np.sum(np.asarray(export_power), axis=0)

    # grid-side consumption without the BSS
    aggregated_pcc_power = aggregated_production - aggregated_bss_power - aggregated_load
    grid_import = np.where(aggregated_pcc_power < 0, np.abs(aggregated_pcc_power), 0)
    grid_export = np.where(aggregated_pcc_power > 0, aggregated_pcc_power, 0)

    self_consumption = np.minimum(aggregated_production, aggregated_load)

    consumption_wh =  aggregated_load * 0.25
    production_wh = aggregated_production * 0.25
    grid_import_wh = grid_import * 0.25
    grid_export_wh = grid_export * 0.25
    self_consumption_wh = self_consumption * 0.25

    total_energy_demanded = consumption_wh.sum() * 0.001
    total_energy_produced = production_wh.sum() * 0.001
    total_grid_import = grid_import_wh.sum() * 0.001
    total_grid_export = grid_export_wh.sum() * 0.001
    self_consumed_energy = self_consumption_wh.sum() * 0.001

    self_sufficiency = (self_consumed_energy / total_energy_demanded) * 100.0
    self_consumption = (self_consumed_energy / total_energy_produced) * 100.0

    expenses = total_grid_export * op_grid_feed_in - total_grid_import * (op_consumption + tariff_kwh)


    output = {
        'start_datetime': date1,
        'end_datetime': date2,
        'results': {
            'revenue': expenses,
            'imported_energy': total_grid_import,
            'exported_energy': total_grid_export,
            'self_consumption': self_consumption,
            'self_sufficiency': self_sufficiency,
            'produced_energy': total_energy_produced,
            'demanded_energy': total_energy_demanded
        }
    }
    

    json_str = json.dumps(output)


    return json_str



@router.post("/optimisation")
async def optimised_ec_sc_report(params: BaselineRequest, db: Session = Depends(get_db)): #, token=Depends(require_token("admin"))):
    '''
    This endpoint loads EC data and simulates the situation with BSS maximising self-consumption and uses emergy community prices

    '''
    date1 = params.start_datetime
    date2 = params.end_datetime
    print(f"start: {date1}, end: {date2}")

    # metering point name E003
    # BSS 139188  capacity: 8kW/55kWh 
    # pv1
    entity1 = Entity(bss_capacity=55000.0, bss_max_power=8000.0)
    import_power = get_sm_readings(service_point_code='E003', channel_role='import', column_name='import', start_date=date1, end_date=date2).astype(float)*1000.0
    export_power = get_sm_readings(service_point_code='E003', channel_role='export', column_name='export', start_date=date1, end_date=date2).astype(float)*1000.0
    production_power = get_power_data(service_point_code='E003', asset_type='PV', sm_hrid='pv1', role='export_power', column_name='production', start_date=date1, end_date=date2).astype(float)
    consumption_power = get_power_data(service_point_code='E003', asset_type='PM', sm_hrid='pm1', role='consumption', column_name='consumption', start_date=date1, end_date=date2).astype(float)
    bss_charge_power = get_power_data(service_point_code='E003', asset_type='BSS', sm_hrid='BSS 139188', role='charge_power', column_name='bss_charge_power', start_date=date1, end_date=date2).astype(float)
    bss_discharge_power = get_power_data(service_point_code='E003', asset_type='BSS', sm_hrid='BSS 139188', role='discharge_power', column_name='bss_discharge_power', start_date=date1, end_date=date2).astype(float)
    bss_soc = get_power_data(service_point_code='E003', asset_type='BSS', sm_hrid='BSS 139188', role='state_of_charge', column_name='bss_state_of_charge', start_date=date1, end_date=date2).astype(float)
    entity1.set_entity_data(import_power=import_power, export_power=export_power, production_power=production_power, 
                            consumption_power=consumption_power, bss_charge_power=bss_charge_power, bss_discharge_power=bss_discharge_power,
                            bss_state_of_charge=bss_soc)

    # # metering point name E004
    # # BSS 136739  capacity: 8kW/55kWh 
    # # pv2
    entity2 = Entity(bss_capacity=55000.0, bss_max_power=8000.0)
    import_power = get_sm_readings(service_point_code='E004', channel_role='import', column_name='import', start_date=date1, end_date=date2).astype(float)*1000.0
    export_power = get_sm_readings(service_point_code='E004', channel_role='export', column_name='export', start_date=date1, end_date=date2).astype(float)*1000.0
    production_power = get_power_data(service_point_code='E004', asset_type='PV', sm_hrid='pv2', role='export_power', column_name='production', start_date=date1, end_date=date2).astype(float)
    consumption_power = get_power_data(service_point_code='E004', asset_type='PM', sm_hrid='pm2', role='consumption', column_name='consumption', start_date=date1, end_date=date2).astype(float)
    bss_charge_power = get_power_data(service_point_code='E004', asset_type='BSS', sm_hrid='BSS 136739', role='charge_power', column_name='bss_charge_power', start_date=date1, end_date=date2).astype(float)
    bss_discharge_power = get_power_data(service_point_code='E004', asset_type='BSS', sm_hrid='BSS 136739', role='discharge_power', column_name='bss_discharge_power', start_date=date1, end_date=date2).astype(float)
    bss_soc = get_power_data(service_point_code='E004', asset_type='BSS', sm_hrid='BSS 136739', role='state_of_charge', column_name='bss_state_of_charge', start_date=date1, end_date=date2).astype(float)
    entity2.set_entity_data(import_power=import_power, export_power=export_power, production_power=production_power, 
                            consumption_power=consumption_power, bss_charge_power=bss_charge_power, bss_discharge_power=bss_discharge_power,
                            bss_state_of_charge=bss_soc)

    # # metering point name E007
    # # BSS 137085  capacity: 16kW/110kWh 
    # # pv3
    entity3 = Entity(bss_capacity=110000.0, bss_max_power=16000.0)
    import_power = get_sm_readings(service_point_code='E007', channel_role='import', column_name='import', start_date=date1, end_date=date2).astype(float)*1000.0
    export_power = get_sm_readings(service_point_code='E007', channel_role='export', column_name='export', start_date=date1, end_date=date2).astype(float)*1000.0
    production_power = get_power_data(service_point_code='E007', asset_type='PV', sm_hrid='pv3', role='export_power', column_name='production', start_date=date1, end_date=date2).astype(float)
    consumption_power = get_power_data(service_point_code='E007', asset_type='PM', sm_hrid='pm3', role='consumption', column_name='consumption', start_date=date1, end_date=date2).astype(float)
    bss_charge_power = get_power_data(service_point_code='E007', asset_type='BSS', sm_hrid='BSS 137085', role='charge_power', column_name='bss_charge_power', start_date=date1, end_date=date2).astype(float)
    bss_discharge_power = get_power_data(service_point_code='E007', asset_type='BSS', sm_hrid='BSS 137085', role='discharge_power', column_name='bss_discharge_power', start_date=date1, end_date=date2).astype(float)
    bss_soc = get_power_data(service_point_code='E007', asset_type='BSS', sm_hrid='BSS 137085', role='state_of_charge', column_name='bss_state_of_charge', start_date=date1, end_date=date2).astype(float)
    entity3.set_entity_data(import_power=import_power, export_power=export_power, production_power=production_power, 
                            consumption_power=consumption_power, bss_charge_power=bss_charge_power, bss_discharge_power=bss_discharge_power,
                            bss_state_of_charge=bss_soc)


    # €/kWh
    tariff_kwh = 0.02296
    # €/Wh
    tariff = tariff_kwh * 0.001

    op_grid_feed_in = 0.0757
    op_consumption = 0.1814

    # make a self-consumption optimization
    #########
    data1_opti = entity1.self_consumption_optimization()
    data2_opti = entity2.self_consumption_optimization()
    data3_opti = entity3.self_consumption_optimization()

    allocation_coefficients, allocation = surplus_allocation(data1_opti, data2_opti, data3_opti)
    local_import = aggregate_local_import(allocation)

    data1_opti['local_import'] = local_import[0]
    data2_opti['local_import'] = local_import[1]
    data3_opti['local_import'] = local_import[2]

    data1_opti['grid_import_power'] = data1_opti['grid_import_power'] - data1_opti['local_import']
    data2_opti['grid_import_power'] = data2_opti['grid_import_power'] - data2_opti['local_import']
    data3_opti['grid_import_power'] = data3_opti['grid_import_power'] - data3_opti['local_import']

    data_combined = data1_opti + data2_opti + data3_opti
    # results = generate_report(data_combined, energy_prices)


    production = data_combined['pv_production_power']
    consumption_bss = data_combined['consumption'] + data_combined['bss_charge_power']
    consumption = data_combined['consumption']
    self_consumption = production.combine(consumption_bss, func=np.minimum)
    grid_feed_in = data_combined['grid_export_power']
    grid_import = data_combined['grid_import_power']
    local_import = data_combined['local_import']
    # convert power values into the Wh values (energy)
    # 15-min values
    consumption_wh = consumption * 0.25 
    production_wh = production * 0.25 
    self_consumption_wh = self_consumption * 0.25 
    grid_feed_in_wh = grid_feed_in * 0.25 
    grid_import_wh = grid_import * 0.25 
    local_import_wh = local_import * 0.25 
    # sum the energies
    total_energy_demanded = consumption_wh.sum() * 0.001
    total_energy_produced = production_wh.sum() * 0.001
    total_grid_feed_in = grid_feed_in_wh.sum() * 0.001
    total_grid_import = grid_import_wh.sum() * 0.001
    total_local_import = local_import_wh.sum() * 0.001
    self_consumed_energy = self_consumption_wh.sum() * 0.001

    self_sufficiency = (self_consumed_energy / total_energy_demanded) * 100.0
    self_consumption = (self_consumed_energy / total_energy_produced) * 100.0

    # cost = ((grid_import_wh * energy_prices * 1.15 + grid_import_wh * grid_tarrif) + (local_import_wh * energy_cost)).sum()
    # energy_prices = energy_prices['price']/1000.0
    # expenses = total_grid_export * op_grid_feed_in - total_grid_import * (op_consumption + tariff_kwh)
    consumption_cost = total_grid_import * (op_consumption + tariff) + (total_local_import * op_consumption)
    income = total_grid_feed_in * op_grid_feed_in
    expenses = income - consumption_cost


    output = {
        'start_datetime': date1,
        'end_datetime': date2,
        'results': {
            'revenue': expenses,
            'imported_energy': total_grid_import,
            'exported_energy': total_grid_feed_in,
            'self_consumption': self_consumption,
            'self_sufficiency': self_sufficiency,
            'produced_energy': total_energy_produced,
            'demanded_energy': total_energy_demanded
        }
    }
    

    json_str = json.dumps(output)


    return json_str



@router.post("/flexibility")
async def flexibility_report(params: BaselineRequest, db: Session = Depends(get_db)): #, token=Depends(require_token("admin")))
    '''
    This endpoint loads EC data and simulates the situation with BSS maximising self-consumption, uses energy community prices and dispatches the flexibility if requested

    '''
    date1 = params.start_datetime
    date2 = params.end_datetime
    print(f"start: {date1}, end: {date2}")

    # metering point name E003
    # BSS 139188  capacity: 8kW/55kWh 
    # pv1
    entity1 = Entity(bss_capacity=55000.0, bss_max_power=8000.0)
    import_power = get_sm_readings(service_point_code='E003', channel_role='import', column_name='import', start_date=date1, end_date=date2).astype(float)*1000.0
    export_power = get_sm_readings(service_point_code='E003', channel_role='export', column_name='export', start_date=date1, end_date=date2).astype(float)*1000.0
    production_power = get_power_data(service_point_code='E003', asset_type='PV', sm_hrid='pv1', role='export_power', column_name='production', start_date=date1, end_date=date2).astype(float)
    consumption_power = get_power_data(service_point_code='E003', asset_type='PM', sm_hrid='pm1', role='consumption', column_name='consumption', start_date=date1, end_date=date2).astype(float)
    bss_charge_power = get_power_data(service_point_code='E003', asset_type='BSS', sm_hrid='BSS 139188', role='charge_power', column_name='bss_charge_power', start_date=date1, end_date=date2).astype(float)
    bss_discharge_power = get_power_data(service_point_code='E003', asset_type='BSS', sm_hrid='BSS 139188', role='discharge_power', column_name='bss_discharge_power', start_date=date1, end_date=date2).astype(float)
    bss_soc = get_power_data(service_point_code='E003', asset_type='BSS', sm_hrid='BSS 139188', role='state_of_charge', column_name='bss_state_of_charge', start_date=date1, end_date=date2).astype(float)
    entity1.set_entity_data(import_power=import_power, export_power=export_power, production_power=production_power, 
                            consumption_power=consumption_power, bss_charge_power=bss_charge_power, bss_discharge_power=bss_discharge_power,
                            bss_state_of_charge=bss_soc)

    # # metering point name E004
    # # BSS 136739  capacity: 8kW/55kWh 
    # # pv2
    entity2 = Entity(bss_capacity=55000.0, bss_max_power=8000.0)
    import_power = get_sm_readings(service_point_code='E004', channel_role='import', column_name='import', start_date=date1, end_date=date2).astype(float)*1000.0
    export_power = get_sm_readings(service_point_code='E004', channel_role='export', column_name='export', start_date=date1, end_date=date2).astype(float)*1000.0
    production_power = get_power_data(service_point_code='E004', asset_type='PV', sm_hrid='pv2', role='export_power', column_name='production', start_date=date1, end_date=date2).astype(float)
    consumption_power = get_power_data(service_point_code='E004', asset_type='PM', sm_hrid='pm2', role='consumption', column_name='consumption', start_date=date1, end_date=date2).astype(float)
    bss_charge_power = get_power_data(service_point_code='E004', asset_type='BSS', sm_hrid='BSS 136739', role='charge_power', column_name='bss_charge_power', start_date=date1, end_date=date2).astype(float)
    bss_discharge_power = get_power_data(service_point_code='E004', asset_type='BSS', sm_hrid='BSS 136739', role='discharge_power', column_name='bss_discharge_power', start_date=date1, end_date=date2).astype(float)
    bss_soc = get_power_data(service_point_code='E004', asset_type='BSS', sm_hrid='BSS 136739', role='state_of_charge', column_name='bss_state_of_charge', start_date=date1, end_date=date2).astype(float)
    entity2.set_entity_data(import_power=import_power, export_power=export_power, production_power=production_power, 
                            consumption_power=consumption_power, bss_charge_power=bss_charge_power, bss_discharge_power=bss_discharge_power,
                            bss_state_of_charge=bss_soc)

    # # metering point name E007
    # # BSS 137085  capacity: 16kW/110kWh 
    # # pv3
    entity3 = Entity(bss_capacity=110000.0, bss_max_power=16000.0)
    import_power = get_sm_readings(service_point_code='E007', channel_role='import', column_name='import', start_date=date1, end_date=date2).astype(float)*1000.0
    export_power = get_sm_readings(service_point_code='E007', channel_role='export', column_name='export', start_date=date1, end_date=date2).astype(float)*1000.0
    production_power = get_power_data(service_point_code='E007', asset_type='PV', sm_hrid='pv3', role='export_power', column_name='production', start_date=date1, end_date=date2).astype(float)
    consumption_power = get_power_data(service_point_code='E007', asset_type='PM', sm_hrid='pm3', role='consumption', column_name='consumption', start_date=date1, end_date=date2).astype(float)
    bss_charge_power = get_power_data(service_point_code='E007', asset_type='BSS', sm_hrid='BSS 137085', role='charge_power', column_name='bss_charge_power', start_date=date1, end_date=date2).astype(float)
    bss_discharge_power = get_power_data(service_point_code='E007', asset_type='BSS', sm_hrid='BSS 137085', role='discharge_power', column_name='bss_discharge_power', start_date=date1, end_date=date2).astype(float)
    bss_soc = get_power_data(service_point_code='E007', asset_type='BSS', sm_hrid='BSS 137085', role='state_of_charge', column_name='bss_state_of_charge', start_date=date1, end_date=date2).astype(float)
    entity3.set_entity_data(import_power=import_power, export_power=export_power, production_power=production_power, 
                            consumption_power=consumption_power, bss_charge_power=bss_charge_power, bss_discharge_power=bss_discharge_power,
                            bss_state_of_charge=bss_soc)

    # get/load flexibility request if exists for the current day
    stmt = (
        select(flex.FlexibilityRequest)
        .where(
            flex.FlexibilityRequest.start < date2,
            flex.FlexibilityRequest.end > date1
        )
    )
    flreq = db.execute(stmt).scalars().first()

    if None != flreq:
        print(f"id: {flreq.id}")
        print(f"congestion_point: {flreq.congestion_point}")
        print(f"start: {flreq.start}")
        print(f"end: {flreq.end}")
        print(f"type: {flreq.type}")
        print(f"amount: {flreq.amount}")
        print(f"uom: {flreq.uom}")
        print(f"price: {flreq.price}")
        print(f"price_uom: {flreq.price_uom}")
    else:
        print("No flex request present!")


    # €/kWh
    tariff_kwh = 0.02296
    # €/Wh
    tariff = tariff_kwh * 0.001

    op_grid_feed_in = 0.0757 * 0.001
    op_consumption = 0.1814 * 0.001

    # make a self-consumption optimization
    #########
    data1_opti = entity1.self_consumption_optimization()
    data2_opti = entity2.self_consumption_optimization()
    data3_opti = entity3.self_consumption_optimization()

    allocation_coefficients, allocation = surplus_allocation(data1_opti, data2_opti, data3_opti)
    local_import = aggregate_local_import(allocation)

    data1_opti['local_import'] = local_import[0]
    data2_opti['local_import'] = local_import[1]
    data3_opti['local_import'] = local_import[2]

    data1_opti['grid_import_power'] = data1_opti['grid_import_power'] - data1_opti['local_import']
    data2_opti['grid_import_power'] = data2_opti['grid_import_power'] - data2_opti['local_import']
    data3_opti['grid_import_power'] = data3_opti['grid_import_power'] - data3_opti['local_import']

    data_combined = data1_opti + data2_opti + data3_opti
    # results = generate_report(data_combined, energy_prices)

    production = data_combined['pv_production_power']
    consumption_bss = data_combined['consumption'] + data_combined['bss_charge_power']
    consumption = data_combined['consumption']
    self_consumption = production.combine(consumption_bss, func=np.minimum)
    grid_feed_in = data_combined['grid_export_power']
    grid_import = data_combined['grid_import_power']
    local_import = data_combined['local_import']
    # convert power values into the Wh values (energy)
    # 15-min values
    consumption_wh = consumption * 0.25 
    production_wh = production * 0.25 
    self_consumption_wh = self_consumption * 0.25 
    grid_feed_in_wh = grid_feed_in * 0.25 
    grid_import_wh = grid_import * 0.25 
    local_import_wh = local_import * 0.25 
    # sum the energies
    total_energy_demanded = consumption_wh.sum() * 0.001
    total_energy_produced = production_wh.sum() * 0.001
    total_grid_feed_in = grid_feed_in_wh.sum() * 0.001
    total_grid_import = grid_import_wh.sum() * 0.001
    total_local_import = local_import_wh.sum() * 0.001
    self_consumed_energy = self_consumption_wh.sum() * 0.001

    self_sufficiency = (self_consumed_energy / total_energy_demanded) * 100.0
    self_consumption = (self_consumed_energy / total_energy_produced) * 100.0

    # cost = ((grid_import_wh * energy_prices * 1.15 + grid_import_wh * grid_tarrif) + (local_import_wh * energy_cost)).sum()
    # energy_prices = energy_prices['price']/1000.0
    # expenses = total_grid_export * op_grid_feed_in - total_grid_import * (op_consumption + tariff_kwh)
    consumption_cost = total_grid_import * (op_consumption + tariff) + (total_local_import * op_consumption)
    income = total_grid_feed_in * op_grid_feed_in
    expenses = (income - consumption_cost) * 1000.0

    print(f"expenses: {expenses}")

    output_baseline = {
        'revenue': expenses,
        'imported_energy': total_grid_import,
        'exported_energy': total_grid_feed_in,
        'self_consumption': self_consumption,
        'self_sufficiency': self_sufficiency,
        'produced_energy': total_energy_produced,
        'demanded_energy': total_energy_demanded
    }

    if None != flreq:
        # just for the timestamps at the moment
        energy_prices = get_energy_prices(service_point_code='E003', start_date=date1, end_date=date2).astype(float)

        data1 = entity1.get_entity_data().copy()
        data2 = entity2.get_entity_data().copy()
        data3 = entity3.get_entity_data().copy()

        # get number of steps in data
        data_length = len(data1)
        T = data_length  # Number of time periods (should be 96)

        dt = 0.25
        N = 3 #number of users

        # data placeholders
        pv = np.zeros((N,T))
        load = np.zeros((N,T))
        energy_cost = np.zeros(T)
        # flexibility request caps
        flex_up_req = np.zeros(T)
        flex_down_req = np.zeros(T)
        # activation payment
        flex_price_up = np.zeros(T)
        flex_price_down = np.zeros(T)

        # load data
        pv[0,:] = data1['pv_production_power']
        pv[1,:] = data2['pv_production_power']
        pv[2,:] = data3['pv_production_power']
        load[0,:] = data1['consumption']
        load[1,:] = data2['consumption']
        load[2,:] = data3['consumption']

        # print(f"pv: {pv}")
        # print(f"load: {load}")

        pv_agg = np.sum(pv, axis=0)

        # print(f"pv_agg: {pv_agg}")

        # Prices (€/kWh), shape (T,)
        energy_cost_import = np.ones(T)
        energy_cost_export = np.ones(T)

        energy_cost_import = np.array([
            energy_cost_import[t] * op_consumption + tariff
            for t in range(T)
        ])

        energy_cost_export = np.array([
            energy_cost_export[t] * op_grid_feed_in
            for t in range(T)
        ])

        # print(f"energy_cost_import: {energy_cost_import}")
        # print(f"energy_cost_export: {energy_cost_export}")

        # get the full flexibility request object
        # req = flex_crud.get_flex_request_by_id(db=db, request_id=UUID(reqid))
        # print(f"Fetched flexibility request: {req}")

        dt_start = flreq.start
        idx_start = dt_start.hour * 4 + dt_start.minute // 15
        # print(f"Start index: {idx_start}")

        dt_stop = flreq.end
        idx_stop = dt_stop.hour * 4 + dt_stop.minute // 15
        # print(f"Stop index: {idx_stop}")

        if 'REDUCE' == flreq.type:
            flex_down_req[idx_start:idx_stop] = float(flreq.amount) * 1000.0
            flex_price_down[idx_start:idx_stop] = float(flreq.price) * 0.001
        elif 'INCREASE' == flreq.type:
            flex_up_req[idx_start:idx_stop] = float(flreq.amount) * 1000.0
            flex_price_up[idx_start:idx_stop] = float(flreq.price) * 0.001

        # print(f"flex_down_req: {flex_down_req}")
        # print(f"flex_price_down: {flex_price_down}")
        # print(f"flex_up_req: {flex_up_req}")
        # print(f"flex_price_up: {flex_price_up}")

        # length of activation
        L = idx_stop - idx_start
        # define the flexibility activation window
        W = slice(idx_start, idx_stop)
        # print(f"L: {L}")

        # define the flexibility window
        # W = ((flex_down_req > 0) | (flex_up_req > 0)).astype(int)

        # BSS parameters
        Pch_max = np.array([8000.0, 8000.0, 16000.0])
        Pdis_max = np.array([8000.0, 8000.0, 16000.0])
        SoC_min = np.array([0.0, 0.0, 0.0])
        SoC_max = np.array([55000.0, 55000.0, 110000.0])
        SoC0 = np.array([0.0, 0.0, 0.0])
        eta_ch = np.array([0.95, 0.95, 0.95])
        eta_dis = np.array([0.95, 0.95, 0.95])

        # penalty for simultaneous charging and discharging - to prevent it happen in schedules
        c_thr = 0.01

        print("Start preparing baseline")
        res1 = solve_baseline(
            pv, load, dt, L,
            energy_cost_import, energy_cost_export,
            Pch_max, Pdis_max, SoC_min, SoC_max, SoC0, eta_ch, eta_dis,
            c_thr
        )

        p_agg_baseline = res1["p_agg_baseline"]
        baseline_revenue = res1["baseline_revenue"]

        res2 = prepare_flex_offer(
            pv, load, p_agg_baseline, baseline_revenue, dt, L, W,
            energy_cost_import, energy_cost_export,
            flex_up_req, flex_down_req, flex_price_up, flex_price_down,
            Pch_max, Pdis_max, SoC_min, SoC_max, SoC0, eta_ch, eta_dis,
            c_thr
        )

        offer_up = res2["offer_up"]
        offer_down = res2["offer_down"]
        flex_revenue = res2["flex_revenue"]

        # print(f"p_charge: {res2['p_ch_baseline']}")
        # print(f"p_discharge: {res2['p_dis_baseline']}")

        # print(f"p_agg_baseline: {p_agg_baseline}")
        print(f"offer_up: {offer_up}")
        print(f"offer_down: {offer_down}")
        print(f"flex_revenue: {flex_revenue}")

        # 2) Contract arrives: build activation schedules (must be <= offer)
        act_up_req = np.zeros(T)
        act_down_req = np.zeros(T)
        act_up_req = np.minimum(32000.0, offer_up)
        act_down_req = np.minimum(32000.0, offer_down)
        # print(f"act_up_req: {act_up_req}")
        # print(f"act_down_req: {act_down_req}")

        # define the flexibility activation window
        W = slice(idx_start, idx_stop)

        print("Solve for activation dispatch")

        # 3) Activation dispatch
        res3 = solve_activation_dispatch(
            pv, load, dt, L, W,
            energy_cost_import, energy_cost_export,
            flex_price_up, flex_price_down,
            Pch_max, Pdis_max, SoC_min, SoC_max, SoC0, eta_ch, eta_dis,
            p_agg_baseline, offer_up, offer_down,
            act_up_req, act_down_req,
            c_thr
        )


        print("Baseline energy-optimal objective:", res1["baseline_revenue"])
        print("Offer preparation objective:", res2["flex_revenue"])
        print("Activation run objective:", res3["activation_revenue"])

        # prepare results
        ts_index = energy_prices.index
        ts_ms = (ts_index.view("int64") // 10**6).astype(np.int64)  # unix ms, shape (193,)
        A = np.asarray(res3["p_ch"])
        charge_power = [np.column_stack((ts_ms, A[i])).tolist() for i in range(3)]
        aggregated_bss_power = np.round(np.sum(A, axis=0), 3)
        A = np.asarray(res3["p_dis"])
        discharge_power = [np.column_stack((ts_ms, A[i])).tolist() for i in range(3)]
        aggregated_bss_power = aggregated_bss_power - np.round(np.sum(A, axis=0), 3)
        aggregated_bss_power_list = np.column_stack((ts_ms, aggregated_bss_power)).tolist()
        # print(aggregated_bss_power_list)
        # stored energy
        A = np.asarray(res3["SoC"][:,:-1])
        soc_out = [np.column_stack((ts_ms, A[i])).tolist() for i in range(3)]
        soc_agg = np.sum(A, axis=0)
        soc_agg = np.column_stack((ts_ms, soc_agg)).tolist()

        A = np.asarray(res3["p_agg_activated"])
        aggregated_power = np.column_stack((ts_ms, A)).tolist()
        A = np.asarray(pv_agg)
        aggregated_production = A
        aggregated_production_list = np.column_stack((ts_ms, A)).tolist()

        series = energy_prices["price"]  # <-- Series, index should be timestamps (or convertible)
        series.index = pd.to_datetime(series.index, utc=True, errors="raise")

        ts_ms = (series.index.view("int64") // 10**6).astype("int64")
        energy_prices_out = [[int(t), float(v)] for t, v in zip(ts_ms, series.to_numpy())]

        # consumption
        A = np.asarray(np.sum(load, axis=0))
        consumption = np.column_stack((ts_ms, A)).tolist()

        # baseline results
        A = np.asarray(res3["p_agg_baseline"])
        baseline_agg = np.column_stack((ts_ms, A)).tolist()

        # importerd power
        grid_import = np.asarray(res3['p_import'])
        grid_export = np.asarray(res3['p_export'])


        consumption_bss = np.sum(load, axis=0) + aggregated_bss_power
        # self_consumption = aggregated_production.combine(consumption_bss, func=np.minimum)
        self_consumption = np.minimum(aggregated_production, consumption_bss)

        consumption_wh =  np.sum(load, axis=0) * 0.25
        production_wh = aggregated_production * 0.25
        grid_import_wh = grid_import * 0.25
        grid_export_wh = grid_export * 0.25
        self_consumption_wh = self_consumption * 0.25

        total_energy_demanded = consumption_wh[-96:].sum()
        total_energy_produced = production_wh[-96:].sum()
        total_grid_import = grid_import_wh[-96:].sum()
        total_grid_export = grid_export_wh[-96:].sum()
        self_consumed_energy = self_consumption_wh[-96:].sum()

        self_sufficiency = (self_consumed_energy / total_energy_demanded) * 100.0
        self_consumption = (self_consumed_energy / total_energy_produced) * 100.0

        expenses = res3['activation_revenue']
        print(f"expenses: {expenses}")

        output_flex = {
            'revenue': expenses,
            'baseline_revenue': res1['baseline_revenue'],
            'flex_revenue': res2['flex_revenue'],
            'activation_revenue': res3['activation_revenue'],
            'shifted_load': res3['shifted_load'],
            'imported_energy': total_grid_import,
            'exported_energy': total_grid_feed_in,
            'self_consumption': self_consumption,
            'self_sufficiency': self_sufficiency,
            'produced_energy': total_energy_produced,
            'demanded_energy': total_energy_demanded
        }

        print(output_flex)
    

    if None != flreq:
        output = {
            'start_datetime': date1,
            'end_datetime': date2,
            'results_baseline': output_baseline,
            'results_flex': output_flex
        }  
        print(f"baseline: {output_baseline}") 
        print(f"results_flex: {output_flex}") 
    else:
        output = {
            'start_datetime': date1,
            'end_datetime': date2,
            'results_baseline': output_baseline
        } 

    json_str = json.dumps(output)


    return json_str