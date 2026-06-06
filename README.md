# Discord × GitHub OAuth 連携 Bot

Discord のボタンから GitHub OAuth 認証を行い、管理者承認後に GitHub 組織へ自動招待するシステムです。

## システム構成

```
discord-github-bot/
├── bot/
│   ├── bot.py           # Discord Bot 本体
│   ├── db.py            # SQLite データ層
│   └── requirements.txt
├── server/
│   ├── main.py          # OAuth コールバックサーバー（Vercel）
│   └── requirements.txt
├── vercel.json          # Vercel デプロイ設定
└── .env.example         # 環境変数テンプレート
```

## セットアップ手順

### 1. Discord Bot の作成

1. [Discord Developer Portal](https://discord.com/developers/applications) を開く
2. **New Application** → Bot タブ → **Add Bot**
3. **TOKEN** をコピー → `.env` の `DISCORD_TOKEN` に設定
4. **Privileged Gateway Intents** で `SERVER MEMBERS INTENT` を有効化
5. OAuth2 タブで以下のスコープを選択してサーバーに招待:
   - `bot`, `applications.commands`
   - Bot Permissions: `Send Messages`, `Embed Links`, `Read Message History`, `Use Slash Commands`

### 2. GitHub OAuth App の作成

1. [GitHub Settings > Developer settings > OAuth Apps](https://github.com/settings/developers) を開く
2. **New OAuth App** を作成:
   - **Homepage URL**: `https://your-app.vercel.app`
   - **Authorization callback URL**: `https://your-app.vercel.app/auth/callback`
3. **Client ID** と **Client Secret** をコピー → `.env` に設定

### 3. GitHub Personal Access Token の作成

1. [GitHub Settings > Developer settings > Personal access tokens](https://github.com/settings/tokens)
2. **Generate new token (classic)**
3. スコープ: `admin:org` を選択（組織への招待権限）
4. トークンをコピー → `.env` の `GITHUB_TOKEN` に設定

### 4. Vercel へデプロイ（OAuth サーバー）

```bash
# Vercel CLI インストール
npm i -g vercel

# リポジトリルートで実行
vercel

# 環境変数を設定
vercel env add GITHUB_CLIENT_ID
vercel env add GITHUB_CLIENT_SECRET
vercel env add DISCORD_BOT_WEBHOOK
vercel env add WEBHOOK_SECRET
vercel env add OAUTH_SERVER_URL
```

### 5. Bot のホスティング（Railway / Render 推奨）

```bash
# ローカルでの起動
cd bot
pip install -r requirements.txt
cp ../.env.example .env   # .env を編集して各値を設定
python bot.py
```

**Railway へのデプロイ例:**
1. Railway で新プロジェクト作成 → GitHub リポジトリを接続
2. `bot/` ディレクトリをルートに設定
3. Environment Variables に `.env` の内容を貼り付け
4. デプロイ後の URL を `DISCORD_BOT_WEBHOOK` に設定

### 6. Bot のセットアップコマンド実行

Discord サーバーで管理者として以下を実行:

```
/setup_auth
```

指定チャンネルに連携ボタンパネルが設置されます。

---

## フロー説明

```
ユーザー
  ↓ ①「GitHubアカウントを連携する」ボタンを押す
Discord Bot
  ↓ ② GitHub OAuth URL（state=discord_id）を DM でエフェメラル表示
Webサーバー（Vercel）
  ↓ ③ GitHub OAuth ページへリダイレクト
GitHub
  ↓ ④ 認証後、/auth/callback へリダイレクト
Webサーバー
  ↓ ⑤ アクセストークンでユーザー名取得 → Bot に Webhook POST
Bot（Webhook 受信）
  ↓ ⑥ DB に discord_id ⇔ github_username を保存
  ↓ ⑦ 管理者チャンネルに承認/拒否ボタン付き Embed を送信
管理者
  ↓ ⑧ 「承認」ボタンを押す
Bot
  ↓ ⑨ GitHub API で組織招待
  ↓ ⑩ ユーザーに DM で通知
```

## 注意事項

- **本番環境の state 管理**: Vercel はステートレスなため、`_state_store` を [Upstash Redis](https://upstash.com/) や [Vercel KV](https://vercel.com/docs/storage/vercel-kv) に置き換えてください。`server/main.py` のコメントを参照。
- **SQLite の永続化**: Railway / Render 等では再デプロイ時に SQLite が消えることがあります。永続ボリュームの設定か PostgreSQL への移行を推奨します。
- **WEBHOOK_SECRET**: Bot と Webサーバー間の通信を保護するため、必ず設定してください。

## コマンド一覧

| コマンド | 説明 | 権限 |
|---|---|---|
| `/setup_auth` | 連携ボタンパネルを設置 | 管理者 |
| `/status` | 自分の連携状況を確認 | 全員 |
