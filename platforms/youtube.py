"""
YouTube直播检测器
通过yt-dlp获取直播信息和流地址
"""
import json
import subprocess
import logging

from config import Config

logger = logging.getLogger(__name__)


def check_youtube_live(url):
    """
    检测YouTube直播状态并获取流地址
    返回 (is_live, stream_url, error)
    """
    try:
        cmd = [
            Config.YTDLP_PATH,
            '--no-warnings',
            '--no-check-certificates',
            '-J',
            '--no-playlist',
            url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            return False, None, f'yt-dlp: {result.stderr[:200]}'

        data = json.loads(result.stdout)
        is_live = data.get('is_live', False)
        formats = data.get('formats', [])

        if not is_live and not formats:
            return False, None, None

        stream_url = None
        if formats:
            stream_url = formats[-1].get('url')
        if not stream_url:
            stream_url = data.get('url') or data.get('manifest_url')

        if stream_url:
            return True, stream_url, None
        return False, None, '无法获取流地址'

    except subprocess.TimeoutExpired:
        return False, None, 'yt-dlp超时'
    except json.JSONDecodeError:
        return False, None, 'yt-dlp返回非JSON'
    except Exception as e:
        return False, None, f'异常: {e}'


def get_streamer_info(url):
    """获取YouTube博主信息(名称+直播状态)"""
    try:
        cmd = [
            Config.YTDLP_PATH,
            '--no-warnings',
            '--no-check-certificates',
            '-J',
            '--no-playlist',
            url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0 or not result.stdout.strip() or result.stdout.strip() == 'null':
            return {'name': '', 'live': False, 'error': f'yt-dlp: {result.stderr[:200]}'}

        data = json.loads(result.stdout)
        name = data.get('uploader') or data.get('channel') or ''
        is_live = data.get('is_live', False)
        return {'name': name, 'live': is_live, 'error': None}

    except subprocess.TimeoutExpired:
        return {'name': '', 'live': False, 'error': 'yt-dlp超时'}
    except json.JSONDecodeError:
        return {'name': '', 'live': False, 'error': 'yt-dlp返回非JSON'}
    except Exception as e:
        return {'name': '', 'live': False, 'error': str(e)}
