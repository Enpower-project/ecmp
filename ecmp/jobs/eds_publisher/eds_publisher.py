import os
import logging
from datetime import datetime, timedelta
from jobs.openmeteo.openmeteo.openmeteo import OpenMeteo
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
from app.models import ec
# import app.models.mdms as mdms
from app.db.session import SessionLocal
from app.schemas.status import StatusParameters
from app.optimisation.entity import Entity
from app.optimisation.ec_data_query import get_sm_readings, get_power_data, get_energy_prices, get_co2_emissions_data
import pandas as pd
import numpy as np
import json
import requests
import base64
from dotenv import load_dotenv


logger = logging.getLogger(__name__)


load_dotenv()


def request_auth_token() -> str:
    path = os.getenv("MIDDLEWARE_URL") + "/api/user/auth/token"

    try:
        response = requests.post(
            path,
            json={
                "username": os.getenv("EDSC_USER"),
                "password": os.getenv("EDSC_PASSWORD"),
            }
        )
        response.raise_for_status()
        return response.json().get("accessToken")

    except Exception as error:
        print("Failed to obtain authentication token:", error)
        raise


def fetch_connector_settings():
    try:
        path = (
            os.getenv("MIDDLEWARE_URL")
            + f"/api/custom-query/data-objects/?id={os.getenv("CONNECTOR_ID")}"
        )

        # Request auth token
        token = request_auth_token()

        # Make GET request
        headers = {
            "Authorization": f"Bearer {token}"
        }

        response = requests.get(path, headers=headers)
        response.raise_for_status()

        data = response.json()

        return data[0]

    except Exception as error:
        print("Failed to fetch connector settings:", error)
        raise


def generate_report(data, energy_prices):
    co2 = 0.150
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
    consumption_wh = consumption * 0.25 
    production_wh = production * 0.25 
    self_consumption_wh = self_consumption * 0.25 
    grid_feed_in_wh = grid_feed_in * 0.25 
    grid_import_wh = grid_import * 0.25 

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

    energy_prices = energy_prices['price']/1000.0
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
        'cost': cost,
        'co2_emissions': co2em
        }



