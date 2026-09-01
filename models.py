from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

db = SQLAlchemy()


class User(UserMixin, db.Model):
    """用户表"""
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Streamer(db.Model):
    """直播博主表 - 手动添加的各平台直播源"""
    __tablename__ = 'streamers'
    id = db.Column(db.Integer, primary_key=True)
    platform = db.Column(db.String(50), nullable=False)  # douyin/kuaishou/bilibili/...
    name = db.Column(db.String(200), nullable=False)     # 博主名称/备注
    room_id = db.Column(db.String(500), nullable=False)  # 房间号或完整URL
    url = db.Column(db.String(1000))                     # 标准化后的URL
    is_monitoring = db.Column(db.Boolean, default=True)  # 是否监控中
    is_live = db.Column(db.Boolean, default=False)       # 当前是否在直播
    last_checked = db.Column(db.DateTime)                # 最后检查时间
    last_live_at = db.Column(db.DateTime)                # 最后在线时间
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # 关联
    active_streams = db.relationship('ActiveStream', backref='streamer', lazy='dynamic')
    logs = db.relationship('StreamLog', backref='streamer', lazy='dynamic')


class YouTubeChannel(db.Model):
    """YouTube频道凭据(OAuth2)"""
    __tablename__ = 'youtube_channels'
    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(db.String(100))
    channel_title = db.Column(db.String(200))
    access_token = db.Column(db.Text)
    refresh_token = db.Column(db.Text)
    token_expiry = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # 默认直播配置
    default_title_template = db.Column(db.String(500), default='{streamer_name} 直播转播')
    default_description = db.Column(db.Text, default='由 StreamBridge 自动转播')
    default_privacy = db.Column(db.String(20), default='public')  # public/unlisted/private
    default_category_id = db.Column(db.String(20), default='22')  # 22=People & Blogs


class ActiveStream(db.Model):
    """活跃推流记录 - 正在推到YouTube的流"""
    __tablename__ = 'active_streams'
    id = db.Column(db.Integer, primary_key=True)
    streamer_id = db.Column(db.Integer, db.ForeignKey('streamers.id'), nullable=False)
    youtube_channel_id = db.Column(db.Integer, db.ForeignKey('youtube_channels.id'))
    
    # YouTube广播信息
    broadcast_id = db.Column(db.String(100))
    broadcast_title = db.Column(db.String(500))
    stream_id = db.Column(db.String(100))
    rtmp_url = db.Column(db.String(500))
    stream_key = db.Column(db.String(500))
    
    # FFmpeg进程
    ffmpeg_pid = db.Column(db.Integer)
    
    # 状态
    status = db.Column(db.String(50), default='starting')  # starting/running/stopping/error
    source_url = db.Column(db.String(1000))  # 实际拉流地址
    
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    stopped_at = db.Column(db.DateTime)
    error_message = db.Column(db.Text)
    
    youtube_channel = db.relationship('YouTubeChannel', backref='active_streams')


class StreamLog(db.Model):
    """日志表"""
    __tablename__ = 'logs'
    id = db.Column(db.Integer, primary_key=True)
    streamer_id = db.Column(db.Integer, db.ForeignKey('streamers.id'), nullable=True)
    level = db.Column(db.String(20), default='info')  # info/warning/error/success
    action = db.Column(db.String(100))
    message = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
