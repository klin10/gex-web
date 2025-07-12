from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import relationship
import datetime
from datetime import timezone # Added import

db = SQLAlchemy()

class Ticker(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    symbol = db.Column(db.String(10), unique=True, nullable=False)
    company_name = db.Column(db.String(100))
    last_updated_info = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    expirations = relationship("Expiration", back_populates="ticker", cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Ticker {self.symbol}>'

class Expiration(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticker_id = db.Column(db.Integer, db.ForeignKey('ticker.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    last_fetched_gex = db.Column(db.DateTime) # Will be set by ingestor as naive UTC
    zero_gamma_level = db.Column(db.Float, nullable=True) # Price where Net GEX flips

    ticker = relationship("Ticker", back_populates="expirations")
    strikes = relationship("GEXStrikeData", back_populates="expiration", cascade="all, delete-orphan")

    __table_args__ = (db.UniqueConstraint('ticker_id', 'date', name='_ticker_date_uc'),)

    def __repr__(self):
        return f'<Expiration {self.ticker.symbol} {self.date}>'

class GEXStrikeData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    expiration_id = db.Column(db.Integer, db.ForeignKey('expiration.id'), nullable=False)

    strike = db.Column(db.Float, nullable=False)
    call_gex_usd = db.Column(db.Float)
    put_gex_usd = db.Column(db.Float)
    net_gex_usd = db.Column(db.Float)

    # Snapshot of context for this calculation
    spot_price_at_calculation = db.Column(db.Float)
    calculation_timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    expiration = relationship("Expiration", back_populates="strikes")

    def __repr__(self):
        return f'<GEXStrikeData {self.expiration.ticker.symbol} {self.expiration.date} @{self.strike}>'
