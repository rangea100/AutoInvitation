"""
OAuth コールバックサーバー - Vercel / Cloudflare Workers 向け
GitHub OAuth フローを処理し、Discord Bot に Webhook で通知する

デプロイ: Vercel (Python Serverless Functions)
"""

import os
import secrets
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI()

# ─────────────────────────────────────────
#  環境変数
# ─────────────────────────────────────────
GITHUB_CLIENT_ID     = os.environ["GITHUB_CLIENT_ID"]
GITHUB_CLIENT_SECRET = os.environ["GITHUB_CLIENT_SECRET"]
DISCORD_BOT_WEBHOOK  = os.environ["DISCORD_BOT_WEBHOOK"]   # Bot の Webhook エンドポイント
WEBHOOK_SECRET       = os.environ.get("WEBHOOK_SECRET", "")
BASE_URL             = os.environ["OAUTH_SERVER_URL"]        # https://your-app.vercel.app

# state -> discord_id マッピング（本番では Redis/KV を使用してください）
# Vercel Serverless は stateless なので KV Store 必須
# ここでは Vercel KV / Upstash Redis を使う例を示す
_state_store: dict[str, str] = {}   # state -> discord_id（開発用インメモリ）


# ─────────────────────────────────────────
#  ステップ1: GitHub OAuth リダイレクト
# ─────────────────────────────────────────
@app.get("/auth/github")
async def start_oauth(request: Request, discord_id: str):
    """
    Discord Bot から誘導されるエンドポイント。
    state を生成して GitHub OAuth ページにリダイレクトする。
    """
    if not discord_id:
        raise HTTPException(status_code=400, detail="discord_id is required")

    # CSRF対策: ランダムなstateを生成
    state = secrets.token_urlsafe(32)
    _state_store[state] = discord_id  # 本番: KV.set(state, discord_id, ex=600)

    github_oauth_url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={BASE_URL}/auth/callback"
        f"&scope=read:user"
        f"&state={state}"
    )

    return RedirectResponse(url=github_oauth_url)


# ─────────────────────────────────────────
#  ステップ2: GitHub OAuth コールバック
# ─────────────────────────────────────────
@app.get("/auth/callback")
async def oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """
    GitHub からのコールバック。
    code と state を受け取り、アクセストークンを取得して GitHub ユーザー名を解決する。
    """
    # エラーチェック
    if error:
        return HTMLResponse(content=_render_result(
            success=False,
            message=f"GitHub認証がキャンセルされました: {error}"
        ))

    # state 検証
    discord_id = _state_store.pop(state, None)  # 本番: KV.get(state) then KV.delete(state)
    if not discord_id:
        return HTMLResponse(content=_render_result(
            success=False,
            message="無効または期限切れのリクエストです。もう一度 Discord からやり直してください。"
        ))

    async with httpx.AsyncClient() as client:
        # アクセストークン取得
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id":     GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code":          code,
                "redirect_uri":  f"{BASE_URL}/auth/callback",
            },
            headers={"Accept": "application/json"},
        )
        token_data = token_resp.json()

        if "error" in token_data:
            return HTMLResponse(content=_render_result(
                success=False,
                message=f"トークン取得に失敗しました: {token_data.get('error_description', token_data['error'])}"
            ))

        access_token = token_data["access_token"]

        # GitHub ユーザー情報取得
        user_resp = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        if user_resp.status_code != 200:
            return HTMLResponse(content=_render_result(
                success=False,
                message="GitHub ユーザー情報の取得に失敗しました。"
            ))

        github_user = user_resp.json()
        github_username = github_user["login"]

        # Discord ユーザー名を取得（Bot経由では困難なのでID表示に留める）
        # ここでは discord_id をそのまま渡す
        discord_username = f"user_{discord_id}"

        # Discord Bot に Webhook 通知
        webhook_headers = {
            "Content-Type": "application/json",
            "ngrok-skip-browser-warning": "true",  # ← この行を追加
        }
        if WEBHOOK_SECRET:
            webhook_headers["X-Webhook-Secret"] = WEBHOOK_SECRET

        bot_resp = await client.post(
            f"{DISCORD_BOT_WEBHOOK}/webhook/oauth_complete",
            json={
                "discord_id":       discord_id,
                "github_username":  github_username,
                "discord_username": discord_username,
            },
            headers=webhook_headers,
            timeout=10.0,
        )

        if bot_resp.status_code != 200:
            return HTMLResponse(content=_render_result(
                success=False,
                message="Bot への通知に失敗しました。管理者にお問い合わせください。"
            ))

    return HTMLResponse(content=_render_result(
        success=True,
        message=f"GitHub アカウント <strong>@{github_username}</strong> の連携が完了しました！\n"
                "管理者の承認をお待ちください。このページは閉じて構いません。"
    ))


# ─────────────────────────────────────────
#  ヘルスチェック
# ─────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok"}


# ─────────────────────────────────────────
#  HTML レスポンス生成
# ─────────────────────────────────────────
def _render_result(success: bool, message: str) -> str:
    color  = "#5865F2" if success else "#ED4245"
    icon   = "✅" if success else "❌"
    title  = "連携完了" if success else "エラー"

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>GitHub連携 - {title}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      display: flex; align-items: center; justify-content: center;
      min-height: 100vh; background: #36393f; color: #dcddde;
    }}
    .card {{
      background: #2f3136; border-radius: 12px; padding: 48px 40px;
      max-width: 480px; width: 90%; text-align: center;
      box-shadow: 0 8px 32px rgba(0,0,0,0.4);
    }}
    .icon {{ font-size: 56px; margin-bottom: 16px; }}
    h1 {{ font-size: 22px; font-weight: 600; color: {color}; margin-bottom: 16px; }}
    p {{ font-size: 15px; line-height: 1.7; color: #b9bbbe; }}
    .badge {{
      display: inline-block; background: #40444b; border-radius: 4px;
      padding: 2px 8px; font-family: monospace; font-size: 14px; color: #dcddde;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">{icon}</div>
    <h1>{title}</h1>
    <p>{message}</p>
  </div>
</body>
</html>"""
