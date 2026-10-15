# Do not share this code
__version__ = "0.01"
__author__ = "Klemen Bregar"

import logging
import requests
from datetime import datetime, time, timedelta, timezone
import pytz
from lxml import etree
import pandas as pd
# import json

# Prepare standard logging
#logger = ic_utils.create_logger('PROSUMP', log_to_file=True, level=logging.INFO)
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Transparency Platform base API URL
TP_API_BASE_URL = 'https://web-api.tp.entsoe.eu/api?'

# area codes
AREA_CODE_SI = '10YSI-ELES-----O'
AREA_CODE_AT = '10YAT-APG------L'
AREA_CODE_IR = '10Y1001A1001A59C'
AREA_CODE_ES = '10YES-REE------0'
# ENTSO-E day-ahead prices are published for the market's local calendar day.
# Do not use one shared timezone here: Ireland has a different UTC offset from
# Slovenia, Austria, and Spain for much of the year.
AREA_TIMEZONES = {
    'AT': 'Europe/Vienna',
    'SI': 'Europe/Ljubljana',
    'IR': 'Europe/Dublin',
    'ES': 'Europe/Madrid',
}

# map of country codes
country_map = {
    'AT': AREA_CODE_AT,
    'SI': AREA_CODE_SI,
    'IR': AREA_CODE_IR,
    'ES': AREA_CODE_ES,
}


psr_type = {
    'A03': 'Mixed',
    'A04': 'Generation',
    'A05': 'Load',
    'B01': 'Biomass',
    'B02': 'Fossil Brown coal/Lignite',
    'B03': 'Fossil Coal-derived gas',
    'B04': 'Fossil Gas',
    'B05': 'Fossil Hard coal',
    'B06': 'Fossil Oil',
    'B07': 'Fossil Oil shale',
    'B08': 'Fossil Peat',
    'B09': 'Geothermal',
    'B10': 'Hydro Pumped Storage',
    'B11': 'Hydro Run-of-river and poundage',
    'B12': 'Hydro Water Reservoir',
    'B13': 'Marine',
    'B14': 'Nuclear',
    'B15': 'Other renewable',
    'B16': 'Solar',
    'B17': 'Waste',
    'B18': 'Wind Offshore',
    'B19': 'Wind Onshore',
    'B20': 'Other',
    'B21': 'AC Link',
    'B22': 'DC Link',
    'B23': 'Substation',
    'B24': 'Transformer'
}

src_carbon_intensity = {
    'A03': None,
    'A04': None,
    'A05': None,
    'B01': 230.0,
    'B02': 1187.0,
    'B03': None,
    'B04': 528.0,
    'B05': 1187.0,
    'B06': 1170.0,
    'B07': 1170.0,
    'B08': None,
    'B09': 38.0,
    'B10': 237.0,
    'B11': 11.0,
    'B12': 11.0,
    'B13': None,
    'B14': 5.0,
    'B15': 31.0,
    'B16': 31.0,
    'B17': 700.0,
    'B18': 13.0,
    'B19': 13.0,
    'B20': 700.0,
    'B21': None,
    'B22': None,
    'B23': None,
    'B24': None
}

business_type = {
    'A01': 'Production',
    'A04': 'Consumption',
    'A14': 'Aggregated energy data',
    'A19': 'Balance energy deviation',
    'A25': 'General Capacity Information',
    'A29': 'Already allocated capacity (AAC)',
    'A37': 'Installed generation',
    'A43': 'Requested capacity (without price)',
    'A46': 'System Operator redispatching',
    'A53': 'Planned maintenance',
    'A54': 'Unplanned outage',
    'A60': 'Minimum possible',
    'A61': 'Maximum possible',
    'A85': 'Internal redispatch',
    'A91': 'Positive forecast margin (if installed capacity > load forecast)',
    'A92': 'Negative forecast margin (if load forecast > installed capacity)',
    'A93': 'Wind generation',
    'A94': 'Solar generation',
    'A95': 'Frequency containment reserve',
    'A96': 'Automatic frequency restoration reserve',
    'A97': 'Manual frequency restoration reserve',
    'A98': 'Replacement reserve',
    'B01': 'Interconnector network evolution',
    'B02': 'Interconnector network dismantling',
    'B03': 'Counter trade',
    'B04': 'Congestion costs',
    'B05': 'Capacity allocated (including price)',
    'B07': 'Auction revenue',
    'B08': 'Total nominated capacity',
    'B09': 'Net position',
    'B10': 'Congestion income',
    'B11': 'Production unit',
    'B33': 'Area Control Error',
    'B74': 'Offer',
    'B75': 'Need',
    'B95': 'Procured capacity',
    'C22': 'Shared Balancing Reserve Capacity',
    'C23': 'Share of reserve capacity',
    'C24': 'Actual reserve capacity'
}


