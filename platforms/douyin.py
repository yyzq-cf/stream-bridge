"""
抖音直播检测器
通过抖音webcast API获取直播流地址(带ttwid cookie + a_bogus签名)
参考: github.com/ihmily/DouyinLiveRecorder
"""
import re
import time
import json
import logging
import subprocess
import urllib.parse

from platforms.ab_sign import ab_sign

logger = logging.getLogger(__name__)

# 抖音webcast API用的tcookie (无需登录, ttwid是匿名标识)
DOUYIN_COOKIE = 'ttwid=1%7C2iDIYVmjzMcpZ20fcaFde0VghXAA3NaNXE_SLR68IyE%7C1761045455%7Cab35197d5cfb21df6cbb2fa7ef1c9262206b062c315b9d04da746d0b37dfbc7d'

DOUYIN_UA = 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) ' \
             'Chrome/116.0.5845.97 Safari/537.36 Core/1.116.567.400 QQBrowser/19.7.6764.400'


def _get_proxy():
    """从数据库读取代理配置"""
    try:
        from models import Setting
        from app import app
        with app.app_context():
            s = Setting.query.filter_by(key='proxy').first()
            return s.value.strip() if s and s.value and s.value.strip() else None
    except:
        return None


def check_douyin_live(url):
    """
    检测抖音直播状态并获取流地址
    通过webcast API获取, 返回 (is_live, stream_url, error)
    """
    # 清理URL: 只保留 https://live.douyin.com/{room_id}
    web_rid = _extract_web_rid(url)
    if not web_rid:
        return False, None, f'无法提取房间号: {url}'

    proxy = _get_proxy()

    # 构建webcast API请求
    params = {
        "aid": "6383",
        "app_name": "douyin_web",
        "live_id": "1",
        "device_platform": "web",
        "language": "zh-CN",
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Chrome",
        "browser_version": "116.0.0.0",
        "web_rid": web_rid,
        'msToken': '',
    }

    api = f'https://live.douyin.com/webcast/room/web/enter/?{urllib.parse.urlencode(params)}'
    
    # 生成a_bogus签名
    query_string = urllib.parse.urlparse(api).query
    a_bogus = ab_sign(query_string, DOUYIN_UA)
    api += "&a_bogus=" + a_bogus

    # 请求API (重试3次)
    headers = [
        '-H', f'User-Agent: {DOUYIN_UA}',
        '-H', f'Cookie: {DOUYIN_COOKIE}',
        '-H', 'Referer: https://live.douyin.com/',
        '-H', 'Accept: application/json, text/plain, */*',
        '-H', 'Accept-Language: zh-CN,zh;q=0.9',
    ]

    for attempt in range(3):
        try:
            cmd = ['curl', '-s', '--compressed', '-L', api] + headers
            if proxy:
                cmd += ['--proxy', proxy]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            resp = result.stdout

            if not resp or len(resp) < 100:
                raise Exception(f'API返回内容过少({len(resp)}字节)')

            data = json.loads(resp)

            # 检查风控
            if data.get('status_code') != 0:
                status_msg = data.get('status_msg', '')
                if 'risk' in str(data).lower() or 'verify' in str(data).lower():
                    raise Exception('触发风控')
                # 有些非0状态码表示未开播
                logger.warning(f"抖音API状态: status_code={data.get('status_code')} msg={status_msg}")

            break
        except json.JSONDecodeError:
            logger.warning(f"抖音API返回非JSON, 第{attempt+1}次重试...")
            if attempt < 2:
                time.sleep(2)
                continue
            return False, None, '抖音API返回非JSON'
        except Exception as e:
            if attempt < 2:
                logger.warning(f"抖音重试, 第{attempt+1}次: {e}")
                time.sleep(2)
                continue
            return False, None, f'抖音检测异常: {e}'
    else:
        return False, None, '抖音API多次请求失败'

    # 解析房间数据
    try:
        room_data = data['data']['data'][0]
        anchor_name = data['data'].get('user', {}).get('nickname', '')
    except (KeyError, IndexError, TypeError):
        # data.data为空 = 可能是VR直播或特殊类型
        if not data.get('data', {}).get('data'):
            return False, None, '该直播间类型不支持(可能是VR直播)'
        return False, None, '无法解析房间数据'

    # status: 2=直播中, 4=已结束
    room_status = room_data.get('status')
    if room_status != 2:
        logger.info(f"抖音房间 {web_rid} 未直播 (status={room_status})")
        return False, None, None

    # 提取FLV流地址
    stream_url = _extract_flv_url(room_data)
    if stream_url:
        logger.info(f"抖音直播流 [{anchor_name}]: {stream_url[:100]}...")
        return True, stream_url, None

    # 如果web API拿不到流地址, 尝试从页面提取(降级方案)
    logger.warning("webcast API未返回流地址, 尝试页面提取降级方案...")
    return _fallback_page_extract(web_rid, proxy)


