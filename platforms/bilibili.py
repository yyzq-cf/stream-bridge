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
