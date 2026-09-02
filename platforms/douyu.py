"""
斗鱼直播检测器
通过斗鱼API获取直播流地址
参考: github.com/ihmily/DouyinLiveRecorder

注意: 斗鱼需要JS签名, 这里使用简化方式获取流地址
"""
import re
import json
import time
import hashlib
import urllib.parse
import logging

from platforms.base import get_proxy, get_cookie, http_get, http_post

logger = logging.getLogger(__name__)

# 斗鱼固定did
DOUYU_DID = '10000000000000000000000000003306'


def check_douyu_live(url):
    """
    检测斗鱼直播状态并获取流地址
    返回 (is_live, stream_url, error)
    """
    proxy = get_proxy()
    cookie = get_cookie('douyu') or ''

    # 提取房间号
    rid = _extract_rid(url)
    if not rid:
        return False, None, f'无法提取房间号: {url}'

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
        'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
        'Referer': f'https://www.douyu.com/{rid}',
    }
    if cookie:
        headers['Cookie'] = cookie

    # Step1: 检查直播状态
    try:
        resp = http_get(
            f'https://www.douyu.com/betard/{rid}',
            headers=headers, proxy=proxy
        )
        room_data = json.loads(resp)
        room = room_data.get('room', {})

        # videoLoop=0 且 show_status=1 表示正在直播
        if room.get('videoLoop') != 0 or room.get('show_status') != 1:
            logger.info(f"斗鱼房间 {rid} 未直播")
            return False, None, None

        anchor_name = room.get('nickname', '')
    except Exception as e:
        return False, None, f'斗鱼betard异常: {e}'

    # Step2: 获取流地址 — 使用getH5Play API
    try:
        stream_url = _get_douyu_stream(rid, headers, proxy)
        if stream_url:
            logger.info(f"斗鱼直播流 [{anchor_name}]: {stream_url[:100]}...")
            return True, stream_url, None
        return False, None, '斗鱼未返回流地址'
    except Exception as e:
        return False, None, f'斗鱼getH5Play异常: {e}'


def _get_douyu_stream(rid, headers, proxy):
    """通过斗鱼H5 API获取流地址"""
    # 斗鱼需要sign签名, 这里用一个简化方式: 直接请求页面提取
    # 方式1: 尝试直接API (部分房间可以)
    api_url = f'https://www.douyu.com/lapi/live/getH5Play/{rid}'

    # 构建签名参数 (简化版)
    t10 = str(int(time.time()))
    v = '2401'
    rb = hashlib.md5(f'{rid}{DOUYU_DID}{t10}{v}'.encode()).hexdigest()

    data = f'v={v}&did={DOUYU_DID}&tt={t10}&sign={rb}&ver=22011191&rid={rid}&rate=-1'

    try:
        resp = http_post(api_url, data=data, headers=headers, proxy=proxy)
        result = json.loads(resp)
        if result.get('error') == 0:
            base_url = result.get('data', {}).get('rtmp_url', '')
            live_url = result.get('data', {}).get('rtmp_live', '')
            if base_url and live_url:
                return f'{base_url}/{live_url}'
    except Exception:
        pass

    # 方式2: 从页面提取 (降级)
    try:
        html = http_get(f'https://www.douyu.com/{rid}', headers=headers, proxy=proxy)
        # 尝试提取FLV地址
        flv_urls = re.findall(
            r'(https?://[^"\'<>]+\.flv[^"\'<>]*)',
            html
        )
        if flv_urls:
            return flv_urls[0].split('\\')[0].split('"')[0].split("'")[0]
    except Exception:
        pass

    return None


def _extract_rid(url):
    """从URL中提取斗鱼房间号"""
    # 从URL参数提取
    match = re.search(r'rid=(.*?)(?=&|$)', url)
    if match:
        return match.group(1)
    # 从路径提取
    match = re.search(r'douyu\.com/(.*?)(?=\\?|$)', url)
    if match:
        return match.group(1)
    # 纯数字
    if url.strip().isdigit():
        return url.strip()
    return None


def get_streamer_info(url):
    """
    获取斗鱼直播博主信息(不获取流地址, 只查信息)
    通过betard API查询, 返回 {'name': str, 'live': bool, 'error': str or None}
    复用已有的房间号提取(_extract_rid)和请求逻辑(headers/cookie/proxy)
    """
    try:
        proxy = get_proxy()
        cookie = get_cookie('douyu') or ''

        rid = _extract_rid(url)
        if not rid:
            return {'name': '', 'live': False, 'error': f'无法提取房间号: {url}'}

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
            'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
            'Referer': f'https://www.douyu.com/{rid}',
        }
        if cookie:
            headers['Cookie'] = cookie

        # 调用 betard API (同 check_douyin_live 的 Step1)
        resp = http_get(
            f'https://www.douyu.com/betard/{rid}',
            headers=headers, proxy=proxy
        )
        room_data = json.loads(resp)
        room = room_data.get('room', {})

        # 博主名: json_data['room']['nickname']
        name = room.get('nickname', '')

        # 直播状态: videoLoop==0 and show_status==1
        video_loop = room.get('videoLoop', 1)
        show_status = room.get('show_status', 0)
        live = (video_loop == 0 and show_status == 1)

        return {'name': name, 'live': live, 'error': None}

    except Exception as e:
        return {'name': '', 'live': False, 'error': str(e)}


def get_streamer_info(url):
    """获取斗鱼博主信息(名称+直播状态)"""
    proxy = get_proxy()
    cookie = get_cookie('douyu') or ''
    rid = _extract_rid(url)
    if not rid:
        return {'name': '', 'live': False, 'error': '无法提取房间号'}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0',
        'Accept-Language': 'zh-CN,zh;q=0.8',
        'Referer': f'https://www.douyu.com/{rid}',
    }
    if cookie:
        headers['Cookie'] = cookie
    try:
        resp = http_get(f'https://www.douyu.com/betard/{rid}', headers=headers, proxy=proxy)
        data = json.loads(resp)
        room = data.get('room', {})
        name = room.get('nickname', '')
        is_live = room.get('videoLoop') == 0 and room.get('show_status') == 1
        return {'name': name, 'live': is_live, 'error': None}
    except Exception as e:
        return {'name': '', 'live': False, 'error': str(e)}

