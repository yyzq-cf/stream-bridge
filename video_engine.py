"""
视频文件推流引擎 - 用FFmpeg将视频文件推流到RTMP
"""
import subprocess
import threading
import logging
import os
import json
import random
from datetime import datetime

from models import db, VideoPush, StreamLog, PushTarget
from config import Config

logger = logging.getLogger(__name__)

video_lock = threading.Lock()
# 全局停止标志: task_id -> bool
stop_flags = {}
# 当前播放文件: task_id -> filename
current_files = {}
# 播放控制: task_id -> {'mode': 'sequential'|'random', 'action': 'next'|'prev'|None}
play_controls = {}


def _build_ffmpeg_cmd(source, target, loop=False, is_url=False):
    """构建FFmpeg推流命令, target可以是ORM对象或dict"""
    rtmp_url = target['rtmp_url'] if isinstance(target, dict) else target.rtmp_url
    stream_key = target['stream_key'] if isinstance(target, dict) else target.stream_key
    headers = []
    if 'douyincdn' in source or 'douyin' in source:
        headers = [
            '-headers', 'Referer: https://live.douyin.com/\r\nUser-Agent: Mozilla/5.0\r\n',
        ]
    elif 'yximgs' in source or 'kuaishou' in source or 'gifshow' in source:
        headers = [
            '-headers', 'Referer: https://live.kuaishou.com/\r\nUser-Agent: Mozilla/5.0\r\n',
        ]

    cmd = [
        Config.FFMPEG_PATH,
        '-hide_banner',
        '-loglevel', 'warning',
    ] + headers

    if loop:
        cmd += ['-stream_loop', '-1']

    cmd += [
        '-re',
        '-i', source,
        '-c:v', Config.DEFAULT_VIDEO_CODEC,
        '-c:a', Config.DEFAULT_AUDIO_CODEC,
        '-b:a', Config.DEFAULT_AUDIO_BITRATE,
        '-ar', '44100',
        '-f', 'flv',
        '-flvflags', 'no_duration_filesize',
        f'{rtmp_url}/{stream_key}'
    ]
    return cmd


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

        # 清除停止标志
        stop_flags.pop(task_id, None)
        play_controls.pop(task_id, None)

        # 目录模式: 逐个文件播放
        if task.source_type == 'directory' and task.file_path:
            try:
                files = json.loads(task.file_path)
            except:
                files = []
            if not files:
                return False, '目录中没有视频文件'

            task.status = 'running'
            task.started_at = datetime.utcnow()
            task.error_message = None
            db.session.commit()

            # 初始化播放控制: 默认顺序播放
            play_controls[task_id] = {'mode': 'sequential', 'action': None}

            # 提前取出target属性, 避免子线程跨session访问
            tgt = {'rtmp_url': target.rtmp_url, 'stream_key': target.stream_key, 'name': target.name}
            t = threading.Thread(
                target=_directory_play_loop,
                args=(task_id, files, tgt, task.loop),
                daemon=True
            )
            t.start()

            logger.info(f"目录循环推流已启动 task={task.name} files={len(files)}")
            return True, f'推流已启动 ({len(files)}个文件)'

        # 单文件/URL模式
        source = task.source_url if task.source_type == 'url' and task.source_url else task.file_path
        if not source:
            return False, '未配置视频源'

        cmd = _build_ffmpeg_cmd(source, target, task.loop)

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            task.ffmpeg_pid = proc.pid
            task.status = 'running'
            task.started_at = datetime.utcnow()
            task.error_message = None
            db.session.commit()

            logger.info(f"视频推流已启动 PID={proc.pid} task={task.name}")

            t = threading.Thread(
                target=_monitor_single_process,
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


def _directory_play_loop(task_id, files, target, loop):
    """目录模式: 逐个文件播放, 支持顺序/随机/上一个/下一个"""
    from app import app

    current_idx = 0
    total_rounds = 0

    while not stop_flags.get(task_id, False):
        total_rounds += 1
        ordered_files = list(files)  # 默认顺序

        # 随机模式: 打乱顺序
        ctrl = play_controls.get(task_id, {})
        if ctrl.get('mode') == 'random':
            ordered_files = random.sample(files, len(files))

        idx = 0
        while idx < len(ordered_files) and not stop_flags.get(task_id, False):
            # 检查是否有上一个/下一个请求
            ctrl = play_controls.get(task_id, {})
            action = ctrl.get('action')

            if action == 'next':
                ctrl['action'] = None
                idx += 1
                continue
            elif action == 'prev':
                ctrl['action'] = None
                idx = max(0, idx - 1)
                # 不continue, 重新播放当前(上一个)文件

            if idx >= len(ordered_files):
                break

            f_path = ordered_files[idx]

            with app.app_context():
                task = VideoPush.query.get(task_id)
                if not task or task.status != 'running':
                    return

            # 逐个文件推流
            cmd = _build_ffmpeg_cmd(f_path, target, loop=False)

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                )

                with app.app_context():
                    task = VideoPush.query.get(task_id)
                    if task:
                        task.ffmpeg_pid = proc.pid
                        db.session.commit()

                current_files[task_id] = os.path.basename(f_path)
                file_idx_display = idx + 1
                total_files = len(ordered_files)
                logger.info(f"目录推流: [{file_idx_display}/{total_files}] {os.path.basename(f_path)} PID={proc.pid}")

                # 等待播放完成或被控制中断
                proc.wait()
                retcode = proc.returncode

                if retcode != 0 and retcode != 255:
                    if not stop_flags.get(task_id, False):
                        # 检查是不是因为切换文件被杀的
                        ctrl = play_controls.get(task_id, {})
                        if ctrl.get('action') in ('next', 'prev'):
                            # 是用户主动切换, 不算错误
                            pass
                        else:
                            with app.app_context():
                                task = VideoPush.query.get(task_id)
                                if task:
                                    task.status = 'error'
                                    task.error_message = f'FFmpeg退出 code={retcode} (文件: {os.path.basename(f_path)})'
                                    task.stopped_at = datetime.utcnow()
                                    db.session.commit()
                            logger.warning(f"目录推流异常退出 code={retcode} file={f_path}")
                            return

            except Exception as e:
                if not stop_flags.get(task_id, False):
                    logger.error(f"目录推流异常: {e}")
                    with app.app_context():
                        task = VideoPush.query.get(task_id)
                        if task:
                            task.status = 'error'
                            task.error_message = str(e)
                            task.stopped_at = datetime.utcnow()
                            db.session.commit()
                    return

            idx += 1

        # 非循环模式: 播完一轮就停
        if not loop:
            break

    # 正常结束
    with app.app_context():
        task = VideoPush.query.get(task_id)
        if task:
            task.status = 'stopped'
            task.stopped_at = datetime.utcnow()
            task.ffmpeg_pid = None
            db.session.commit()
    current_files.pop(task_id, None)
    play_controls.pop(task_id, None)
    logger.info(f"目录推流结束 task_id={task_id} 共播放{total_rounds}轮")


