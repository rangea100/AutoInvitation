@echo off
cd /d C:\Users\kenyu\OneDrive\ドキュメント\作業場\mypython\discordBot\AutoInvitation\bot

:: ngrokをバックグラウンドで起動
start "" ngrok http --domain=tulip-tiger-craftwork.ngrok-free.dev 8081

:: 3秒待ってからBot起動
timeout /t 3 /nobreak > nul

:: Bot起動
call .venv\Scripts\activate
python bot.py