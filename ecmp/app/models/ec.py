import uuid
from sqlalchemy import Column, Integer, text, Boolean, DateTime, ForeignKey, Text, CheckConstraint, JSON, Enum, TIMESTAMP, Numeric, PrimaryKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, ARRAY, ENUM, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base import Base
from app.models import enums




class EnergyCommunity(Base):
    __tablename__ = "energy_communities"

    ec_id = Column(UUID(as_uuid=True), primary_key=True, unique=True, default=uuid.uuid4)
    code = Column(Text, unique=True, nullable=False)
    type = Column(enums.community_type, nullable=True)
    name = Column(Text, nullable=False)
    description = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    active = Column(Boolean, default=True)

    service_points = relationship("ServicePoint", secondary="community_service_points", back_populates="communities")
    members = relationship("CommunityMembership", back_populates="community")
    allocation_links = relationship("ServicePointAllocationLink", back_populates="community")


class CommunityMembership(Base):
    __tablename__ = "community_membership"

    ec_id = Column(UUID(as_uuid=True), ForeignKey("energy_communities.ec_id"), primary_key=True)
    member_id = Column(UUID(as_uuid=True), ForeignKey("members.member_id"), primary_key=True)

    role = Column(enums.community_role, nullable=False)
    joined_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    left_at = Column(TIMESTAMP(timezone=True))

    community = relationship("EnergyCommunity", back_populates="members")
    member = relationship("Member")


class Member(Base):
    __tablename__ = "members"

    member_id = Column(UUID(as_uuid=True), primary_key=True, unique=True, default=uuid.uuid4)
    external_ref = Column(UUID(as_uuid=True), unique=True)
    name = Column(Text, nullable=False)
    first_name = Column(Text, nullable=False)
    last_name = Column(Text, nullable=False)
    email = Column(Text, nullable=False)
    type = Column(Text, CheckConstraint("type IN ('prosumer','consumer','producer')"))
    created_at = Column(DateTime(timezone=True), server_default="now()")
    service_points = relationship("ServicePoint", back_populates="member")


class GridElement(Base):
    __tablename__ = "grid_elements"

    grid_element_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("grid_elements.grid_element_id"), nullable=True)
    code = Column(Text, unique=True, nullable=False)
    type = Column(enums.grid_element_type, nullable=False)
    name = Column(Text)
    voltage_level = Column(Text)   # e.g. "0.4kV", "20kV"
    location = Column(Text)        # if not using PostGIS, store as WKT or JSON
    created_at = Column(TIMESTAMP(timezone=True), server_default="now()")
    parent = relationship("GridElement", remote_side=[grid_element_id], backref="children")
    service_points = relationship("ServicePoint", back_populates="grid_element")


class ServicePoint(Base):
    __tablename__ = "service_points"

    sp_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id = Column(UUID(as_uuid=True), ForeignKey("members.member_id"), nullable=False)
    code = Column(Text, unique=True, nullable=False)
    role = Column(enums.service_point_role)
    voltage_level = Column(Text)
    address = Column(JSON)
    timezone = Column(Text, nullable=False)
    active = Column(Boolean, default=True)
    # link the service point to the member id
    member = relationship("Member", back_populates="service_points")
    # link the service point to the grid element (substation, transformer, etc.)
    grid_element_id = Column(UUID(as_uuid=True), ForeignKey("grid_elements.grid_element_id"))
    grid_element = relationship(GridElement, back_populates="service_points")
    communities = relationship("EnergyCommunity", secondary="community_service_points", back_populates="service_points")
    assets = relationship("Asset", back_populates="service_point")
    meters = relationship("Meter", back_populates="service_point")
    weather_locations = relationship("WeatherLocation", secondary="service_point_weather", back_populates="service_points",)
    price_zones = relationship("PriceZone", secondary="service_point_price_zone", back_populates="service_points",)
    emission_zones = relationship("EmissionZone", secondary="service_point_emission_zone", back_populates="service_points",)
    outgoing_allocation_links = relationship(
        "ServicePointAllocationLink",
        foreign_keys="ServicePointAllocationLink.producer_sp_id",
        back_populates="producer_service_point",
    )
    incoming_allocation_links = relationship(
        "ServicePointAllocationLink",
        foreign_keys="ServicePointAllocationLink.recipient_sp_id",
        back_populates="recipient_service_point",
    )


