"""
快手直播检测器
从快手直播页面提取FLV流地址
"""
import re
import subprocess
import logging

logger = logging.getLogger(__name__)

KUAISHOU_HEADERS = [
    '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    '-H', 'Referer: https://live.kuaishou.com/',
    '-H', 'Accept-Encoding: gzip, deflate',
]


def _get_kuaishou_cookie():
    """从数据库读取快手Cookie"""
    try:
        from models import Setting
        from app import app
        with app.app_context():
            s = Setting.query.filter_by(key='kuaishou_cookie').first()
            return s.value if s and s.value else 'clientid=3'
    except:
        return 'clientid=3'


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


def check_kuaishou_live(url):
    """
    检测快手直播状态并获取流地址
    返回 (is_live, stream_url, error)
    """
    try:
        # 从数据库读取Cookie
        cookie = _get_kuaishou_cookie()
        headers = KUAISHOU_HEADERS + ['-H', f'Cookie: {cookie}']

        # 用curl获取页面
        cmd = ['curl', '-s', '--compressed', '-L', url] + headers
        
        # 加代理
        proxy = _get_proxy()
        if proxy:
            cmd += ['--proxy', proxy]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

        if result.returncode != 0:
            return False, None, f'curl错误: {result.stderr[:200]}'

        html = result.stdout
        if len(html) < 1000:
            # 可能是br压缩,尝试用brotli解压
            try:
                import brotli
                cmd2 = ['curl', '-s', '-L', url] + headers
                if proxy:
                    cmd2 += ['--proxy', proxy]
                result2 = subprocess.run(cmd2, capture_output=True, timeout=15)
                html = brotli.decompress(result2.stdout).decode('utf-8', errors='replace')
                logger.info(f"快手页面(brotli解压) len={len(html)}")
            except Exception:
                return False, None, '页面内容过少,可能被快手限制'

        # 解码unicode转义
        decoded = html.replace('\\u002F', '/').replace('\\u0026', '&')

        # 检查是否被限流
        if '请求过快' in html or 'errorType' in html:
            return False, None, '被快手限流,请更新Cookie'

        # 提取FLV流地址
        flv_urls = re.findall(
            r"(https?://pull-flv[^'\"]+\.flv[^'\"]*)",
            decoded
        )

        if not flv_urls:
            if 'playUrls' not in html:
                return False, None, None  # 正常未开播
            return False, None, '未找到流地址, 可能需要更新Cookie'

        # 优先选择高清流(Fhd > Hd)
        best = None
        for u in flv_urls:
            if 'Fhd' in u or 'fhd' in u:
                best = u
                break
        if not best:
            for u in flv_urls:
                if 'Hd' in u or 'hd' in u:
                    best = u
                    break
        if not best:
            best = flv_urls[0]

        best = best.split('"')[0].split("'")[0].split('<')[0].split('\\')[0].strip()

        logger.info(f"快手直播流: {best[:100]}...")
        return True, best, None

    except subprocess.TimeoutExpired:
        return False, None, 'curl超时'
    except Exception as e:
        return False, None, f'快手检测异常: {e}'
