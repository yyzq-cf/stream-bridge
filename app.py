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
from models import db, User, Streamer, PushTarget, ActiveStream, StreamLog, Setting
from monitor_engine import start_monitor, stop_monitor
import stream_engine

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

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
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('用户名或密码错误', 'error')
    return render_template('login.html')


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
    targets = PushTarget.query.all()
    recent_logs = StreamLog.query.order_by(
        StreamLog.timestamp.desc()
    ).limit(20).all()
    return render_template('dashboard.html',
                           streamers=streamers,
                           active_streams=active_streams,
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

@app.route('/settings')
@login_required
def settings():
    kuaishou_cookie = Setting.query.filter_by(key='kuaishou_cookie').first()
    kuaishou_cookie = kuaishou_cookie.value if kuaishou_cookie else ''
    return render_template('settings.html', kuaishou_cookie=kuaishou_cookie)


@app.route('/settings/cookie', methods=['POST'])
@login_required
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


@app.route('/targets')
@login_required
def targets():
    targets = PushTarget.query.order_by(PushTarget.created_at.desc()).all()
    return render_template('targets.html', targets=targets, yt_rtmp=Config.YOUTUBE_RTMP_BASE)


@app.route('/targets/add', methods=['POST'])
@login_required
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

with app.app_context():
    db.create_all()
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
