"""
Discord Bot - GitHub OAuth 連携システム
メインBot処理: 認証ボタン表示・管理者通知・承認処理・GitHub招待・DM通知
"""

import os
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
from dotenv import load_dotenv

from db import Database

load_dotenv()

# ─────────────────────────────────────────
#  設定
# ─────────────────────────────────────────
DISCORD_TOKEN        = os.environ["DISCORD_TOKEN"]
ADMIN_CHANNEL_ID     = int(os.environ["ADMIN_CHANNEL_ID"])
GITHUB_ORG           = os.environ["GITHUB_ORG"]
GITHUB_TOKEN         = os.environ["GITHUB_TOKEN"]          # org:write権限が必要
OAUTH_SERVER_URL     = os.environ["OAUTH_SERVER_URL"]      # https://your-app.vercel.app

# ─────────────────────────────────────────
#  Intents & Bot
# ─────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
db  = Database()


# ─────────────────────────────────────────
#  UI: 認証開始ボタン
# ─────────────────────────────────────────
class AuthView(discord.ui.View):
    """
    ユーザーに表示する「GitHub連携」ボタン。
    押すと GitHub OAuth URL をエフェメラルで返す。
    """

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="GitHubアカウントを連携する",
        style=discord.ButtonStyle.primary,
        custom_id="auth_start",
        emoji="🔗",
    )
    async def auth_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        discord_id = str(interaction.user.id)

        # 既に連携済みならスキップ
        existing = db.get_github_id(discord_id)
        if existing:
            await interaction.response.send_message(
                f"✅ すでに GitHub アカウント（`{existing}`）と連携済みです。",
                ephemeral=True,
            )
            return

        # stateパラメータにDiscord IDを埋め込む
        oauth_url = (
            f"{OAUTH_SERVER_URL}/auth/github"
            f"?discord_id={discord_id}"
        )

        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label="GitHubで認証する",
                url=oauth_url,
                style=discord.ButtonStyle.link,
                emoji="🐱",
            )
        )

        await interaction.response.send_message(
            "下のボタンから GitHub にログインして連携を完了してください。\n"
            "⚠️ このリンクは **あなた専用** です。他の人に共有しないでください。",
            view=view,
            ephemeral=True,
        )


# ─────────────────────────────────────────
#  UI: 管理者承認ボタン
# ─────────────────────────────────────────
class ApprovalView(discord.ui.View):
    """
    管理者チャンネルに送る承認/拒否ボタン。
    custom_id にターゲットの discord_id を埋め込む。
    """

    def __init__(self, discord_id: str):
        super().__init__(timeout=None)
        self.discord_id = discord_id

        self.approve_btn = discord.ui.Button(
            label="承認",
            style=discord.ButtonStyle.success,
            custom_id=f"approve_{discord_id}",
            emoji="✅",
        )
        self.reject_btn = discord.ui.Button(
            label="拒否",
            style=discord.ButtonStyle.danger,
            custom_id=f"reject_{discord_id}",
            emoji="❌",
        )
        self.approve_btn.callback = self.approve_callback
        self.reject_btn.callback  = self.reject_callback
        self.add_item(self.approve_btn)
        self.add_item(self.reject_btn)

    async def approve_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        github_id = db.get_github_id(self.discord_id)
        if not github_id:
            await interaction.followup.send("⚠️ GitHubアカウント情報が見つかりません。", ephemeral=True)
            return

        # GitHub組織に招待
        success, error_msg = await invite_to_github_org(github_id)

        if success:
            db.set_status(self.discord_id, "approved")

            # 管理者チャンネルのメッセージを更新
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.green()
            embed.set_footer(text=f"✅ {interaction.user.display_name} が承認しました")
            for item in self.children:
                item.disabled = True
            await interaction.message.edit(embed=embed, view=self)

            # ユーザーにDM通知
            await send_dm(
                self.discord_id,
                "🎉 **GitHub組織への参加が承認されました！**\n\n"
                f"`{GITHUB_ORG}` から招待メールが届いているか、"
                "GitHub の通知を確認してください。\n"
                "https://github.com/orgs/" + GITHUB_ORG + "/invitation",
            )
        else:
            await interaction.followup.send(
                f"⚠️ GitHub招待に失敗しました: {error_msg}", ephemeral=True
            )

    async def reject_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()

        db.set_status(self.discord_id, "rejected")

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.set_footer(text=f"❌ {interaction.user.display_name} が拒否しました")
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(embed=embed, view=self)

        await send_dm(
            self.discord_id,
            "😔 **GitHub組織への参加申請が拒否されました。**\n\n"
            "詳細については管理者にお問い合わせください。",
        )


# ─────────────────────────────────────────
#  ヘルパー: GitHub 組織招待
# ─────────────────────────────────────────
async def invite_to_github_org(github_username: str) -> tuple[bool, str]:
    """
    GitHub REST API で組織にユーザーを招待する。
    Returns (success: bool, error_message: str)
    """
    url = f"https://api.github.com/orgs/{GITHUB_ORG}/invitations"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {"invitee_id": None}  # username で招待する場合は下で解決

    # まずユーザー ID を取得
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://api.github.com/users/{github_username}",
            headers=headers,
        ) as resp:
            if resp.status != 200:
                return False, f"GitHub ユーザーが見つかりません: {github_username}"
            user_data = await resp.json()
            payload["invitee_id"] = user_data["id"]

        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status in (201, 200):
                return True, ""
            elif resp.status == 422:
                # 既にメンバー or 招待済みの場合もある
                body = await resp.json()
                msg = body.get("message", "Unknown error")
                if "already" in msg.lower():
                    return True, ""   # 実質成功扱い
                return False, msg
            else:
                body = await resp.json()
                return False, body.get("message", f"Status {resp.status}")


