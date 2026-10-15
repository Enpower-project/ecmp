import requests
import logging
import pandas as pd
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class OpenMeteoError(RuntimeError):
    """Open-Meteo did not return usable weather data."""


class OpenMeteo(object):
    def __init__(self):
        pass

    def historical_forecast_15min_range(self, latitude, longitude, start_datetime, end_datetime, chunk_days=356):
        start_ts = pd.Timestamp(start_datetime)
        end_ts = pd.Timestamp(end_datetime)
        if start_ts.tzinfo is None:
            start_ts = start_ts.tz_localize("UTC")
        else:
            start_ts = start_ts.tz_convert("UTC")
        if end_ts.tzinfo is None:
            end_ts = end_ts.tz_localize("UTC")
        else:
            end_ts = end_ts.tz_convert("UTC")

        chunks = []
        chunk_start = start_ts
        chunk_number = 1

        while chunk_start < end_ts:
            chunk_end = min(
                chunk_start + pd.Timedelta(days=chunk_days),
                end_ts,
            )

            chunk = self._historical_forecast_15min_range_single(
                latitude=latitude,
                longitude=longitude,
                start_datetime=chunk_start,
                end_datetime=chunk_end,
            )
            logger.info(
                "Open-Meteo historical chunk %d completed: %d rows",
                chunk_number,
                len(chunk),
            )
            chunks.append(chunk)
            chunk_start = chunk_end

        if not chunks:
            return pd.DataFrame()

        result = pd.concat(chunks).sort_index()
        return result[~result.index.duplicated(keep="first")]

    
    def _historical_forecast_15min_range_single(self, latitude, longitude, start_datetime, end_datetime):
        """
        open-meteo.com wrapper for historical 15-minute weather data on an arbitrary datetime range.

        Parameters
        ----------
        latitude : float
            Latitude of the geolocation.
        longitude : float
            Longitude of the geolocation.
        start_datetime : str | datetime | pandas.Timestamp
            Inclusive range start.
        end_datetime : str | datetime | pandas.Timestamp
            Inclusive range end.

        Returns
        -------
        values: pandas dataframe with 15-min data including temperature and shortwave radiation (solar irradiation)
        """
        start_ts = pd.Timestamp(start_datetime)
        end_ts = pd.Timestamp(end_datetime)
        if start_ts.tzinfo is None:
            start_ts = start_ts.tz_localize("UTC")
        else:
            start_ts = start_ts.tz_convert("UTC")
        if end_ts.tzinfo is None:
            end_ts = end_ts.tz_localize("UTC")
        else:
            end_ts = end_ts.tz_convert("UTC")

        date_start = start_ts.strftime("%Y-%m-%d")
        date_end = end_ts.strftime("%Y-%m-%d")
        url = (
            "https://historical-forecast-api.open-meteo.com/v1/forecast"
            f"?latitude={latitude}&longitude={longitude}"
            f"&start_date={date_start}&end_date={date_end}"
            "&minutely_15=temperature_2m,shortwave_radiation"
            "&timezone=UTC"
        )
        logger.info(f"Request URL: {url}")

        try:
            response = requests.get(url, timeout=(5, 30))
            response.raise_for_status()
        except requests.RequestException as exc:
            raise OpenMeteoError(
                f"Open-Meteo request failed for {date_start}–{date_end}: {exc}"
            ) from exc

        try:
            payload = response.json()
        except requests.JSONDecodeError as exc:
            body_preview = response.text[:500].replace("\n", " ")
            raise OpenMeteoError(
                "Open-Meteo returned a non-JSON response "
                f"(status={response.status_code}, "
                f"content_type={response.headers.get('content-type')!r}, "
                f"body={body_preview!r})"
            ) from exc

        minutely_15 = payload.get("minutely_15")
        if not isinstance(minutely_15, dict):
            raise OpenMeteoError(
                "Open-Meteo response lacks 'minutely_15': "
                f"{payload.get('reason', payload)}"
            )

        values = pd.json_normalize(minutely_15).apply(
            pd.Series.explode, ignore_index=True
        )

        values = values.set_index("time")
        values.index = pd.to_datetime(values.index, utc=True)
        values = values.rename(
            columns={"temperature_2m": "temperature", "shortwave_radiation": "radiation"}
        )
        values = values[(values.index >= start_ts) & (values.index < end_ts)]
        return values


    def weather_archive(self, latitude, longitude, start_date, end_date):
        """
        open-meteo.com wrapper for historical data, missing last 7 days - one week delay

        Parameters
        ----------
        latitude : float
            Latitude of the geolocation.
        longitude : float
            Longitude of the geolocation.
        start_date : str
            Timestamp of the starting datetime (e.g. "2022-01-01").
        end_date : str
            Timestamp of the ending datetime (e.g. "2022-01-01").

        Returns
        -------
        values: pandas dataframe with hourly data including temperature and shortwave radiation (solar irradiation)

        """
        url=f"https://archive-api.open-meteo.com/v1/archive?latitude={latitude}&longitude={longitude}&start_date={start_date}&end_date={end_date}&hourly=temperature_2m,shortwave_radiation&timezone=UTC"
        print(url)
        try:
            x = requests.get(url)
        except requests.exceptions.RequestException as e:
            raise SystemExit(e)
        values = []
        if x.status_code == 200:
            values = pd.json_normalize(x.json()['hourly']).apply(pd.Series.explode,ignore_index=True)
            values = values.set_index('time')
            values.index = pd.to_datetime(values.index, utc=True)
            values = values.rename(columns={'temperature_2m': 'temperature', 'shortwave_radiation': 'radiation'})
        return values
    

    def weather_history(self, latitude, longitude, history):
        """
        open-meteo.com wrapper for short-term weather history (up to 10 days)

        Parameters
        ----------
        latitude : float
            Latitude of the geolocation.
        longitude : float
            Longitude of the geolocation.
        hisotry : int
            Number of days from history that we want an outdoor temperature

        Returns
        -------
        values: pandas dataframe with hourly data including temperature and shortwave radiation (solar irradiation)

        """
        # url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&hourly=temperature_2m,shortwave_radiation&past_days={history}"
    
        # try:
        #     x = requests.get(url)
        # except requests.exceptions.RequestException as e:
        #     raise SystemExit(e)
        # values = []
        # if x.status_code == 200:
        #     values = pd.json_normalize(x.json()['hourly']).apply(pd.Series.explode,ignore_index=True)
        #     values = values.set_index('time')
        #     values.index = pd.to_datetime(values.index).tz_localize('UTC')
        # return values
        now = pd.Timestamp.now(tz="UTC")
        now = now.normalize()
        date_start = now - pd.Timedelta(days=history)
        date_end = now

        url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&hourly=temperature_2m,shortwave_radiation&past_days={history}&timezone=UTC"

        try:
            x = requests.get(url)
        except requests.exceptions.RequestException as e:
            raise SystemExit(e)
        
        values = pd.DataFrame()
        if x.status_code == 200:
            values = pd.json_normalize(x.json()['hourly']).apply(pd.Series.explode,ignore_index=True)
            values = values.set_index('time')
            values.index = pd.to_datetime(values.index, utc=True)
            values = values.rename(columns={'temperature_2m': 'temperature', 'shortwave_radiation': 'radiation'})

        # filter data frame to have only values in time frame [date_start, date_end]
        values = values[(values.index >= date_start) & (values.index < date_end)]

        return values
    

    def weather_forecast_hourly(self, latitude, longitude, length):
        """
        open-meteo.com wrapper for weather forecast. When the length is set to 1, the weather forecast will be given for the current day.

        Parameters
        ----------
        latitude : float
            Latitude of the geolocation.
        longitude : float
            Longitude of the geolocation.
        length : int
            Length of weather forecast in days

        Returns
        -------
        values: pandas dataframe with hourly data including temperature and shortwave radiation (solar irradiation)
        """
        now = pd.Timestamp.now(tz="UTC")
        now = now.normalize()
        date_start = now
        date_end = now + pd.Timedelta(days=length)
        
        url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&hourly=temperature_2m,shortwave_radiation&past_days=0&timezone=UTC"

        try:
            x = requests.get(url)
        except requests.exceptions.RequestException as e:
            raise SystemExit(e)

        values = pd.DataFrame()
        if x.status_code == 200:
            values = pd.json_normalize(x.json()['hourly']).apply(pd.Series.explode,ignore_index=True)
            values = values.set_index('time')
            values.index = pd.to_datetime(values.index, utc=True)
            values = values.rename(columns={'temperature_2m': 'temperature', 'shortwave_radiation': 'radiation'})
        # filter data frame to have only values in time frame [date_start, date_end]
        values = values[(values.index >= date_start) & (values.index < date_end)]

        return values
    

    def weather_forecast_15min(self, latitude, longitude, length):
        """
        open-meteo.com wrapper for weather forecast. When the length is set to 1, the weather forecast will be given for the current day. Length describes the number of days from the beginning of today.

        Parameters
        ----------
        latitude : float
            Latitude of the geolocation.
        longitude : float
            Longitude of the geolocation.
        length : int
            Length of weather forecast in days

        Returns
        -------
        values: pandas dataframe with 15-min data including temperature and shortwave radiation (solar irradiation)
        """
        now = pd.Timestamp.now(tz="UTC")
        day_start = now.normalize()
        # number of 15-minute intervals back to the start of current day
        delta = int((now - day_start) / pd.Timedelta(minutes=15))
        # number of 15-min intervals from now until selected length
        length = length * 24 * 4 - delta + 1
        
        url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&minutely_15=temperature_2m,shortwave_radiation&past_days=0&timezone=UTC&forecast_minutely_15={length}&past_minutely_15={delta}"

        try:
            x = requests.get(url)
        except requests.exceptions.RequestException as e:
            raise SystemExit(e)

        values = pd.DataFrame()
        if x.status_code == 200:
            values = pd.json_normalize(x.json()['minutely_15']).apply(pd.Series.explode,ignore_index=True)
            values = values.set_index('time')
            values.index = pd.to_datetime(values.index, utc=True)
            values = values.rename(columns={'temperature_2m': 'temperature', 'shortwave_radiation': 'radiation'})

        return values
    

    def historical_forecast_15min(self, latitude, longitude, length):
        """
        open-meteo.com wrapper for historical weather forecast. When the length is set to 1, the weather forecast will be given for the past day.

        Parameters
        ----------
        latitude : float
            Latitude of the geolocation.
        longitude : float
            Longitude of the geolocation.
        length : int
            Length of weather forecast in days

        Returns
        -------
        values: pandas dataframe with 15-min data including temperature and shortwave radiation (solar irradiation)
        """
        now = pd.Timestamp.now(tz="UTC")
        today_start = now.normalize()
        start = today_start - pd.Timedelta(days=length)
        # query time
        date_start = (start - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        date_end = today_start.strftime("%Y-%m-%d")


        url = f"https://historical-forecast-api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&start_date={date_start}&end_date={date_end}&minutely_15=temperature_2m,shortwave_radiation&timezone=UTC"#&timeformat=unixtime"

        try:
            x = requests.get(url)
            print(x.status_code)
        except requests.exceptions.RequestException as e:
            raise SystemExit(e)

        values = pd.DataFrame()
        if x.status_code == 200:
            values = pd.json_normalize(x.json()['minutely_15']).apply(pd.Series.explode,ignore_index=True)
            values = values.set_index('time')
            values.index = pd.to_datetime(values.index, utc=True)
            values = values.rename(columns={'temperature_2m': 'temperature', 'shortwave_radiation': 'radiation'})
        # filter data frame to have only values in time frame [date_start, date_end]
        values = values[(values.index >= start) & (values.index < today_start)]

        return values
    

    def outdoor_temperature_history(self, latitude, longitude, history):
        """
        open-meteo.com wrapper for outdoor temperature for the selected number of historical days

        Parameters
        ----------
        latitude : float
            Latitude of the geolocation.
        longitude : float
            Longitude of the geolocation.
        hisotry : int
            Number of days from history that we want an outdoor temperature

        Returns
        -------
        values: pandas dataframe with hourly data including temperature and shortwave radiation (solar irradiation)

        """
        now = pd.Timestamp.now(tz="UTC")
        now = now.normalize()
        date_start = now - pd.Timedelta(days=history)
        date_end = now

        url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&hourly=temperature_2m,shortwave_radiation&past_days={history}&timezone=UTC"

        try:
            x = requests.get(url)
        except requests.exceptions.RequestException as e:
            raise SystemExit(e)
        
        values = pd.DataFrame()
        if x.status_code == 200:
            values = pd.json_normalize(x.json()['hourly']).apply(pd.Series.explode,ignore_index=True)
            values = values.set_index('time')
            values.index = pd.to_datetime(values.index, utc=True)
            values = pd.DataFrame(values['temperature_2m'])
            values = values.rename(columns={'temperature_2m': 'temperature'})

        # filter data frame to have only values in time frame [date_start, date_end]
        values = values[(values.index >= date_start) & (values.index < date_end)]

        return values


    


if '__main__' == __name__:

    latitude = 47.82705799377995
    longitude = 16.254065826053928

    weather = OpenMeteo()

    # print("weather forecast")
    # forecast = weather.weather_forecast_hourly(latitude=latitude, longitude=longitude, length=2)
    # print(forecast)
    # print()

    print("historical forecast")
    forecast = weather.historical_forecast_15min(latitude=latitude, longitude=longitude, length=30)
    print(forecast)
    print()

    # print("15-min forecast")
    # forecast = weather.weather_forecast_15min(latitude=latitude, longitude=longitude, length=2)
    # print(forecast)

    # print("recent weather")
    # recent_weather = weather.weather_history(latitude=latitude, longitude=longitude, history=10)
    # print(recent_weather)
    # print()

    # print("historical weather")
    # historical_weather = weather.weather_archive(latitude=latitude, longitude=longitude, start_date='2024-01-01', end_date='2024-01-05')
    # print(historical_weather)
    # print()

    # print("historical temperature")
    # historical_temperature = weather.outdoor_temperature_history(latitude=latitude, longitude=longitude, history=10)
    # print(historical_temperature)
