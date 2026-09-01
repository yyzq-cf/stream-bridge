# 🎥 StreamBridge - 直播转推YouTube管理平台

实时监控抖音/快手等平台博主开播状态，自动拉流转推到YouTube Live。支持视频文件/在线直链推流。

## ✨ 功能特性

### 直播转推
- **多平台支持**：抖音、快手、B站、Twitch、YouTube、自定义RTMP
- **自动监控**：定时检测博主开播状态，开播自动推流
- **手动推流**：一键拉流推送到指定RTMP目标
- **RTMP直推**：只需配置RTMP地址+流密钥，无需OAuth授权
- **灵活绑定**：每个博主可绑定不同推流目标（多频道/多平台）
- **FFmpeg拉流**：视频直接copy不转码，自动加Referer头解决抖音/快手CDN 403

### 视频文件推流
- **本地文件上传**：支持mp4/mkv等格式，带实时进度条（百分比+速度+大小）
- **在线视频直链**：直接填写URL，FFmpeg拉取推流
- **目录循环推流**：扫描服务器目录下所有视频文件，按文件名排序拼接循环播放，24小时不间断
- **循环推流**：视频结束后自动重头开始
- **在线编辑**：任务名称、URL、文件路径、推流目标随时修改

### 用户管理
- **角色权限**：管理员/普通用户，功能分级
- **修改密码**：用户修改自己密码需验证当前密码
- **防暴力破解**：同一IP 5次失败后锁定5分钟，显示剩余尝试次数和倒计时

### 监控与统计
- **实时码率**：仪表盘显示输入/输出码率，每3秒刷新，颜色区分码率高低
- **推流时长**：实时显示每个推流任务运行时间
- **系统日志**：完整操作日志，支持级别筛选和分页
- **仪表盘**：博主总数、正在直播、推流中（含视频推流）、推流目标数一览

### 界面
- **亮色/暗色主题**：一键切换，自动记忆
- **GitHub链接**：导航栏GitHub SVG图标
- **版本号**：导航栏显示当前版本
- **响应式布局**：手机/平板自适应

### 部署
- **Docker双平台**：支持 amd64 + arm64
- **GitHub Actions CI**：push代码自动构建推送到Docker Hub
- **docker compose**：一键启动，数据持久化

## 🚀 快速开始

**方式一：docker run**

```bash
docker run -d \
  --name streambridge \
  -p 5300:5300 \
  -v ./data:/app/data \
  --restart always \
  ywsj/stream-bridge:latest
```

**方式二：docker-compose.yml**

```yaml
services:
  streambridge:
    image: ywsj/stream-bridge:latest    # Docker镜像地址
    container_name: streambridge        # 容器名称
    restart: always                     # 容器异常时自动重启
    ports:
      - "5300:5300"                     # 端口映射(主机:容器)
    volumes:
      - ./data:/app/data                # 数据持久化目录(数据库+配置)
    environment:
      - ADMIN_USER=admin                # 管理员用户名
      - ADMIN_PASS=admin123             # 管理员密码(首次启动后建议修改)
      - SECRET_KEY=<your-secret-key>     # Session加密密钥(生产环境务必修改)
      - MONITOR_INTERVAL=60             # 监控轮询间隔(秒)，检测博主是否开播
```

```bash
docker compose up -d
```

访问 `http://your-ip:5300`，默认账号 `admin / admin123`

## 📖 使用流程

### 直播转推
1. **推流目标**（管理员）→ 添加RTMP地址和流密钥
   - YouTube地址默认 `rtmp://a.rtmp.youtube.com/live2`
   - 只需填入YouTube Studio后台获取的流密钥
2. **博主管理** → 选择平台 → 填入房间号或URL → 绑定推流目标 → 开启监控
3. 博主开播后自动拉流推送到YouTube

### 视频推流
1. **视频推流** → 选择上传文件或在线直链
2. 选择推流目标 → 勾选循环（可选）→ 添加
3. 点击"开始推流"

### 快手/抖音配置
- **快手Cookie**：设置页面配置Cookie绕过限流（F12 → Network → Request Headers → Cookie）
- **代理配置**：设置页面配置HTTP/SOCKS5代理绕过IP限制

## ⚙️ 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ADMIN_USER` | admin | 管理员用户名 |
| `ADMIN_PASS` | admin123 | 管理员密码 |
| `SECRET_KEY` | (内置) | Session加密密钥，生产环境建议修改 |
| `MONITOR_INTERVAL` | 60 | 监控轮询间隔(秒)，可在仪表盘在线修改 |
| `VIDEO_CODEC` | copy | 视频编码(copy=不转码) |
| `AUDIO_CODEC` | aac | 音频编码 |
| `AUDIO_BITRATE` | 128k | 音频码率 |

## 👥 权限说明

| 功能 | 管理员 | 普通用户 |
|------|--------|---------|
| 仪表盘 | ✅ | ✅ |
| 博主管理 | ✅ | ✅ |
| 视频推流 | ✅ | ✅ |
| 日志 | ✅ | ✅ |
| 修改自己密码 | ✅ | ✅ |
| 推流目标管理 | ✅ | ❌ |
| 系统设置 | ✅ | ❌ |
| 用户管理 | ✅ | ❌ |

## 📁 项目结构

```
stream-bridge/
├── app.py                 # Flask主应用(路由+API)
├── config.py              # 配置
├── models.py              # 数据模型(User/Streamer/PushTarget/ActiveStream/VideoPush/StreamLog/Setting)
├── monitor_engine.py      # 监控引擎(自动检测开播+推流)
├── stream_engine.py       # 直播推流引擎(FFmpeg管理+码率统计)
├── video_engine.py        # 视频推流引擎(文件/URL/目录推流+循环)
├── platforms/             # 平台检测器
│   ├── douyin.py          # 抖音直播检测(FLV流提取+URL清理+503重试)
│   └── kuaishou.py        # 快手直播检测(curl+gzip/brotli+Cookie+代理)
├── templates/             # Jinja2模板(8个页面)
├── static/                # CSS暗色/亮色主题 + JS交互
├── data/                  # SQLite数据库+上传文件+配置
├── .github/workflows/     # GitHub Actions CI(双平台Docker构建)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 🛠️ 技术栈

- **后端**：Flask + SQLAlchemy + SQLite + Gunicorn
- **推流**：FFmpeg (-c:v copy 不转码)
- **检测**：抖音/快手专用检测器 + yt-dlp(B站/Twitch)
- **前端**：原生JS + CSS变量(亮/暗主题) + XMLHttpRequest(上传进度)
- **部署**：Docker(amd64+arm64) + GitHub Actions CI

## 📦 GitHub

源码：https://github.com/yyzq-cf/stream-bridge  
Docker Hub：https://hub.docker.com/r/ywsj/stream-bridge

## ⚠️ 注意事项

- **抖音/快手**：依赖页面解析流地址，平台改版可能导致失效
- **快手限流**：服务器IP可能被快手限流，需配置Cookie或代理
- **转码**：默认不转码(`copy`)，如需转码设 `VIDEO_CODEC=libx264`（增加CPU消耗）
- **带宽**：不转码时带宽≈源流码率，1080p约3-6Mbps
- **YouTube限制**：免费频道同时推流数量有限(1-3路)
- **版权**：转推他人直播内容需注意版权问题

## 📝 License

MIT