def upsert_kpi_report():
    try:
        # date = datetime.now() - timedelta(days=14)
        date = datetime(2026, 2, 14, 23, 0, 0)
        date1 = (date - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        print(f"start: {date1}, end: {date}")

        # metering point name E003
        # BSS 139188  capacity: 8kW/55kWh 
        # pv1
        entity1 = Entity(bss_capacity=55000.0, bss_max_power=8000.0)
        import_power = get_sm_readings(service_point_code='E003', channel_role='import', column_name='import', start_date=date1, end_date=date).astype(float)*1000.0
        export_power = get_sm_readings(service_point_code='E003', channel_role='export', column_name='export', start_date=date1, end_date=date).astype(float)*1000.0
        production_power = get_power_data(service_point_code='E003', asset_type='PV', sm_hrid='pv1', role='export_power', column_name='production', start_date=date1, end_date=date).astype(float)
        consumption_power = get_power_data(service_point_code='E003', asset_type='PM', sm_hrid='pm1', role='consumption', column_name='consumption', start_date=date1, end_date=date).astype(float)
        bss_charge_power = get_power_data(service_point_code='E003', asset_type='BSS', sm_hrid='BSS 139188', role='charge_power', column_name='bss_charge_power', start_date=date1, end_date=date).astype(float)
        bss_discharge_power = get_power_data(service_point_code='E003', asset_type='BSS', sm_hrid='BSS 139188', role='discharge_power', column_name='bss_discharge_power', start_date=date1, end_date=date).astype(float)
        bss_soc = get_power_data(service_point_code='E003', asset_type='BSS', sm_hrid='BSS 139188', role='state_of_charge', column_name='bss_state_of_charge', start_date=date1, end_date=date).astype(float)
        entity1.set_entity_data(import_power=import_power, export_power=export_power, production_power=production_power, 
                                consumption_power=consumption_power, bss_charge_power=bss_charge_power, bss_discharge_power=bss_discharge_power,
                                bss_state_of_charge=bss_soc)

        # # metering point name E004
        # # BSS 136739  capacity: 8kW/55kWh 
        # # pv2
        entity2 = Entity(bss_capacity=55000.0, bss_max_power=8000.0)
        import_power = get_sm_readings(service_point_code='E004', channel_role='import', column_name='import', start_date=date1, end_date=date).astype(float)*1000.0
        export_power = get_sm_readings(service_point_code='E004', channel_role='export', column_name='export', start_date=date1, end_date=date).astype(float)*1000.0
        production_power = get_power_data(service_point_code='E004', asset_type='PV', sm_hrid='pv2', role='export_power', column_name='production', start_date=date1, end_date=date).astype(float)
        consumption_power = get_power_data(service_point_code='E004', asset_type='PM', sm_hrid='pm2', role='consumption', column_name='consumption', start_date=date1, end_date=date).astype(float)
        bss_charge_power = get_power_data(service_point_code='E004', asset_type='BSS', sm_hrid='BSS 136739', role='charge_power', column_name='bss_charge_power', start_date=date1, end_date=date).astype(float)
        bss_discharge_power = get_power_data(service_point_code='E004', asset_type='BSS', sm_hrid='BSS 136739', role='discharge_power', column_name='bss_discharge_power', start_date=date1, end_date=date).astype(float)
        bss_soc = get_power_data(service_point_code='E004', asset_type='BSS', sm_hrid='BSS 136739', role='state_of_charge', column_name='bss_state_of_charge', start_date=date1, end_date=date).astype(float)
        entity2.set_entity_data(import_power=import_power, export_power=export_power, production_power=production_power, 
                                consumption_power=consumption_power, bss_charge_power=bss_charge_power, bss_discharge_power=bss_discharge_power,
                                bss_state_of_charge=bss_soc)

        # # metering point name E007
        # # BSS 137085  capacity: 16kW/110kWh 
        # # pv3
        entity3 = Entity(bss_capacity=110000.0, bss_max_power=16000.0)
        import_power = get_sm_readings(service_point_code='E007', channel_role='import', column_name='import', start_date=date1, end_date=date).astype(float)*1000.0
        export_power = get_sm_readings(service_point_code='E007', channel_role='export', column_name='export', start_date=date1, end_date=date).astype(float)*1000.0
        production_power = get_power_data(service_point_code='E007', asset_type='PV', sm_hrid='pv3', role='export_power', column_name='production', start_date=date1, end_date=date).astype(float)
        consumption_power = get_power_data(service_point_code='E007', asset_type='PM', sm_hrid='pm3', role='consumption', column_name='consumption', start_date=date1, end_date=date).astype(float)
        bss_charge_power = get_power_data(service_point_code='E007', asset_type='BSS', sm_hrid='BSS 137085', role='charge_power', column_name='bss_charge_power', start_date=date1, end_date=date).astype(float)
        bss_discharge_power = get_power_data(service_point_code='E007', asset_type='BSS', sm_hrid='BSS 137085', role='discharge_power', column_name='bss_discharge_power', start_date=date1, end_date=date).astype(float)
        bss_soc = get_power_data(service_point_code='E007', asset_type='BSS', sm_hrid='BSS 137085', role='state_of_charge', column_name='bss_state_of_charge', start_date=date1, end_date=date).astype(float)
        entity3.set_entity_data(import_power=import_power, export_power=export_power, production_power=production_power, 
                                consumption_power=consumption_power, bss_charge_power=bss_charge_power, bss_discharge_power=bss_discharge_power,
                                bss_state_of_charge=bss_soc)

        # prices
        energy_prices = get_energy_prices(service_point_code='E003', start_date=date1, end_date=date).astype(float)

        data1 = entity1.get_entity_data().copy()
        data2 = entity2.get_entity_data().copy()
        data3 = entity3.get_entity_data().copy()

        pv1 = data1['pv_production_power']
        pv2 = data2['pv_production_power']
        pv3 = data3['pv_production_power']

        data_combined = data1 + data2 + data3
        results = generate_report(data_combined, energy_prices)

        output = {
            'datetime': date1,
            'data': {
                'pv1': [
                    [int(ts.timestamp()), value] for ts, value in zip(pv1.index, pv1.to_numpy())
                ],
                'pv2': [
                    [int(ts.timestamp()), value] for ts, value in zip(pv2.index, pv2.to_numpy())
                ], 
                'pv3': [
                    [int(ts.timestamp()), value] for ts, value in zip(pv3.index, pv3.to_numpy())
                ]
            }
        }

        # convert numpy types to python native types and limit floats to two decimal places
        def normalize(o):
            if isinstance(o, np.generic):
                o = o.item()
            if isinstance(o, float):
                return round(o, 2)
            
            return o
        
        def clean_dict(d):
            if isinstance(d, dict):
                return {k: clean_dict(v) for k, v in d.items()}
            return normalize(d)
        
        json_str = json.dumps(clean_dict(output))

        # logger.debug(f"KPI report: {json_str}")
        # print(f"KPI report: {json_str}")

        token = request_auth_token()
        payload = base64.b64encode(json_str.encode()).decode()

        name = "pv_production_data"
        extension = ".json"

        connector_settings = fetch_connector_settings()

        logger.debug(f"connector_settings: {connector_settings}")

        body = {
            "data_send": {
                "fileName": name + extension,
                "data_catalog_data_offerings_id": os.getenv("DATA_OFFERING_CATALOG_ID"),
                "description": "PV production profiles at GreenPoint Energy Community",
                "title": name,
                "fileSize": str(len(payload)),
                "message": f"data:text/plain;base64,{payload}",
                "sub-entities": {
                    "provider": {
                        "id": connector_settings["id"],
                        "broker_url": connector_settings["broker_url"],
                        "ecc_url": connector_settings["ecc_url"],
                        "provider_fiware_url": connector_settings["data_app_url"],
                        "ed_api_url": connector_settings["ed_api_url"],
                    }
                },
                "created_by": connector_settings["id"],
            }
        }

        path = f"{connector_settings['ed_api_url']}/entity?id={os.getenv("MIDDLEWARE_ID")}"
        

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        response = requests.post(path, json=body, headers=headers)
        logger.debug(f"response code: {response.status_code}")
        logger.debug(f"response: {response.text}")
        response.raise_for_status()
        logger.debug(f"Data for {name} sent successfully.")

    except Exception as error:
        logger.debug(f"Failed upsert KPI results to EDS {name}:", error)
        # print(f"Failed upsert KPI results to EDS {name}:", error)







