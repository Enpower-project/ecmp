from datetime import datetime, timedelta
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload
from sqlalchemy.orm import Session

from app.api import community_utils as cu

from app.auth.dependencies import require_token
from app.crud.service_points import get_service_points_by_ec_id_crud
from app.db.session import SessionLocal
import app.models.ec as ec
from app.optimisation.entity import Entity
from jobs.openmeteo.openmeteo.openmeteo import OpenMeteo
from app.schemas.forecast import ForecastParameters
from prophet import Prophet
from sklearn.ensemble import HistGradientBoostingRegressor


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/forecast", tags=["forecast"])

COMMUNITY_HISTORY_START = pd.Timestamp("2021-08-30T13:45:00+00:00")
# COMMUNITY_HISTORY_END = pd.Timestamp("2021-09-16T13:15:00+00:00")
COMMUNITY_HISTORY_END = pd.Timestamp("2022-01-20T13:15:00+00:00")

def get_db():
    """Yield a database session for a request and close it afterwards.

    Yields:
        Session: An open SQLAlchemy session connected to the application
        database.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _get_community_weather_location(db: Session, service_points) -> ec.WeatherLocation:
    """Return the weather location configured for a community's service points.

    If more than one location is associated with the supplied service points,
    the first location ordered by name and ID is used and a warning is logged.

    Args:
        db: Active SQLAlchemy session.
        service_points: Service-point ORM objects belonging to the community.

    Returns:
        The selected weather location.

    Raises:
        HTTPException: If no weather location is configured.
    """
    sp_ids = [service_point.sp_id for service_point in service_points]
    stmt = (
        select(ec.WeatherLocation)
        .join(ec.ServicePointWeather, ec.ServicePointWeather.location_id == ec.WeatherLocation.location_id)
        .where(ec.ServicePointWeather.sp_id.in_(sp_ids))
        .order_by(ec.WeatherLocation.name, ec.WeatherLocation.location_id)
    )
    locations = db.scalars(stmt).unique().all()
    if not locations:
        raise HTTPException(status_code=404, detail="No weather location configured for this energy community.")

    if len(locations) > 1:
        logger.warning(
            "Multiple weather locations found for energy community service points; using the first one: %s",
            locations[0].location_id,
        )

    return locations[0]


def forecast_profile(train_data, forecast_feed):
    """Fit a Prophet model and predict a weather-driven power profile.

    Both frames must provide ``temperature`` and ``radiation`` regressors.
    ``train_data`` must additionally contain Prophet's ``ds`` timestamp column
    and ``y`` target column; ``forecast_feed`` must contain ``ds``.

    Args:
        train_data: Historical timestamped target and weather observations.
        forecast_feed: Future timestamps and weather-regressor values.

    Returns:
        pandas.DataFrame: Prophet forecast output, including ``yhat``.
    """

    m = Prophet()
    m.add_regressor('temperature', standardize=True)
    m.add_regressor('radiation', standardize=True)
    # m.add_regressor("radiation", standardize=False)
    # m.add_regressor("temperature", standardize=False)

    m.fit(train_data)

    # logger.debug(f"m_params: {m.params["beta"]}")

    # make a forecast
    forecast = m.predict(forecast_feed)

    # logger.debug("raw input vs prophet:")
    # logger.debug(forecast_feed["radiation"].head(20))
    # logger.debug(forecast["radiation"].head(20))

    return forecast


def forecast_pv_profile(train_data: pd.DataFrame, forecast_feed: pd.DataFrame) -> pd.DataFrame:
    """Forecast PV production from weather.

    The model uses radiation and temperature only. Radiation at or below
    zero is treated as night-time production and is always predicted as zero.

    Args:
        train_data: Historical weather data with a non-negative ``y`` PV
            production target.
        forecast_feed: Weather inputs to predict, with ``temperature``,
            ``radiation``, and ``ds`` columns.

    Returns:
        A dataframe with Prophet-compatible ``ds`` and ``yhat`` columns.
    """
    feature_columns = ["radiation", "temperature"]
    required_train_columns = set(feature_columns + ["y"])
    required_forecast_columns = set(feature_columns + ["ds"])
    if missing_columns := required_train_columns - set(train_data.columns):
        raise ValueError(f"PV training data is missing columns: {sorted(missing_columns)}")
    if missing_columns := required_forecast_columns - set(forecast_feed.columns):
        raise ValueError(f"PV forecast data is missing columns: {sorted(missing_columns)}")

    training_frame = train_data.loc[:, feature_columns + ["y"]].dropna()
    if training_frame.empty:
        raise ValueError("PV training data contains no complete weather and production rows.")

    train_features = training_frame.loc[:, feature_columns]
    train_target = training_frame["y"].clip(lower=0.0)
    forecast_features = forecast_feed.loc[:, feature_columns].copy()
    forecast_features["radiation"] = forecast_features["radiation"].fillna(0.0)
    forecast_features["temperature"] = forecast_features["temperature"].fillna(
        train_features["temperature"].median()
    )

    model = HistGradientBoostingRegressor(
        learning_rate=0.05,
        max_iter=200,
        l2_regularization=1.0,
        random_state=0,
    )
    model.fit(train_features, train_target)

    prediction = model.predict(forecast_features)
    prediction = np.clip(prediction, 0.0, None)
    prediction[forecast_features["radiation"].to_numpy() <= 0.0] = 0.0

    return pd.DataFrame(
        {"ds": forecast_feed["ds"].to_numpy(), "yhat": prediction},
        index=forecast_feed.index,
    )


def _consumption_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Build recurring weather and calendar features for consumption forecasting.
    """
    required_columns = {"ds", "temperature", "radiation"}
    if missing_columns := required_columns - set(frame.columns):
        raise ValueError(f"Consumption data is missing columns: {sorted(missing_columns)}")

    timestamps = pd.DatetimeIndex(frame["ds"])
    hour = timestamps.hour + timestamps.minute / 60.0
    weekday = timestamps.dayofweek

    return pd.DataFrame(
        {
            "temperature": frame["temperature"].to_numpy(),
            "radiation": frame["radiation"].to_numpy(),
            "hour_sin": np.sin(2 * np.pi * hour / 24.0),
            "hour_cos": np.cos(2 * np.pi * hour / 24.0),
            "weekday_sin": np.sin(2 * np.pi * weekday / 7.0),
            "weekday_cos": np.cos(2 * np.pi * weekday / 7.0),
            "is_weekend": (weekday >= 5).astype(int),
        },
        index=frame.index,
    )


