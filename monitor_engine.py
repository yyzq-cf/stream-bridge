"""
监控引擎 - 轮询检测各平台博主开播状态, 开播自动推流
"""
import subprocess
import json
import logging
import threading
import time
import re
from datetime import datetime

from models import db, Streamer, ActiveStream, StreamLog, PushTarget
from config import Config

logger = logging.getLogger(__name__)

monitor_running = False
monitor_thread = None
monitor_interval = Config.MONITOR_INTERVAL  # 可动态修改


def check_streamer_live(streamer):
    """
    检测博主是否在直播
    根据平台选择检测方式
    返回 (is_live, stream_url, error)
    """
    url = _normalize_url(streamer.platform, streamer.room_id)

    # 各平台专用检测器(API方式)
    platform_checkers = {
        'douyin': 'platforms.douyin:check_douyin_live',
        'kuaishou': 'platforms.kuaishou:check_kuaishou_live',
        'bilibili': 'platforms.bilibili:check_bilibili_live',
        'huya': 'platforms.huya:check_huya_live',
        'douyu': 'platforms.douyu:check_douyu_live',
        'yy': 'platforms.yy:check_yy_live',
        'youtube': 'platforms.youtube:check_youtube_live',
    }

    if streamer.platform in platform_checkers:
        module_path, func_name = platform_checkers[streamer.platform].split(':')
        import importlib
        mod = importlib.import_module(module_path)
        check_fn = getattr(mod, func_name)
        return check_fn(url)

    # 其他平台(twitch/custom): 用yt-dlp
    return _check_with_ytdlp(url)


def _check_with_ytdlp(url):
    """用yt-dlp检测直播流"""
    try:
        cmd = [
            Config.YTDLP_PATH,
            '--no-warnings',
            '--no-check-certificates',
            '-J',
            '--no-playlist',
            url
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )

        if result.returncode != 0:
            return False, None, f'yt-dlp: {result.stderr[:200]}'

        data = json.loads(result.stdout)

        is_live = data.get('is_live', False)
        formats = data.get('formats', [])

        if not is_live and not formats:
            return False, None, None  # 未开播

        # 获取流地址
        stream_url = None
        if formats:
            stream_url = formats[-1].get('url')
        if not stream_url:
            stream_url = data.get('url') or data.get('manifest_url')

        if stream_url:
            return True, stream_url, None
        return False, None, '无法获取流地址'

    except subprocess.TimeoutExpired:
        return False, None, 'yt-dlp超时'
    except json.JSONDecodeError:
        return False, None, 'yt-dlp返回非JSON'
    except Exception as e:
        return False, None, f'异常: {e}'


def _normalize_url(platform, room_id):
    """根据平台标准化URL"""
    room_id = room_id.strip()
    # 如果已经是完整URL直接返回
    if room_id.startswith('http'):
        return room_id

    if platform == 'douyin':
        return f'https://live.douyin.com/{room_id}'
    elif platform == 'kuaishou':
        return f'https://live.kuaishou.com/{room_id}'
    elif platform == 'bilibili':
        return f'https://live.bilibili.com/{room_id}'
    elif platform == 'huya':
        return f'https://www.huya.com/{room_id}'
    elif platform == 'douyu':
        return f'https://www.douyu.com/{room_id}'
    elif platform == 'yy':
        return f'https://www.yy.com/{room_id}'
    elif platform == 'twitch':
        return f'https://www.twitch.tv/{room_id}'
    elif platform == 'youtube':
        return f'https://www.youtube.com/watch?v={room_id}'
    else:
        return room_id


def start_monitor():
    """启动监控线程"""
    global monitor_running, monitor_thread
    if monitor_running:
        return
    monitor_running = True
    monitor_thread = threading.Thread(target=_monitor_loop, daemon=True)
    monitor_thread.start()
    logger.info("监控引擎已启动")


def stop_monitor():
    """停止监控"""
    global monitor_running
    monitor_running = False


def set_interval(seconds):
    """动态修改监控间隔"""
    global monitor_interval
    monitor_interval = max(10, int(seconds))
    logger.info(f"监控间隔已修改为 {monitor_interval}秒")
    return monitor_interval


def get_interval():
    """获取当前监控间隔"""
    return monitor_interval


