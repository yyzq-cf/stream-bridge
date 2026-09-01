/* StreamBridge 前端交互 */

// 通用API请求
async function api(url, method = 'POST', data = null) {
    const opts = {
        method,
        headers: {},
    };
    if (data) {
        opts.headers['Content-Type'] = 'application/x-www-form-urlencoded';
        opts.body = new URLSearchParams(data);
    }
    try {
        const resp = await fetch(url, opts);
        return await resp.json();
    } catch (e) {
        console.error('API error:', e);
        return { ok: false, message: '网络错误: ' + e.message };
    }
}

// ─── 博主操作 ───

async function toggleMonitor(id) {
    const r = await api(`/streamers/${id}/toggle`);
    if (r.ok) {
        showToast(`监控已${r.monitoring ? '开启' : '关闭'}`);
    } else {
        showToast('操作失败: ' + (r.message || ''), 'error');
    }
}

async function checkNow(id) {
    showToast('正在检测...');
    const r = await api(`/streamers/${id}/check`);
    if (r.ok) {
        const row = document.getElementById(`streamer-${id}`);
        if (row) {
            const dot = row.querySelector('.live-dot');
            const text = row.querySelector('.live-text');
            if (r.is_live) {
                dot?.classList.add('live');
                text && (text.textContent = '直播中');
            } else {
                dot?.classList.remove('live');
                text && (text.textContent = '未直播');
            }
        }
        showToast(r.message);
    } else {
        showToast('检测失败: ' + (r.message || ''), 'error');
    }
}

async function startPush(id) {
    if (!confirm('确认开始推流到YouTube?')) return;
    showToast('正在启动推流...');
    const r = await api(`/streamers/${id}/start-push`);
    if (r.ok) {
        showToast('✅ ' + r.message, 'success');
        setTimeout(() => location.reload(), 2000);
    } else {
        showToast('❌ ' + (r.message || '推流失败'), 'error');
    }
}

async function stopPush(id) {
    if (!confirm('确认停止推流?')) return;
    const r = await api(`/streamers/${id}/stop-push`);
    if (r.ok) {
        showToast('✅ 推流已停止', 'success');
        setTimeout(() => location.reload(), 2000);
    } else {
        showToast('❌ ' + (r.message || '停止失败'), 'error');
    }
}

async function deleteStreamer(id, name) {
    if (!confirm(`确认删除博主 "${name}"?`)) return;
    const r = await api(`/streamers/${id}/delete`);
    if (r.ok) {
        showToast('已删除: ' + name, 'success');
        setTimeout(() => location.reload(), 1500);
    } else {
        showToast('删除失败', 'error');
    }
}

// ─── YouTube操作 ───

async function startYouTubeAuth() {
    const r = await api('/youtube/auth-start');
    if (r.ok) {
        document.getElementById('auth-url-box').style.display = 'block';
        document.getElementById('auth-url-link').href = r.auth_url;
        document.getElementById('auth-code-box').style.display = 'flex';
    } else {
        showToast('❌ ' + (r.message || '获取授权链接失败'), 'error');
    }
}

async function completeYouTubeAuth() {
    const code = document.getElementById('auth-code').value.trim();
    if (!code) {
        showToast('请输入授权码', 'error');
        return;
    }
    const r = await api('/youtube/auth-complete', 'POST', { code });
    if (r.ok) {
        showToast('✅ ' + r.message, 'success');
        setTimeout(() => location.reload(), 2000);
    } else {
        showToast('❌ ' + (r.message || '授权失败'), 'error');
    }
}

async function toggleChannel(id) {
    // TODO: 需要后端接口, 暂用表单提交
    showToast('请使用删除后重新添加', 'info');
}

// ─── 用户操作 ───

let resetPassUid = null;
let resetPassUsername = null;

async function addUser(form) {
    const data = {};
    new FormData(form).forEach((v, k) => data[k] = v);
    const r = await api('/users/add', 'POST', data);
    if (r.ok) {
        showToast('✅ ' + r.message, 'success');
        setTimeout(() => location.reload(), 1500);
    } else {
        showToast('❌ ' + (r.message || '添加失败'), 'error');
    }
}

function resetPass(id, username) {
    resetPassUid = id;
    resetPassUsername = username;
    document.getElementById('reset-username').textContent = username;
    document.getElementById('reset-pass-modal').style.display = 'flex';
    document.getElementById('reset-password-input').focus();
}

