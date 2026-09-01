"""
抖音直播检测器
从抖音直播页面提取FLV流地址(含签名参数)
"""
import re
import time
import logging

logger = logging.getLogger(__name__)

DOUYIN_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}


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
    返回 (is_live, stream_url, error)
    """
    import subprocess

    # 清理URL: 去掉多余query参数, 只保留 https://live.douyin.com/{room_id}
    url = _clean_douyin_url(url)

    proxy = _get_proxy()
    
    # 重试3次(抖音有503反爬)
    for attempt in range(3):
        try:
            cmd = ['curl', '-s', '--compressed', '-L', url,
                   '-H', f'User-Agent: {DOUYIN_HEADERS["User-Agent"]}']
            if proxy:
                cmd += ['--proxy', proxy]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            html = result.stdout
            if len(html) < 1000:
                raise Exception(f'页面内容过少({len(html)}字节)')
            break
        except Exception as e:
            if '503' in str(e) and attempt < 2:
                logger.warning(f"抖音重试, 第{attempt+1}次...")
                time.sleep(2)
                continue
            return False, None, f'抖音检测异常: {e}'
    else:
        return False, None, '抖音多次503, 放弃'

    if len(html) < 1000:
        return False, None, '页面内容过少'

    # 抖音页面中 & 被编码为 \u0026
    decoded = html.replace('\\u0026', '&')

    # 提取带sign参数的完整FLV URL
    flv_urls = re.findall(
        r'(https?://pull-flv-l?\d+\.douyincdn\.com/[^"\s\\<>]+\.flv\?[^"\s\\<>]+)',
        decoded
    )

    if not flv_urls:
        # 也试试 douyinliving.com 域名
        flv_urls = re.findall(
            r'(https?://pull-flv[^"\s\\<>]*\.douyinliving\.com/[^"\s\\<>]+\.flv\?[^"\s\\<>]+)',
            decoded
        )

    if not flv_urls:
        # 检查是否未开播
        if '主播正在休息' in html or '直播已结束' in html or '暂未开始' in html:
            return False, None, None
        return False, None, '未找到流地址, 可能未开播或需要Cookie'

    # 找带sign参数的URL
    signed_urls = [u for u in flv_urls if 'sign=' in u]
    if signed_urls:
        # 优先选原画流(or4)或默认流
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

    # 清理URL末尾的多余字符
    best = best.split('"')[0].split("'")[0].split('<')[0].split('\\')[0].strip()

    logger.info(f"抖音直播流: {best[:100]}...")
    return True, best, None


def _clean_douyin_url(url):
    """清理抖音URL, 只保留 https://live.douyin.com/{room_id}"""
    # 提取房间号
    match = re.search(r'live\.douyin\.com/(\d+)', url)
    if match:
        return f'https://live.douyin.com/{match.group(1)}'
    return url
