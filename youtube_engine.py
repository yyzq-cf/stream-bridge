"""
YouTube引擎 - OAuth2认证 + 创建直播广播 + 获取RTMP推流地址
"""
import os
import json
import logging
from datetime import datetime, timedelta

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from models import db, YouTubeChannel
from config import Config

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/youtube']

YOUTUBE_API_SERVICE_NAME = 'youtube'
YOUTUBE_API_VERSION = 'v3'


def get_oauth_flow():
    """创建OAuth2 Flow"""
    flow = Flow.from_client_secrets_file(
        Config.YOUTUBE_CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri='urn:ietf:wg:oauth:2.0:oob'  # 设备模式
    )
    return flow


def get_auth_url():
    """获取OAuth授权URL"""
    flow = get_oauth_flow()
    auth_url, state = flow.authorization_url(
        access_type='offline',
        prompt='consent',  # 每次都要求refresh_token
        include_granted_scopes='true'
    )
    return auth_url


def exchange_code_for_token(code):
    """用授权码换token"""
    flow = get_oauth_flow()
    flow.fetch_token(code=code)

    credentials = flow.credentials
    return {
        'access_token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'expiry': credentials.expiry,
    }


def save_channel(token_info):
    """保存频道凭据"""
    # 获取频道信息
    creds = Credentials(
        token=token_info['access_token'],
        refresh_token=token_info.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=_get_client_id(),
        client_secret=_get_client_secret(),
        scopes=SCOPES,
    )
    youtube = build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION,
                    credentials=creds)

    resp = youtube.channels().list(
        part='snippet',
        mine=True
    ).execute()
    items = resp.get('items', [])
    if items:
        channel_id = items[0]['id']
        channel_title = items[0]['snippet']['title']
    else:
        channel_id = ''
        channel_title = 'Unknown'

    channel = YouTubeChannel(
        channel_id=channel_id,
        channel_title=channel_title,
        access_token=token_info['access_token'],
        refresh_token=token_info.get('refresh_token'),
        token_expiry=token_info.get('expiry'),
        is_active=True
    )
    db.session.add(channel)
    db.session.commit()
    logger.info(f"YouTube频道已保存: {channel_title}")
    return channel


def _get_client_id():
    """从client_secret.json读取client_id"""
    with open(Config.YOUTUBE_CLIENT_SECRETS_FILE, 'r') as f:
        data = json.load(f)
    key = list(data.keys())[0]
    return data[key]['client_id']


def _get_client_secret():
    with open(Config.YOUTUBE_CLIENT_SECRETS_FILE, 'r') as f:
        data = json.load(f)
    key = list(data.keys())[0]
    return data[key]['client_secret']


def get_credentials(channel):
    """获取有效凭据, 必要时刷新token"""
    creds = Credentials(
        token=channel.access_token,
        refresh_token=channel.refresh_token,
        token_uri='https://oauth2.googleapis.com/token',
        client_id=_get_client_id(),
        client_secret=_get_client_secret(),
        scopes=SCOPES,
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        # 更新数据库
        channel.access_token = creds.token
        channel.token_expiry = creds.expiry
        db.session.commit()
    return creds


def create_broadcast_and_get_rtmp(channel, title, description, privacy='public'):
    """
    创建YouTube直播广播并获取RTMP推流地址
    返回 (rtmp_url, stream_key, broadcast_id, stream_id)
    """
    creds = get_credentials(channel)
    youtube = build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION,
                    credentials=creds)

    # 1. 创建liveStream (定义推流终端)
    stream_response = youtube.liveStreams().insert(
        part='snippet,cdn',
        body={
            'snippet': {
                'title': f'StreamBridge-{title[:80]}'
            },
            'cdn': {
                'frameRate': 'variable',
                'resolution': 'variable',
                'ingestionType': 'rtmp'
            }
        }
    ).execute()

    stream_id = stream_response['id']
    rtmp_url = stream_response['cdn']['ingestionInfo']['ingestionAddress']
    stream_key = stream_response['cdn']['ingestionInfo']['streamName']

    # 2. 创建liveBroadcast (定义直播间)
    now = datetime.utcnow()
    start_time = (now + timedelta(seconds=5)).isoformat() + 'Z'
    end_time = (now + timedelta(hours=12)).isoformat() + 'Z'

    broadcast_response = youtube.liveBroadcasts().insert(
        part='snippet,status,contentDetails',
        body={
            'snippet': {
                'title': title[:100],
                'description': description,
                'scheduledStartTime': start_time,
                'scheduledEndTime': end_time,
            },
            'status': {
                'privacyStatus': privacy,
                'selfDeclaredMadeForKids': False
            },
            'contentDetails': {
                'enableAutoStart': False,
                'enableAutoStop': True,
                'enableDvr': True,
                'enableContentEncryption': False,
                'enableEmbed': True,
                'recordFromStart': True,
                'startWithSlate': False,
                'monitorStream': {
                    'enableMonitorStream': False,
                }
            }
        }
    ).execute()

    broadcast_id = broadcast_response['id']

    # 3. 绑定 broadcast <-> stream
    youtube.liveBroadcasts().bind(
        id=broadcast_id,
        streamId=stream_id,
        part='id,contentDetails'
    ).execute()

    # 4. 设置直播状态为testing然后live
    youtube.liveBroadcasts().transition(
        broadcastStatus='testing',
        id=broadcast_id,
        part='status'
    ).execute()

    # 等待2秒让testing状态生效
    import time
    time.sleep(2)

    youtube.liveBroadcasts().transition(
        broadcastStatus='live',
        id=broadcast_id,
        part='status'
    ).execute()

    logger.info(f"YouTube直播已创建: {title} broadcast={broadcast_id}")
    return rtmp_url, stream_key, broadcast_id, stream_id


def end_broadcast(channel, broadcast_id):
    """结束直播"""
    creds = get_credentials(channel)
    youtube = build(YOUTUBE_API_SERVICE_NAME, YOUTUBE_API_VERSION,
                    credentials=creds)
    youtube.liveBroadcasts().transition(
        broadcastStatus='complete',
        id=broadcast_id,
        part='status'
    ).execute()
    logger.info(f"直播已结束: {broadcast_id}")


def delete_channel(channel_id):
    """删除YouTube频道凭据"""
    channel = YouTubeChannel.query.get(channel_id)
    if channel:
        db.session.delete(channel)
        db.session.commit()