def forecast_consumption_profile(
    train_data: pd.DataFrame,
    forecast_feed: pd.DataFrame,
) -> pd.DataFrame:
    """Forecast consumption from weather and recurring daily/weekly patterns.

    This deliberately avoids an absolute-time trend, preventing the 2021--2022
    training period from being extrapolated into the requested forecast year.

    Args:
        train_data: Historical weather data with a non-negative ``y``
            consumption target.
        forecast_feed: Weather inputs to predict, including ``ds``.

    Returns:
        A dataframe with Prophet-compatible ``ds`` and ``yhat`` columns.
    """
    if "y" not in train_data.columns:
        raise ValueError("Consumption training data is missing the 'y' column.")

    train_features = _consumption_features(train_data)
    train_target = train_data["y"].clip(lower=0.0)
    valid_rows = train_features.notna().all(axis=1) & train_target.notna()
    train_features = train_features.loc[valid_rows]
    train_target = train_target.loc[valid_rows]
    if train_features.empty:
        raise ValueError("Consumption training data contains no complete rows.")

    forecast_features = _consumption_features(forecast_feed)
    forecast_features = forecast_features.fillna(train_features.median())

    model = HistGradientBoostingRegressor(
        learning_rate=0.05,
        max_iter=200,
        l2_regularization=1.0,
        random_state=0,
    )
    model.fit(train_features, train_target)

    prediction = np.clip(model.predict(forecast_features), 0.0, None)
    return pd.DataFrame(
        {"ds": forecast_feed["ds"].to_numpy(), "yhat": prediction},
        index=forecast_feed.index,
    )


def _to_utc_datetime(ts):
    """Convert a pandas timestamp to the timezone-aware datetime used by the DB.

    Args:
        ts: Timestamp with a UTC offset.

    Returns:
        datetime: Timezone-aware datetime suitable for a reading timestamp.
    """
    return datetime.fromisoformat(ts.isoformat().replace("Z", "+00:00"))


def _upsert_subreadings(db: Session, subchannel_id, series: pd.Series) -> int:
    """Upsert one asset time series into the main ``subreadings`` table.

    Missing values are skipped.  Existing rows with the same subchannel ID and
    timestamp have their value updated; all other rows are inserted.

    Args:
        db: Active SQLAlchemy session used for the write.
        subchannel_id: Target PV or other asset subchannel UUID.
        series: Timestamp-indexed power values to persist.

    Returns:
        int: Number of non-null readings inserted or updated.
    """
    series = series.dropna()
    records = [
        {"id": subchannel_id, "ts": _to_utc_datetime(ts), "value": value}
        for ts, value in series.items()
    ]
    if not records:
        return 0

    stmt = insert(ec.SubReading).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=["id", "ts"],
        set_={"value": stmt.excluded.value},
    )
    db.execute(stmt)
    db.commit()
    return len(records)


