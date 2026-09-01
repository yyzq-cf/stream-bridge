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
    """直播博主表"""
    __tablename__ = 'streamers'
    id = db.Column(db.Integer, primary_key=True)
    platform = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    room_id = db.Column(db.String(500), nullable=False)
    url = db.Column(db.String(1000))
    is_monitoring = db.Column(db.Boolean, default=True)
    is_live = db.Column(db.Boolean, default=False)
    last_checked = db.Column(db.DateTime)
    last_live_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # 绑定推流目标
    push_target_id = db.Column(db.Integer, db.ForeignKey('push_targets.id'), nullable=True)

    active_streams = db.relationship('ActiveStream', backref='streamer', lazy='dynamic')
    logs = db.relationship('StreamLog', backref='streamer', lazy='dynamic')
    push_target = db.relationship('PushTarget', backref='streamers')


class PushTarget(db.Model):
    """推流目标 - RTMP地址+流密钥"""
    __tablename__ = 'push_targets'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)          # 目标名称, 如"YouTube主频道"
    rtmp_url = db.Column(db.String(500), nullable=False)      # RTMP地址, 如 rtmp://a.rtmp.youtube.com/live2
    stream_key = db.Column(db.String(500), nullable=False)    # 流密钥
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # 直播标题模板(可选, 部分平台支持)
    title_template = db.Column(db.String(500), default='{streamer_name} 直播转播')


class ActiveStream(db.Model):
    """活跃推流记录"""
    __tablename__ = 'active_streams'
    id = db.Column(db.Integer, primary_key=True)
    streamer_id = db.Column(db.Integer, db.ForeignKey('streamers.id'), nullable=False)
    push_target_id = db.Column(db.Integer, db.ForeignKey('push_targets.id'), nullable=True)

    rtmp_url = db.Column(db.String(500))
    stream_key = db.Column(db.String(500))
    ffmpeg_pid = db.Column(db.Integer)

    status = db.Column(db.String(50), default='starting')  # starting/running/stopping/error
    source_url = db.Column(db.String(1000))

    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    stopped_at = db.Column(db.DateTime)
    error_message = db.Column(db.Text)

    push_target = db.relationship('PushTarget', backref='active_streams')


class StreamLog(db.Model):
    """日志表"""
    __tablename__ = 'logs'
    id = db.Column(db.Integer, primary_key=True)
    streamer_id = db.Column(db.Integer, db.ForeignKey('streamers.id'), nullable=True)
    level = db.Column(db.String(20), default='info')
    action = db.Column(db.String(100))
    message = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


class Setting(db.Model):
    """系统设置表(key-value)"""
    __tablename__ = 'settings'
    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text)