async function confirmResetPass() {
    const pass = document.getElementById('reset-password-input').value.trim();
    if (!pass) { showToast('请输入新密码', 'error'); return; }
    const r = await api(`/users/${resetPassUid}/reset-pass`, 'POST', { password: pass });
    if (r.ok) {
        showToast('✅ ' + r.message, 'success');
        closeModal();
    } else {
        showToast('❌ ' + (r.message || '重置失败'), 'error');
    }
}

async function deleteUser(id, username) {
    if (!confirm(`确认删除用户 "${username}"?`)) return;
    const r = await api(`/users/${id}/delete`);
    if (r.ok) {
        showToast('✅ ' + r.message, 'success');
        setTimeout(() => location.reload(), 1500);
    } else {
        showToast('❌ ' + (r.message || '删除失败'), 'error');
    }
}

function closeModal() {
    document.getElementById('reset-pass-modal').style.display = 'none';
    document.getElementById('reset-password-input').value = '';
}

// ─── 日志筛选 ───

function filterLog(level) {
    document.querySelectorAll('.filter-buttons .btn').forEach(b => b.classList.remove('active'));
    event.target.classList.add('active');
    document.querySelectorAll('.log-row').forEach(row => {
        if (level === 'all' || row.classList.contains(`log-${level}`)) {
            row.style.display = '';
        } else {
            row.style.display = 'none';
        }
    });
}

// ─── 仪表盘自动刷新 ───

let autoRefreshTimer = null;

function startAutoRefresh() {
    if (autoRefreshTimer) return;
    autoRefreshTimer = setInterval(async () => {
        try {
            const resp = await fetch('/api/status');
            const data = await resp.json();

            // 更新活跃推流
            const activeContainer = document.querySelector('.stats-grid');
            if (activeContainer) {
                const liveCount = data.streamers?.filter(s => s.is_live).length || 0;
                const liveEl = document.getElementById('live-count');
                if (liveEl) liveEl.textContent = liveCount;
            }

            // 更新博主列表(如果在streamers页面)
            data.streamers?.forEach(s => {
                const row = document.getElementById(`streamer-${s.id}`);
                if (row) {
                    const dot = row.querySelector('.live-dot');
                    const text = row.querySelector('.live-text');
                    if (s.is_live) {
                        dot?.classList.add('live');
                        text && (text.textContent = '直播中');
                    } else {
                        dot?.classList.remove('live');
                        text && (text.textContent = '未直播');
                    }
                }
            });
        } catch (e) {
            console.debug('Auto-refresh error:', e);
        }
    }, 10000); // 10秒更新一次
}

// ─── Toast通知 ───

function showToast(message, type = 'info') {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.style.cssText = 'position:fixed;top:20px;right:20px;z-index:300;display:flex;flex-direction:column;gap:8px;';
        document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    const colors = {
        info: 'var(--info)',
        success: 'var(--success)',
        error: 'var(--danger)',
        warning: 'var(--warning)',
    };
    toast.style.cssText = `padding:10px 16px;background:var(--surface);border:1px solid ${colors[type]||colors.info};border-radius:6px;color:${colors[type]||colors.info};font-size:13px;max-width:350px;box-shadow:0 4px 12px rgba(0,0,0,0.3);`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ─── 初始化 ───

document.addEventListener('DOMContentLoaded', () => {
    startAutoRefresh();

    // 添加用户表单
    const addForm = document.getElementById('add-user-form');
    if (addForm) {
        addForm.addEventListener('submit', (e) => {
            e.preventDefault();
            addUser(addForm);
        });
    }

    // Modal 点击外部关闭
    const modal = document.getElementById('reset-pass-modal');
    if (modal) {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeModal();
        });
    }
});

// ─── 主题切换 ───
function toggleTheme() {
    const html = document.documentElement;
    const current = html.getAttribute('data-theme');
    const next = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
    const btn = document.querySelector('.theme-toggle');
    if (btn) btn.textContent = next === 'dark' ? '☀️' : '🌙';
}

// 应用保存的主题
(function() {
    const saved = localStorage.getItem('theme') || 'light';
    if (saved === 'dark') {
        document.documentElement.setAttribute('data-theme', 'dark');
        document.addEventListener('DOMContentLoaded', () => {
            const btn = document.querySelector('.theme-toggle');
            if (btn) btn.textContent = '☀️';
        });
    }
})();
