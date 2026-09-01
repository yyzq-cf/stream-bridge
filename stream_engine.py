"""
推流引擎 - 管理FFmpeg子进程拉流推流到YouTube
"""
import subprocess
import threading
import time
import logging
import os
from datetime import datetime

from models import db, ActiveStream, StreamLog
from config import Config

logger = logging.getLogger(__name__)

# 全局进程锁
ffmpeg_lock = threading.Lock()
# pid -> ActiveStream.id 映射
active_processes = {}


def start_ffmpeg_push(active_stream_id):
    """启动FFmpeg推流进程"""
    with ffmpeg_lock:
        as_record = ActiveStream.query.get(active_stream_id)
        if not as_record:
            logger.error(f"ActiveStream {active_stream_id} not found")
            return False

        source_url = as_record.source_url
        rtmp_url = as_record.rtmp_url
        stream_key = as_record.stream_key

        if not source_url or not rtmp_url or not stream_key:
            as_record.status = 'error'
            as_record.error_message = '缺少推流参数(source_url/rtmp_url/stream_key)'
            db.session.commit()
            return False

        # 如果已有进程在跑先杀掉
        if as_record.ffmpeg_pid and as_record.ffmpeg_pid in active_processes:
            stop_ffmpeg_push(active_stream_id, force=True)

        # FFmpeg命令: 拉流转推, 不转码视频, 音频转AAC(YouTube要求AAC)
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

        cmd = [
            Config.FFMPEG_PATH,
            '-hide_banner',
            '-loglevel', 'warning',
            '-rw_timeout', '10000000',  # 10秒超时
        ] + headers + [
            '-i', source_url,
            '-c:v', Config.DEFAULT_VIDEO_CODEC,        # 视频直接copy
            '-c:a', Config.DEFAULT_AUDIO_CODEC,        # 音频转AAC
            '-b:a', Config.DEFAULT_AUDIO_BITRATE,
            '-ar', '44100',
            '-f', 'flv',
            '-flvflags', 'no_duration_filesize',
            f'{rtmp_url}/{stream_key}'
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
            as_record.ffmpeg_pid = proc.pid
            as_record.status = 'running'
            as_record.error_message = None
            as_record.started_at = datetime.utcnow()
            db.session.commit()

            active_processes[proc.pid] = (proc, active_stream_id)
            _add_log(as_record.streamer_id, 'success', 'push_start',
                     f'FFmpeg推流已启动 PID={proc.pid}')

            # 启动监控线程
            t = threading.Thread(
                target=_monitor_ffmpeg_process,
                args=(proc, active_stream_id),
                daemon=True
            )
            t.start()

            logger.info(f"FFmpeg started PID={proc.pid} for ActiveStream {active_stream_id}")
            return True

        except Exception as e:
            as_record.status = 'error'
            as_record.error_message = str(e)
            db.session.commit()
            _add_log(as_record.streamer_id, 'error', 'push_start_fail',
                     f'FFmpeg启动失败: {e}')
            logger.error(f"Failed to start FFmpeg: {e}")
            return False


def stop_ffmpeg_push(active_stream_id, force=False):
    """停止FFmpeg推流"""
    with ffmpeg_lock:
        as_record = ActiveStream.query.get(active_stream_id)
        if not as_record:
            return False

        pid = as_record.ffmpeg_pid
        if not pid or pid not in active_processes:
            as_record.status = 'stopped'
            as_record.stopped_at = datetime.utcnow()
            db.session.commit()
            return True

        proc, _ = active_processes[pid]
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        except Exception as e:
            logger.warning(f"Error killing FFmpeg pid={pid}: {e}")
            try:
                proc.kill()
            except:
                pass

        del active_processes[pid]

        as_record.status = 'stopped'
        as_record.stopped_at = datetime.utcnow()
        db.session.commit()
        _add_log(as_record.streamer_id, 'info', 'push_stop',
                 'FFmpeg推流已停止')
        return True


def _monitor_ffmpeg_process(proc, active_stream_id):
    """监控FFmpeg进程, 退出时记录"""
    # 读取stderr输出(合并到了stdout)
    output_lines = []
    try:
        for line in iter(proc.stdout.readline, b''):
            text = line.decode('utf-8', errors='replace').strip()
            if text:
                output_lines.append(text)
                if len(output_lines) > 100:
                    output_lines.pop(0)
    except:
        pass

    proc.wait()
    retcode = proc.returncode

    # 清理映射
    with ffmpeg_lock:
        if proc.pid in active_processes:
            del active_processes[proc.pid]

    # 更新数据库
    from app import app
    with app.app_context():
        as_record = ActiveStream.query.get(active_stream_id)
        if as_record:
            as_record.status = 'error' if retcode != 0 else 'stopped'
            as_record.stopped_at = datetime.utcnow()
            if retcode != 0:
                last_err = '\n'.join(output_lines[-10:])
                as_record.error_message = f'FFmpeg exit code={retcode}\n{last_err}'
            db.session.commit()

            if retcode != 0:
                _add_log(as_record.streamer_id, 'error', 'ffmpeg_exit',
                         f'FFmpeg异常退出 code={retcode}: {last_err if output_lines else "无输出"}')
            else:
                _add_log(as_record.streamer_id, 'info', 'ffmpeg_exit',
                         'FFmpeg正常退出')


def get_ffmpeg_status(active_stream_id):
    """获取FFmpeg运行状态"""
    as_record = ActiveStream.query.get(active_stream_id)
    if not as_record:
        return 'none'
    if as_record.status == 'running' and as_record.ffmpeg_pid:
        if as_record.ffmpeg_pid in active_processes:
            return 'running'
        else:
            return 'dead'
    return as_record.status


def _add_log(streamer_id, level, action, message):
    log = StreamLog(
        streamer_id=streamer_id,
        level=level,
        action=action,
        message=message
    )
    db.session.add(log)
    db.session.commit()


def get_stream_stats(active_stream_id):
    """获取FFmpeg推流的实时码率统计 - 通过/proc/{pid}/io读取字节数"""
    as_record = ActiveStream.query.get(active_stream_id)
    if not as_record or as_record.status != 'running':
        return None
    
    pid = as_record.ffmpeg_pid
    if not pid:
        return None
    
    try:
        import time as _time
        import os
        
        def read_io():
            """读取进程IO字节数(rchar=总读取字节含网络, wchar=总写入字节)"""
            r, w = 0, 0
            try:
                with open(f'/proc/{pid}/io', 'r') as f:
                    for line in f:
                        if line.startswith('rchar:'):
                            r = int(line.split()[1])
                        elif line.startswith('wchar:'):
                            w = int(line.split()[1])
            except:
                pass
            return r, w
        
        # 检查进程是否还活着
        if not os.path.exists(f'/proc/{pid}'):
            return None
        
        r1, w1 = read_io()
        _time.sleep(1.5)
        r2, w2 = read_io()
        
        read_bps = (r2 - r1) * 8 // 1.5
        write_bps = (w2 - w1) * 8 // 1.5
        
        def fmt(bps):
            if bps > 1_000_000:
                return f'{bps/1_000_000:.1f} Mbps'
            elif bps > 1_000:
                return f'{bps/1_000:.0f} kbps'
            else:
                return f'{bps} bps'
        
        # 运行时长
        uptime = '-'
        if as_record.started_at:
            delta = datetime.utcnow() - as_record.started_at
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime = f'{hours:02d}:{minutes:02d}:{seconds:02d}'
        
        return {
            'input_bitrate': fmt(read_bps),
            'output_bitrate': fmt(write_bps),
            'input_kbps': round(read_bps / 1000, 1),
            'output_kbps': round(write_bps / 1000, 1),
            'uptime': uptime,
        }
    except Exception as e:
        logger.debug(f"获取码率失败: {e}")
        return None