def _upsert_readings(db: Session, channel_id, series: pd.Series) -> int:
    """Upsert one meter time series into the main ``readings`` table.

    Missing values are skipped. Existing rows with the same channel ID and
    timestamp have their value updated; all other rows are inserted.

    Args:
        db: Active SQLAlchemy session used for the write.
        channel_id: Target import or export meter-channel UUID.
        series: Timestamp-indexed power values to persist.

    Returns:
        int: Number of non-null readings inserted or updated.
    """
    series = series.dropna()
    records = [
        {"channel_id": channel_id, "ts": _to_utc_datetime(ts), "value": value}
        for ts, value in series.items()
    ]
    if not records:
        return 0

    stmt = insert(ec.Reading).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=["channel_id", "ts"],
        set_={"value": stmt.excluded.value},
    )
    db.execute(stmt)
    db.commit()
    return len(records)
        

@router.post("/emulate")
async def emulate(
    params: ForecastParameters,
    db: Session = Depends(get_db),
    token=Depends(require_token("admin")),
):
    """Forecast and persist PV, grid-export, and grid-import power profiles.

    The endpoint trains separate Prophet models from the fixed historical
    period: one for PV production and one for consumption. It uses weather
    data for the requested period as regressors. The model and persistence
    target are deliberately scoped to ``community_relations["service_points"][0]``.

    Production is upserted to that service point's PV ``export_power``
    subchannel. Grid power is ``production - consumption``: positive values
    are stored in the meter's ``export`` channel, while the absolute value of
    negative values is stored in its ``import`` channel.

    Args:
        params: Requested forecast start and end timestamps.
        db: Request-scoped database session.
        token: Authenticated admin token used to identify the community.

    Raises:
        HTTPException: If prerequisite weather, service-point, PV-subchannel,
        or meter-channel data is unavailable.

    """
    requested_start = pd.Timestamp(params.start_datetime)
    requested_end = pd.Timestamp(params.end_datetime)
    if requested_start.tzinfo is None:
        requested_start = requested_start.tz_localize("UTC")
    else:
        requested_start = requested_start.tz_convert("UTC")
    if requested_end.tzinfo is None:
        requested_end = requested_end.tz_localize("UTC")
    else:
        requested_end = requested_end.tz_convert("UTC")

    # load energy community metadata
    ec_id = cu.get_energy_community_id_from_token(db=db, token_data=token)
    service_points = get_service_points_by_ec_id_crud(db=db, ec_id=ec_id)
    community_relations = cu.build_community_relations(
        db=db,
        ec_id=ec_id,
        service_points=service_points,
    )
    logger.debug(f"community_relations: {community_relations}")

    # load historical weather data
    weather_location = _get_community_weather_location(db=db, service_points=service_points)
    open_meteo = OpenMeteo()
    historical_weather_data = open_meteo.historical_forecast_15min_range(
        latitude=float(weather_location.latitude),
        longitude=float(weather_location.longitude),
        start_datetime=COMMUNITY_HISTORY_START,
        end_datetime=COMMUNITY_HISTORY_END,
    )
    if historical_weather_data.empty:
        raise HTTPException(
            status_code=502,
            detail="Open-Meteo returned no historical weather data for the configured community range.",
        )

    historical_weather_data.index = historical_weather_data.index.tz_convert("UTC")
    
    logger.debug(f"weather_data: {historical_weather_data}")

    # get weather data for selected time range
    today_start = pd.Timestamp.now(tz="UTC").normalize()
    if requested_start < today_start:
        weather_forecast = open_meteo.historical_forecast_15min_range(
            latitude=float(weather_location.latitude),
            longitude=float(weather_location.longitude),
            start_datetime=requested_start,
            end_datetime=requested_end,
        )
    else:
        forecast_length_days = max(
            1,
            int(np.ceil((requested_end - today_start) / pd.Timedelta(days=1))),
        )
        weather_forecast = open_meteo.weather_forecast_15min(
            latitude=float(weather_location.latitude),
            longitude=float(weather_location.longitude),
            length=forecast_length_days,
        )
        weather_forecast.index = weather_forecast.index.tz_convert("UTC")
        weather_forecast = weather_forecast[
            (weather_forecast.index >= requested_start) & (weather_forecast.index < requested_end)
        ]

    if weather_forecast.empty:
        raise HTTPException(
            status_code=502,
            detail="Open-Meteo returned no weather data for the requested time range.",
        )

    logger.debug(f"forecast weather data: {weather_forecast}")

    # loop for creating the emulated data in a per-service point-manner
    # service_point_data = [
    #     cu.load_service_point_data(
    #         db=db,
    #         service_point=service_point,
    #         start_date=COMMUNITY_HISTORY_START,
    #         end_date=COMMUNITY_HISTORY_END,
    #     )
    #     for service_point in community_relations["service_points"]
    # ]

    # 

    # loop over all service points
    for service_point in community_relations["service_points"]:
        #load service point data
        service_point_data = cu.load_service_point_data(
                db=db,
                service_point=service_point,
                start_date=COMMUNITY_HISTORY_START,
                end_date=COMMUNITY_HISTORY_END,
        )
        logger.debug(f"service_point_data: {service_point_data}")
        # PV model build
        # prepare training data
        historical_data = historical_weather_data.copy()
        historical_data["production"] = pd.Series(
            service_point_data.production,
            index=historical_data.index,
            dtype=float,
        )
        historical_data['ds'] = historical_data.index
        historical_data = historical_data.fillna(0)
        historical_data = historical_data.rename(columns={'production': 'y'})
        historical_data['ds'] = historical_data['ds'].dt.tz_localize(None)
        logger.info(f"historical_data_ {historical_data}")

        # prepare forecast data
        forecast_data = pd.DataFrame()
        forecast_data['temperature'] = weather_forecast['temperature']
        forecast_data['radiation'] = weather_forecast['radiation']
        forecast_data['ds'] = forecast_data.index
        forecast_data['ds'] = forecast_data['ds'].dt.tz_localize(None)

        logger.info(f"forecast_data: {forecast_data}")

        # Prophet extrapolates its time trend from 2021--2022 to the requested
        # date, which can produce physically impossible negative PV power.
        # forecast = forecast_profile(historical_data, forecast_data)
        forecast = forecast_pv_profile(historical_data, forecast_data)

        # prepare for saving
        output = forecast.loc[:, ["ds", "yhat"]].rename(columns={"ds": "timestamp"}).copy()
        output["timestamp"] = pd.to_datetime(output["timestamp"], utc=True)
        output["yhat"] = output["yhat"].astype(float).clip(lower=0.0)

        logger.info(f"output: {output}")

        # The forecast is built from service_point_data[0], so use the PV
        # export-power subchannel contained in the first service point relation.
        service_point_relations = community_relations["service_points"]
        if not service_point_relations:
            raise HTTPException(status_code=404, detail="No service points found for this community.")

        # first_service_point = service_point_relations[0]
        pv_export_subchannel_id = next(
            (
                subchannel["subchannel_id"]
                for asset in service_point["assets"]
                if asset["type"] == "PV"
                for submeter in asset["submeters"]
                for subchannel in submeter["subchannels"]
                if subchannel["role"] == "export_power"
            ),
            None,
        )
        if pv_export_subchannel_id is None:
            raise HTTPException(
                status_code=404,
                detail="No PV export-power subchannel found for the first service point.",
            )

        logger.info(f"pv output: {output}")
        pv_export_count = _upsert_subreadings(
            db=db,
            subchannel_id=UUID(pv_export_subchannel_id),
            series=output.set_index("timestamp")["yhat"],
        )
        logger.info(
            "Upserted %s forecasted production subreadings for service point %s.",
            pv_export_count,
            service_point["sp_id"],
        )

        # consumption model build
        historical_data = historical_weather_data.copy()
        historical_data["consumption"] = pd.Series(
            service_point_data.consumption,
            index=historical_data.index,
            dtype=float,
        )
        historical_data['ds'] = historical_data.index
        historical_data = historical_data.fillna(0)
        historical_data = historical_data.rename(columns={'consumption': 'y'})
        historical_data['ds'] = historical_data['ds'].dt.tz_localize(None)
        
        # prepare forecast data
        # forecast_data = pd.DataFrame()
        # forecast_data['temperature'] = weather_forecast['temperature']
        # forecast_data['radiation'] = weather_forecast['radiation']
        # forecast_data['ds'] = forecast_data.index
        # forecast_data['ds'] = forecast_data['ds'].dt.tz_localize(None)
        
        # Prophet extrapolates its time trend from 2021--2022 to the requested
        # date, which can distort consumption and consequently grid forecasts.
        # forecast = forecast_profile(historical_data, forecast_data)
        forecast = forecast_consumption_profile(historical_data, forecast_data)

        # prepare for saving
        consumption = forecast.loc[:, ["ds", "yhat"]].rename(columns={"ds": "timestamp"}).copy()
        consumption["timestamp"] = pd.to_datetime(consumption["timestamp"], utc=True)
        consumption["yhat"] = consumption["yhat"].astype(float).clip(lower=0.0)

        production_series = output.set_index("timestamp")["yhat"]
        consumption_series = consumption.set_index("timestamp")["yhat"]
        service_point_power = production_series.sub(consumption_series, fill_value=0.0)
        export_power = service_point_power.clip(lower=0.0)
        import_power = (-service_point_power).clip(lower=0.0)

        meter_channel_ids = {
            channel["role"]: channel["channel_id"]
            for meter in service_point["meters"]
            for channel in meter["channels"]
            if channel["role"] in {"import", "export"}
        }
        missing_channel_roles = {"import", "export"} - meter_channel_ids.keys()
        if missing_channel_roles:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Missing meter channel(s) for the first service point: "
                    f"{', '.join(sorted(missing_channel_roles))}."
                ),
            )

        logger.debug(f"meter_channel_ids: {meter_channel_ids}")

        logger.debug(f"import: {import_power}")
        logger.debug(f"export: {export_power}")

        export_count = _upsert_readings(
            db=db,
            channel_id=UUID(meter_channel_ids["export"]),
            series=export_power,
        )
        import_count = _upsert_readings(
            db=db,
            channel_id=UUID(meter_channel_ids["import"]),
            series=import_power,
        )
        logger.info(
            "Upserted %s export and %s import forecast readings for service point %s.",
            export_count,
            import_count,
            service_point["sp_id"],
        )

        # Match the PV forecast timeline while leaving battery charge power at zero.
        bss_charge_power = pd.Series(
            0.0,
            index=production_series.index,
            dtype=float,
            name="charge_power",
        )

        bss_charge_subchannel_id = next(
            (
                subchannel["subchannel_id"]
                for asset in service_point["assets"]
                if asset["type"] == "BSS"
                for submeter in asset["submeters"]
                for subchannel in submeter["subchannels"]
                if subchannel["role"] == "charge_power"
            ),
            None,
        )
        if bss_charge_subchannel_id is None:
            raise HTTPException(
                status_code=404,
                detail="No BSS charge_power subchannel found.",
            )
        
        logger.info(f"bss_charge_power: {bss_charge_power}")
        bss_charge_power_count = _upsert_subreadings(
            db=db,
            subchannel_id=UUID(bss_charge_subchannel_id),
            series=bss_charge_power,
        )
        logger.info(
            "Upserted %s charge_power subreadings.",
            bss_charge_power_count,
        )

        # Match the PV forecast timeline while leaving battery discharge power at zero.
        bss_discharge_power = pd.Series(
            0.0,
            index=production_series.index,
            dtype=float,
            name="discharge_power",
        )

        bss_discharge_subchannel_id = next(
            (
                subchannel["subchannel_id"]
                for asset in service_point["assets"]
                if asset["type"] == "BSS"
                for submeter in asset["submeters"]
                for subchannel in submeter["subchannels"]
                if subchannel["role"] == "discharge_power"
            ),
            None,
        )

        if bss_discharge_subchannel_id is None:
            raise HTTPException(
                status_code=404,
                detail="No BSS discharge_power subchannel found.",
            )

        logger.info(f"bss_discharge_power: {bss_discharge_power}")

        bss_discharge_power_count = _upsert_subreadings(
            db=db,
            subchannel_id=UUID(bss_discharge_subchannel_id),
            series=bss_discharge_power,
        )

        logger.info(
            "Upserted %s discharge_power subreadings.",
            bss_discharge_power_count,
        )



    return {
        "requested_start_datetime": params.start_datetime,
        "requested_end_datetime": params.end_datetime,
        "community_history_start_datetime": COMMUNITY_HISTORY_START.isoformat(),
        "community_history_end_datetime": COMMUNITY_HISTORY_END.isoformat(),
        "weather_location_id": str(weather_location.location_id),
        "weather_location_name": weather_location.name,
        "latitude": float(weather_location.latitude),
        "longitude": float(weather_location.longitude),
        "weather_rows_loaded": int(len(historical_weather_data)),
        "weather_columns": historical_weather_data.columns.tolist(),
    }
