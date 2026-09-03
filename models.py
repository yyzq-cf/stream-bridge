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
    # ─── 二步验证(2FA/TOTP) ───
    totp_secret = db.Column(db.String(64), nullable=True)    # TOTP密钥(Base32)
    totp_enabled = db.Column(db.Boolean, default=False)       # 是否已启用2FA

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def generate_totp_secret(self):
        """生成新的TOTP密钥"""
        import pyotp
        self.totp_secret = pyotp.random_base32()
        return self.totp_secret

    def get_totp_uri(self, issuer='StreamBridge'):
        """获取otpauth:// URI"""
        import pyotp
        if not self.totp_secret:
            self.generate_totp_secret()
        return pyotp.totp.TOTP(self.totp_secret).provisioning_uri(
            name=self.username, issuer_name=issuer)

    def verify_totp(self, code):
        """验证TOTP验证码"""
        import pyotp
        if not self.totp_secret or not self.totp_enabled:
            return True  # 未启用2FA, 放行
        if not code:
            return False
        totp = pyotp.TOTP(self.totp_secret)
        return totp.verify(code, valid_window=1)  # 允许前后各30秒


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


class VideoPush(db.Model):
    """视频文件推流记录"""
    __tablename__ = 'video_pushes'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)          # 推流任务名称
    file_path = db.Column(db.String(1000))                    # 本地视频文件路径
    source_url = db.Column(db.String(1000))                   # 在线视频URL(直链)
    source_type = db.Column(db.String(20), default='file')    # file=本地文件 url=在线链接 directory=目录循环
    file_list = db.Column(db.Text)                            # 目录模式下的文件列表(JSON)
    push_target_id = db.Column(db.Integer, db.ForeignKey('push_targets.id'), nullable=False)
    loop = db.Column(db.Boolean, default=False)                # 是否循环推流
    status = db.Column(db.String(50), default='stopped')       # stopped/running/error
    ffmpeg_pid = db.Column(db.Integer)
    started_at = db.Column(db.DateTime)
    stopped_at = db.Column(db.DateTime)
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    push_target = db.relationship('PushTarget', backref='video_pushes')