class CommunityServicePoint(Base):
    __tablename__ = "community_service_points"

    ec_id = Column(UUID(as_uuid=True), ForeignKey("energy_communities.ec_id"), primary_key=True)
    sp_id = Column(UUID(as_uuid=True), ForeignKey("service_points.sp_id"), primary_key=True)


class ServicePointAllocationLink(Base):
    __tablename__ = "service_point_allocation_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ec_id = Column(UUID(as_uuid=True), ForeignKey("energy_communities.ec_id"), nullable=False)
    producer_sp_id = Column(UUID(as_uuid=True), ForeignKey("service_points.sp_id"), nullable=False)
    recipient_sp_id = Column(UUID(as_uuid=True), ForeignKey("service_points.sp_id"), nullable=False)
    name = Column(Text)
    description = Column(Text)
    active = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "ec_id",
            "producer_sp_id",
            "recipient_sp_id",
            name="uq_service_point_allocation_link_triplet",
        ),
        CheckConstraint(
            "producer_sp_id <> recipient_sp_id",
            name="ck_service_point_allocation_link_distinct_points",
        ),
    )

    community = relationship("EnergyCommunity", back_populates="allocation_links")
    producer_service_point = relationship(
        "ServicePoint",
        foreign_keys=[producer_sp_id],
        back_populates="outgoing_allocation_links",
    )
    recipient_service_point = relationship(
        "ServicePoint",
        foreign_keys=[recipient_sp_id],
        back_populates="incoming_allocation_links",
    )
    coefficients = relationship("ServicePointAllocationCoefficient", back_populates="allocation_link")


class ServicePointAllocationCoefficient(Base):
    __tablename__ = "service_point_allocation_coefficients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    allocation_link_id = Column(
        UUID(as_uuid=True),
        ForeignKey("service_point_allocation_links.id"),
        nullable=False,
    )
    valid_from = Column(TIMESTAMP(timezone=True), nullable=False)
    valid_to = Column(TIMESTAMP(timezone=True))
    coefficient = Column(Numeric, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "allocation_link_id",
            "valid_from",
            name="uq_service_point_allocation_coefficients_link_valid_from",
        ),
        CheckConstraint(
            "coefficient >= 0 AND coefficient <= 1",
            name="ck_service_point_allocation_coefficients_range",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_service_point_allocation_coefficients_window",
        ),
    )

    allocation_link = relationship("ServicePointAllocationLink", back_populates="coefficients")


class Meter(Base):
    __tablename__ = "meters"

    meter_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sp_id = Column(UUID(as_uuid=True), ForeignKey("service_points.sp_id"), nullable=False)
    serial_no = Column(Text, nullable=False)
    model = Column(Text, nullable=False)
    hrid = Column(Text, nullable=False)  # human-readable ID or secondary ID
    installed_at = Column(DateTime(timezone=True), server_default="now()", nullable=False)
    service_point = relationship("ServicePoint", back_populates="meters")
    channels = relationship("Channel", back_populates="meter")


class Channel(Base):
    __tablename__ = "channels"

    channel_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meter_id = Column(UUID(as_uuid=True), ForeignKey("meters.meter_id"), nullable=False)
    src_id = Column(Text)
    role = Column(enums.meter_role, nullable=False)
    uom = Column(enums.unit_of_measure, nullable=False)
    interval_minutes = Column(Integer, CheckConstraint("interval_minutes IN (1,5,10,15,30,60)"), nullable=False)
    scaling_factor = Column(Numeric, default=1.0)
    meter = relationship("Meter", back_populates="channels")
    readings = relationship("Reading", back_populates="channel")
    readings_raw = relationship("ReadingRAW", back_populates="channel")