class TransparencyPlatform(object):
    def __init__(self, api_token):
        self._token = api_token


    def _raise_for_status(self, response, url):
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            logger.error(
                "ENTSO-E request failed with status %s for URL %s. Response body: %s",
                response.status_code,
                url,
                response.text,
            )
            raise


    def _get_area_timezone(self, country_code):
        try:
            return pytz.timezone(AREA_TIMEZONES[country_code])
        except KeyError as exc:
            raise ValueError(
                f"No timezone configured for country_code '{country_code}'."
            ) from exc


    def _to_zulu_range(self, country_code, start_dt, end_dt):
        local_tz = self._get_area_timezone(country_code)

        if start_dt.tzinfo is None:
            raise ValueError(
                "start_dt must include timezone information. "
                "Use an aware datetime such as 2026-05-01T00:00:00+00:00."
            )
        else:
            local_start_dt = start_dt.astimezone(local_tz)

        if end_dt.tzinfo is None:
            raise ValueError(
                "end_dt must include timezone information. "
                "Use an aware datetime such as 2026-05-03T00:00:00+00:00."
            )
        else:
            local_end_dt = end_dt.astimezone(local_tz)

        start_dt_zulu = local_start_dt.astimezone(pytz.utc).strftime("%Y%m%d%H%M")
        end_dt_zulu = local_end_dt.astimezone(pytz.utc).strftime("%Y%m%d%H%M")

        return (start_dt_zulu, end_dt_zulu)


    def _day_zulu_range(self, country_code, day_offset=0):
        """Return one market-local calendar day as ENTSO-E UTC parameters."""
        local_tz = self._get_area_timezone(country_code)
        local_date = datetime.now(local_tz).date() + timedelta(days=day_offset)
        start_dt = local_tz.localize(datetime.combine(local_date, time.min))
        end_dt = local_tz.localize(datetime.combine(local_date + timedelta(days=1), time.min))
        return self._to_zulu_range(country_code, start_dt, end_dt)


    def _current_day_zulu(self, country_code):
        return self._day_zulu_range(country_code)


    def _next_day_zulu(self, country_code):
        return self._day_zulu_range(country_code, day_offset=1)


    def _yesterday_zulu(self, country_code):
        return self._day_zulu_range(country_code, day_offset=-1)


    def _last_30_days_zulu(self, country_code):
        local_tz = self._get_area_timezone(country_code)
        today = datetime.now(local_tz).date()
        start_dt = local_tz.localize(datetime.combine(today - timedelta(days=30), time.min))
        end_dt = local_tz.localize(datetime.combine(today, time.min))
        return self._to_zulu_range(country_code, start_dt, end_dt)
    

    def _daily_prices_from_response(self, response):
        root = etree.fromstring(response.content)

        ns = {'ns': 'urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3'}
        timeseries_list = root.findall('ns:TimeSeries', namespaces=ns)

        series_list = []

        for ts in timeseries_list:
            mrid = ts.find('ns:mRID', namespaces=ns).text 
            btype = ts.find('ns:businessType', namespaces=ns).text 
            in_domain = ts.find('ns:in_Domain.mRID', namespaces=ns).text
            out_domain = ts.find('ns:out_Domain.mRID', namespaces=ns).text
            currency = ts.find('ns:currency_Unit.name', namespaces=ns).text
            unit = ts.find('ns:price_Measure_Unit.name', namespaces=ns).text
            period = ts.find('ns:Period', namespaces=ns)
            time_start = period.find('ns:timeInterval/ns:start', namespaces=ns).text
            time_end = period.find('ns:timeInterval/ns:end', namespaces=ns).text
            resolution = period.find('ns:resolution', ns).text

            if 'PT15M' == resolution:
                prices = [[i+1, None] for i in range(0,96)]
            else:
                prices = [[i+1, None] for i in range(0,24)]

            for point in period.findall('ns:Point', ns):
                idx = int(point.find('ns:position', ns).text)
                prices[idx-1][1] = float(point.find('ns:price.amount', ns).text)

            # forward-fill the missing values - specifics of entsoe-api
            for i in range(len(prices)):
                if None == prices[i][1]:
                    prices[i][1] = prices[i-1][1]
            
            series_list.append({'business_type': btype, 'in_domain': in_domain, 'out_domain': out_domain, 
                            'currency': currency, 'unit_of_measure': unit, 'time_start': time_start, 'time_end': time_end,
                            'resolution': resolution, 'values': prices})
        
        return series_list
    


    def _prices_from_response(self, response):
        root = etree.fromstring(response.content)

        ns = {'ns': 'urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3'}
        timeseries_list = root.findall('ns:TimeSeries', namespaces=ns)

        series_by_period = {}

        for ts in timeseries_list:
            mrid = ts.find('ns:mRID', namespaces=ns).text 
            auction_type_node = ts.find('ns:auction.type', namespaces=ns)
            auction_type = auction_type_node.text if auction_type_node is not None else None
            btype = ts.find('ns:businessType', namespaces=ns).text 
            in_domain = ts.find('ns:in_Domain.mRID', namespaces=ns).text
            out_domain = ts.find('ns:out_Domain.mRID', namespaces=ns).text
            currency = ts.find('ns:currency_Unit.name', namespaces=ns).text
            unit = ts.find('ns:price_Measure_Unit.name', namespaces=ns).text
            sequence_node = ts.find('ns:classificationSequence_AttributeInstanceComponent.position', namespaces=ns)
            sequence = sequence_node.text if sequence_node is not None else None
            period = ts.find('ns:Period', namespaces=ns)
            time_start = period.find('ns:timeInterval/ns:start', namespaces=ns).text
            time_end = period.find('ns:timeInterval/ns:end', namespaces=ns).text
            resolution = period.find('ns:resolution', ns).text

            if sequence not in (None, '1'):
                continue

            if 'PT15M' == resolution:
                prices = [[i+1, None] for i in range(0,96)]
            elif 'PT60M' == resolution:
                prices = [[i+1, None] for i in range(0,24)]
            else:
                logger.debug("Skipping unsupported price resolution %s for series %s", resolution, mrid)
                continue

            for point in period.findall('ns:Point', ns):
                idx = int(point.find('ns:position', ns).text)
                ##### NOTE: temporary workaround to prevent crashing when daylight saving time changes.
                if idx <= len(prices):
                    prices[idx-1][1] = float(point.find('ns:price.amount', ns).text)
            
            # forward-fill the missing values - specifics of entsoe-api
            for i in range(len(prices)):
                if None == prices[i][1]:
                    prices[i][1] = prices[i-1][1]

            period_key = (time_start, time_end, resolution, in_domain, out_domain)
            candidate = {
                'mrid': mrid,
                'business_type': btype,
                'auction_type': auction_type,
                'in_domain': in_domain,
                'out_domain': out_domain,
                'sequence': sequence,
                'currency': currency,
                'unit_of_measure': unit,
                'time_start': time_start,
                'time_end': time_end,
                'resolution': resolution,
                'values': prices,
            }

            existing = series_by_period.get(period_key)
            if existing is None:
                series_by_period[period_key] = candidate
            elif existing['sequence'] == '1' and sequence is None:
                series_by_period[period_key] = candidate
        
        return list(series_by_period.values())

    

    def _co2_emissions_from_response(self, response):
        root = etree.fromstring(response.content)

        ns = {'ns': 'urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0'}
        timeseries_list = root.findall('ns:TimeSeries', namespaces=ns)

        sources = []

        for ts in timeseries_list:
            in_domain = ts.find('ns:inBiddingZone_Domain.mRID', namespaces=ns)
            if in_domain is None:
                # print()
                continue
            mrid = ts.find('ns:mRID', namespaces=ns).text 
            # print(mrid)
            btype = ts.find('ns:businessType', namespaces=ns).text 
            # print('business type: ' + btype + ' (' + business_type[btype] + ')')
            in_domain = in_domain.text
            unit = ts.find('ns:quantity_Measure_Unit.name', namespaces=ns).text
            # print(unit)
            type = ts.find('ns:MktPSRType', namespaces=ns)
            type = type.find('ns:psrType', namespaces=ns).text
            # print('psr type: ' + type + ' (' + psr_type[type] + ')')
            period = ts.find('ns:Period', namespaces=ns)
            time_start = period.find('ns:timeInterval/ns:start', namespaces=ns).text
            # print(time_start)
            time_end = period.find('ns:timeInterval/ns:end', namespaces=ns).text
            # print(time_end)
            resolution = period.find('ns:resolution', ns).text
            # print(resolution)
            values = []
            for point in period.findall('ns:Point', ns):
                values.append(float(point.find('ns:quantity', ns).text))
            sources.append({'energy': values, 'intensity': src_carbon_intensity[type], 'source': type, 'emission': [(values[i]*src_carbon_intensity[type]) for i in range(len(values))]})


        intensity = []

        for i in range(len(sources[0]['energy'])):
            dt = datetime.strptime(time_start, "%Y-%m-%dT%H:%MZ") + timedelta(minutes=15*i)
            timestamp = int(dt.timestamp())
            intensity.append({'timestamp': timestamp, 'energy': 0.0, 'aggregate': 0.0, 'emission': 0.0})
            for source in sources:
                intensity[i]['energy'] += source['energy'][i]
                intensity[i]['aggregate'] += source['emission'][i]
            intensity[i]['emission'] = float(intensity[i]['aggregate']/intensity[i]['energy'])

        emissions = []
        for item in intensity:
            emissions.append({'emission': item['emission'], 'timestamp': datetime.fromtimestamp(item["timestamp"], tz=timezone.utc).isoformat()})

        return emissions


    def get_30_day_prices(self, country_code):
        # check if country_code given exists in country_map
        if country_code not in country_map.keys():
            raise Exception(f"Given country_code parameter ({country_code}) not supported. Currently supported country_code parameters: {country_map.keys()}")
        
        area_code = country_map[country_code]

        start_dt_zulu, end_dt_zulu = self._last_30_days_zulu(country_code)

        url = TP_API_BASE_URL + f'documentType=A44&in_Domain={area_code}&out_Domain={area_code}&periodStart={start_dt_zulu}&periodEnd={end_dt_zulu}&securityToken={self._token}'
        # print(url)
        response = requests.get(url)
        self._raise_for_status(response, url)
        series_list = self._prices_from_response(response)
        
        records = []
        for item in series_list:

            # transform into output
            time_start = item['time_start']
            time_end = item['time_end']
            data = item['values']

            df = pd.DataFrame(data, columns=["Index", "Value"])
            if 'PT15M' == item['resolution']:
                timestamps = pd.date_range(start=time_start, periods=96, freq="15min")
                df["Timestamp"] = timestamps
            else:
                timestamps = pd.date_range(start=time_start, periods=24, freq="60min")
                df["Timestamp"] = timestamps
                df = df.loc[df.index.repeat(4)].reset_index(drop=True)
                df["Timestamp"] = pd.date_range(start=time_start, periods=len(df), freq="15min")

            for _, row in df.iterrows():
                records.append({
                    "timestamp": row["Timestamp"].isoformat(),
                    "value": round(row["Value"] / 1000.0, 6)
                })

        return records


    def get_prices_in_range(self, country_code, start_dt, end_dt):
        # check if country_code given exists in country_map
        if country_code not in country_map.keys():
            raise Exception(f"Given country_code parameter ({country_code}) not supported. Currently supported country_code parameters: {country_map.keys()}")
        if start_dt >= end_dt:
            raise ValueError("start_dt must be earlier than end_dt.")
        
        area_code = country_map[country_code]

        start_dt_zulu, end_dt_zulu = self._to_zulu_range(country_code, start_dt, end_dt)

        url = TP_API_BASE_URL + f'documentType=A44&in_Domain={area_code}&out_Domain={area_code}&periodStart={start_dt_zulu}&periodEnd={end_dt_zulu}&securityToken={self._token}'
        # print(url)
        response = requests.get(url)
        self._raise_for_status(response, url)
        series_list = self._prices_from_response(response)
        
        records = []
        for item in series_list:

            # transform into output
            time_start = item['time_start']
            time_end = item['time_end']
            data = item['values']

            df = pd.DataFrame(data, columns=["Index", "Value"])
            if 'PT15M' == item['resolution']:
                timestamps = pd.date_range(start=time_start, periods=96, freq="15min")
                df["Timestamp"] = timestamps
            else:
                timestamps = pd.date_range(start=time_start, periods=24, freq="60min")
                df["Timestamp"] = timestamps
                df = df.loc[df.index.repeat(4)].reset_index(drop=True)
                df["Timestamp"] = pd.date_range(start=time_start, periods=len(df), freq="15min")

            for _, row in df.iterrows():
                records.append({
                    "timestamp": row["Timestamp"].isoformat(),
                    "value": round(row["Value"] / 1000.0, 6)
                })

        return records
    

        
    def get_current_prices(self, country_code):
        # check if country_code given exists in country_map
        if country_code not in country_map.keys():
            raise Exception(f"Given country_code parameter ({country_code}) not supported. Currently supported country_code parameters: {country_map.keys()}")
        
        area_code = country_map[country_code]

        start_dt_zulu, end_dt_zulu = self._current_day_zulu(country_code)
        
        url = TP_API_BASE_URL + f'documentType=A44&in_Domain={area_code}&out_Domain={area_code}&periodStart={start_dt_zulu}&periodEnd={end_dt_zulu}&securityToken={self._token}'
        # print(url)
        response = requests.get(url)
        self._raise_for_status(response, url)
        # extract daily prices from the response
        series_list = self._daily_prices_from_response(response)

        series = {}

        # check if prices with 15min resolution are available
        availability_15_min = False
        for item in series_list:
            if 'PT15M' == item['resolution']:
                availability_15_min = True
        #         series = item

        if False == availability_15_min:
            series = series_list[0]

        if availability_15_min:
            series = series_list[0]
        
        # transform into output
        time_start = series['time_start']
        time_end = series['time_end']
        data = series['values']

        df = pd.DataFrame(data, columns=["Index", "Value"])
        if 'PT15M' == series['resolution']:
            timestamps = pd.date_range(start=time_start, periods=96, freq="15min")
            df["Timestamp"] = timestamps
        else:
            timestamps = pd.date_range(start=time_start, periods=24, freq="60min")
            df["Timestamp"] = timestamps
            df = df.loc[df.index.repeat(4)].reset_index(drop=True)
            df["Timestamp"] = pd.date_range(start=time_start, periods=len(df), freq="15min")

        records = df.apply(
            lambda row: {"timestamp": row["Timestamp"].isoformat(), "value": round(row["Value"]/1000.0, 6)},
            axis=1
        ).tolist()

        return records
    

    def get_day_ahead_prices(self, country_code):
        # check if country_code given exists in country_map
        if country_code not in country_map.keys():
            raise Exception(f"Given country_code parameter ({country_code}) not supported. Currently supported country_code parameters: {country_map.keys()}")
        
        area_code = country_map[country_code]

        start_dt_zulu, end_dt_zulu = self._next_day_zulu(country_code)

        # print(start_dt_zulu, end_dt_zulu)
        
        url = TP_API_BASE_URL + f'documentType=A44&out_Domain={area_code}&in_Domain={area_code}&periodStart={start_dt_zulu}&periodEnd={end_dt_zulu}&securityToken={self._token}'
        logger.debug(f"url:{url}")
        response = requests.get(url)
        self._raise_for_status(response, url)
        
        series_list = self._daily_prices_from_response(response)

        series = {}

        # check if prices with 15min resolution are available
        availability_15_min = False
        for item in series_list:
            if 'PT15M' == item['resolution']:
                availability_15_min = True
        #         series = item

        if False == availability_15_min:
            series = series_list[0]

        if availability_15_min:
            series = series_list[0]
        
        # transform into output
        time_start = series['time_start']
        time_end = series['time_end']
        data = series['values']

        df = pd.DataFrame(data, columns=["Index", "Value"])
        if 'PT15M' == series['resolution']:
            timestamps = pd.date_range(start=time_start, periods=96, freq="15min")
            df["Timestamp"] = timestamps
        else:
            timestamps = pd.date_range(start=time_start, periods=24, freq="60min")
            df["Timestamp"] = timestamps
            df = df.loc[df.index.repeat(4)].reset_index(drop=True)
            df["Timestamp"] = pd.date_range(start=time_start, periods=len(df), freq="15min")

        records = df.apply(
            lambda row: {"timestamp": row["Timestamp"].isoformat(), "value": round(row["Value"]/1000.0, 6)},
            axis=1
        ).tolist()

        return records
    

    def get_latest_co2_emission(self, country_code):
        # check if country_code given exists in country_map
        if country_code not in country_map.keys():
            raise Exception(f"Given country_code parameter ({country_code}) not supported. Currently supported country_code parameters: {country_map.keys()}")
        
        area_code = country_map[country_code]

        start_dt_zulu, end_dt_zulu = self._current_day_zulu(country_code)

        url = TP_API_BASE_URL + f'documentType=A75&processType=A16&in_Domain={area_code}&periodStart={start_dt_zulu}&periodEnd={end_dt_zulu}&securityToken={self._token}'
        logger.debug(f"url: {url}")
        
        response = requests.get(url)
        self._raise_for_status(response, url)
        #print(response.text)

        emissions = self._co2_emissions_from_response(response)
        emission = emissions[-1]

        return emission
    

    def get_yesterday_co2_emission(self, country_code):
        # check if country_code given exists in country_map
        if country_code not in country_map.keys():
            raise Exception(f"Given country_code parameter ({country_code}) not supported. Currently supported country_code parameters: {country_map.keys()}")
        
        area_code = country_map[country_code]

        start_dt_zulu, end_dt_zulu = self._yesterday_zulu(country_code)

        url = TP_API_BASE_URL + f'documentType=A75&processType=A16&in_Domain={area_code}&periodStart={start_dt_zulu}&periodEnd={end_dt_zulu}&securityToken={self._token}'
        # print(url)
        
        response = requests.get(url)
        self._raise_for_status(response, url)
        #print(response.text)

        emissions = self._co2_emissions_from_response(response)

        return emissions
    

    def get_today_co2_emission(self, country_code):
        # check if country_code given exists in country_map
        if country_code not in country_map.keys():
            raise Exception(f"Given country_code parameter ({country_code}) not supported. Currently supported country_code parameters: {country_map.keys()}")
        
        area_code = country_map[country_code]

        start_dt_zulu, end_dt_zulu = self._current_day_zulu(country_code)

        url = TP_API_BASE_URL + f'documentType=A75&processType=A16&in_Domain={area_code}&periodStart={start_dt_zulu}&periodEnd={end_dt_zulu}&securityToken={self._token}'
        # print(url)

        response = requests.get(url)
        self._raise_for_status(response, url)
        emissions = self._co2_emissions_from_response(response)

        return emissions

        



