import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'stream-bridge-secret-key-change-me')
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        f'sqlite:///{os.path.join(DATA_DIR, "streambridge.db")}'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # FFmpeg
    FFMPEG_PATH = os.environ.get('FFMPEG_PATH', 'ffmpeg')
    # yt-dlp
    YTDLP_PATH = os.environ.get('YTDLP_PATH', 'yt-dlp')

    # 监控间隔(秒)
    MONITOR_INTERVAL = int(os.environ.get('MONITOR_INTERVAL', '60'))

    # YouTube OAuth
    YOUTUBE_CLIENT_SECRETS_FILE = os.environ.get(
        'CLIENT_SECRET_FILE',
        os.path.join(DATA_DIR, 'client_secret.json')
    )

    # 默认管理员
    DEFAULT_ADMIN_USER = os.environ.get('ADMIN_USER', 'admin')
    DEFAULT_ADMIN_PASS = os.environ.get('ADMIN_PASS', 'admin123')

    # 推流默认参数
    DEFAULT_VIDEO_CODEC = os.environ.get('VIDEO_CODEC', 'copy')
    DEFAULT_AUDIO_CODEC = os.environ.get('AUDIO_CODEC', 'aac')
    DEFAULT_AUDIO_BITRATE = os.environ.get('AUDIO_BITRATE', '128k')

    # 支持的平台
    SUPPORTED_PLATFORMS = {
        'douyin': '抖音',
        'kuaishou': '快手',
        'bilibili': 'B站',
        'twitch': 'Twitch',
        'youtube': 'YouTube',
        'custom': '自定义',
    }
