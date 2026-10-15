from sqlalchemy.dialects.postgresql import ENUM

quality_flag = ENUM(
    'V_VALID','V_SUSPECT','V_REJECTED',
    'E_ESTIMATED','E_EDITED','SRC_PRIMARY','SRC_BACKFILL',
    name='quality_flag', create_type=True
)

grid_element_type = ENUM(
    "TSO","HV_SUB","MV_FEEDER","LV_SUB","TRANSFORMER","SWITCH","NODE",
    name="grid_element_type",
    create_type=True    
)

meter_role = ENUM(
    "import", "export", "reactive_import", "reactive_export", "voltage", "current",
    name="meter_role",
    create_type=True
)

submeter_role = ENUM(
    "charge_power", "discharge_power", "state_of_charge", "export_power", "import_power", "consumption",
    name="submeter_role",
    create_type=True
)

unit_of_measure = ENUM(
    "kWh", "kW", "kvarh", "kvar", "Wh", "W", "V", "A", "%",
    name="unit_of_measure",
    create_type=True
)

community_role = ENUM(
    "prosumer", "consumer", "producer", "admin",
    name="community_role",
    create_type=True
)

service_point_role = ENUM(
    "producer", "consumer", "prosumer",
    name="service_point_role",
    create_type=True
)

community_type = ENUM(
    "CSC_00",
    name="community_type",
    create_type=True
)

weather_variable = ENUM(
    "temperature",
    "solar_radiation",
    name="weather_variable",
    create_type=False,
)

price_category = ENUM(
    "day_ahead",
    "intraday",
    "imbalance",
    "retail_tariff",
    "grid_fee",
    name="price_category",
    create_type=False,
)

price_unit = ENUM(
    "EUR/MWh", 
    "EUR/kWh", 
    "EUR/kW", 
    "EUR/MW",
    name="price_unit",
    create_type=False,
)

co2_emissions_unit = ENUM(
    "g/kWh",
    "g/MWh", 
    "kg/kWh",
    "kg/MWh", 
    name="co2_emissions_unit",
    create_type=False,
)

asset_type = ENUM(
    "PV", "BSS", "PM",
    name="asset_type",
    create_type=True
)

flexibility_type = ENUM(
    "REDUCE","INCREASE",
    name="flexibility_type",
    create_type=True    
)
