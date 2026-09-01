"""
StreamBridge 主应用
Flask Web界面 + 用户管理 + 直播监控推流
"""
import os
import logging
from datetime import datetime

from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user

from config import Config
from models import db, User, Streamer, YouTubeChannel, ActiveStream, StreamLog
from monitor_engine import start_monitor, stop_monitor
import stream_engine
import youtube_engine

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
    channels = YouTubeChannel.query.all()
    recent_logs = StreamLog.query.order_by(
        StreamLog.timestamp.desc()
    ).limit(20).all()
    return render_template('dashboard.html',
                           streamers=streamers,
                           active_streams=active_streams,
                           channels=channels,
                           recent_logs=recent_logs)


# ─── 博主管理 ───

@app.route('/streamers')
@login_required
def streamers():
    streamers = Streamer.query.order_by(Streamer.created_at.desc()).all()
    channels = YouTubeChannel.query.all()
    return render_template('streamers.html', streamers=streamers, channels=channels)


@app.route('/streamers/add', methods=['POST'])
@login_required
def add_streamer():
    platform = request.form.get('platform', '').strip()
    name = request.form.get('name', '').strip()
    room_id = request.form.get('room_id', '').strip()
    monitor = request.form.get('monitor', 'on') == 'on'
    yt_channel_id = request.form.get('youtube_channel_id', '').strip()
    yt_channel_id = int(yt_channel_id) if yt_channel_id else None

    if not platform or not name or not room_id:
        flash('平台、名称、房间号不能为空', 'error')
        return redirect(url_for('streamers'))

    streamer = Streamer(
        platform=platform,
        name=name,
        room_id=room_id,
        url=room_id if room_id.startswith('http') else None,
        is_monitoring=monitor,
        youtube_channel_id=yt_channel_id
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
    # 先停止活跃推流
    active = ActiveStream.query.filter_by(
        streamer_id=sid, status='running'
    ).first()
    if active:
        stream_engine.stop_ffmpeg_push(active.id, force=True)
    db.session.delete(streamer)
    db.session.commit()
    flash(f'博主 {streamer.name} 已删除', 'success')
    return redirect(url_for('streamers'))


@app.route('/streamers/<int:sid>/toggle', methods=['POST'])
@login_required
def toggle_monitor(sid):
    streamer = Streamer.query.get_or_404(sid)
    streamer.is_monitoring = not streamer.is_monitoring
    db.session.commit()
    action = '开启监控' if streamer.is_monitoring else '关闭监控'
    _log(sid, 'info', 'toggle_monitor', f'{action}: {streamer.name}')
    return jsonify({'ok': True, 'monitoring': streamer.is_monitoring})


@app.route('/streamers/<int:sid>/update-youtube', methods=['POST'])
@login_required
def update_streamer_youtube(sid):
    """更新博主绑定的YouTube频道"""
    streamer = Streamer.query.get_or_404(sid)
    channel_id = request.form.get('youtube_channel_id', '').strip()
    streamer.youtube_channel_id = int(channel_id) if channel_id else None
    db.session.commit()
    return jsonify({'ok': True, 'message': 'YouTube频道已更新'})


@app.route('/streamers/<int:sid>/check', methods=['POST'])
@login_required
def check_now(sid):
    """手动立即检测某博主"""
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

    # 检查是否已有活跃推流
    existing = ActiveStream.query.filter_by(
        streamer_id=sid
    ).filter(ActiveStream.status.in_(['running', 'starting'])).first()
    if existing:
        return jsonify({'ok': False, 'message': '已有推流在运行'})

    # 获取流地址
    from monitor_engine import check_streamer_live
    is_live, stream_url, err = check_streamer_live(streamer)
    if not is_live or not stream_url:
        return jsonify({'ok': False, 'message': f'未在直播或获取流失败: {err}'})

    # 获取博主绑定的YouTube频道
    channel = YouTubeChannel.query.get(streamer.youtube_channel_id) if streamer.youtube_channel_id else None
    if not channel:
        return jsonify({'ok': False, 'message': '该博主未绑定YouTube频道，请先在列表中选择'})

    try:
        title = (channel.default_title_template or '{streamer_name} 直播转播').format(
            streamer_name=streamer.name
        )
        rtmp_url, stream_key, broadcast_id, stream_id = \
            youtube_engine.create_broadcast_and_get_rtmp(
                channel, title,
                channel.default_description or '',
                channel.default_privacy or 'public'
            )

        active = ActiveStream(
            streamer_id=streamer.id,
            youtube_channel_id=channel.id,
            broadcast_id=broadcast_id,
            broadcast_title=title,
            stream_id=stream_id,
            rtmp_url=rtmp_url,
            stream_key=stream_key,
            source_url=stream_url,
            status='starting'
        )
        db.session.add(active)
        db.session.commit()

        stream_engine.start_ffmpeg_push(active.id)
        _log(sid, 'success', 'manual_push',
             f'手动推流已启动: {title}')
        return jsonify({'ok': True, 'message': f'推流已启动: {title}'})
    except Exception as e:
        _log(sid, 'error', 'manual_push_fail', f'手动推流失败: {e}')
        return jsonify({'ok': False, 'message': f'推流失败: {e}'})


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

    # 结束YouTube广播
    if active.youtube_channel and active.broadcast_id:
        try:
            youtube_engine.end_broadcast(
                active.youtube_channel, active.broadcast_id
            )
        except Exception as e:
            logger.warning(f"结束广播失败: {e}")

    _log(sid, 'info', 'manual_stop', '手动停止推流')
    return jsonify({'ok': True, 'message': '推流已停止'})


# ─── YouTube频道管理 ───

@app.route('/youtube')
@login_required
def youtube_page():
    channels = YouTubeChannel.query.all()
    has_secret = os.path.exists(Config.YOUTUBE_CLIENT_SECRETS_FILE)
    return render_template('youtube.html',
                           channels=channels,
                           has_secret=has_secret)


@app.route('/youtube/auth-start', methods=['POST'])
@login_required
def youtube_auth_start():
    """生成OAuth授权URL"""
    try:
        auth_url = youtube_engine.get_auth_url()
        session['youtube_auth_started'] = True
        return jsonify({'ok': True, 'auth_url': auth_url})
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e)})