class Reading(Base):
    __tablename__ = "readings"

    channel_id = Column(UUID(as_uuid=True), ForeignKey("channels.channel_id"), nullable=False)
    ts = Column(DateTime(timezone=True), nullable=False)  # UTC
    value = Column(Numeric, nullable=False)
    flags = Column(ARRAY(enums.quality_flag), nullable=False, server_default=text("ARRAY['SRC_PRIMARY','V_VALID']::quality_flag[]"))
    source = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default="now()")

    __table_args__ = (
        PrimaryKeyConstraint("channel_id", "ts"),
    )
    channel = relationship("Channel", back_populates="readings")


class ReadingRAW(Base):
    __tablename__ = "readings_raw"

    channel_id = Column(UUID(as_uuid=True), ForeignKey("channels.channel_id"), nullable=False)
    ts = Column(DateTime(timezone=True), nullable=False)  # UTC
    value = Column(Numeric, nullable=False)
    flags = Column(ARRAY(enums.quality_flag), nullable=False, server_default=text("ARRAY['SRC_PRIMARY','V_VALID']::quality_flag[]"))
    source = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default="now()")

    __table_args__ = (
        PrimaryKeyConstraint("channel_id", "ts"),
    )
    channel = relationship("Channel", back_populates="readings_raw")


###############
# weather data
###############
class WeatherLocation(Base):
    __tablename__ = "weather_location"

    location_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text)
    latitude = Column(Numeric(8, 5))
    longitude = Column(Numeric(8, 5))
    elevation = Column(Numeric(6, 2))

    service_points = relationship(
        "ServicePoint",
        secondary="service_point_weather",
        back_populates="weather_locations",
    )

    timeseries = relationship("WeatherTimeseries", back_populates="location")


class WeatherTimeseries(Base):
    __tablename__ = "weather_timeseries"

    ts = Column(TIMESTAMP(timezone=True), nullable=False)
    location_id = Column(UUID(as_uuid=True), ForeignKey("weather_location.location_id"), nullable=False)
    variable = Column(enums.weather_variable, nullable=False)
    value = Column(Numeric)

    __table_args__ = (
        PrimaryKeyConstraint("ts", "location_id", "variable", name="pk_weather_timeseries"),
    )

    location = relationship("WeatherLocation", back_populates="timeseries")


# Association Table
class ServicePointWeather(Base):
    __tablename__ = "service_point_weather"

    sp_id = Column(UUID(as_uuid=True), ForeignKey("service_points.sp_id"), nullable=False)
    location_id = Column(UUID(as_uuid=True), ForeignKey("weather_location.location_id"), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("sp_id", "location_id", name="pk_service_point_weather"),
    )



#####################
# co2 emissions data
#####################

class EmissionZone(Base):
    __tablename__ = "emission_zone"

    zone_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False)

    emissions = relationship("CO2EmissionsTimeseries", back_populates="zone")
    service_points = relationship("ServicePoint", secondary="service_point_emission_zone", back_populates="emission_zones")


class CO2EmissionsTimeseries(Base):
    __tablename__ = "co2_emissions_timeseries"

    ts = Column(TIMESTAMP(timezone=True), nullable=False)
    zone_id = Column(UUID(as_uuid=True), ForeignKey("emission_zone.zone_id"), nullable=False)
    value = Column(Numeric, nullable=False)  
    unit = Column(enums.co2_emissions_unit, nullable=False, default="g/kWh")

    __table_args__ = (
        PrimaryKeyConstraint("ts", "zone_id", name="pk_co2_emissions_timeseries"),
    )
    zone = relationship("EmissionZone", back_populates="emissions")


# Association Table
class ServicePointEmissionZone(Base):
    __tablename__ = "service_point_emission_zone"

    sp_id = Column(UUID(as_uuid=True), ForeignKey("service_points.sp_id"), nullable=False)
    zone_id = Column(UUID(as_uuid=True), ForeignKey("emission_zone.zone_id"), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("sp_id", "zone_id", name="pk_service_point_emission_zone"),
    )



#####################
# energy prices data
#####################


class PriceZone(Base):
    __tablename__ = "price_zone"

    zone_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False)
    currency = Column(Text, nullable=False, default="EUR")

    prices = relationship("EnergyPriceTimeseries", back_populates="zone")
    service_points = relationship("ServicePoint", secondary="service_point_price_zone", back_populates="price_zones")


