"""
StreamBridge 主应用
Flask Web界面 + 用户管理 + 直播监控推流(RTMP直推)
"""
import os
import logging
from datetime import datetime

from flask import (Flask, render_template, redirect, url_for, request,
                   flash, jsonify, session)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)

from config import Config
from models import db, User, Streamer, PushTarget, ActiveStream, StreamLog, Setting, VideoPush
from monitor_engine import start_monitor, stop_monitor
import stream_engine
import record_engine

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

# ─── 权限检查装饰器 ───
def admin_required(f):
    """需要管理员权限"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            if request.is_json or request.headers.get('Content-Type') == 'application/json':
                return jsonify({'ok': False, 'message': '需要管理员权限'}), 403
            flash('需要管理员权限', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


# ─── 登录限流(防暴力破解) ───
from collections import defaultdict
import time as _time

login_attempts = defaultdict(list)  # IP -> [timestamp, ...]
MAX_ATTEMPTS = 5        # 最多尝试次数
LOCKOUT_TIME = 300      # 锁定时间(秒) = 5分钟
ATTEMPT_WINDOW = 600    # 尝试窗口(秒) = 10分钟


def check_login_rate(ip):
    """检查IP是否被锁定, 返回 (allowed, remaining_lock_time)"""
    now = _time.time()
    # 清理过期记录
    login_attempts[ip] = [t for t in login_attempts[ip] if now - t < ATTEMPT_WINDOW]
    
    if len(login_attempts[ip]) >= MAX_ATTEMPTS:
        first_attempt = login_attempts[ip][0]
        lock_remaining = LOCKOUT_TIME - (now - first_attempt)
        if lock_remaining > 0:
            return False, int(lock_remaining)
        else:
            # 锁定过期, 清理
            login_attempts[ip] = []
    return True, 0


def record_failed_login(ip):
    """记录失败登录"""
    now = _time.time()
    login_attempts[ip].append(now)


def clear_login_attempts(ip):
    """登录成功后清理"""
    if ip in login_attempts:
        del login_attempts[ip]


def get_client_ip():
    """获取客户端IP"""
    return request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr or '').split(',')[0].strip()

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = '请先登录'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def init_db():
    with app.app_context():
        db.create_all()
        if not User.query.filter_by(username=Config.DEFAULT_ADMIN_USER).first():
            admin = User(username=Config.DEFAULT_ADMIN_USER, is_admin=True)
            admin.set_password(Config.DEFAULT_ADMIN_PASS)
            db.session.add(admin)
            db.session.commit()
            logger.info(f"默认管理员已创建: {Config.DEFAULT_ADMIN_USER}")


# ─── 认证路由 ───

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    client_ip = get_client_ip()
    
    if request.method == 'POST':
        # 检查是否被锁定
        allowed, lock_time = check_login_rate(client_ip)
        if not allowed:
            flash(f'登录尝试过多, 请 {lock_time} 秒后再试', 'error')
            return render_template('login.html', locked=True, lock_time=lock_time)
        
        # ─── 2FA验证阶段 ───
        totp_code = request.form.get('totp_code', '').strip()
        if totp_code:
            # 第二步: 验证TOTP
            pending_user = session.pop('pending_2fa_user', None)
            if not pending_user:
                flash('2FA会话已过期, 请重新登录', 'error')
                return redirect(url_for('login'))
            user = User.query.get(pending_user)
            if not user:
                flash('用户不存在', 'error')
                return redirect(url_for('login'))
            if user.verify_totp(totp_code):
                clear_login_attempts(client_ip)
                login_user(user)
                logger.info(f"2FA登录成功: {user.username} IP={client_ip}")
                return redirect(url_for('dashboard'))
            else:
                record_failed_login(client_ip)
                flash('验证码错误, 请重试', 'error')
                return render_template('login.html', totp_required=True,
                                       username=user.username)
        
        # ─── 第一步: 用户名+密码 ───
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            clear_login_attempts(client_ip)
            # 检查是否启用了2FA
            if user.totp_enabled:
                # 存入session, 等待第二步验证
                session['pending_2fa_user'] = user.id
                logger.info(f"密码验证通过, 等待2FA: {username} IP={client_ip}")
                return render_template('login.html', totp_required=True,
                                       username=username)
            else:
                login_user(user)
                logger.info(f"登录成功(无2FA): {username} IP={client_ip}")
                return redirect(url_for('dashboard'))
        else:
            record_failed_login(client_ip)
            remaining = MAX_ATTEMPTS - len(login_attempts[client_ip])
            if remaining > 0:
                flash(f'用户名或密码错误, 剩余尝试次数: {remaining}', 'error')
            else:
                flash(f'登录失败次数过多, 账户已锁定 {LOCKOUT_TIME} 秒', 'error')
            logger.warning(f"登录失败: username={username} IP={client_ip} attempts={len(login_attempts[client_ip])}")
    
    # 检查是否已被锁定(GET请求时)
    allowed, lock_time = check_login_rate(client_ip)
    locked = not allowed
    return render_template('login.html', locked=locked, lock_time=lock_time)


@app.route('/login/cancel-2fa')
def cancel_2fa():
    """取消2FA登录流程"""
    session.pop('pending_2fa_user', None)
    return redirect(url_for('login'))


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# ─── 主页面 ───

@app.route('/')
@login_required
def dashboard():
    streamers = Streamer.query.order_by(Streamer.created_at.desc()).all()
    active_streams = ActiveStream.query.filter(
        ActiveStream.status.in_(['running', 'starting'])
    ).all()
    active_video_pushes = VideoPush.query.filter(
        VideoPush.status == 'running'
    ).all()
    targets = PushTarget.query.all()
    recent_logs = StreamLog.query.order_by(
        StreamLog.timestamp.desc()
    ).limit(20).all()
    # 合并推流总数
    total_active = len(active_streams) + len(active_video_pushes)
    return render_template('dashboard.html',
                           streamers=streamers,
                           active_streams=active_streams,
                           active_video_pushes=active_video_pushes,
                           total_active=total_active,
                           targets=targets,
                           recent_logs=recent_logs)


# ─── 博主管理 ───

@app.route('/streamers')
@login_required
def streamers():
    streamers = Streamer.query.order_by(Streamer.created_at.desc()).all()
    targets = PushTarget.query.all()
    return render_template('streamers.html', streamers=streamers, targets=targets)


@app.route('/streamers/add', methods=['POST'])
@login_required
def add_streamer():
    platform = request.form.get('platform', '').strip()
    name = request.form.get('name', '').strip()
    room_id = request.form.get('room_id', '').strip()
    monitor = request.form.get('monitor', 'on') == 'on'
    target_id = request.form.get('push_target_id', '').strip()
    target_id = int(target_id) if target_id else None

    if not platform or not name or not room_id:
        flash('平台、名称、房间号不能为空', 'error')
        return redirect(url_for('streamers'))

    streamer = Streamer(
        platform=platform,
        name=name,
        room_id=room_id,
        url=room_id if room_id.startswith('http') else None,
        is_monitoring=monitor,
        push_target_id=target_id
    )
    db.session.add(streamer)
    db.session.commit()
    _log(streamer.id, 'info', 'add', f'添加博主: {name} ({platform})')
    flash(f'博主 {name} 已添加', 'success')
    return redirect(url_for('streamers'))


@app.route('/streamers/<int:sid>/delete', methods=['POST'])
@login_required
def delete_streamer(sid):
    streamer = Streamer.query.get_or_404(sid)
    name = streamer.name
    active = ActiveStream.query.filter_by(
        streamer_id=sid, status='running'
    ).first()
    if active:
        stream_engine.stop_ffmpeg_push(active.id, force=True)
    db.session.delete(streamer)
    db.session.commit()
    return jsonify({'ok': True, 'message': f'博主 {name} 已删除'})


@app.route('/streamers/<int:sid>/toggle', methods=['POST'])
@login_required
def toggle_monitor(sid):
    streamer = Streamer.query.get_or_404(sid)
    streamer.is_monitoring = not streamer.is_monitoring
    db.session.commit()
    action = '开启监控' if streamer.is_monitoring else '关闭监控'
    _log(sid, 'info', 'toggle_monitor', f'{action}: {streamer.name}')
    return jsonify({'ok': True, 'monitoring': streamer.is_monitoring})


@app.route('/streamers/<int:sid>/update-target', methods=['POST'])
@login_required
def update_streamer_target(sid):
    """更新博主绑定的推流目标"""
    streamer = Streamer.query.get_or_404(sid)
    target_id = request.form.get('push_target_id', '').strip()
    streamer.push_target_id = int(target_id) if target_id else None
    db.session.commit()
    return jsonify({'ok': True, 'message': '推流目标已更新'})


@app.route('/streamers/<int:sid>/edit', methods=['POST'])
@login_required
def edit_streamer(sid):
    """编辑博主信息"""
    streamer = Streamer.query.get_or_404(sid)
    streamer.platform = request.form.get('platform', streamer.platform).strip()
    streamer.name = request.form.get('name', streamer.name).strip()
    streamer.room_id = request.form.get('room_id', streamer.room_id).strip()
    streamer.url = streamer.room_id if streamer.room_id.startswith('http') else None
    target_id = request.form.get('push_target_id', '').strip()
    streamer.push_target_id = int(target_id) if target_id else None
    db.session.commit()
    _log(streamer.id, 'info', 'edit', f'编辑博主: {streamer.name}')
    flash(f'博主 {streamer.name} 已更新', 'success')
    return redirect(url_for('streamers'))


@app.route('/streamers/<int:sid>/check', methods=['POST'])
@login_required
def check_now(sid):
    from monitor_engine import check_streamer_live
    streamer = Streamer.query.get_or_404(sid)
    is_live, stream_url, err = check_streamer_live(streamer)
    streamer.last_checked = datetime.utcnow()
    streamer.is_live = is_live
    db.session.commit()
    return jsonify({
        'ok': True,
        'is_live': is_live,
        'message': err or ('正在直播' if is_live else '未在直播')
    })


@app.route('/streamers/preview', methods=['POST'])
@login_required
def preview_streamer():
    """根据平台和房间号自动识别博主信息"""
    platform = request.form.get('platform', '').strip()
    room_id = request.form.get('room_id', '').strip()

    if not platform or not room_id:
        return jsonify({'ok': False, 'message': '平台和房间号不能为空'})

    from monitor_engine import _normalize_url
    url = _normalize_url(platform, room_id)

    info_funcs = {
        'douyin': 'platforms.douyin:get_streamer_info',
        'kuaishou': 'platforms.kuaishou:get_streamer_info',
        'bilibili': 'platforms.bilibili:get_streamer_info',
        'huya': 'platforms.huya:get_streamer_info',
        'douyu': 'platforms.douyu:get_streamer_info',
        'yy': 'platforms.yy:get_streamer_info',
        'youtube': 'platforms.youtube:get_streamer_info',
    }

    if platform not in info_funcs:
        return jsonify({'ok': True, 'name': '', 'live': False, 'message': '该平台暂不支持自动识别'})

    try:
        import importlib
        module_path, func_name = info_funcs[platform].split(':')
        mod = importlib.import_module(module_path)
        info_fn = getattr(mod, func_name)
        result = info_fn(url)
        return jsonify({
            'ok': True,
            'name': result.get('name', ''),
            'live': result.get('live', False),
            'message': result.get('error', '')
        })
    except Exception as e:
        return jsonify({'ok': False, 'message': f'识别异常: {e}'})


# ─── 推流控制 ───

@app.route('/streamers/<int:sid>/start-push', methods=['POST'])
@login_required
def start_manual_push(sid):
    """手动启动推流"""
    streamer = Streamer.query.get_or_404(sid)

    existing = ActiveStream.query.filter_by(
        streamer_id=sid
    ).filter(ActiveStream.status.in_(['running', 'starting'])).first()
    if existing:
        return jsonify({'ok': False, 'message': '已有推流在运行'})

    from monitor_engine import check_streamer_live
    is_live, stream_url, err = check_streamer_live(streamer)
    if not is_live or not stream_url:
        return jsonify({'ok': False, 'message': f'未在直播或获取流失败: {err}'})

    target = PushTarget.query.get(streamer.push_target_id) if streamer.push_target_id else None
    if not target:
        return jsonify({'ok': False, 'message': '该博主未绑定推流目标，请先选择'})

    active = ActiveStream(
        streamer_id=streamer.id,
        push_target_id=target.id,
        rtmp_url=target.rtmp_url,
        stream_key=target.stream_key,
        source_url=stream_url,
        status='starting'
    )
    db.session.add(active)
    db.session.commit()

    stream_engine.start_ffmpeg_push(active.id)
    _log(sid, 'success', 'manual_push', f'手动推流已启动 → {target.name}')
    return jsonify({'ok': True, 'message': f'推流已启动 → {target.name}'})


@app.route('/streamers/<int:sid>/stop-push', methods=['POST'])
@login_required
def stop_manual_push(sid):
    """停止推流"""
    active = ActiveStream.query.filter_by(
        streamer_id=sid
    ).filter(ActiveStream.status.in_(['running', 'starting'])).first()
    if not active:
        return jsonify({'ok': False, 'message': '没有运行中的推流'})

    stream_engine.stop_ffmpeg_push(active.id, force=True)
    _log(sid, 'info', 'manual_stop', '手动停止推流')
    return jsonify({'ok': True, 'message': '推流已停止'})


# ─── 推流目标管理(替代YouTube OAuth) ───

@app.route('/video-push')
@login_required
def video_push():
    tasks = VideoPush.query.order_by(VideoPush.created_at.desc()).all()
    targets = PushTarget.query.all()
    return render_template('video_push.html', tasks=tasks, targets=targets)


@app.route('/video-push/upload', methods=['POST'])
@login_required
def video_upload():
    """上传视频文件"""
    import os
    if 'file' not in request.files:
        return jsonify({'ok': False, 'message': '未选择文件'})
    f = request.files['file']
    if not f.filename:
        return jsonify({'ok': False, 'message': '未选择文件'})

    upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)

    # 安全文件名
    import uuid
    ext = os.path.splitext(f.filename)[1] or '.mp4'
    safe_name = f'{uuid.uuid4().hex[:12]}{ext}'
    filepath = os.path.join(upload_dir, safe_name)
    f.save(filepath)

    return jsonify({
        'ok': True,
        'filename': f.filename,
        'filepath': filepath,
        'message': f'文件 {f.filename} 上传成功'
    })


@app.route('/video-push/add', methods=['POST'])
@login_required
def video_push_add():
    """添加视频推流任务"""
    name = request.form.get('name', '').strip()
    file_path = request.form.get('file_path', '').strip()
    source_url = request.form.get('source_url', '').strip()
    target_id = request.form.get('push_target_id', '').strip()
    loop = request.form.get('loop') == 'on'

    if not name or not target_id:
        return jsonify({'ok': False, 'message': '名称和推流目标不能为空'})

    if not file_path and not source_url:
        return jsonify({'ok': False, 'message': '请上传文件或填写在线视频URL'})

    if file_path:
        import os
        if not os.path.exists(file_path):
            return jsonify({'ok': False, 'message': '视频文件不存在'})
        source_type = 'file'
    else:
        source_type = 'url'

    # 目录模式
    directory = request.form.get('directory', '').strip()
    if source_type == 'directory':
        if not directory:
            return jsonify({'ok': False, 'message': '请填写目录路径'})
        import os, json
        if not os.path.isdir(directory):
            return jsonify({'ok': False, 'message': '目录不存在'})
        # 扫描目录下所有视频文件
        video_exts = ('.mp4', '.mkv', '.flv', '.avi', '.mov', '.ts', '.webm', '.m4v')
        files = []
        for f_name in sorted(os.listdir(directory)):
            if f_name.lower().endswith(video_exts):
                files.append(os.path.join(directory, f_name))
        if not files:
            return jsonify({'ok': False, 'message': '目录中没有视频文件'})
        file_path = json.dumps(files)
        source_url = None

    task = VideoPush(
        name=name,
        file_path=file_path if file_path else None,
        source_url=source_url if source_url else None,
        source_type=source_type,
        push_target_id=int(target_id),
        loop=loop
    )
    db.session.add(task)
    db.session.commit()
    flash(f'推流任务 {name} 已添加', 'success')
    return redirect(url_for('video_push'))


@app.route('/video-push/<int:tid>/edit', methods=['POST'])
@login_required
def video_push_edit(tid):
    """编辑视频推流任务"""
    task = VideoPush.query.get_or_404(tid)
    if task.status == 'running':
        return jsonify({'ok': False, 'message': '请先停止推流再编辑'})
    task.name = request.form.get('name', task.name).strip()
    task.source_url = request.form.get('source_url', '').strip() or None
    task.source_type = request.form.get('source_type', task.source_type).strip()
    if task.source_type == 'directory':
        directory = request.form.get('directory', '').strip()
        if directory:
            import os, json
            video_exts = ('.mp4', '.mkv', '.flv', '.avi', '.mov', '.ts', '.webm', '.m4v')
            files = [os.path.join(directory, f) for f in sorted(os.listdir(directory)) if f.lower().endswith(video_exts)]
            task.file_path = json.dumps(files) if files else task.file_path
    else:
        task.file_path = request.form.get('file_path', '').strip() or None
    target_id = request.form.get('push_target_id', '').strip()
    task.push_target_id = int(target_id) if target_id else task.push_target_id
    task.loop = request.form.get('loop') == 'on'
    db.session.commit()
    return jsonify({'ok': True, 'message': f'任务 {task.name} 已更新'})


@app.route('/video-push/<int:tid>/delete', methods=['POST'])
@login_required
def video_push_delete(tid):
    task = VideoPush.query.get_or_404(tid)
    # 先停止
    if task.status == 'running':
        from video_engine import stop_video_push
        stop_video_push(tid)
    db.session.delete(task)
    db.session.commit()
    return jsonify({'ok': True, 'message': f'任务 {task.name} 已删除'})


@app.route('/video-push/<int:tid>/start', methods=['POST'])
@login_required
def video_push_start(tid):
    """启动视频推流"""
    from video_engine import start_video_push
    ok, msg = start_video_push(tid)
    return jsonify({'ok': ok, 'message': msg})


@app.route('/video-push/<int:tid>/stop', methods=['POST'])
@login_required
def video_push_stop(tid):
    """停止视频推流"""
    from video_engine import stop_video_push
    stop_video_push(tid)
    return jsonify({'ok': True, 'message': '推流已停止'})


@app.route('/api/video-push/stats/<int:tid>')
@login_required
def video_push_stats(tid):
    """获取视频推流状态"""
    task = VideoPush.query.get_or_404(tid)
    import os, json
    if task.status != 'running' or not task.ffmpeg_pid:
        return jsonify({'ok': False})
    if not os.path.exists(f'/proc/{task.ffmpeg_pid}'):
        task.status = 'error'
        task.error_message = 'FFmpeg进程已退出'
        db.session.commit()
        return jsonify({'ok': False})

    # 运行时长
    uptime = '-'
    if task.started_at:
        from datetime import datetime
        delta = datetime.utcnow() - task.started_at
        h, rem = divmod(int(delta.total_seconds()), 3600)
        m, s = divmod(rem, 60)
        uptime = f'{h:02d}:{m:02d}:{s:02d}'

    # 码率统计
    import time as _time
    r1 = w1 = 0
    try:
        with open(f'/proc/{task.ffmpeg_pid}/io', 'r') as f:
            for line in f:
                if line.startswith('rchar:'): r1 = int(line.split()[1])
                elif line.startswith('wchar:'): w1 = int(line.split()[1])
    except: pass
    _time.sleep(1.5)
    r2 = w2 = 0
    try:
        with open(f'/proc/{task.ffmpeg_pid}/io', 'r') as f:
            for line in f:
                if line.startswith('rchar:'): r2 = int(line.split()[1])
                elif line.startswith('wchar:'): w2 = int(line.split()[1])
    except: pass

    read_bps = (r2 - r1) * 8 // 1.5
    write_bps = (w2 - w1) * 8 // 1.5

    def fmt(bps):
        if bps > 1_000_000: return f'{bps/1_000_000:.1f} Mbps'
        elif bps > 1_000: return f'{bps/1_000:.0f} kbps'
        else: return f'{bps} bps'

    return jsonify({'ok': True, 'status': task.status, 'uptime': uptime, 'pid': task.ffmpeg_pid,
                    'input_bitrate': fmt(read_bps), 'output_bitrate': fmt(write_bps)})


@app.route('/settings')
@login_required
@admin_required
def settings():
    # 读取所有平台Cookie
    cookies = {}
    for pkey in ['kuaishou', 'douyu', 'bilibili', 'huya', 'yy']:
        s = Setting.query.filter_by(key=f'{pkey}_cookie').first()
        cookies[pkey] = s.value if s else ''
    proxy = Setting.query.filter_by(key='proxy').first()
    proxy = proxy.value if proxy else ''
    return render_template('settings.html', cookies=cookies, proxy=proxy)


@app.route('/settings/cookie', methods=['POST'])
@login_required
@admin_required
def update_cookie():
    platform = request.form.get('platform', '').strip()
    cookie = request.form.get('cookie', '').strip()
    key = f'{platform}_cookie'
    s = Setting.query.filter_by(key=key).first()
    if s:
        s.value = cookie
    else:
        s = Setting(key=key, value=cookie)
        db.session.add(s)
    db.session.commit()
    return jsonify({'ok': True, 'message': f'{platform} Cookie已保存'})


@app.route('/settings/proxy', methods=['POST'])
@login_required
@admin_required
def update_proxy():
    proxy = request.form.get('proxy', '').strip()
    s = Setting.query.filter_by(key='proxy').first()
    if s:
        s.value = proxy
    else:
        s = Setting(key='proxy', value=proxy)
        db.session.add(s)
    db.session.commit()
    return jsonify({'ok': True, 'message': '代理已保存'})


@app.route('/targets')
@login_required
@admin_required
def targets():
    targets = PushTarget.query.order_by(PushTarget.created_at.desc()).all()
    return render_template('targets.html', targets=targets, yt_rtmp=Config.YOUTUBE_RTMP_BASE)


@app.route('/targets/add', methods=['POST'])
@login_required
@admin_required
def add_target():
    name = request.form.get('name', '').strip()
    rtmp_url = request.form.get('rtmp_url', '').strip()
    stream_key = request.form.get('stream_key', '').strip()
    title_tpl = request.form.get('title_template', '{streamer_name} 直播转播').strip()

    if not name or not rtmp_url or not stream_key:
        return jsonify({'ok': False, 'message': '名称、RTMP地址、流密钥不能为空'})

    target = PushTarget(
        name=name,
        rtmp_url=rtmp_url,
        stream_key=stream_key,
        title_template=title_tpl
    )
    db.session.add(target)
    db.session.commit()
    flash(f'推流目标 {name} 已添加', 'success')
    return redirect(url_for('targets'))


@app.route('/targets/<int:tid>/delete', methods=['POST'])
@login_required
@admin_required
def delete_target(tid):
    target = PushTarget.query.get_or_404(tid)
    # 检查是否有关联博主
    linked = Streamer.query.filter_by(push_target_id=tid).count()
    if linked:
        flash(f'有 {linked} 个博主绑定到此目标，请先解绑', 'error')
        return redirect(url_for('targets'))
    db.session.delete(target)
    db.session.commit()
    flash(f'推流目标 {target.name} 已删除', 'success')
    return redirect(url_for('targets'))


@app.route('/targets/<int:tid>/update', methods=['POST'])
@login_required
@admin_required
def update_target(tid):
    target = PushTarget.query.get_or_404(tid)
    target.name = request.form.get('name', target.name).strip()
    target.rtmp_url = request.form.get('rtmp_url', target.rtmp_url).strip()
    new_key = request.form.get('stream_key', '').strip()
    if new_key:
        target.stream_key = new_key
    target.title_template = request.form.get('title_template', target.title_template).strip()
    target.is_active = request.form.get('is_active', 'off') == 'on'
    db.session.commit()
    flash('推流目标已更新', 'success')
    return redirect(url_for('targets'))


# ─── 日志 ───

@app.route('/logs')
@login_required
def logs():
    page = request.args.get('page', 1, type=int)
    pagination = StreamLog.query.order_by(
        StreamLog.timestamp.desc()
    ).paginate(page=page, per_page=50, error_out=False)
    return render_template('logs.html', logs=pagination.items,
                           pagination=pagination)


@app.route('/api/logs/recent')
@login_required
def api_recent_logs():
    logs = StreamLog.query.order_by(
        StreamLog.timestamp.desc()
    ).limit(20).all()
    return jsonify([{
        'id': l.id,
        'streamer_id': l.streamer_id,
        'level': l.level,
        'action': l.action,
        'message': l.message,
        'timestamp': l.timestamp.strftime('%Y-%m-%d %H:%M:%S') if l.timestamp else '',
        'streamer_name': l.streamer.name if l.streamer else '系统'
    } for l in logs])


# ─── 用户管理 ───

@app.route('/users')
@login_required
@admin_required
def users():
    if not current_user.is_admin:
        flash('需要管理员权限', 'error')
        return redirect(url_for('dashboard'))
    users = User.query.all()
    return render_template('users.html', users=users)


@app.route('/users/add', methods=['POST'])
@login_required
def add_user():
    if not current_user.is_admin:
        return jsonify({'ok': False, 'message': '无权限'})
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    is_admin = request.form.get('is_admin') == 'on'
    if not username or not password:
        return jsonify({'ok': False, 'message': '用户名密码不能为空'})
    if User.query.filter_by(username=username).first():
        return jsonify({'ok': False, 'message': '用户名已存在'})
    user = User(username=username, is_admin=is_admin)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return jsonify({'ok': True, 'message': f'用户 {username} 已添加'})


@app.route('/users/<int:uid>/delete', methods=['POST'])
@login_required
def delete_user(uid):
    if not current_user.is_admin:
        return jsonify({'ok': False, 'message': '无权限'})
    if uid == current_user.id:
        return jsonify({'ok': False, 'message': '不能删除自己'})
    user = User.query.get_or_404(uid)
    db.session.delete(user)
    db.session.commit()
    return jsonify({'ok': True, 'message': f'用户 {user.username} 已删除'})


@app.route('/users/<int:uid>/reset-pass', methods=['POST'])
@login_required
def reset_password(uid):
    if not current_user.is_admin:
        return jsonify({'ok': False, 'message': '无权限'})
    new_pass = request.form.get('password', '').strip()
    if len(new_pass) < 6:
        return jsonify({'ok': False, 'message': '密码至少6位'})
    user = User.query.get_or_404(uid)
    user.set_password(new_pass)
    db.session.commit()
    return jsonify({'ok': True, 'message': f'{user.username} 密码已重置'})


@app.route('/users/change-password', methods=['POST'])
@login_required
def change_password():
    """用户修改自己的密码, 需要验证旧密码"""
    old_pass = request.form.get('old_password', '').strip()
    new_pass = request.form.get('new_password', '').strip()
    confirm_pass = request.form.get('confirm_password', '').strip()

    if not old_pass or not new_pass:
        return jsonify({'ok': False, 'message': '请填写旧密码和新密码'})
    if not current_user.check_password(old_pass):
        return jsonify({'ok': False, 'message': '旧密码不正确'})
    if len(new_pass) < 6:
        return jsonify({'ok': False, 'message': '新密码至少6位'})
    if new_pass != confirm_pass:
        return jsonify({'ok': False, 'message': '两次输入的新密码不一致'})
    if new_pass == old_pass:
        return jsonify({'ok': False, 'message': '新密码不能和旧密码相同'})

    current_user.set_password(new_pass)
    db.session.commit()
    logger.info(f"用户修改密码: {current_user.username}")
    return jsonify({'ok': True, 'message': '密码修改成功'})


# ─── 二步验证(2FA)管理 ───

@app.route('/2fa/setup', methods=['GET', 'POST'])
@login_required
def setup_2fa():
    """生成TOTP密钥并返回QR码"""
    user = current_user
    if not user.totp_secret:
        user.generate_totp_secret()
        db.session.commit()
    
    if request.method == 'GET':
        # 返回otpauth URI供前端生成QR码
        uri = user.get_totp_uri()
        secret = user.totp_secret
        # 分组展示密钥方便手动输入
        formatted_secret = ' '.join([secret[i:i+4] for i in range(0, len(secret), 4)])
        return jsonify({
            'ok': True,
            'secret': secret,
            'formatted_secret': formatted_secret,
            'uri': uri,
            'enabled': user.totp_enabled
        })
    
    # POST: 验证用户输入的验证码, 启用2FA
    code = request.form.get('code', '').strip()
    if not code:
        return jsonify({'ok': False, 'message': '请输入验证码'})
    
    import pyotp
    totp = pyotp.TOTP(user.totp_secret)
    if totp.verify(code, valid_window=1):
        user.totp_enabled = True
        db.session.commit()
        logger.info(f"2FA已启用: {user.username}")
        return jsonify({'ok': True, 'message': '二步验证已启用'})
    else:
        return jsonify({'ok': False, 'message': '验证码错误, 请重试'})


@app.route('/2fa/disable', methods=['POST'])
@login_required
def disable_2fa():
    """关闭2FA, 需要验证当前验证码"""
    user = current_user
    code = request.form.get('code', '').strip()
    if not user.totp_enabled:
        return jsonify({'ok': False, 'message': '2FA未启用'})
    if not code:
        return jsonify({'ok': False, 'message': '请输入验证码'})
    
    import pyotp
    totp = pyotp.TOTP(user.totp_secret)
    if totp.verify(code, valid_window=1):
        user.totp_enabled = False
        user.totp_secret = None
        db.session.commit()
        logger.info(f"2FA已关闭: {user.username}")
        return jsonify({'ok': True, 'message': '二步验证已关闭'})
    else:
        return jsonify({'ok': False, 'message': '验证码错误'})


@app.route('/2fa/qrcode')
@login_required
def qrcode_2fa():
    """生成QR码图片"""
    import io
    import qrcode
    from flask import Response
    
    user = current_user
    if user.totp_enabled:
        return Response('2FA already enabled', status=400)
    if not user.totp_secret:
        user.generate_totp_secret()
        db.session.commit()
    uri = user.get_totp_uri()
    
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color='black', back_color='white')
    buf = io.BytesIO()
    try:
        img.save(buf, format='PNG')
    except TypeError:
        # qrcode 8.x PyPNGImage doesn't accept format kwarg
        img.save(buf)
    buf.seek(0)
    return Response(buf.getvalue(), mimetype='image/png')


# ─── API: 状态 ───

@app.route('/api/stream-stats/<int:sid>')
@login_required
def api_stream_stats(sid):
    """获取推流实时码率统计"""
    active = ActiveStream.query.filter_by(
        streamer_id=sid
    ).filter(ActiveStream.status.in_(['running', 'starting'])).first()
    if not active:
        return jsonify({'ok': False, 'message': '无活跃推流'})
    
    from stream_engine import get_stream_stats
    stats = get_stream_stats(active.id)
    if not stats:
        return jsonify({'ok': False, 'message': '获取失败'})
    
    stats['ok'] = True
    stats['status'] = active.status
    stats['target'] = active.push_target.name if active.push_target else '-'
    return jsonify(stats)


@app.route('/api/monitor-interval', methods=['GET', 'POST'])
@login_required
def api_monitor_interval():
    """获取或设置监控间隔"""
    from monitor_engine import get_interval, set_interval
    if request.method == 'POST':
        seconds = request.form.get('seconds', '60').strip()
        try:
            val = set_interval(int(seconds))
            return jsonify({'ok': True, 'interval': val, 'message': f'监控间隔已设为 {val}秒'})
        except ValueError:
            return jsonify({'ok': False, 'message': '请输入有效数字'})
    return jsonify({'ok': True, 'interval': get_interval()})


@app.route('/api/server-stats')
@login_required
def api_server_stats():
    """服务器资源占用: CPU/内存/硬盘/负载"""
    import psutil
    import shutil

    # CPU
    cpu_percent = psutil.cpu_percent(interval=0.5)
    cpu_count = psutil.cpu_count()

    # 内存
    mem = psutil.virtual_memory()

    # 硬盘 (数据目录所在分区)
    disk = shutil.disk_usage('/')

    # 负载
    try:
        load1, load5, load15 = os.getloadavg()
    except:
        load1 = load5 = load15 = 0

    # 上传/下载网络速率 (差值计算)
    net = psutil.net_io_counters()

    return jsonify({
        'ok': True,
        'cpu': {
            'percent': round(cpu_percent, 1),
            'cores': cpu_count,
        },
        'memory': {
            'percent': mem.percent,
            'used': mem.used,
            'total': mem.total,
            'used_str': _fmt_bytes(mem.used),
            'total_str': _fmt_bytes(mem.total),
        },
        'disk': {
            'percent': round(disk.used / disk.total * 100, 1),
            'used': disk.used,
            'total': disk.total,
            'used_str': _fmt_bytes(disk.used),
            'total_str': _fmt_bytes(disk.total),
        },
        'load': {
            'load1': round(load1, 2),
            'load5': round(load5, 2),
            'load15': round(load15, 2),
        },
        'uptime': _get_uptime(),
    })


def _fmt_bytes(b):
    """格式化字节数"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(b) < 1024:
            return f'{b:.1f} {unit}'
        b /= 1024
    return f'{b:.1f} PB'


