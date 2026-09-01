"""
快手直播检测器
从快手直播页面提取FLV流地址
"""
import re
import subprocess
import json
import logging

logger = logging.getLogger(__name__)

KUAISHOU_HEADERS = [
    '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    '-H', 'Referer: https://live.kuaishou.com/',
    '-H', 'Cookie: clientid=3; did=web_d1a2b3c4e5f6',
]


def check_kuaishou_live(url):
    """
    检测快手直播状态并获取流地址
    返回 (is_live, stream_url, error)
    """
    try:
        # 用curl获取页面(快手拒绝Python urllib)
        cmd = ['curl', '-s', '-L', url] + KUAISHOU_HEADERS
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

        if result.returncode != 0:
            return False, None, f'curl错误: {result.stderr[:200]}'

        html = result.stdout
        if len(html) < 1000:
            return False, None, '页面内容过少'

        # 解码unicode转义
        decoded = html.replace('\\u002F', '/').replace('\\u0026', '&')

        # 提取FLV流地址
        flv_urls = re.findall(
            r'(https?://[^"\s\\<>]+\.flv[^"\s\\<>]*)',
            decoded
        )

        if not flv_urls:
            # 检查是否未开播
            if '主播正在休息' in html or '直播已结束' in html or '暂未开播' in html:
                return False, None, None
            return False, None, '未找到流地址, 可能未开播或需要Cookie'

        # 优先选高清流(Fhd > Hd)
        best = None
        for u in flv_urls:
            if 'Fhd' in u or 'fhd' in u:  # 超高清
                best = u
                break
        if not best:
            for u in flv_urls:
                if 'Hd' in u or 'hd' in u:  # 高清
                    best = u
                    break
        if not best:
            best = flv_urls[0]

        # 清理URL末尾的多余字符
        best = best.split('"')[0].split("'")[0].split('<')[0].split('\\')[0].strip()

        logger.info(f"快手直播流: {best[:100]}...")
        return True, best, None

    except subprocess.TimeoutExpired:
        return False, None, 'curl超时'
    except Exception as e:
        return False, None, f'快手检测异常: {e}'
