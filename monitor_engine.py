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


def check_streamer_live(streamer):
    """
    检测博主是否在直播
    根据平台选择检测方式
    返回 (is_live, stream_url, error)
    """
    url = _normalize_url(streamer.platform, streamer.room_id)

    # 抖音: 用专用检测器(从页面提取FLV流)
    if streamer.platform == 'douyin':
        from platforms.douyin import check_douyin_live
        return check_douyin_live(url)

    # 快手: 用专用检测器(curl获取页面提取FLV流)
    if streamer.platform == 'kuaishou':
        from platforms.kuaishou import check_kuaishou_live
        return check_kuaishou_live(url)

    # 其他平台: 用yt-dlp
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
        # 抖音房间号 -> 完整URL
        return f'https://live.douyin.com/{room_id}'
    elif platform == 'kuaishou':
        # 快手需要用户主页URL或分享链接
        return f'https://www.kuaishou.com/profile/{room_id}'
    elif platform == 'bilibili':
        return f'https://live.bilibili.com/{room_id}'
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


def _monitor_loop():
    """监控主循环"""
    from app import app
    while monitor_running:
        try:
            with app.app_context():
                _check_all_streamers()
        except Exception as e:
            logger.error(f"监控循环异常: {e}")
        # 等待下一轮
        for _ in range(Config.MONITOR_INTERVAL):
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
        # 已在推流, 检查源流是否还活着
        from stream_engine import get_ffmpeg_status
        status = get_ffmpeg_status(active.id)
        if status == 'dead' or status == 'error':
            # FFmpeg挂了, 尝试重启
            _add_log(streamer.id, 'warning', 'ffmpeg_dead',
                     f'检测到FFmpeg进程异常({status}), 尝试重新拉流')
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
            # 自动启动推流
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
