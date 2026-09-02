"""
录制引擎 - FFmpeg拉流录制直播为本地MP4文件
"""
import subprocess
import threading
import os
import logging
from datetime import datetime

from models import db, Streamer, ActiveStream, StreamLog
from config import Config

logger = logging.getLogger(__name__)

record_lock = threading.Lock()
# pid -> (proc, streamer_id, file_path)
active_recordings = {}


def start_recording(streamer_id, source_url=None):
    """
    开始录制直播流为MP4文件
    如果没有source_url, 先检测直播状态获取流地址
    返回 (success, message)
    """
    from monitor_engine import check_streamer_live

    with record_lock:
        streamer = Streamer.query.get(streamer_id)
        if not streamer:
            return False, '博主不存在'

        # 如果已经在录制, 不重复启动
        for pid, (proc, sid, _) in active_recordings.items():
            if sid == streamer_id and proc.poll() is None:
                return False, '该博主正在录制中'

        # 获取流地址
        if not source_url:
            is_live, source_url, err = check_streamer_live(streamer)
            if not is_live or not source_url:
                return False, f'无法获取直播流: {err or "未在直播"}'

        # 创建录制目录
        record_dir = os.path.join(__import__('config').DATA_DIR, 'recordings')
        os.makedirs(record_dir, exist_ok=True)

        # 生成文件名: 博主名_平台_日期时间.mp4
        safe_name = ''.join(c for c in streamer.name if c.isalnum() or c in '_-') or f'streamer_{streamer_id}'
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'{safe_name}_{streamer.platform}_{timestamp}.mp4'
        filepath = os.path.join(record_dir, filename)

        # 根据源流URL判断是否需要加请求头
        headers = []
        if 'douyincdn' in source_url or 'douyin' in source_url:
            headers = [
                '-headers', 'Referer: https://live.douyin.com/\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n',
            ]
        elif 'yximgs' in source_url or 'kuaishou' in source_url or 'gifshow' in source_url:
            headers = [
                '-headers', 'Referer: https://live.kuaishou.com/\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n',
            ]
        elif 'huya' in source_url:
            headers = [
                '-headers', 'Referer: https://www.huya.com/\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n',
            ]
        elif 'bilivideo' in source_url or 'bilibili' in source_url:
            headers = [
                '-headers', 'Referer: https://live.bilibili.com/\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n',
            ]

        # FFmpeg命令: 拉流直接copy保存为MP4
        cmd = [
            Config.FFMPEG_PATH,
            '-hide_banner',
            '-loglevel', 'warning',
            '-rw_timeout', '10000000',
        ] + headers + [
            '-i', source_url,
            '-c:v', 'copy',
            '-c:a', 'copy',
            '-f', 'mp4',
            '-movflags', '+faststart',
            filepath
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
            active_recordings[proc.pid] = (proc, streamer_id, filepath)

            _add_log(streamer_id, 'success', 'record_start',
                     f'开始录制: {filename} (PID={proc.pid})')

            # 启动监控线程
            t = threading.Thread(
                target=_monitor_recording,
                args=(proc, streamer_id, filepath),
                daemon=True
            )
            t.start()

            logger.info(f"录制已启动 PID={proc.pid} -> {filepath}")
            return True, filepath

        except Exception as e:
            _add_log(streamer_id, 'error', 'record_start_fail',
                     f'录制启动失败: {e}')
            logger.error(f"录制启动失败: {e}")
            return False, str(e)


def stop_recording(streamer_id):
    """停止录制"""
    with record_lock:
        pid_to_stop = None
        for pid, (proc, sid, filepath) in active_recordings.items():
            if sid == streamer_id and proc.poll() is None:
                pid_to_stop = pid
                break

        if not pid_to_stop:
            return False, '该博主未在录制'

        proc, sid, filepath = active_recordings[pid_to_stop]
        try:
            # 发送q键优雅停止(让FFmpeg正常写入文件尾部)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        except Exception as e:
            logger.warning(f"停止录制异常 pid={pid_to_stop}: {e}")
            try:
                proc.kill()
            except:
                pass

        del active_recordings[pid_to_stop]

        # 获取文件大小
        size = 0
        try:
            size = os.path.getsize(filepath)
        except:
            pass

        size_str = f'{size / 1024 / 1024:.1f}MB' if size > 0 else '未知'
        _add_log(streamer_id, 'info', 'record_stop',
                 f'录制已停止: {os.path.basename(filepath)} ({size_str})')

        return True, filepath


def is_recording(streamer_id):
    """检查是否正在录制"""
    for pid, (proc, sid, _) in active_recordings.items():
        if sid == streamer_id and proc.poll() is None:
            return True
    return False


def get_recording_info(streamer_id):
    """获取录制信息"""
    for pid, (proc, sid, filepath) in active_recordings.items():
        if sid == streamer_id and proc.poll() is None:
            size = 0
            try:
                size = os.path.getsize(filepath)
            except:
                pass
            return {
                'recording': True,
                'pid': pid,
                'filepath': filepath,
                'filename': os.path.basename(filepath),
                'size_mb': round(size / 1024 / 1024, 1) if size > 0 else 0,
            }
    return {'recording': False}


def list_recordings():
    """列出所有录制文件"""
    record_dir = os.path.join(__import__('config').DATA_DIR, 'recordings')
    if not os.path.exists(record_dir):
        return []

    files = []
    for f in sorted(os.listdir(record_dir), reverse=True):
        if f.endswith('.mp4'):
            filepath = os.path.join(record_dir, f)
            size = os.path.getsize(filepath)
            mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
            # 检查是否正在录制
            recording = False
            for pid, (proc, sid, fp) in active_recordings.items():
                if fp == filepath and proc.poll() is None:
                    recording = True
                    break
            files.append({
                'filename': f,
                'size_mb': round(size / 1024 / 1024, 1),
                'created_at': mtime.strftime('%Y-%m-%d %H:%M'),
                'recording': recording,
            })
    return files


def delete_recording(filename):
    """删除录制文件"""
    record_dir = os.path.join(__import__('config').DATA_DIR, 'recordings')
    filepath = os.path.join(record_dir, filename)

    # 安全检查: 文件名不能包含路径分隔符
    if os.path.sep in filename or '..' in filename:
        return False, '非法文件名'

    if not os.path.exists(filepath):
        return False, '文件不存在'

    # 检查是否正在录制
    for pid, (proc, sid, fp) in active_recordings.items():
        if fp == filepath and proc.poll() is None:
            return False, '文件正在录制中, 无法删除'

    try:
        os.remove(filepath)
        return True, '已删除'
    except Exception as e:
        return False, str(e)


def _monitor_recording(proc, streamer_id, filepath):
    """监控录制进程, 退出时记录"""
    output_lines = []
    try:
        for line in iter(proc.stdout.readline, b''):
            text = line.decode('utf-8', errors='replace').strip()
            if text:
                output_lines.append(text)
                if len(output_lines) > 50:
                    output_lines.pop(0)
    except:
        pass

    proc.wait()
    retcode = proc.returncode

    with record_lock:
        if proc.pid in active_recordings:
            del active_recordings[proc.pid]

    size = 0
    try:
        size = os.path.getsize(filepath)
    except:
        pass
    size_str = f'{size / 1024 / 1024:.1f}MB' if size > 0 else '0MB'

    from app import app
    with app.app_context():
        if retcode != 0:
            last_err = '\n'.join(output_lines[-5:]) if output_lines else '无输出'
            _add_log(streamer_id, 'error', 'record_exit',
                     f'录制异常结束 code={retcode} ({size_str}): {last_err[:200]}')
        else:
            _add_log(streamer_id, 'info', 'record_done',
                     f'录制完成: {os.path.basename(filepath)} ({size_str})')


def _add_log(streamer_id, level, action, message):
    log = StreamLog(
        streamer_id=streamer_id,
        level=level,
        action=action,
        message=message
    )
    db.session.add(log)
    db.session.commit()