class EnergyPriceTimeseries(Base):
    __tablename__ = "energy_price_timeseries"

    ts = Column(TIMESTAMP(timezone=True), nullable=False)
    zone_id = Column(UUID(as_uuid=True), ForeignKey("price_zone.zone_id"), nullable=False)
    category = Column(enums.price_category, nullable=False)
    value = Column(Numeric, nullable=False)  
    unit = Column(enums.price_unit, nullable=False, default="EUR/MWh")

    __table_args__ = (
        PrimaryKeyConstraint("ts", "zone_id", "category", name="pk_energy_price_timeseries"),
    )
    zone = relationship("PriceZone", back_populates="prices")


# Association Table
class ServicePointPriceZone(Base):
    __tablename__ = "service_point_price_zone"

    sp_id = Column(UUID(as_uuid=True), ForeignKey("service_points.sp_id"), nullable=False)
    zone_id = Column(UUID(as_uuid=True), ForeignKey("price_zone.zone_id"), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("sp_id", "zone_id", name="pk_service_point_price_zone"),
    )



#######################
# submeters and assets
#######################

class Asset(Base):
    __tablename__ = "assets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sp_id = Column(UUID(as_uuid=True), ForeignKey("service_points.sp_id"), nullable=False)
    hrid = Column(Text)
    type = Column(enums.asset_type, nullable=False)
    installed_power = Column(Numeric)
    uom = Column(Text)
    description = Column(Text)
    properties = Column(JSONB, nullable=True)
    installed_at = Column(DateTime(timezone=True), server_default="now()")
    service_point = relationship("ServicePoint", back_populates="assets")
    submeters = relationship("SubMeter", back_populates="asset")


class SubMeter(Base):
    __tablename__ = "submeters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False)
    serial_no = Column(Text)
    hrid = Column(Text)
    type = Column(Text)
    description = Column(Text)
    installed_at = Column(DateTime(timezone=True), server_default="now()")
    asset = relationship("Asset", back_populates="submeters")
    subchannels = relationship("SubChannel", back_populates="submeter")


class SubChannel(Base):
    __tablename__ = "subchannels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submeter_id = Column(UUID(as_uuid=True), ForeignKey("submeters.id"), nullable=False)
    hrid = Column(Text)
    role = Column(enums.submeter_role, nullable=False)
    uom = Column(enums.unit_of_measure, nullable=False)
    interval_minutes = Column(Integer, CheckConstraint("interval_minutes IN (1,5,10,15,30,60)"), nullable=False)
    scaling_factor = Column(Numeric, default=1.0)
    submeter = relationship("SubMeter", back_populates="subchannels")
    subreadings = relationship("SubReading", back_populates="subchannel")
    subreadings_raw = relationship("SubReadingRAW", back_populates="subchannel")


class SubReading(Base):
    __tablename__ = "subreadings"

    id = Column(UUID(as_uuid=True), ForeignKey("subchannels.id"), nullable=False)
    ts = Column(DateTime(timezone=True), nullable=False)  # UTC
    value = Column(Numeric, nullable=False)
    flags = Column(ARRAY(enums.quality_flag), nullable=False, server_default=text("ARRAY['SRC_PRIMARY','V_VALID']::quality_flag[]"))
    source = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default="now()")

    __table_args__ = (
        PrimaryKeyConstraint("id", "ts"),
    )
    subchannel = relationship("SubChannel", back_populates="subreadings")


class SubReadingRAW(Base):
    __tablename__ = "subreadings_raw"

    id = Column(UUID(as_uuid=True), ForeignKey("subchannels.id"), nullable=False)
    ts = Column(DateTime(timezone=True), nullable=False)  # UTC
    value = Column(Numeric, nullable=False)
    flags = Column(ARRAY(enums.quality_flag), nullable=False, server_default=text("ARRAY['SRC_PRIMARY','V_VALID']::quality_flag[]"))
    source = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default="now()")

    __table_args__ = (
        PrimaryKeyConstraint("id", "ts"),
    )
    subchannel = relationship("SubChannel", back_populates="subreadings_raw")



    
