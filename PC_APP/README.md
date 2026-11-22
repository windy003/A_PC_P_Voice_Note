# R2 录音机（PC 版）

一个使用 Python + Tkinter 的桌面录音工具：在本机录制语音，保存为 M4A（无 ffmpeg 时保存 WAV），
并通过 S3 兼容接口自动上传到 Cloudflare R2。

## 功能

- 一键录音，实时显示录音时长
- **F9 全局快捷键**开始 / 停止录音，任何程序在前台时都能用
- 操作反馈使用 **屏幕下方居中的置顶提示**（不管当前是哪个程序在前台、主窗口是否隐藏都会显示）
- **打开程序后默认静默运行在系统托盘**，不在桌面弹出窗口
- 窗口、任务栏与系统托盘统一使用同一个麦克风图标
- **关闭窗口（Alt+F4 / ✕）最小化到系统托盘**，录音和上传在后台继续；托盘右键可显示窗口、开始/停止录音、重启或关闭程序
- 录音保存在本地，可随时播放、删除
- 配置 R2 后自动上传；失败自动重试（最多 3 次）
- 列表显示每条录音的上传状态（待上传 / 上传中 / 已上传 / 上传失败）
- 支持「重试」「重试全部」
- 配置保存在项目文件夹的 `.env` 中，界面内即可编辑
- 跟随系统深色 / 浅色主题（可用环境变量 `R2_RECORDER_THEME=dark|light` 强制）

## 运行

需要 Python 3.10+，并安装依赖：

```bat
cd /d D:\files\projects\tmp\PC_APP
pip install -r requirements.txt
python main.py
```

也可以双击 `run.bat`，或使用：

```bat
python -m r2recorder
```

### ffmpeg（可选）

安装了 `ffmpeg` 并加入 `PATH` 后，录音会压缩为 M4A（AAC）并支持播放更多格式；
没有 ffmpeg 时自动使用 WAV，功能不受影响。

## 配置 Cloudflare R2

1. 在 Cloudflare 控制台创建一个 R2 Bucket。
2. 创建一个仅允许访问该 Bucket 的 API Token（建议权限：Object Read & Write）。
3. 在应用右上角打开「R2 设置」，填写：
   - 账户 ID（Account ID）
   - 存储桶名称（Bucket）
   - Access Key ID / Secret Access Key
   - 公开访问域名（可选，用于列表里的「打开」链接）
   - 对象前缀（可选，默认 `recordings/`）
4. 可先「测试连接」（需要 Token 有读取权限），再保存。

配置同样可以直接写入项目根目录的 `.env`（参考 `.env.example`）。

> 安全提示：Access Key 和 Secret 以明文保存在 `.env` 中，请勿提交到 Git 或分享给他人。

## 关闭与退出

- 启动应用后不会弹出主窗口，程序直接待在系统托盘；需要界面时双击托盘图标（或右键「显示主窗口」）。
- 点击窗口关闭按钮或按 **Alt+F4**：窗口隐藏到系统托盘，录音和上传在后台继续。
- 此时 **F9 全局快捷键仍然有效**，按 F9 即可开始 / 停止录音，结果通过 Windows 系统通知提示。
- 双击托盘图标（或右键选择「显示主窗口」）：恢复窗口。
- 托盘右键菜单可以直接「开始录音 / 停止录音」。
- 托盘右键菜单还提供 **「重启(&R)」** 和 **「关闭(&X)」**：
  - 重启：若正在录音会先提示并保存，然后重新启动程序
  - 关闭：真正退出程序
- 也可以按 **Ctrl+Q** 退出；若正在录音，会先提示保存。

## 数据位置

| 内容 | 路径 |
| --- | --- |
| 录音文件 | `data/recordings/` |
| 录音元数据 | `data/recordings.json` |
| 播放缓存 | `data/cache/` |
| 日志 | `data/app.log` |
| 配置 | `.env` |

可通过环境变量 `R2_RECORDER_DATA_DIR` 把数据目录改到其它位置。

## 目录结构

```
PC_APP/
├── main.py                 # 启动脚本
├── app.ico                 # 程序图标（16–256 多种尺寸）
├── requirements.txt
├── .env.example
├── run.bat
└── r2recorder/
    ├── __main__.py         # python -m r2recorder 入口
    ├── audio.py            # 录音、播放、ffmpeg 编码
    ├── config.py           # .env 读写与 R2Config
    ├── formats.py          # 时间 / 大小格式化
    ├── hotkey.py           # Windows 全局快捷键（F9）
    ├── model.py            # 录音记录与 JSON 仓库
    ├── paths.py            # 目录与文件路径
    ├── r2.py               # 最小 S3 客户端（AWS SigV4）
    ├── upload.py           # 后台上传队列
    ├── util.py
    └── ui/
        ├── main_window.py  # 主窗口
        ├── settings_dialog.py
        ├── theme.py
        ├── toast.py        # 窗口内 toast 提示
        ├── tray.py         # 系统托盘图标与菜单
        └── widgets.py
```
