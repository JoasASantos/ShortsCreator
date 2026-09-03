import type { DocsPage } from "./types";

export const docsZh: DocsPage = {
  title: "安装",
  intro: "快速指南",
  sections: [
    {
      title: "环境要求",
      blocks: [
        {
          kind: "ul",
          items: [
            "**Python 3.11+** — 后端与媒体流水线",
            "**Node 20+** — 网页界面",
            "**FFmpeg** — 视频合成（必需）",
            "一个 LLM：已登录的 **Claude Code** 或 **Codex** CLI（直接用你的订阅，无需密钥），或者 Anthropic/OpenAI 的 API 密钥，或者本地 Ollama",
          ],
        },
      ],
    },
    {
      title: "1. 安装 FFmpeg",
      blocks: [
        {
          kind: "code",
          text: `# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# Windows (winget)
winget install Gyan.FFmpeg`,
        },
        {
          kind: "p",
          text: "如果你的 FFmpeg 不带 `libass`，一切照样可用：流水线会检测到缺失，并把字幕渲染成本地生成的 PNG 图片烧进视频。",
        },
      ],
    },
    {
      title: "2. 后端",
      blocks: [
        {
          kind: "code",
          text: `git clone <你的仓库> ShortsCreator && cd ShortsCreator
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
pip install -r backend/requirements.txt
cp .env.example .env             # 填入密钥`,
        },
      ],
    },
    {
      title: "3. 配置 .env",
      blocks: [
        {
          kind: "p",
          text: "最省钱的做法是复用你已经在付费的订阅，而不是按 token 计费的 API 密钥。后端会调用本地已登录的 CLI：",
        },
        {
          kind: "code",
          text: `# 链式调用：上一个模型失败就换下一个
LLM_PROVIDER=chain
LLM_CHAIN=claude_cli:claude-fable-5-1,claude_cli:claude-opus-5,codex_cli:gpt-5.6-sol

TTS_PROVIDER=edge                # 免费，pt-BR 神经语音
EDGE_VOICE=pt-BR-AntonioNeural`,
        },
        {
          kind: "p",
          text: "每一环的写法是 `provider:模型`。Fable 是主模型，Opus 5 是备用，Codex（GPT-5.6 Sol）排在最后。前两环通过 Claude Code 使用 Claude Pro/Max；第三环通过 `codex login` 使用 ChatGPT Plus/Pro。若只想固定一个模型，改成 `LLM_PROVIDER=claude_cli` 并配上 `CLAUDE_CLI_MODEL=claude-fable-5-1`。",
        },
        {
          kind: "p",
          text: "这种模式下 `.env` 里不需要任何密钥。用 `claude --version` 或 `codex login status` 确认登录状态 — 侧边栏的 **Auth** 指示器会显示 `订阅`，链上的每一环也会列在下方，显示所用模型以及是否可用。",
        },
        {
          kind: "p",
          text: "每次调用需要 25 到 40 秒，因为底层跑的是一个完整的智能体。如果你更在意延迟、也不介意按 token 付费，就用密钥模式：",
        },
        {
          kind: "code",
          text: `LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-opus-5`,
        },
        {
          kind: "p",
          text: "也可以完全离线运行：把 `LLM_PROVIDER=ollama` 指向一个本地模型即可。",
        },
        { kind: "p", text: "几个能明显提升成品质量的可选项：" },
        {
          kind: "ul",
          items: [
            "`PEXELS_API_KEY` — 用真实的竖屏 B-roll 取代渐变背景",
            "`ELEVENLABS_API_KEY` — 角色克隆语音，且带精确时间轴",
            "`YOUTUBE_CLIENT_SECRETS` 和 `TIKTOK_CLIENT_KEY` — 发布",
            "`TELEGRAM_BOT_TOKEN` 或 `DISCORD_WEBHOOK_URL` — 短视频完成或失败时通知你",
          ],
        },
        {
          kind: "p",
          text: "这些也都可以在 **账号** 页面里填写，它会写入数据库并覆盖 `.env` — 适合不想把密钥留在文件里的情况。",
        },
      ],
    },
    {
      title: "4. 启动两个服务",
      blocks: [
        {
          kind: "code",
          text: `# 终端 1 — API
make api        # 或：uvicorn app.main:app --app-dir backend --reload

# 终端 2 — 界面
make web        # 或：cd web && npm install && npm run dev`,
        },
        {
          kind: "p",
          text: "界面在 `http://localhost:3000`，API 在 `http://localhost:8000`（交互式文档在 `/docs`）。",
        },
      ],
    },
    {
      title: "5. 连接发布账号",
      blocks: [
        {
          kind: "p",
          text: "**YouTube：** 在 Google Cloud Console 中启用 **YouTube Data API v3**，创建一个 **Web 应用** 类型的 OAuth 凭据，重定向 URI 填 `http://localhost:8000/api/publish/youtube/callback`，然后把 JSON 保存到 `data/secrets/youtube_client_secret.json`。",
        },
        {
          kind: "p",
          text: "**TikTok：** 在 TikTok for Developers 注册一个应用，申请 `video.upload`、`video.publish` 和 `video.list` 权限（最后一个用于获取数据指标）。在应用通过审核之前，上传的内容只会进入应用的草稿箱 — 这是平台的限制，不是项目的问题。",
        },
        {
          kind: "p",
          text: "**Instagram Reels：** 需要一个关联到 Facebook 主页的专业账号，以及带 `instagram_content_publish` 权限的长期令牌。有个常见的坑：Graph API 不接受上传，而是从一个公网 URL **下载** MP4。在本机运行时，先开一条隧道，再把 `PUBLIC_API_URL` 指向它：",
        },
        {
          kind: "code",
          text: `cloudflared tunnel --url http://localhost:8000
# 或：ngrok http 8000
# 然后写进 .env：
PUBLIC_API_URL=https://你的公网地址`,
        },
        {
          kind: "p",
          text: "**LinkedIn：** 需要带 `w_member_social` 的令牌。如果令牌同时具备 `openid` 和 `profile`，作者 URN 可以留空 — 这种情况下它会自动获取。",
        },
      ],
    },
    {
      title: "6. 闭合循环",
      blocks: [
        {
          kind: "p",
          text: "第一次发布之后，系统就开始从你自己的频道里学习：",
        },
        {
          kind: "ul",
          items: [
            "**趋势** — 按细分领域和地区汇总的热门话题，来自 Google Trends、Reddit、Hacker News 和 YouTube。不需要任何密钥。点一下就能打开已填好的表单。",
            "**批量** — 一期播客或一堂长课可以变成多条短视频：先转写，再由模型挑出最精彩的片段，每个片段都走完整流水线。可以一次排好每天发一条。",
            "**钩子测试** — 在做好的短视频上生成三个采用不同机制的钩子，用视频原本的声音逐个试听后替换。也可以把 B 版本另存为一条独立短视频，两条一起发。",
            "**表现** — 播放量、点赞、评论和平均留存率每 6 小时自动回传。留存表现最好的钩子会回到下一条脚本的提示词里，作为机制层面的参考 — 绝不照搬原文。",
          ],
        },
      ],
    },
    {
      title: "角色配音",
      blocks: [
        {
          kind: "p",
          text: "用角色的声音来配旁白有两条路。在 **ElevenLabs** 上，在他们的面板里克隆声音，再把 `voice_id` 粘贴到「语音」页面。用 **本地 XTTS** 时，先启动服务器，然后上传一段 6 到 30 秒的样本 — 完全离线运行，也不按字符计费。",
        },
        {
          kind: "code",
          text: `# 用 Docker 运行本地 XTTS
docker run -d --name xtts -p 8020:80 \\
  ghcr.io/coqui-ai/xtts-streaming-server:latest`,
        },
      ],
    },
    {
      title: "QA 会检查什么",
      blocks: [
        {
          kind: "p",
          text: "每次渲染都会对最终文件做一次自动审核 — 而不是对流水线自认为产出的东西：",
        },
        {
          kind: "ul",
          items: [
            "分辨率精确为 1080×1920，比例 9:16，SAR 1:1",
            "没有黑边（对视频抽样运行 `cropdetect`）",
            "H.264 采用 `yuv420p`，30 fps，`moov` 原子位于文件开头",
            "AAC 音频归一化到 −14 LUFS，真峰值低于 −1 dBTP",
            "开头的静音（会毁掉钩子）以及结尾多余的静音",
            "字幕避开被 App 界面占用的底部 340 px",
            "字幕在整段视频中的覆盖率，以及时长处在有效区间内",
          ],
        },
      ],
    },
    {
      title: "重做而不消耗订阅",
      blocks: [
        {
          kind: "p",
          text: "脚本和旁白的时间轴都保存在任务目录里。**重新处理** 按钮会打开一个菜单，列出可以从哪些阶段续跑 — 选择 `字幕` 就会复用脚本和语音，只重做后面的步骤。如果服务器在渲染途中挂掉，任务在重启后会自行续跑。",
        },
      ],
    },
    {
      title: "测试",
      blocks: [
        {
          kind: "code",
          text: `make test        # 整个后端
make test-fast   # 跳过需要 FFmpeg 的部分
make typecheck   # 前端类型
make check       # 两者都跑`,
        },
        {
          kind: "p",
          text: "QA 与同步相关的测试会生成真实的 MP4 和 MP3，并审核生成的文件。如果 PATH 里没有 FFmpeg，这些用例会被跳过而不是失败。",
        },
      ],
    },
    {
      title: "常见问题",
      blocks: [
        {
          kind: "ul",
          items: [
            "**「找不到 yt-dlp」** — 在与 API 相同的虚拟环境里用 `pip install yt-dlp` 安装。",
            "**成品视频没有字幕** — 检查 `assets/fonts/` 或系统里是否存在 TrueType 字体。",
            "**没有背景音乐** — 把无版权的 `.mp3` 文件放到 `assets/music/`。",
            "**QA 因时长不合格** — 在表单里调整目标时长；脚本长度是按它推算的。",
            "**「No space left on device」** — 渲染会占用大量临时磁盘空间。`make clean` 会清理旧任务、缓存和输出。",
            "**Instagram 报错 2207026 拒绝发布** — 视频必须是 9:16，而且公网 URL 必须能从外网访问。可以关掉 Wi-Fi，用手机打开 `PUBLIC_API_URL/api/outputs/<job_id>.mp4` 测试。",
            "**YouTube 上看不到留存率** — 在 `yt-analytics.readonly` 权限出现之前连接的账号只能拿到公开统计数据。到「账号」页面断开再重新连接即可。",
            "**TikTok 指标为空** — 落进草稿箱的上传没有公开的视频 id，也就无从统计。",
          ],
        },
      ],
    },
  ],
};
