# 地图 (Map) — 离线 + 在线双模式 Web 地图应用

一个纯前端 + 可选后端的 Leaflet 地图应用，支持搜索、路线规划、地点详情、附近照片、语音搜索、离线瓦片下载与浏览。

## 目录

- [特性](#特性)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [使用说明](#使用说明)
- [API 文档](#api-文档)
- [后端依赖](#后端依赖)
- [浏览器兼容](#浏览器兼容)
- [常见问题](#常见问题)

---

## 特性

### 核心功能
- 🗺️ **Leaflet 地图** — 平滑拖动、惯性、双击缩放、滚轮防抖调优
- 🔍 **多源搜索** — Nominatim / Open-Meteo / Photon，按语言自动切换
- 🧭 **路线规划** — 驾车 / 骑行 / 步行（OSRM）
- 📍 **地点详情** — 单击地图或搜索结果，弹出底部抽屉（Bottom Sheet）
- 🖼️ **附近照片** — 抽屉全屏时自动加载 Wikimedia Commons 附近地理照片
- ⭐ **收藏地点** — localStorage 持久化，分组管理
- 📦 **离线瓦片** — 可视区域瓦片多线程下载并打包为 ZIP
- 🛰️ **多图层** — OpenStreetMap / Carto / Satellite 等切换

### 体验细节
- 🤚 **跟手 Bottom Sheet** — 触摸 / 鼠标拖动，三档吸附（收起 / 半屏 / 全屏）
- 🌐 **自动语言检测** — 根据 `navigator.language` 切换 Web Speech、TTS、API 语言
- 🎤 **语音搜索** — 优先使用浏览器原生 Web Speech API，降级到 MediaRecorder + faster-whisper 后端
- 🔊 **TTS 导航播报** — 路线导航按用户语言播报
- 📱 **PWA 友好** — `user-scalable=no`、触摸手势优化

---

## 项目结构

```
.
├── index.html              # 主入口（后端代理版本，推荐）
├── index-pure.html         # 纯前端版本（无后端，直接调用第三方 API）
├── server.py               # 统一后端（静态服务 + API 代理 + ASR）
├── asr_server.py           # 独立语音识别服务（faster-whisper）
├── leaflet.css / .js       # 地图库（本地引用）
├── jszip.min.js            # 离线瓦片打包
├── initial.png             # 界面截图
├── route-*.png             # 路线功能截图
├── nav-*.png               # 导航功能截图
└── server.log / asr_server.log  # 运行日志
```

### 两个 HTML 的区别

| 特性 | `index.html` | `index-pure.html` |
|---|---|---|
| 静态服务 | 由 `server.py` 提供 | 可用任意 HTTP 服务（甚至 `file://`） |
| 搜索 API | 经后端代理（[Nominatim 政策](https://operations.osmfoundation.org/policies/nominatim/) 友好） | 直接调用 CDN |
| 附近照片 | 经后端 `/api/wikimedia` | 直接调用 Commons API |
| 语音输入 | Web Speech API + 后端 Whisper 降级 | Web Speech API（无 Whisper） |
| 离线瓦片 | 后端多线程下载 + 打包 | 浏览器端下载（受 CORS 限制，部分源可能失败） |
| 启动方式 | `python3 server.py` | 直接打开 `index-pure.html` |

> 两个 HTML 共享同一份交互逻辑（拖动、Bottom Sheet、搜索、地图手感），只是后端依赖不同。

---

## 快速开始

### 1. 启动后端版本（推荐）

```bash
# 1. 安装 Python 依赖（首次）
pip install faster-whisper

# 2. 启动主服务（默认 8080 端口）
PORT=8080 python3 server.py

# 3. 浏览器访问
open http://localhost:8080/
```

打开后会自动加载瓦片、显示当前位置（如已授权）。

### 2. 启动纯前端版本

```bash
# 任意 HTTP 服务即可
python3 -m http.server 8000

# 浏览器访问
open http://localhost:8000/index-pure.html
```

或直接双击 `index-pure.html`（部分浏览器对 `file://` 的 `fetch()` 有限制，建议用 HTTP）。

### 3. 启动独立语音识别服务（可选）

```bash
# 默认 8081 端口，提供 POST /asr
python3 asr_server.py
```

> 当前 `index.html` 已经把 ASR 集成在主服务 `server.py` 中，`/api/asr` 端点可用。只有想分离部署时才需要独立运行 `asr_server.py`。

---

## 使用说明

### 搜索地点
1. 点击顶部搜索框
2. 输入地名（自动按浏览器语言搜索）
3. 点击候选项或按 Enter 跳转

### 查看地点详情
1. **单击地图**上的标记或兴趣点
2. 底部弹出 Bottom Sheet 显示名称、坐标、地址
3. **拖动顶部把手**：
   - 向下拖 → 收起
   - 向上拖 → 半屏 / 全屏
   - 全屏后自动加载 **附近照片**

### 路线规划
1. 搜索或点击地图选起点 / 终点
2. 选择出行方式（驾车 / 骑行 / 步行）
3. 路线渲染到地图，右上角显示距离和时间
4. 启用 TTS 后，导航播报会按语言朗读

### 离线下载
1. 调整地图到目标区域
2. 点击"下载瓦片"
3. 后端多线程拉取当前可视范围瓦片
4. 完成后浏览器下载 `tiles.zip`，包含 `metadata.json` 和 `tiles/{z}/{x}/{y}.png`

### 语音搜索
1. 点击搜索框右侧的麦克风按钮 🎤
2. 浏览器弹出权限请求 → 允许
3. 开始说话，松手后自动识别并填入搜索框

> 优先使用浏览器原生 Web Speech API（Chrome / Edge / Safari），不支持时降级到 MediaRecorder + 后端 Whisper。

---

## API 文档

由 `server.py` 提供：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查，返回 `model_loaded` |
| GET | `/api/search?q=...&limit=8` | 统一搜索（按查询语言路由 Nominatim / Open-Meteo / Photon） |
| GET | `/api/route?profile=car&from_lng=..&from_lat=..&to_lng=..&to_lat=..` | OSRM 路线规划，`profile` ∈ `car` / `bike` / `foot` |
| GET | `/api/overpass?lat=..&lon=..&radius=5000` | 附近地标（Overpass） |
| GET | `/api/wikimedia?lat=..&lon=..&radius=10000` | 附近照片（Wikimedia Commons） |
| POST | `/api/asr` | 语音识别（multipart 或 raw 音频） |
| POST | `/api/download-tiles` | 瓦片多线程下载并打包 ZIP（body: JSON） |

所有 API 支持 CORS（`Access-Control-Allow-Origin: *`）。

---

## 后端依赖

```txt
# 必需
# (无第三方依赖 — Python 3.8+ 标准库即可启动静态服务和 API 代理)

# 可选：语音识别
faster-whisper    # ASR 模型 (~75MB base，首次运行自动下载)
```

语音识别模型默认从 Hugging Face 下载，可通过设置环境变量 `HF_ENDPOINT=https://hf-mirror.com` 切换镜像（在 `server.py` 中已默认设置）。

---

## 浏览器兼容

| 特性 | Chrome | Edge | Firefox | Safari |
|---|---|---|---|---|
| 基础地图 | ✅ | ✅ | ✅ | ✅ |
| 搜索 / 路线 | ✅ | ✅ | ✅ | ✅ |
| 附近照片 | ✅ | ✅ | ✅ | ✅ |
| Web Speech API | ✅ | ✅ | ⚠️ 受限 | ✅ |
| MediaRecorder ASR | ✅ | ✅ | ✅ | ⚠️ 格式 |
| 离线瓦片下载 | ✅ | ✅ | ⚠️ CORS | ⚠️ CORS |

> Firefox 没有原生 Web Speech API，会自动降级到后端 ASR。
> Safari 的 Web Speech 在某些版本只支持 `en-US`，此时语音会回退到 MediaRecorder 路径。

---

## 常见问题

### Q: 打开页面后地图是灰色的？
A: 瓦片加载被网络拦截。本地 `leaflet.js` 已自带，可在 DevTools Network 看瓦片请求。考虑改用 `index.html` + 后端代理模式。

### Q: 搜索不到结果？
A: 后端版本会自动降级到多个搜索引擎（Photon / Nominatim / Open-Meteo）。如果仍无结果，可能是网络隔离。

### Q: 语音按钮点了没反应？
A: 检查：
1. 浏览器是否支持 `webkitSpeechRecognition`（看 console 是否有 `[mic] using Web Speech API`）
2. 是否授予了麦克风权限
3. 是否在 HTTPS 或 localhost 下（Web Speech 要求安全上下文）

### Q: 离线瓦片下载失败 / 数量少？
A: 某些瓦片源（OpenStreetMap、卫星图）有反爬限制。后端已实现自动重试 + 子域名分散；如果仍失败，缩下载范围或换源。

### Q: 启动时 `[whisper] 正在加载模型` 卡住？
A: 首次启动 faster-whisper 会下载 ~75MB 模型，1-2 分钟属正常。模型加载后缓存在内存。

---

## 截图

参见工作目录的 `*.png`：
- `initial.png` — 主界面
- `route-*.png` — 路线规划各阶段
- `nav-*.png` — 导航与 TTS 播报

---

## 许可与数据来源

- 地图数据 © [OpenStreetMap](https://www.openstreetmap.org/copyright) 贡献者
- 路线 © [OSRM](http://project-osrm.org/) (Mapbox / OSRM)
- 照片 © [Wikimedia Commons](https://commons.wikimedia.org/) 贡献者
- 语音识别 © [faster-whisper](https://github.com/guillaumekln/faster-whisper) (CTranslate2 / OpenAI Whisper)

仅用于学习与个人使用。