def _extract_flv_url(room_data):
    """从webcast API返回的room_data中提取FLV流地址"""
    stream_url_obj = room_data.get('stream_url')
    if not stream_url_obj:
        return None

    # 方式1: flv_pull_url 直接有FLV地址
    flv_pull_url = stream_url_obj.get('flv_pull_url')
    if flv_pull_url:
        # 优先选origin(原画), 其次选key里不含_sd/_md/_ld的
        if isinstance(flv_pull_url, dict):
            # 找origin
            for key in ['ORIGIN', 'origin', 'FULL_HD1', 'FULL_HD1_265']:
                if key in flv_pull_url:
                    url = flv_pull_url[key]
                    if url and '.flv' in url:
                        return url
            # 找不带低画质后缀的
            for key, url in flv_pull_url.items():
                if url and '.flv' in url and not any(s in key.lower() for s in ['_sd', '_md', '_ld', '_ao']):
                    return url
            # 随便取一个
            for url in flv_pull_url.values():
                if url and '.flv' in url:
                    return url

    # 方式2: 从live_core_sdk_data解析
    live_core_sdk_data = stream_url_obj.get('live_core_sdk_data')
    if live_core_sdk_data:
        pull_data = live_core_sdk_data.get('pull_data', {})
        stream_data_str = pull_data.get('stream_data')
        if stream_data_str:
            try:
                stream_data = json.loads(stream_data_str)
                data_obj = stream_data.get('data', {})
                # 优先origin
                for quality in ['origin', 'sd', 'hd', 'ld', 'md']:
                    if quality in data_obj:
                        main = data_obj[quality].get('main', {})
                        flv = main.get('flv')
                        if flv:
                            return flv
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

    # 方式3: hls_pull_url_map 转FLV (把m3u8路径的pull-hls改为pull-flv)
    hls_pull_url_map = stream_url_obj.get('hls_pull_url_map')
    if hls_pull_url_map:
        for key, url in hls_pull_url_map.items():
            if url and ('pull-hls' in url or 'pull-flv' in url):
                # 尝试把hls地址转flv
                flv_url = url.replace('pull-hls', 'pull-flv')
                # m3u8路径转flv文件路径
                flv_url = re.sub(r'/index\.m3u8', '.flv', flv_url)
                flv_url = re.sub(r'/[^/]+\.m3u8', '.flv', flv_url)
                if '.flv' in flv_url:
                    return flv_url

    return None


def _fallback_page_extract(web_rid, proxy=None):
    """降级方案: 从抖音直播页面提取FLV流(旧方式)"""
    url = f'https://live.douyin.com/{web_rid}'
    try:
        cmd = ['curl', '-s', '--compressed', '-L', url,
               '-H', f'User-Agent: {DOUYIN_UA}',
               '-H', 'Referer: https://live.douyin.com/',
               '-H', 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
               '-H', 'Accept-Language: zh-CN,zh;q=0.9']
        if proxy:
            cmd += ['--proxy', proxy]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        html = result.stdout

        if len(html) < 1000:
            return False, None, f'页面内容过少({len(html)}字节), 可能被风控'

        decoded = html.replace('\\u0026', '&').replace('&amp;', '&')
        flv_urls = re.findall(
            r'(https?://pull-flv-[a-z0-9-]+\.douyincdn\.com/[^"\s\\<>]+\.flv\?[^"\s\\<>]+)',
            decoded
        )
        if not flv_urls:
            flv_urls = re.findall(
                r'(https?://pull-flv[^"\s\\<>]*\.douyinliving\.com/[^"\s\\<>]+\.flv\?[^"\s\\<>]+)',
                decoded
            )
        if not flv_urls:
            if '主播正在休息' in html or '直播已结束' in html or '暂未开始' in html:
                return False, None, None
            return False, None, '未找到流地址, 可能未开播或需要Cookie'

        signed_urls = [u for u in flv_urls if 'sign=' in u or 'k=' in u]
        if signed_urls:
            best = None
            for u in signed_urls:
                if '_or4' in u or '_orig' in u:
                    best = u
                    break
            if not best:
                for u in signed_urls:
                    if not any(s in u for s in ['_md', '_sd', '_hd', '_uhd', '_Stage', '_ld', 'only_audio']):
                        best = u
                        break
            if not best:
                best = signed_urls[0]
        else:
            best = flv_urls[0]

        best = best.split('"')[0].split("'")[0].split('<')[0].split('\\')[0].strip()
        logger.info(f"抖音直播流(页面提取): {best[:100]}...")
        return True, best, None

    except Exception as e:
        return False, None, f'页面提取异常: {e}'


def _extract_web_rid(url):
    """从URL中提取抖音房间号(web_rid)"""
    # 从完整URL提取: https://live.douyin.com/163823390463?...
    match = re.search(r'live\.douyin\.com/(\d+)', url)
    if match:
        return match.group(1)
    # 纯数字
    if url.strip().isdigit():
        return url.strip()
    return None