# ─────────────────────────────────────────
#  ヘルパー: Discord DM 送信
# ─────────────────────────────────────────
async def send_dm(discord_id: str, message: str):
    try:
        user = await bot.fetch_user(int(discord_id))
        await user.send(message)
    except discord.Forbidden:
        print(f"[WARN] DM送信失敗: {discord_id} (DM受信拒否の可能性)")
    except Exception as e:
        print(f"[ERROR] DM送信エラー: {e}")


# ─────────────────────────────────────────
#  ヘルパー: 管理者チャンネルに申請通知
# ─────────────────────────────────────────
async def notify_admin(discord_id: str, github_username: str, discord_username: str):
    """
    OAuth完了後にWebサーバーからHTTP POSTで呼ばれることを想定。
    Bot内部でも直接呼べる。
    """
    channel = bot.get_channel(ADMIN_CHANNEL_ID)
    if channel is None:
        channel = await bot.fetch_channel(ADMIN_CHANNEL_ID)

    embed = discord.Embed(
        title="🔔 GitHub連携申請",
        description=(
            f"**Discord ユーザー**: <@{discord_id}> (`{discord_username}`)\n"
            f"**GitHub アカウント**: [`{github_username}`](https://github.com/{github_username})\n\n"
            f"組織 `{GITHUB_ORG}` への招待を承認または拒否してください。"
        ),
        color=discord.Color.blurple(),
    )
    embed.set_thumbnail(url=f"https://github.com/{github_username}.png?size=128")

    view = ApprovalView(discord_id)
    await channel.send(embed=embed, view=view)


# ─────────────────────────────────────────
#  Slash コマンド
# ─────────────────────────────────────────
@bot.tree.command(name="setup_auth", description="GitHub連携パネルを設置します（管理者用）")
@app_commands.checks.has_permissions(administrator=True)
async def setup_auth(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🔗 GitHub アカウント連携",
        description=(
            "下のボタンを押して GitHub アカウントを連携し、\n"
            f"`{GITHUB_ORG}` 組織への参加を申請してください。\n\n"
            "連携後、管理者が承認すると組織に招待されます。"
        ),
        color=discord.Color.blurple(),
    )
    await interaction.response.send_message(embed=embed, view=AuthView())


@bot.tree.command(name="status", description="自分の連携状況を確認します")
async def check_status(interaction: discord.Interaction):
    discord_id  = str(interaction.user.id)
    github_id   = db.get_github_id(discord_id)
    status      = db.get_status(discord_id)

    status_map = {
        "pending":  "⏳ 審査中",
        "approved": "✅ 承認済み",
        "rejected": "❌ 拒否",
        None:       "未申請",
    }

    embed = discord.Embed(title="連携状況", color=discord.Color.blurple())
    embed.add_field(name="GitHub アカウント", value=f"`{github_id}`" if github_id else "未連携")
    embed.add_field(name="ステータス", value=status_map.get(status, status))
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─────────────────────────────────────────
#  Bot 内部 HTTP サーバー（Webhook受信用）
# ─────────────────────────────────────────
from aiohttp import web

async def webhook_handler(request: web.Request):
    """
    Webサーバー（Vercel等）からのOAuth完了通知を受け取る。
    POST JSON: { "discord_id": "...", "github_username": "...", "discord_username": "..." }
    セキュリティ: shared secret で検証
    """
    secret = os.environ.get("WEBHOOK_SECRET", "")
    auth_header = request.headers.get("X-Webhook-Secret", "")

    if secret and auth_header != secret:
        return web.Response(status=403, text="Forbidden")

    try:
        data = await request.json()
        discord_id       = data["discord_id"]
        github_username  = data["github_username"]
        discord_username = data.get("discord_username", "Unknown")

        db.save_link(discord_id, github_username)
        db.set_status(discord_id, "pending")

        asyncio.create_task(
            notify_admin(discord_id, github_username, discord_username)
        )

        return web.Response(status=200, text="OK")
    except Exception as e:
        print(f"[ERROR] Webhook処理エラー: {e}")
        return web.Response(status=500, text=str(e))


async def start_webhook_server():
    app = web.Application()
    app.router.add_post("/webhook/oauth_complete", webhook_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("WEBHOOK_PORT", 8080)))
    await site.start()
    print(f"[INFO] Webhookサーバー起動 port={os.environ.get('WEBHOOK_PORT', 8080)}")


# ─────────────────────────────────────────
#  Bot イベント
# ─────────────────────────────────────────
@bot.event
async def on_ready():
    await start_webhook_server()
    # Persistent View を再登録（Bot再起動後もボタンが機能するように）
    bot.add_view(AuthView())

    # 既存の承認待ちレコードから ApprovalView を復元
    pending = db.get_all_pending()
    for discord_id in pending:
        bot.add_view(ApprovalView(discord_id))

    await bot.tree.sync()
    print(f"[INFO] Botログイン完了: {bot.user} (id={bot.user.id})")


# ─────────────────────────────────────────
#  エントリーポイント
# ─────────────────────────────────────────
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
