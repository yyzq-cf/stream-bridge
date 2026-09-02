"""
YY直播检测器
通过YY API获取直播流地址
参考: github.com/ihmily/DouyinLiveRecorder
"""
import re
import json
import time
import urllib.parse
import logging

from platforms.base import get_proxy, get_cookie, http_get, http_post

logger = logging.getLogger(__name__)


def check_yy_live(url):
    """
    检测YY直播状态并获取流地址
    返回 (is_live, stream_url, error)
    """
    proxy = get_proxy()
    cookie = get_cookie('yy') or 'hd_newui=0.2103068903976506; hdjs_session_id=0.4929014850884579'

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0',
        'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
        'Referer': 'https://www.yy.com/',
        'Cookie': cookie,
    }

    # Step1: 获取页面提取cid和主播名
    try:
        html = http_get(url, headers=headers, proxy=proxy)
    except Exception as e:
        return False, None, f'YY请求异常: {e}'

    if len(html) < 1000:
        return False, None, f'页面内容过少({len(html)}字节)'

    try:
        anchor_match = re.search(r'nick: "(.*?)"', html)
        anchor_name = anchor_match.group(1) if anchor_match else ''

        cid_match = re.search(r'sid : "(.*?)"', html, re.DOTALL)
        if not cid_match:
            return False, None, 'YY无法提取cid'
        cid = cid_match.group(1)
    except Exception as e:
        return False, None, f'YY解析页面异常: {e}'

    # Step2: 调用stream-manager API获取流地址
    data = json.dumps({
        "head": {
            "seq": int(time.time() * 1000),
            "appidstr": "0",
            "bidstr": "121",
            "cidstr": cid,
            "sidstr": cid,
            "uid64": 0,
            "client_type": 108,
            "client_ver": "5.17.0",
            "stream_sys_ver": 1,
            "app": "yylive_web",
            "playersdk_ver": "5.17.0",
            "thundersdk_ver": "0",
            "streamsdk_ver": "5.17.0"
        },
        "client_attribute": {
            "client": "web",
            "model": "web0",
            "cpu": "",
            "graphics_card": "",
            "os": "chrome",
            "osversion": "0",
            "vsdk_version": "",
            "app_identify": "",
            "app_version": "",
            "business": "",
            "width": "1920",
            "height": "1080",
            "scale": "",
            "client_type": 8,
            "h265": 0
        },
        "avp_parameter": {
            "version": 1,
            "client_type": 8,
            "service_type": 0,
            "imsi": 0,
            "send_time": int(time.time()),
            "line_seq": -1,
            "gear": 4,
            "ssl": 1,
            "stream_format": 0
        }
    })

    params = {
        "uid": "0",
        "cid": cid,
        "sid": cid,
        "appid": "0",
        "sequence": str(int(time.time() * 1000)),
        "encode": "json"
    }
    api_url = f'https://stream-manager.yy.com/v3/channel/streams?{urllib.parse.urlencode(params)}'

    try:
        resp = http_post(api_url, data=data, headers=headers, proxy=proxy)
        json_data = json.loads(resp)

        # YY返回的流地址在avp_info.streams
        streams = json_data.get('avp_info', {}).get('streams', [])
        if not streams:
            logger.info(f"YY房间 {cid} 未直播")
            return False, None, None

        # 取第一个流的FLV地址
        for stream in streams:
            stream_url = stream.get('cmcc_url', '') or stream.get('unicom_url', '') or stream.get('cdntype_url', '')
            if stream_url:
                logger.info(f"YY直播流 [{anchor_name}]: {stream_url[:100]}...")
                return True, stream_url, None

        return False, None, 'YY未找到可用流地址'

    except Exception as e:
        return False, None, f'YY API异常: {e}'
