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


def get_stream_url(streamer):
    """
    用yt-dlp获取直播流地址
    返回 (stream_url, error_message)
    """
    room_url = streamer.url or streamer.room_id

    # 根据平台标准化URL
    url = _normalize_url(streamer.platform, streamer.room_id)

    try:
        cmd = [
            Config.YTDLP_PATH,
            '--no-warnings',
            '--no-check-certificates',
            '-J',  # 输出JSON
            '--no-playlist',
            url
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            return None, f'yt-dlp错误: {result.stderr[:500]}'

        data = json.loads(result.stdout)

        # 检查是否在直播
        is_live = data.get('is_live', False)
        if not is_live:
            # 有些平台没有is_live字段, 检查formats
            formats = data.get('formats', [])
            if not formats:
                return None, '未在直播'

        # 获取最佳流地址
        # 优先取最底层的url (通常是最好的)
        formats = data.get('formats', [])
        if formats:
            # 取最后一个格式(通常是最高质量)
            best = formats[-1]
            stream_url = best.get('url')
            if not stream_url:
                # 有些流需要manifest_url
                stream_url = data.get('url') or data.get('manifest_url')
        else:
            stream_url = data.get('url') or data.get('manifest_url')

        if not stream_url:
            return None, '无法获取流地址'

        return stream_url, None

    except subprocess.TimeoutExpired:
        return None, 'yt-dlp超时(30s)'
    except json.JSONDecodeError:
        return None, 'yt-dlp返回非JSON'
    except Exception as e:
        return None, f'异常: {e}'


def check_streamer_live(streamer):
    """
    检测博主是否在直播
    返回 (is_live, stream_url, error)
    """
    stream_url, err = get_stream_url(streamer)
    if err:
        return False, None, err
    if stream_url:
        return True, stream_url, None
    return False, None, '未获取到流地址'


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