@app.route('/youtube/auth-complete', methods=['POST'])
@login_required
def youtube_auth_complete():
    """用授权码完成认证"""
    code = request.form.get('code', '').strip()
    if not code:
        return jsonify({'ok': False, 'message': '请输入授权码'})
    try:
        token_info = youtube_engine.exchange_code_for_token(code)
        channel = youtube_engine.save_channel(token_info)
        return jsonify({
            'ok': True,
            'message': f'频道 {channel.channel_title} 已添加'
        })
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e)})


@app.route('/youtube/<int:cid>/delete', methods=['POST'])
@login_required
def delete_youtube_channel(cid):
    youtube_engine.delete_channel(cid)
    flash('YouTube频道已删除', 'success')
    return redirect(url_for('youtube_page'))


@app.route('/youtube/<int:cid>/update', methods=['POST'])
@login_required
def update_youtube_channel(cid):
    channel = YouTubeChannel.query.get_or_404(cid)
    channel.default_title_template = request.form.get(
        'title_template', '{streamer_name} 直播转播'
    )
    channel.default_description = request.form.get('description', '')
    channel.default_privacy = request.form.get('privacy', 'public')
    channel.default_category_id = request.form.get('category_id', '22')
    db.session.commit()
    flash('YouTube配置已更新', 'success')
    return redirect(url_for('youtube_page'))


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
    """API: 获取最近日志"""
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

@app.route('/api/status')
@login_required
def api_status():
    """获取系统状态(给前端轮询用)"""
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
            'title': a.broadcast_title,
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