def control_video_push(task_id, action, mode=None):
    """控制目录推流: 上一个/下一个/切换顺序随机"""
    if task_id not in play_controls:
        return False, '该任务不支持播放控制(仅目录模式)'

    ctrl = play_controls[task_id]

    if mode:
        ctrl['mode'] = mode

    if action in ('next', 'prev'):
        ctrl['action'] = action
        # 杀掉当前ffmpeg进程, 让循环跳到下/上一个文件
        with video_lock:
            task = VideoPush.query.get(task_id)
            if task and task.ffmpeg_pid:
                try:
                    os.kill(task.ffmpeg_pid, 15)
                except:
                    pass

    return True, f'已切换: {action or mode}'


def stop_video_push(task_id):
    """停止视频推流"""
    with video_lock:
        task = VideoPush.query.get(task_id)
        if not task:
            return

        stop_flags[task_id] = True

        pid = task.ffmpeg_pid
        if pid:
            try:
                os.kill(pid, 15)
                import time
                time.sleep(1)
                if os.path.exists(f'/proc/{pid}'):
                    os.kill(pid, 9)
            except:
                pass

        task.status = 'stopped'
        task.stopped_at = datetime.utcnow()
        task.ffmpeg_pid = None
        db.session.commit()
        logger.info(f"视频推流已停止 task={task.name}")


def _monitor_single_process(proc, task_id):
    """监控单文件/URL模式的FFmpeg进程"""
    from app import app

    proc.wait()
    retcode = proc.returncode

    with app.app_context():
        task = VideoPush.query.get(task_id)
        if task:
            if retcode == 0 or retcode == 255:
                task.status = 'stopped'
                logger.info(f"视频推流正常结束 task={task.name}")
            else:
                task.status = 'error'
                task.error_message = f'FFmpeg退出 code={retcode}'
                logger.warning(f"视频推流异常退出 task={task.name} code={retcode}")
            task.stopped_at = datetime.utcnow()
            db.session.commit()
