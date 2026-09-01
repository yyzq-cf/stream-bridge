# 🎥 StreamBridge - 直播转推YouTube管理平台

实时监控各平台直播开播状态，自动拉流转推到YouTube Live。

## ✨ 功能特性

- **多平台支持**：抖音、快手、B站、Twitch、YouTube、自定义RTMP
- **自动监控**：定时轮询检测博主开播状态，开播自动推流
- **手动推流**：一键手动拉流推送到YouTube
- **YouTube API集成**：OAuth2认证，自动创建直播广播，获取RTMP推流地址
- **用户管理**：完整的用户名密码管理，管理员/普通用户角色
- **实时仪表盘**：活跃推流状态、博主直播状态、最近日志一览
- **暗色主题**：现代化的深色UI界面
- **Docker部署**：一键 `docker compose up -d` 启动

## 🚀 快速开始

### 1. Docker部署（推荐）

```bash
git clone https://github.com/yyzq-cf/stream-bridge.git
cd stream-bridge
docker compose up -d
```

访问 `http://localhost:5300`，默认账号 `admin / admin123`

### 2. 配置YouTube OAuth

1. 访问 [Google Cloud Console](https://console.cloud.google.com/)
2. 创建项目 → 启用 **YouTube Data API v3**
3. 创建 **OAuth 2.0 客户端 ID**（类型选"桌面应用"）
4. 下载JSON文件，重命名为 `client_secret.json`
5. 放入 `data/client_secret.json`
6. 在Web界面的 YouTube 页面完成授权

### 3. 添加博主 & 开始推流

1. **博主管理** → 选择平台 → 输入房间号/URL → 添加
2. 系统自动监控开播状态
3. 检测到开播后自动创建YouTube直播并推流
4. 也可手动点击"推流"按钮

## 📁 项目结构

```
stream-bridge/
├── app.py              # Flask主应用
├── config.py           # 配置
├── models.py           # 数据模型
├── monitor_engine.py   # 监控引擎(自动检测开播+推流)
├── stream_engine.py    # 推流引擎(FFmpeg子进程管理)
├── youtube_engine.py   # YouTube引擎(OAuth2+直播API)
├── templates/          # Jinja2模板
├── static/             # CSS+JS
├── data/               # SQLite数据库+OAuth凭据
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## ⚙️ 配置项

通过环境变量配置（见 `docker-compose.yml`）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ADMIN_USER` | admin | 默认管理员用户名 |
| `ADMIN_PASS` | admin123 | 默认管理员密码 |
| `SECRET_KEY` | (内置) | Flask Session密钥 |
| `MONITOR_INTERVAL` | 60 | 监控轮询间隔(秒) |
| `FFMPEG_PATH` | ffmpeg | FFmpeg路径 |
| `YTDLP_PATH` | yt-dlp | yt-dlp路径 |
| `VIDEO_CODEC` | copy | 视频编码(copy=不转码) |
| `AUDIO_CODEC` | aac | 音频编码 |
| `AUDIO_BITRATE` | 128k | 音频码率 |

## 🛠️ 技术栈

- **后端**：Flask + SQLAlchemy + SQLite
- **前端**：Bootstrap风格 + 原生JS + 暗色CSS
- **监控**：yt-dlp获取流地址 + 轮询检测
- **推流**：FFmpeg子进程
- **YouTube**：google-api-python-client + OAuth2
- **部署**：Docker + Gunicorn

## ⚠️ 注意事项

- **抖音/快手**：依赖yt-dlp解析，平台改版可能导致失效
- **转码**：默认不转码(`copy`)，如需转码设 `VIDEO_CODEC=libx264`（增加CPU消耗）
- **带宽**：不转码时带宽≈源流码率，1080p约3-6Mbps
- **YouTube限制**：免费频道同时推流数量有限(1-3路)
- **版权**：转推他人直播内容需注意版权问题

## 📝 License

MIT