def _get_uptime():
    """获取系统运行时间"""
    try:
        with open('/proc/uptime', 'r') as f:
            seconds = float(f.readline().split()[0])
        d, rem = divmod(int(seconds), 86400)
        h, rem = divmod(rem, 3600)
        m, s = divmod(rem, 60)
        if d > 0:
            return f'{d}天{h}时{m}分'
        return f'{h}时{m}分'
    except:
        return '-'


@app.route('/api/status')
@login_required
def api_status():
    streamers = Streamer.query.all()
    active = ActiveStream.query.filter(
        ActiveStream.status.in_(['running', 'starting'])
    ).all()
    return jsonify({
        'streamers': [{
            'id': s.id,
            'name': s.name,
            'platform': s.platform,
            'is_live': s.is_live,
            'is_monitoring': s.is_monitoring,
            'last_checked': s.last_checked.strftime('%H:%M:%S') if s.last_checked else '-',
        } for s in streamers],
        'active_streams': [{
            'id': a.id,
            'streamer_id': a.streamer_id,
            'streamer_name': a.streamer.name if a.streamer else '',
            'status': a.status,
            'started_at': a.started_at.strftime('%H:%M:%S') if a.started_at else '',
        } for a in active],
        'monitor_running': True,
    })



# ─── 直播录制 ───

