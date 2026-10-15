from dataclasses import dataclass
from app.optimisation.bss import BSS
from decimal import Decimal
import pandas as pd


@dataclass
class EntityState:
    grid_import_power: float = 0.0
    grid_export_power: float = 0.0
    pv_production_power: float = 0.0
    bss_charge_power: float = 0.0
    bss_discharge_power: float = 0.0
    bss_state_of_charge: float = 0.0
    bss_stored_energy: float = 0.0
    consumption: float = 0.0



class Entity(object):
    """
    A class used to represent a single entity inside energy community. 
    Individual entity contains a metering point, PV, BSS, Charging stations, etc.

    Attributes
    ----------
    # state_of_charge: float
    #     percentage of full capacity available (0.0 - 1.0)
    # capacity: float
    #     battery capacity in Wh
    # max_charge_power : float
    #     max power in Watts (W) that can be used for charging the battery
    # max_discharge_power : float
    #     max power in Watts (W) that inverter can provide during battery discharge
    # charge_efficiency: float
    #     charging efficiency (0.0 - 1.0)
    # discharge_efficiency: float
    #     discharging efficiency (0.0 - 1.0)
    # stored_energy : float
    #     amount of energy (Wh) stored in battery


    Methods
    -------
    # step_charge(charging_power, dt)
    #     Charge battery for dt seconds with charging_power in Watts
    # step_discharge(discharge_power, dt)
    #     Discharge battery for dt seconds with discharge_power in Watts
    # get_soc
    #     Returns state-of-charge of the battery in range [0.0 - 1.0]
    # get_power
    #     Returns current power of battery output in Watts
    """

    def __init__(self, bss_capacity, bss_max_power):
        """
        Parameters
        ----------
        customer_id: string
            Customer ID
        metering_point_name: string
            name of metering point smart meter
        pv_name: string
            name of PV unit
        bss_name : string
            name of BSS unit
        bss_capacity : float
            capacity of BSS in Wh 
        bss_max_power : float
            max BSS power in W
        start_dt : string
            Datetime string in the following format: 2025-01-29T00:00:00
        end_dt : string
            Datetime string in the following format: 2025-01-29T00:00:00
        """
        # init parameters
        self._bss_capacity = bss_capacity
        self._bss_max_power = bss_max_power

        # empty Pandas DataFrame for results on EC Entity data
        self._entity_data = pd.DataFrame()

        # init assets
        self._bss = BSS(soc=0.0, capacity=self._bss_capacity, max_charge_power=self._bss_max_power, max_discharge_power=self._bss_max_power, charge_efficiency=0.93, discharge_efficiency=0.93)

        # current step
        self._iteration = 0

        # entity state
        self._state = EntityState()
        # self._set_entity_state_from_data()
    

    def set_entity_data(self, import_power, export_power, production_power, consumption_power, bss_charge_power, bss_discharge_power, bss_state_of_charge):
        self._entity_data['grid_import_power'] = import_power
        self._entity_data['grid_export_power'] = export_power
        self._entity_data['pv_production_power'] = production_power
        # self._entity_data['consumption'] = consumption_power
        self._entity_data['bss_charge_power'] = bss_charge_power
        self._entity_data['bss_discharge_power'] = bss_discharge_power
        self._entity_data['bss_state_of_charge'] = bss_state_of_charge*0.01
        self._entity_data['bss_stored_energy'] = self._entity_data['bss_state_of_charge'] * self._bss._capacity
        # calculate the consumption using all the sources
        self._calculate_consumption()
        # self._entity_data['bss_stored_energy'] = self._entity_data['bss_state_of_charge'] * Decimal(str(self._bss._capacity))

        

    def _load_bss_data(self):
        sensor_id = self._data_api.get_sensor_id(self._customer_id, self._bss_name, 'charge_power')
        self._ec_entity_data['bss_charge_power'] = self._data_api.get_sensor_data(self._customer_id, sensor_id, self._start_dt, self._stop_dt)
        # print(self._ec_entity_data['bss_charge_power'])
        sensor_id = self._data_api.get_sensor_id(self._customer_id, self._bss_name, 'discharge_power')
        self._ec_entity_data['bss_discharge_power'] = self._data_api.get_sensor_data(self._customer_id, sensor_id, self._start_dt, self._stop_dt)
        sensor_id = self._data_api.get_sensor_id(self._customer_id, self._bss_name, 'state_of_charge')
        self._ec_entity_data['bss_state_of_charge'] = self._data_api.get_sensor_data(self._customer_id, sensor_id, self._start_dt, self._stop_dt)*0.01
        # calculate BSS stored energy
        self._ec_entity_data['bss_stored_energy'] = self._ec_entity_data['bss_state_of_charge'] * self._bss._capacity

    
    def _calculate_consumption(self):
        """Prepare and calculate the energy community entity report based on historical data.
        As a report, both input and output data is contained. The resulting data contains the data columns for
        PV production, consumption data, battery charging power, battery discharging power, battery state-of-charge, 
        grid import power, grid export power.

        Parameters
        ----------
        
        Returns
        ------
        """

        # calculate consumption
        self._entity_data['consumption'] = (
            self._entity_data['grid_import_power'] - self._entity_data['grid_export_power']
            + self._entity_data['pv_production_power'] - self._entity_data['bss_charge_power']
            + self._entity_data['bss_discharge_power']
            )
        # limit values to positive values. In some cases, consumption becomes negative because of data inconsistencies so it should be clipped to 0.
        self._entity_data['consumption'] = self._entity_data['consumption'].clip(lower=0)
    

    def self_consumption_optimization(self):
        """This function optimizes the energy community entity self-consumption by optimally storing the excess 
        PV production and using the stored energy when there is inadequate PV production available.

        Parameters
        ----------
        
        Returns
        -------
        Pandas DataFrame optimised for local self-consumption
        """
        # placeholder for the results
        simulation = []

        # iterate over data
        for row in self._entity_data.itertuples(index=True, name='Row'):
            production = row.pv_production_power
            # fix the consumption in case there is a production artefact present in data
            consumption = -row.consumption
            if 0.0 < consumption:
                consumption = 0.0
                # print("Negative consumption!")
            grid = row.grid_import_power

            power = self._bss.calculate_power(production=production, consumption=consumption, grid=grid)
            self._bss.set_power(power=power)
            self._bss.update_bss_state(dt=900)

            power = self._bss.get_power()
            soc = self._bss.get_soc()
            stored_energy = self._bss._stored_energy
            grid_power = -production - consumption - power

            # prepare grid power
            if 0.0 <= grid_power:
                 grid_import_power = grid_power
                 grid_export_power = 0.0
            else:
                grid_import_power = 0.0
                grid_export_power = abs(grid_power)

            # prepare BSS power
            if 0.0 <= power:
                # discharging
                bss_charge_power = 0.0
                bss_discharge_power = abs(power)
            else:
                # charging
                bss_charge_power = abs(power)
                bss_discharge_power = 0.0

            # new data
            # production, consumption, battery_power, grid_power, soc
            simulation.append({"Timestamp": row.Index, "grid_import_power": grid_import_power, "grid_export_power": grid_export_power, 
                               "pv_production_power": row.pv_production_power, "bss_charge_power": bss_charge_power, 
                               "bss_discharge_power": bss_discharge_power, "bss_state_of_charge": soc, 
                               "bss_stored_energy": stored_energy, "consumption": row.consumption})
        
        self._entity_data = pd.DataFrame(simulation)
        self._entity_data.set_index("Timestamp", inplace=True)

        return self._entity_data
    

    def get_remaining_charging_capacity(self):
        """Get the remaining charging capacity that is still available to activate

        Parameters
        ----------
        
        Returns
        -------
        float : Charging power in watts available to activate
        """
        available_power = 0.0
        available_power = self._bss_max_power - self._state.bss_charge_power + self._state.bss_discharge_power

        return available_power



    def get_entity_data(self):
        """Return complete EC Entity data including consumption.

        Parameters
        ----------
        
        Returns
        -------
        pandas.DataFrame : DataFrame containing all resulting columns with datetime index.
        """
        return self._entity_data
    

    def set_bss_power(self, bss_power):
        self._bss.set_power(bss_power)

    
    def update_state(self):
        """Set entity state from the current iteration step data
        Use current iteration index to set local entity state from the historical data.
        """
        bss_power = self._bss.get_power()
        soc = self._bss.get_soc()
        stored_energy = self._bss._stored_energy
        consumption = -self._state.consumption
        grid_power = -self._state.pv_production_power - consumption - bss_power
        # prepare grid power
        if 0.0 <= grid_power:
            grid_import_power = grid_power
            grid_export_power = 0.0
        else:
            grid_import_power = 0.0
            grid_export_power = abs(grid_power)
        # prepare BSS power
        if 0.0 <= bss_power:
            # discharging
            bss_charge_power = 0.0
            bss_discharge_power = abs(bss_power)
        else:
            # charging
            bss_charge_power = abs(bss_power)
            bss_discharge_power = 0.0
        # update entity1 state
        self._state.grid_import_power = grid_import_power
        self._state.grid_export_power = grid_export_power
        self._state.bss_charge_power = bss_charge_power
        self._state.bss_discharge_power = bss_discharge_power
        self._state.bss_state_of_charge = soc
        self._state.bss_stored_energy = stored_energy
        self._entity_data.iloc[self._iteration] = [self._state.grid_import_power, self._state.grid_export_power, self._state.pv_production_power, self._state.bss_charge_power, self._state.bss_discharge_power, self._state.bss_state_of_charge, self._state.bss_stored_energy, self._state.consumption]
    

    def get_entity_state(self):
        """Get current entity state
        
        Returns
        -------
        EntityState class
        """
        return self._state
    

    def set_state_from_data(self):
        self._set_entity_state_from_data()

    def step(self):
        self._iteration += 1


    def _set_entity_state_from_data(self):
        """Set entity state from the current iteration step data
        Use current iteration index to set local entity state from the historical data.
        """
        iterstate = self._entity_data.iloc[[self._iteration]]
        # entity state
        self._state.grid_import_power = iterstate['grid_import_power'].values[0]
        self._state.grid_export_power = iterstate['grid_export_power'].values[0]
        self._state.pv_production_power = iterstate['pv_production_power'].values[0]
        self._state.bss_charge_power = iterstate['bss_charge_power'].values[0]
        self._state.bss_discharge_power = iterstate['bss_discharge_power'].values[0]
        self._state.bss_state_of_charge = iterstate['bss_state_of_charge'].values[0]
        self._state.bss_stored_energy = iterstate['bss_stored_energy'].values[0]
        self._state.consumption = iterstate['consumption'].values[0]


