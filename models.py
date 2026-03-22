from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    balance = Column(Float, default=10000.0)
    created_at = Column(DateTime, default=datetime.utcnow)

    holdings = relationship("Holding", back_populates="user")
    transactions = relationship("Transaction", back_populates="user")


class Video(Base):
    __tablename__ = "videos"

    id = Column(Integer, primary_key=True)
    youtube_id = Column(String, unique=True, nullable=False)
    title = Column(String)
    channel_name = Column(String)
    thumbnail_url = Column(String)
    published_at = Column(DateTime)
    current_price = Column(Float, default=10.0)
    added_at = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow)

    stats = relationship("VideoStats", back_populates="video", order_by="VideoStats.recorded_at")
    holdings = relationship("Holding", back_populates="video")
    transactions = relationship("Transaction", back_populates="video")


class VideoStats(Base):
    __tablename__ = "video_stats"

    id = Column(Integer, primary_key=True)
    video_id = Column(Integer, ForeignKey("videos.id"))
    view_count = Column(Integer, default=0)
    like_count = Column(Integer, default=0)
    comment_count = Column(Integer, default=0)
    price_at_time = Column(Float)
    recorded_at = Column(DateTime, default=datetime.utcnow)

    video = relationship("Video", back_populates="stats")


class Holding(Base):
    __tablename__ = "holdings"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    video_id = Column(Integer, ForeignKey("videos.id"))
    shares = Column(Float, default=0)
    avg_cost_basis = Column(Float, default=0)

    user = relationship("User", back_populates="holdings")
    video = relationship("Video", back_populates="holdings")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    video_id = Column(Integer, ForeignKey("videos.id"))
    transaction_type = Column(String)  # "buy" or "sell"
    shares = Column(Float)
    price_per_share = Column(Float)
    total_amount = Column(Float)
    executed_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="transactions")
    video = relationship("Video", back_populates="transactions")