@app.route('/recordings')
@login_required
def recordings_page():
    """录制文件列表页"""
    files = record_engine.list_recordings()
    return render_template('recordings.html', files=files)


@app.route('/streamers/<int:sid>/record', methods=['POST'])
@login_required
def start_record(sid):
    """手动开始录制"""
    success, msg = record_engine.start_recording(sid)
    return jsonify({'ok': success, 'message': msg})


@app.route('/streamers/<int:sid>/stop-record', methods=['POST'])
@login_required
def stop_record(sid):
    """停止录制"""
    success, msg = record_engine.stop_recording(sid)
    return jsonify({'ok': success, 'message': msg})


@app.route('/streamers/<int:sid>/record-status')
@login_required
def record_status(sid):
    """获取录制状态"""
    info = record_engine.get_recording_info(sid)
    return jsonify(info)


@app.route('/recordings/<filename>/download')
@login_required
def download_recording(filename):
    """下载录制文件"""
    import os
    from flask import send_from_directory
    record_dir = os.path.join(Config.DATA_DIR, 'recordings')
    return send_from_directory(record_dir, filename, as_attachment=True)


@app.route('/recordings/<filename>', methods=['DELETE'])
@login_required
@admin_required
def delete_recording_route(filename):
    """删除录制文件"""
    success, msg = record_engine.delete_recording(filename)
    return jsonify({'ok': success, 'message': msg})


