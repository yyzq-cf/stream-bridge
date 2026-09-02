"""
虎牙直播检测器
通过虎牙微信小程序API获取直播流地址
参考: github.com/ihmily/DouyinLiveRecorder
"""
import re
import json
import urllib.parse
import logging

from platforms.base import get_proxy, get_cookie, http_get

logger = logging.getLogger(__name__)


def check_huya_live(url):
    """
    检测虎牙直播状态并获取流地址
    返回 (is_live, stream_url, error)
    """
    proxy = get_proxy()
    cookie = get_cookie('huya') or ''

    # 提取房间号
    room_id = url.split('?')[0].rstrip('/').rsplit('/', 1)[-1]
    if not room_id:
        return False, None, f'无法提取房间号: {url}'

    headers = {
        'User-Agent': 'ios/7.830 (ios 17.0; ; iPhone 15 (A2846/A3089/A3090/A3092))',
        'xweb_xhr': '1',
        'referer': 'https://servicewechat.com/wx74767bf0b684f7d3/301/page-frame.html',
        'accept-language': 'zh-CN,zh;q=0.9',
    }
    if cookie:
        headers['Cookie'] = cookie

    # 如果房间号包含字母, 需要先获取真实房间号
    if any(c.isalpha() for c in room_id):
        try:
            html = http_get(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
                'Accept-Language': 'zh-CN,zh;q=0.8',
            }, proxy=proxy)
            match = re.search(r'ProfileRoom":(.*?),"sPrivateHost', html)
            if match:
                room_id = match.group(1)
            else:
                return False, None, '请使用 https://www.huya.com/房间号 格式'
        except Exception as e:
            return False, None, f'虎牙获取房间号异常: {e}'

    # 调用虎牙微信小程序API
    params = {
        'm': 'Live',
        'do': 'profileRoom',
        'roomid': room_id,
        'showSecret': '1',
    }
    api_url = f'https://mp.huya.com/cache.php?{urllib.parse.urlencode(params)}'

    try:
        resp = http_get(api_url, headers=headers, proxy=proxy)
        json_data = json.loads(resp)

        anchor_name = json_data['data']['profileInfo']['nick']
        live_status = json_data['data']['realLiveStatus']

        if live_status != 'ON':
            logger.info(f"虎牙房间 {room_id} [{anchor_name}] 未直播")
            return False, None, None

        # 提取FLV流地址
        base_steam_info_list = json_data['data']['stream']['baseSteamInfoList']

        # CDN优先级: TX > HW > HS > AL
        priority_order = ['TX', 'HW', 'HS', 'AL']
        selected_flv_url = None

        for cdn in priority_order:
            for item in base_steam_info_list:
                if item['sCdnType'] == cdn:
                    stream_name = item['sStreamName']
                    s_flv_url = item['sFlvUrl']
                    flv_anti_code = item['sFlvAntiCode']
                    selected_flv_url = f'{s_flv_url}/{stream_name}.flv?{flv_anti_code}'
                    break
            if selected_flv_url:
                break

        if not selected_flv_url and base_steam_info_list:
            # 没有优先CDN, 取第一个
            item = base_steam_info_list[0]
            stream_name = item['sStreamName']
            s_flv_url = item['sFlvUrl']
            flv_anti_code = item['sFlvAntiCode']
            selected_flv_url = f'{s_flv_url}/{stream_name}.flv?{flv_anti_code}'

        if not selected_flv_url:
            return False, None, '虎牙未返回流地址'

        # 确保使用https
        if selected_flv_url.startswith('http://'):
            selected_flv_url = 'https://' + selected_flv_url[7:]

        # TX CDN需要替换参数
        for item in base_steam_info_list:
            if item['sCdnType'] == 'TX' and selected_flv_url:
                selected_flv_url = selected_flv_url.replace(
                    '&ctype=tars_mp', '&ctype=huya_webh5'
                ).replace('&fs=bhct', '&fs=bgct')
                break

        logger.info(f"虎牙直播流 [{anchor_name}]: {selected_flv_url[:100]}...")
        return True, selected_flv_url, None

    except Exception as e:
        return False, None, f'虎牙API异常: {e}'


def get_streamer_info(url):
    """
    获取虎牙直播博主信息(不获取流地址, 只查信息)
    通过mp.huya.com/cache.php API查询, 返回 {'name': str, 'live': bool, 'error': str or None}
    复用已有的房间号提取(含别名解析)和请求逻辑(headers/cookie/proxy)
    """
    try:
        proxy = get_proxy()
        cookie = get_cookie('huya') or ''

        # 提取房间号 (同 check_huya_live)
        room_id = url.split('?')[0].rstrip('/').rsplit('/', 1)[-1]
        if not room_id:
            return {'name': '', 'live': False, 'error': f'无法提取房间号: {url}'}

        headers = {
            'User-Agent': 'ios/7.830 (ios 17.0; ; iPhone 15 (A2846/A3089/A3090/A3092))',
            'xweb_xhr': '1',
            'referer': 'https://servicewechat.com/wx74767bf0b684f7d3/301/page-frame.html',
            'accept-language': 'zh-CN,zh;q=0.9',
        }
        if cookie:
            headers['Cookie'] = cookie

        # 如果房间号包含字母, 需要先获取真实房间号
        if any(c.isalpha() for c in room_id):
            try:
                html = http_get(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
                    'Accept-Language': 'zh-CN,zh;q=0.8',
                }, proxy=proxy)
                match = re.search(r'ProfileRoom":(.*?),"sPrivateHost', html)
                if match:
                    room_id = match.group(1)
                else:
                    return {'name': '', 'live': False, 'error': '请使用 https://www.huya.com/房间号 格式'}
            except Exception as e:
                return {'name': '', 'live': False, 'error': f'虎牙获取房间号异常: {e}'}

        # 调用 mp.huya.com/cache.php (同 check_huya_live)
        params = {
            'm': 'Live',
            'do': 'profileRoom',
            'roomid': room_id,
            'showSecret': '1',
        }
        api_url = f'https://mp.huya.com/cache.php?{urllib.parse.urlencode(params)}'

        resp = http_get(api_url, headers=headers, proxy=proxy)
        json_data = json.loads(resp)

        data_obj = json_data.get('data', {})

        # 博主名: data['data']['profileInfo']['nick']
        name = data_obj.get('profileInfo', {}).get('nick', '')

        # 直播状态: data['data']['realLiveStatus'] == 'ON'
        live_status = data_obj.get('realLiveStatus', '')
        live = live_status == 'ON'

        return {'name': name, 'live': live, 'error': None}

    except Exception as e:
        return {'name': '', 'live': False, 'error': str(e)}

