from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from app.db.session import SessionLocal
from app.auth.dependencies import require_token
from app.schemas.simulation import SimulationParameters
import app.models.ec as ec
from app.optimisation.entity import Entity
from datetime import datetime, timedelta
from decimal import Decimal
import json
import pandas as pd
import numpy as np
import cvxpy as cp


import logging

from app.keycloak_instance import kc

logger = logging.getLogger(__name__)


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




# @router.post("")
# async def simulate(params: SimulationParameters):
def simulate():
    params = SimulationParameters(
        start_datetime='2025-12-20T22:00:00',
        end_datetime='2025-12-21T22:00:00',
        # optimization_strategy='collective_self-consumption'
        # optimization_strategy='no_optimization'
        # optimization_strategy='self-consumption'
        optimization_strategy='flex_provision'
    )

    date1 = params.start_datetime
    date2 = params.end_datetime
    strategy = params.optimization_strategy

    # move start date for more realistic battery optimization
    date1 = (datetime.strptime(date1, "%Y-%m-%dT%H:%M:%S") - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")

    print(f"start: {date1}, end: {date2}")
    print(f"Optimization strategy: {strategy}")

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
    # print(f"import_power: {import_power}")
    # print(f"export_power: {export_power}")
    # edt = entity1.get_entity_data()
    # print(f"consumption: {edt['consumption']}")

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
    # print(f"entity_data: {entity2.get_entity_data()}")

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
    # print(f"entity_data: {entity3.get_entity_data()}")

    # prices
    energy_prices = get_energy_prices(service_point_code='E003', start_date=date1, end_date=date2).astype(float)
    # print(f"energy_prices: {energy_prices}")


    # prepare empty/default output
    output = {
        'start_datetime': date1,
        'end_datetime': date2,
        'optimization_strategy': strategy,
        'results': {
            'self_consumption': 0.0,
            'self_sufficiency': 0.0,
            'co2_emissions': 0.0,
            'expenses': 0.0,
            'imported_energy': 0.0,
            'exported_energy': 0.0,
            'produced_energy': 0.0,
            'consumed_energy': 0.0,
            'consumption_profile': {},
            'production_profile': {},
            'stored_energy_profile': {}
        }
    }

    if 'no_optimization' == strategy:
        data1 = entity1.get_entity_data().copy()
        data2 = entity2.get_entity_data().copy()
        data3 = entity3.get_entity_data().copy()
        data_combined = data1 + data2 + data3
        results = generate_report_no(data_combined, energy_prices)

        output = {
            'start_datetime': date1,
            'end_datetime': date2,
            'optimization_strategy': strategy,
            'results': {
                    'self_consumption': results['self_consumption'],
                    'self_sufficiency': results['self_sufficiency'],
                    'co2_emissions': results['co2_emissions'],
                    'expenses': results['cost'],
                    'imported_energy': results['total_import'],
                    'exported_energy': results['total_export'],
                    'produced_energy': results['produced_energy'],
                    'consumed_energy': results['consumed_energy'],
                    'consumption_profile': format_output_series(results['consumption']),
                    'production_profile': format_output_series(results['production']),
                    'stored_energy_profile': format_output_series(results['stored_energy'])
            }
        }

  
    if 'self-consumption' == strategy:
        data1_no = entity1.get_entity_data().copy()
        data2_no = entity2.get_entity_data().copy()
        data3_no = entity3.get_entity_data().copy()

        allocation_coefficients_no, allocation_no = surplus_allocation(data1_no, data2_no, data3_no)
        local_import_no = aggregate_local_import(allocation_no)

        data1_no['local_import'] = local_import_no[0]
        data2_no['local_import'] = local_import_no[1]
        data3_no['local_import'] = local_import_no[2]

        data1_no['grid_import_power'] = data1_no['grid_import_power'] - data1_no['local_import']
        data2_no['grid_import_power'] = data2_no['grid_import_power'] - data2_no['local_import']
        data3_no['grid_import_power'] = data3_no['grid_import_power'] - data3_no['local_import']

        data_combined_no = data1_no + data2_no + data3_no

        data1 = entity1.self_consumption_optimization()
        data2 = entity2.self_consumption_optimization()
        data3 = entity3.self_consumption_optimization()

        allocation_coefficients, allocation = surplus_allocation(data1, data2, data3)
        local_import = aggregate_local_import(allocation)

        data1['local_import'] = local_import[0]
        data2['local_import'] = local_import[1]
        data3['local_import'] = local_import[2]

        data1['grid_import_power'] = data1['grid_import_power'] - data1['local_import']
        data2['grid_import_power'] = data2['grid_import_power'] - data2['local_import']
        data3['grid_import_power'] = data3['grid_import_power'] - data3['local_import']

        data_combined = data1 + data2 + data3
        results = generate_report(data_combined, energy_prices)

        output = {
            'start_datetime': date1,
            'end_datetime': date2,
            'optimization_strategy': strategy,
            'results': {
                'self_consumption': results['self_consumption'],
                'self_sufficiency': results['self_sufficiency'],
                'co2_emissions': results['co2_emissions'],
                'expenses': results['cost'],
                'imported_energy': results['total_import'],
                'exported_energy': results['total_export'],
                'produced_energy': results['produced_energy'],
                'consumed_energy': results['consumed_energy'],
                'consumption_profile': format_output_series(results['consumption']),
                'production_profile': format_output_series(results['production']),
                'stored_energy_profile': format_output_series(results['stored_energy'])
            }
        }

    elif 'collective_self-consumption' == strategy:
        data1_no = entity1.get_entity_data().copy()
        data2_no = entity2.get_entity_data().copy()
        data3_no = entity3.get_entity_data().copy()

        # local self-consumption optimization first
        data1_sc = entity1.self_consumption_optimization().copy()
        data2_sc = entity2.self_consumption_optimization().copy()
        data3_sc = entity3.self_consumption_optimization().copy()

        # get number of steps in data
        data_length = len(data1_no)

        # iterate over steps
        for i in range(data_length):
            entity1.set_state_from_data()
            entity2.set_state_from_data()
            entity3.set_state_from_data()

            # get entity state
            entity1_state = entity1.get_entity_state()
            entity2_state = entity2.get_entity_state()
            entity3_state = entity3.get_entity_state()


            print(" ")
            print(f"i: {i}")

            # print(f"pv1: {entity1_state.pv_production_power}")
            # print(f"pv2: {entity2_state.pv_production_power}")
            # print(f"pv3: {entity3_state.pv_production_power}")

            # print(f"consumption1: {entity1_state.consumption}")
            # print(f"consumption2: {entity2_state.consumption}")
            # print(f"consumption3: {entity3_state.consumption}")

            # print(f"import1: {entity1_state.grid_import_power}")
            # print(f"import2: {entity2_state.grid_import_power}")
            # print(f"import3: {entity3_state.grid_import_power}")

            # print(f"export1: {entity1_state.grid_export_power}")
            # print(f"export2: {entity2_state.grid_export_power}")
            # print(f"export3: {entity3_state.grid_export_power}")

            # print(f"bss1_charge: {entity1_state.bss_charge_power}")
            # print(f"bss2_charge: {entity2_state.bss_charge_power}")
            # print(f"bss3_charge: {entity3_state.bss_charge_power}")

            # print(f"bss1_discharge: {entity1_state.bss_discharge_power}")
            # print(f"bss2_discharge: {entity2_state.bss_discharge_power}")
            # print(f"bss3_discharge: {entity3_state.bss_discharge_power}")

            # print(f"bss1_soc: {entity1_state.bss_stored_energy}")
            # print(f"bss2_soc: {entity2_state.bss_stored_energy}")
            # print(f"bss3_soc: {entity3_state.bss_stored_energy}")

            # print(f"export1: {entity1_state.grid_export_power}")
            # print(f"import1: {entity1_state.grid_import_power}")
            # print(f"export2: {entity2_state.grid_export_power}")
            # print(f"import2: {entity2_state.grid_import_power}")
            # print(f"export3: {entity3_state.grid_export_power}")
            # print(f"import3: {entity3_state.grid_import_power}")

            # aggregate collective export
            collective_export = entity1_state.grid_export_power + entity2_state.grid_export_power + entity3_state.grid_export_power
            print(f"collective_export: {collective_export}")

            # remaining charging capacity
            print(f"rcp1: {entity1.get_remaining_charging_capacity()}, rcp2: {entity2.get_remaining_charging_capacity()}, rcp3: {entity3.get_remaining_charging_capacity()}")

            



            entity1.step()
            entity2.step()
            entity3.step()



            # # calculate charging power
            # production = entity1_state.pv_production_power + entity2_state.pv_production_power + entity3_state.pv_production_power
            # # prepare consumption data
            # consumption1 = -entity1_state.consumption
            # consumption2 = -entity2_state.consumption
            # consumption3 = -entity3_state.consumption
            # consumption = consumption1 + consumption2 +  consumption3

            # # calculate bss_power to maximise self-consumption
            # bss_power = -production - consumption

            # # update entity1
            # entity1._bss.set_power(power=bss_power)
            # entity1._bss.update_bss_state(dt=900)
            # entity1.update_state()
            # entity1.step()
            # # update entity2
            # entity2._bss.set_power(power=bss_power)
            # entity2._bss.update_bss_state(dt=900)
            # entity2.update_state()
            # entity2.step()
            # # update entity3
            # entity3._bss.set_power(power=bss_power)
            # entity3._bss.update_bss_state(dt=900)
            # entity3.update_state()
            # entity3.step()

        data1 = entity1.get_entity_data().copy()
        data2 = entity2.get_entity_data().copy()
        data3 = entity3.get_entity_data().copy()

        allocation_coefficients, allocation = surplus_allocation(data1, data2, data3)
        local_import = aggregate_local_import(allocation)

        data1['local_import'] = local_import[0]
        data2['local_import'] = local_import[1]
        data3['local_import'] = local_import[2]

        data1['grid_import_power'] = data1['grid_import_power'] - data1['local_import']
        data2['grid_import_power'] = data2['grid_import_power'] - data2['local_import']
        data3['grid_import_power'] = data3['grid_import_power'] - data3['local_import']

        data_combined = data1 + data2 + data3
        results = generate_report(data_combined, energy_prices)

        output = {
            'start_datetime': date1,
            'end_datetime': date2,
            'optimization_strategy': strategy,
            'results': {
                'self_consumption': results['self_consumption'],
                'self_sufficiency': results['self_sufficiency'],
                'co2_emissions': 0.0,
                'expenses': results['cost'],
                'imported_energy': results['total_import'],
                'exported_energy': results['total_export'],
                'produced_energy': results['produced_energy'],
                'consumed_energy': results['consumed_energy'],
                'consumption_profile': format_output_series(results['consumption']),
                'production_profile': format_output_series(results['production']),
                'stored_energy_profile': format_output_series(results['stored_energy'])
            }
        }

    elif 'flex_provision' == strategy:

        data1_no = entity1.get_entity_data().copy()
        data2_no = entity2.get_entity_data().copy()
        data3_no = entity3.get_entity_data().copy()

        # get number of steps in data
        data_length = len(data1_no)
        T = data_length  # Number of time periods (should be 96)
        print(T)

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
        pv[0,:] = data1_no['pv_production_power']*0.001
        pv[1,:] = data2_no['pv_production_power']*0.001
        pv[2,:] = data3_no['pv_production_power']*0.001
        load[0,:] = data1_no['consumption']*0.001
        load[1,:] = data2_no['consumption']*0.001
        load[2,:] = data3_no['consumption']*0.001
        energy_cost = energy_prices.to_numpy()

        print(pv)
        print(load)
        print(energy_cost)

        flex_up_req[360] = 100
        flex_up_req[361] = 100
        flex_up_req[362] = 100
        flex_up_req[363] = 100

        flex_price_up[360] = 1.0
        flex_price_up[361] = 1.0
        flex_price_up[362] = 1.0
        flex_price_up[363] = 1.0



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

        # Optional convex regularization (QP). Keep 0 for LP.
        c2 = 0.0

        # assume the offer can change over the provision block (1 - offer can change for each 15 minutes)
        block_len = 1
        B = T // block_len

        # decision variables
        ##
        ###
        p_ch = cp.Variable((N,T), nonneg=True)
        p_dis = cp.Variable((N,T), nonneg=True)
        SoC   = cp.Variable((N, T + 1))

        # aggregated power profile
        p_agg = cp.Variable(T) # net power at aggregation point: +export/-import

        # Aggregated flexibility offers
        offer_up_t = cp.Variable(T, nonneg=True)
        offer_down_t = cp.Variable(T, nonneg=True)

        # profit allocation per-user
        s = cp.Variable(N)

        ##
        # constraints
        ##
        constraints = []

        # SoC and SoC constraints per user
        constraints += [SoC[:, 0] == SoC0]
        for i in range(N):
            constraints += [
                SoC[i, 1:] == SoC[i, :-1]
                + eta_ch[i] * p_ch[i, :] * dt
                - (1.0 / eta_dis[i]) * p_dis[i, :] * dt
            ]
            constraints += [SoC[i, :] >= SoC_min[i], SoC[i, :] <= SoC_max[i]]
            constraints += [p_ch[i, :] <= Pch_max[i], p_dis[i, :] <= Pdis_max[i]]
        
        # power balance from user assets to PCC
        # per-user net injection: pv - load + battery injection
        p_user = pv -load + (p_dis - p_ch) # (N, T) dimensions
        constraints += [p_agg == cp.sum(p_user, axis=0)]

        # DSO request caps
        constraints += [offer_up_t <= flex_up_req]
        constraints += [offer_down_t <= flex_down_req]

        # Aggregate feasibility: reserve must fit into sum of user headroom/footroom
        sum_dis = cp.sum(p_dis, axis=0)
        sum_ch  = cp.sum(p_ch, axis=0)

        constraints += [offer_up_t <= np.sum(Pdis_max) - sum_dis]   # discharge headroom
        constraints += [offer_down_t <= np.sum(Pch_max) - sum_ch]   # charge headroom

        # SoC headroom
        soc_total = cp.sum(SoC[:, :-1], axis=0) # total stored energy
        # calculate aggregated SoC headroom for simplicity --> can be changed to per user reserves (offer_up_i, offer_down_i)
        constraints += [
            soc_total >= np.sum(SoC_min) + dt * (offer_up_t / np.mean(eta_dis)),
            soc_total <= np.sum(SoC_max) - dt * (np.mean(eta_ch) * offer_down_t),
        ]

        ##
        # Objective: Community profit
        # If import/export have different tariffs, use piecewise variables instead.
        energy_profit = -cp.sum(cp.multiply(energy_cost, p_agg)) * dt

        # Paid only on expected activation (day-ahead expectation)
        activation_profit = cp.sum(cp.multiply(flex_price_up, offer_up_t)
                                + cp.multiply(flex_price_down, offer_down_t)) * dt

        # Throughput penalty to discourage simultaneous charge/discharge
        throughput_pen = c_thr * cp.sum(p_ch + p_dis) * dt

        # Optional convex regularization
        quad_reg = c2 * (cp.sum_squares(p_ch) + cp.sum_squares(p_dis))

        J_comm = energy_profit + activation_profit - throughput_pen - quad_reg

        prob1 = cp.Problem(cp.Maximize(J_comm), constraints)
        prob1.solve(solver=cp.OSQP, verbose=True)  # OSQP is good if c2>0 (QP). ECOS works for LP/QP.

        print("Stage 1 status:", prob1.status)
        J_star = prob1.value
        print("Stage 1 community objective:", J_star)


    
        # # reporting
        # data1 = entity1.get_entity_data().copy()
        # data2 = entity2.get_entity_data().copy()
        # data3 = entity3.get_entity_data().copy()

        # allocation_coefficients, allocation = surplus_allocation(data1, data2, data3)
        # local_import = aggregate_local_import(allocation)

        # data1['local_import'] = local_import[0]
        # data2['local_import'] = local_import[1]
        # data3['local_import'] = local_import[2]

        # data1['grid_import_power'] = data1['grid_import_power'] - data1['local_import']
        # data2['grid_import_power'] = data2['grid_import_power'] - data2['local_import']
        # data3['grid_import_power'] = data3['grid_import_power'] - data3['local_import']

        # data_combined = data1 + data2 + data3
        # results = generate_report(data_combined, energy_prices)

        # output = {
        #     'start_datetime': date1,
        #     'end_datetime': date2,
        #     'optimization_strategy': strategy,
        #     'results': {
        #         'self_consumption': results['self_consumption'],
        #         'self_sufficiency': results['self_sufficiency'],
        #         'co2_emissions': 0.0,
        #         'expenses': results['cost'],
        #         'imported_energy': results['total_import'],
        #         'exported_energy': results['total_export'],
        #         'produced_energy': results['produced_energy'],
        #         'consumed_energy': results['consumed_energy'],
        #         'consumption_profile': format_output_series(results['consumption']),
        #         'production_profile': format_output_series(results['production']),
        #         'stored_energy_profile': format_output_series(results['stored_energy'])
        #     }
        # }
    else:
        pass
    
    
    json_str = json.dumps(output)

    print(f"self_consumption: {output['results']['self_consumption']}, self_sufficiency: {output['results']['self_sufficiency']}, expenses: {output['results']['expenses']}, imported_energy: {output['results']['imported_energy']}, exported_energy: {output['results']['exported_energy']}")

    return json_str

if '__main__' == __name__:
    simulate()


