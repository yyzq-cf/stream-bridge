"""
B站直播检测器
通过B站API获取直播流地址
参考: github.com/ihmily/DouyinLiveRecorder
"""
import json
import urllib.parse
import logging

from platforms.base import get_proxy, get_cookie, http_get

logger = logging.getLogger(__name__)


def check_bilibili_live(url):
    """
    检测B站直播状态并获取流地址
    返回 (is_live, stream_url, error)
    """
    proxy = get_proxy()
    cookie = get_cookie('bilibili') or ''

    # 提取房间号
    room_id = url.split('?')[0].rstrip('/').rsplit('/', 1)[-1]
    if not room_id:
        return False, None, f'无法提取房间号: {url}'

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0',
        'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
        'Referer': 'https://live.bilibili.com/',
    }
    if cookie:
        headers['Cookie'] = cookie

    # Step1: 获取房间真实ID和直播状态
    try:
        resp = http_get(
            f'https://api.live.bilibili.com/room/v1/Room/room_init?id={room_id}',
            headers=headers, proxy=proxy
        )
        room_info = json.loads(resp)
        if room_info.get('code') != 0:
            return False, None, f'B站API错误: {room_info.get("message", "")}'

        data = room_info['data']
        real_room_id = data["room_id"]
        live_status = data.get('live_status') == 1

        if not live_status:
            logger.info(f"B站房间 {room_id} 未直播")
            return False, None, None
    except Exception as e:
        return False, None, f'B站room_init异常: {e}'

    # Step2: 获取流地址
    params = {
        'cid': real_room_id,
        'qn': '10000',  # 原画
        'platform': 'web',
        'ptype': 16,
        'dtype': '2',  # flv
        'proto': '0',
    }
    api_url = f'https://api.live.bilibili.com/room/v1/Room/playUrl?{urllib.parse.urlencode(params)}'

    try:
        resp = http_get(api_url, headers=headers, proxy=proxy)
        play_data = json.loads(resp)
        if play_data.get('code') != 0:
            return False, None, f'B站playUrl错误: {play_data.get("message", "")}'

        durl_list = play_data['data'].get('durl', [])
        if not durl_list:
            return False, None, 'B站未返回流地址'

        stream_url = durl_list[0]['url']

        logger.info(f"B站直播流: {stream_url[:100]}...")
        return True, stream_url, None

    except Exception as e:
        return False, None, f'B站playUrl异常: {e}'


def get_streamer_info(url):
    """
    获取B站直播博主信息(不获取流地址, 只查信息)
    通过room_init API获取直播状态, 再通过Master/info API获取博主名
    返回 {'name': str, 'live': bool, 'error': str or None}
    复用已有的房间号提取和请求逻辑(headers/cookie/proxy)
    """
    try:
        proxy = get_proxy()
        cookie = get_cookie('bilibili') or ''

        # 提取房间号 (同 check_bilibili_live)
        room_id = url.split('?')[0].rstrip('/').rsplit('/', 1)[-1]
        if not room_id:
            return {'name': '', 'live': False, 'error': f'无法提取房间号: {url}'}

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0',
            'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
            'Referer': 'https://live.bilibili.com/',
        }
        if cookie:
            headers['Cookie'] = cookie

        # Step1: room_init 获取 live_status 和 uid
        resp = http_get(
            f'https://api.live.bilibili.com/room/v1/Room/room_init?id={room_id}',
            headers=headers, proxy=proxy
        )
        room_info = json.loads(resp)
        if room_info.get('code') != 0:
            return {'name': '', 'live': False, 'error': f'B站API错误: {room_info.get("message", "")}'}

        data = room_info['data']
        uid = data.get('uid', 0)
        live_status = data.get('live_status', 0)
        live = live_status == 1

        # Step2: 通过 Master/info API 获取博主名 (uname)
        name = ''
        if uid:
            try:
                resp2 = http_get(
                    f'https://api.live.bilibili.com/live_user/v1/Master/info?uid={uid}',
                    headers=headers, proxy=proxy
                )
                master_info = json.loads(resp2)
                if master_info.get('code') == 0:
                    name = master_info.get('data', {}).get('info', {}).get('uname', '')
                else:
                    logger.warning(f"B站Master/info错误: {master_info.get('message', '')}")
            except Exception as e:
                logger.warning(f'B站获取博主名异常: {e}')

        return {'name': name, 'live': live, 'error': None}

    except Exception as e:
        return {'name': '', 'live': False, 'error': str(e)}


def get_streamer_info(url):
    """获取B站博主信息(名称+直播状态)"""
    proxy = get_proxy()
    cookie = get_cookie('bilibili') or ''
    room_id = url.split('?')[0].rstrip('/').rsplit('/', 1)[-1]
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0',
        'Accept-Language': 'zh-CN,zh;q=0.8',
        'Referer': 'https://live.bilibili.com/',
    }
    if cookie:
        headers['Cookie'] = cookie
    try:
        resp = http_get(f'https://api.live.bilibili.com/room/v1/Room/room_init?id={room_id}', headers=headers, proxy=proxy)
        info = json.loads(resp)
        if info.get('code') != 0:
            return {'name': '', 'live': False, 'error': info.get('message', 'API错误')}
        data = info['data']
        uid = data['uid']
        is_live = data.get('live_status') == 1
        # 获取主播名
        resp2 = http_get(f'https://api.live.bilibili.com/live_user/v1/Master/info?uid={uid}', headers=headers, proxy=proxy)
        anchor = json.loads(resp2)
        name = anchor.get('data', {}).get('info', {}).get('uname', '')
        return {'name': name, 'live': is_live, 'error': None}
    except Exception as e:
        return {'name': '', 'live': False, 'error': str(e)}