def _monitor_loop():
    """监控主循环"""
    from app import app
    while monitor_running:
        try:
            with app.app_context():
                _check_all_streamers()
        except Exception as e:
            logger.error(f"监控循环异常: {e}")
        # 等待下一轮(用全局变量,可动态修改)
        interval = monitor_interval
        for _ in range(interval):
            if not monitor_running:
                break
            time.sleep(1)


def _check_all_streamers():
    """检查所有正在监控的博主"""
    streamers = Streamer.query.filter_by(is_monitoring=True).all()
    for streamer in streamers:
        try:
            _process_streamer(streamer)
        except Exception as e:
            logger.error(f"检查博主{streamer.name}异常: {e}")
            _add_log(streamer.id, 'error', 'check_fail', f'检查异常: {e}')

        streamer.last_checked = datetime.utcnow()
        db.session.commit()


def _process_streamer(streamer):
    """处理单个博主的检测和推流"""
    # 如果已经有活跃推流在跑, 检查是否需要停止
    active = ActiveStream.query.filter_by(
        streamer_id=streamer.id
    ).filter(
        ActiveStream.status.in_(['running', 'starting'])
    ).first()

    if active:
        # 已在推流, 检查ffmpeg进程是否还活着
        import os
        pid_alive = False
        if active.ffmpeg_pid:
            try:
                os.kill(active.ffmpeg_pid, 0)
                pid_alive = True
            except:
                pid_alive = False
        
        if not pid_alive and active.status == 'running':
            # FFmpeg挂了, 尝试重启
            _add_log(streamer.id, 'warning', 'ffmpeg_dead',
                     f'检测到FFmpeg进程异常(PID已退出), 尝试重新拉流')
            from stream_engine import stop_ffmpeg_push, start_ffmpeg_push
            stop_ffmpeg_push(active.id, force=True)
            # 重新检测并推流
            is_live, stream_url, err = check_streamer_live(streamer)
            if is_live and stream_url:
                active.source_url = stream_url
                active.status = 'starting'
                active.error_message = None
                db.session.commit()
                from stream_engine import start_ffmpeg_push
                start_ffmpeg_push(active.id)
            else:
                streamer.is_live = False
                db.session.commit()
                if err:
                    _add_log(streamer.id, 'info', 'stream_end',
                             f'直播已结束: {err}')
        return

    # 没有活跃推流, 检测是否开播
    is_live, stream_url, err = check_streamer_live(streamer)

    if is_live and stream_url:
        if not streamer.is_live:
            _add_log(streamer.id, 'success', 'live_detected',
                     f'检测到开播! 准备自动推流')
            streamer.is_live = True
            streamer.last_live_at = datetime.utcnow()
            db.session.commit()
        # 在直播就自动推流(新开播或之前推流失败都会触发)
        _auto_start_push(streamer, stream_url)
    else:
        if streamer.is_live:
            streamer.is_live = False
            db.session.commit()
            _add_log(streamer.id, 'info', 'stream_end',
                     f'直播已结束: {err or "未在直播"}')


def _auto_start_push(streamer, source_url):
    """自动启动推流到绑定的推流目标"""
    target = PushTarget.query.get(streamer.push_target_id) if streamer.push_target_id else None
    if not target:
        _add_log(streamer.id, 'warning', 'no_target',
                 '该博主未绑定推流目标, 跳过自动推流')
        return

    try:
        active = ActiveStream(
            streamer_id=streamer.id,
            push_target_id=target.id,
            rtmp_url=target.rtmp_url,
            stream_key=target.stream_key,
            source_url=source_url,
            status='starting'
        )
        db.session.add(active)
        db.session.commit()

        from stream_engine import start_ffmpeg_push
        start_ffmpeg_push(active.id)
        _add_log(streamer.id, 'success', 'push_started',
                 f'已自动推流 → {target.name}')

    except Exception as e:
        _add_log(streamer.id, 'error', 'auto_push_fail',
                 f'自动推流失败: {e}')
        logger.error(f"自动推流失败 streamer={streamer.name}: {e}")


def _add_log(streamer_id, level, action, message):
    log = StreamLog(
        streamer_id=streamer_id,
        level=level,
        action=action,
        message=message
    )
    db.session.add(log)
    db.session.commit()
