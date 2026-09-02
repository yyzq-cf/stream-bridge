"""
快手直播检测器
通过页面__INITIAL_STATE__提取JSON数据获取FLV流地址
参考: github.com/ihmily/DouyinLiveRecorder
"""
import re
import json
import logging

from platforms.base import get_proxy, get_cookie, http_get

logger = logging.getLogger(__name__)


def check_kuaishou_live(url):
    """
    检测快手直播状态并获取流地址
    返回 (is_live, stream_url, error)
    """
    proxy = get_proxy()
    cookie = get_cookie('kuaishou') or 'clientid=3'

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
        'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
        'Cookie': cookie,
        'Referer': 'https://live.kuaishou.com/',
    }

    try:
        html = http_get(url, headers=headers, proxy=proxy, timeout=15)
    except Exception as e:
        return False, None, f'快手请求异常: {e}'

    if len(html) < 1000:
        return False, None, f'页面内容过少({len(html)}字节), 可能被限流'

    # 检查是否被限流
    if '请求过快' in html or 'errorType' in html:
        return False, None, '被快手限流, 请更新Cookie'

    # 从页面提取 __INITIAL_STATE__ JSON
    try:
        json_str = re.search(
            r'<script>window.__INITIAL_STATE__=(.*?);\(function\(\)\{var s;',
            html
        ).group(1)
        # 提取liveStream数据
        play_list_match = re.findall(r'(\{"liveStream".*?),"gameInfo', json_str)
        if not play_list_match:
            return False, None, None  # 未开播
        play_list = json.loads(play_list_match[0] + '}')
    except (AttributeError, IndexError, json.JSONDecodeError) as e:
        logger.warning(f"快手解析JSON失败: {e}")
        return False, None, None

    if 'errorType' in play_list or 'liveStream' not in play_list:
        return False, None, None

    if not play_list.get('liveStream'):
        return False, None, '快手IP被限制, 请更换Cookie或配代理'

    anchor_name = play_list.get('author', {}).get('name', '')

    # 提取FLV流地址
    play_urls = play_list['liveStream'].get('playUrls')
    if not play_urls:
        return False, None, None  # 未开播

    if 'h264' in play_urls:
        if 'adaptationSet' not in play_urls['h264']:
            return False, None, None
        play_url_list = play_urls['h264']['adaptationSet']['representation']
    else:
        play_url_list = play_urls[0].get('adaptationSet', {}).get('representation', [])

    if not play_url_list:
        return False, None, None

    # 选最高画质的FLV URL
    best_url = None
    for item in play_url_list:
        url_val = item.get('url', '')
        if '.flv' in url_val:
            if 'Fhd' in url_val or 'fhd' in url_val:
                best_url = url_val
                break
            if best_url is None:
                best_url = url_val

    if not best_url and play_url_list:
        best_url = play_url_list[0].get('url', '')

    if not best_url:
        return False, None, None

    # 清理URL
    best_url = best_url.split('"')[0].split("'")[0].split('<')[0].split('\\')[0].strip()

    logger.info(f"快手直播流 [{anchor_name}]: {best_url[:100]}...")
    return True, best_url, None


def get_streamer_info(url):
    """
    获取快手直播博主信息(不获取流地址, 只查信息)
    通过页面__INITIAL_STATE__提取, 返回 {'name': str, 'live': bool, 'error': str or None}
    复用已有的页面请求(headers/cookie/proxy)和正则提取逻辑
    """
    try:
        proxy = get_proxy()
        cookie = get_cookie('kuaishou') or 'clientid=3'

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
            'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
            'Cookie': cookie,
            'Referer': 'https://live.kuaishou.com/',
        }

        try:
            html = http_get(url, headers=headers, proxy=proxy, timeout=15)
        except Exception as e:
            return {'name': '', 'live': False, 'error': f'快手请求异常: {e}'}

        if len(html) < 1000:
            return {'name': '', 'live': False, 'error': f'页面内容过少({len(html)}字节), 可能被限流'}

        if '请求过快' in html or 'errorType' in html:
            return {'name': '', 'live': False, 'error': '被快手限流, 请更新Cookie'}

        # 提取 __INITIAL_STATE__ JSON (同 check_kuaishou_live)
        match = re.search(
            r'<script>window.__INITIAL_STATE__=(.*?);\(function\(\)\{var s;',
            html
        )
        if not match:
            return {'name': '', 'live': False, 'error': '未找到__INITIAL_STATE__'}

        json_str = match.group(1)
        play_list_match = re.findall(r'(\{"liveStream".*?),"gameInfo', json_str)

        if not play_list_match:
            # 未开播, 但仍尝试从 author 字段提取博主名
            name = ''
            author_name_match = re.search(r'"author":\{[^}]*?"name":"([^"]+)"', json_str)
            if author_name_match:
                name = author_name_match.group(1)
            return {'name': name, 'live': False, 'error': None}

        play_list = json.loads(play_list_match[0] + '}')

        if 'errorType' in play_list or 'liveStream' not in play_list:
            name = play_list.get('author', {}).get('name', '')
            return {'name': name, 'live': False, 'error': None}

        # 博主名: play_list['author']['name']
        name = play_list.get('author', {}).get('name', '')
        # 直播状态: play_list['liveStream'] 是否存在
        # 注意: liveStream 为假值时表示IP被限制, 此时认为未直播
        live = bool(play_list.get('liveStream'))

        if not live and 'liveStream' in play_list:
            # liveStream 存在但为空, 可能IP被限制
            logger.warning(f"快手房间博主 [{name}] liveStream为空, 可能IP被限制")

        return {'name': name, 'live': live, 'error': None}

    except Exception as e:
        return {'name': '', 'live': False, 'error': str(e)}