def _log(streamer_id, level, action, message):
    log = StreamLog(
        streamer_id=streamer_id,
        level=level,
        action=action,
        message=message
    )
    db.session.add(log)
    db.session.commit()


# ─── 启动 ───

def migrate_db():
    """数据库迁移: 为旧版数据库添加2FA字段"""
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    cols = [c['name'] for c in inspector.get_columns('users')]
    if 'totp_secret' not in cols:
        db.session.execute(text('ALTER TABLE users ADD COLUMN totp_secret VARCHAR(64)'))
        logger.info("数据库迁移: 添加 users.totp_secret 列")
    if 'totp_enabled' not in cols:
        db.session.execute(text('ALTER TABLE users ADD COLUMN totp_enabled BOOLEAN DEFAULT 0'))
        logger.info("数据库迁移: 添加 users.totp_enabled 列")
    db.session.commit()


with app.app_context():
    db.create_all()
    migrate_db()
    if not User.query.filter_by(username=Config.DEFAULT_ADMIN_USER).first():
        admin = User(username=Config.DEFAULT_ADMIN_USER, is_admin=True)
        admin.set_password(Config.DEFAULT_ADMIN_PASS)
        db.session.add(admin)
        db.session.commit()
        logger.info(f"默认管理员已创建: {Config.DEFAULT_ADMIN_USER}")

start_monitor()

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5300, debug=False)
