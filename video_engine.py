"""
视频文件推流引擎 - 用FFmpeg将视频文件推流到RTMP
"""
import subprocess
import threading
import logging
import os
from datetime import datetime

from models import db, VideoPush, StreamLog, PushTarget
from config import Config

logger = logging.getLogger(__name__)

video_lock = threading.Lock()


def start_video_push(task_id):
    """启动视频文件推流"""
    with video_lock:
        task = VideoPush.query.get(task_id)
        if not task:
            return False, '任务不存在'

        if task.status == 'running':
            return False, '任务已在运行'

        target = task.push_target
        if not target:
            return False, '推流目标不存在'

        # 源地址: 在线URL或本地文件
        source = task.source_url if task.source_type == 'url' and task.source_url else task.file_path

        # 构建FFmpeg命令
        base_cmd = [
            Config.FFMPEG_PATH,
            '-hide_banner',
            '-loglevel', 'warning',
        ]

        # 循环推流(仅本地文件支持循环)
        if task.loop and task.source_type == 'file':
            base_cmd += ['-stream_loop', '-1']

        base_cmd += [
            '-re',  # 按原始帧率读取
            '-i', source,
            '-c:v', Config.DEFAULT_VIDEO_CODEC,
            '-c:a', Config.DEFAULT_AUDIO_CODEC,
            '-b:a', Config.DEFAULT_AUDIO_BITRATE,
            '-ar', '44100',
            '-f', 'flv',
            '-flvflags', 'no_duration_filesize',
            f'{target.rtmp_url}/{target.stream_key}'
        ]

        cmd = base_cmd

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
            task.ffmpeg_pid = proc.pid
            task.status = 'running'
            task.started_at = datetime.utcnow()
            task.error_message = None
            db.session.commit()

            logger.info(f"视频推流已启动 PID={proc.pid} task={task.name}")

            # 监控线程
            t = threading.Thread(
                target=_monitor_video_process,
                args=(proc, task_id),
                daemon=True
            )
            t.start()

            return True, f'推流已启动 PID={proc.pid}'

        except Exception as e:
            task.status = 'error'
            task.error_message = str(e)
            db.session.commit()
            logger.error(f"视频推流启动失败: {e}")
            return False, f'启动失败: {e}'


def stop_video_push(task_id):
    """停止视频推流"""
    with video_lock:
        task = VideoPush.query.get(task_id)
        if not task:
            return

        pid = task.ffmpeg_pid
        if pid:
            try:
                os.kill(pid, 15)  # SIGTERM
                import time
                time.sleep(1)
                # 如果还活着,强杀
                if os.path.exists(f'/proc/{pid}'):
                    os.kill(pid, 9)  # SIGKILL
            except:
                pass

        task.status = 'stopped'
        task.stopped_at = datetime.utcnow()
        db.session.commit()
        logger.info(f"视频推流已停止 task={task.name}")


def _monitor_video_process(proc, task_id):
    """监控视频推流FFmpeg进程"""
    from app import app

    proc.wait()
    retcode = proc.returncode

    with app.app_context():
        task = VideoPush.query.get(task_id)
        if task:
            if retcode == 0:
                task.status = 'stopped'
                logger.info(f"视频推流正常结束 task={task.name}")
            else:
                task.status = 'error'
                task.error_message = f'FFmpeg退出 code={retcode}'
                logger.warning(f"视频推流异常退出 task={task.name} code={retcode}")
            task.stopped_at = datetime.utcnow()
            db.session.commit()
