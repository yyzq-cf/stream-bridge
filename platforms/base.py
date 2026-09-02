"""
平台检测器公共工具
提供HTTP请求、代理配置、日志等基础功能
"""
import subprocess
import logging

logger = logging.getLogger(__name__)


def _get_setting(key):
    """从数据库读取配置"""
    try:
        from models import Setting
        from app import app
        with app.app_context():
            s = Setting.query.filter_by(key=key).first()
            return s.value.strip() if s and s.value and s.value.strip() else None
    except:
        return None


def get_proxy():
    """从数据库读取代理配置"""
    return _get_setting('proxy')


def get_cookie(platform):
    """从数据库读取平台Cookie"""
    return _get_setting(f'{platform}_cookie')


def http_get(url, headers=None, proxy=None, timeout=15):
    """
    用curl发起GET请求
    返回: 响应文本
    """
    cmd = ['curl', '-s', '--compressed', '-L', url]
    if headers:
        for k, v in headers.items():
            cmd += ['-H', f'{k}: {v}']
    if proxy:
        cmd += ['--proxy', proxy]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.stdout


def http_post(url, data=None, headers=None, proxy=None, timeout=15):
    """
    用curl发起POST请求
    返回: 响应文本
    """
    cmd = ['curl', '-s', '--compressed', '-L', url, '-X', 'POST']
    if data:
        cmd += ['--data', data]
    if headers:
        for k, v in headers.items():
            cmd += ['-H', f'{k}: {v}']
    if proxy:
        cmd += ['--proxy', proxy]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.stdout
