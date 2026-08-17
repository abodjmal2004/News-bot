# 📡 News Bot - Automated Telegram News Publisher

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![Telegram](https://img.shields.io/badge/Bot-Telegram-blue.svg)](https://core.telegram.org/bots)
[![Framework](https://img.shields.io/badge/Framework-python--telegram--bot-green.svg)](https://python-telegram-bot.org/)
[![Database](https://img.shields.io/badge/Database-SQLite3-lightgrey.svg)](https://www.sqlite.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An advanced, robust, and automated Telegram bot designed to fetch breaking news from live feeds (GraphQL APIs) and instantly broadcast them across multiple Telegram channels and groups. Built with **Python**, **Asyncio**, and **SQLite**.

---

## ✨ Key Features

- **⚡ Real-time Breaking News:** Automatically polls GraphQL endpoints (e.g., Al Jazeera Mubasher) for breaking news updates.
- **📢 Multi-Channel Broadcasting:** Broadcast news effortlessly across multiple channels and groups simultaneously.
- **🛡 Robust Error Handling & Recovery:** Built-in auto-retry mechanisms for network errors, rate limits (Flood control), and database integrity.
- **🔔 Admin Alert System:** Automatically notifies the administrator in case of critical API or runtime errors.
- **🗄 SQLite Persistence:** Ensures no duplicate news are ever published using MD5 hashing and local storage.
- **⚙️ Complete Control Panel:** Interactive management dashboard for administrators to monitor stats, add/remove channels, and manage bot settings.

---

## 🛠 Tech Stack

- **Language:** Python 3.10+
- **Bot Framework:** [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- **Database:** SQLite3 (Zero-configuration persistent storage)
- **Networking:** `requests` & `aiohttp` for asynchronous and synchronous API requests
- **Concurrency:** `asyncio` for non-blocking task scheduling and broadcasting

---

## 🚀 Installation & Setup

### 1. Clone the Repository
```bash
git clone https://github.com/abodjmal2004/News-bot.git
cd News-bot
```

### 2. Install Dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure Credentials
Open `main_bot.py` and set your `BOT_TOKEN` and `ADMIN_USER_ID`:
```python
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
ADMIN_USER_ID = YOUR_TELEGRAM_ID
```

### 4. Run the Bot
```bash
python main_bot.py
```

---

## 🔒 Security & Privacy
This bot keeps administrative controls strictly secured by verifying the `ADMIN_USER_ID` for all sensitive commands. Never expose your bot token publicly.

---

## 👨‍💻 Developer

**Abod Jamal** — Software Developer & Computer Science Graduate  
Passionate about building modern applications, clean architecture, automation, and smooth user experiences. Based in Gaza, Palestine.

### 🌐 Connect With Me
[![Telegram](https://img.shields.io/badge/Telegram-Contact-blue?logo=telegram)](https://t.me/xw_25aa)
[![Instagram](https://img.shields.io/badge/Instagram-Follow-E4405F?logo=instagram&logoColor=white)](https://instagram.com/xw_.0)
[![GitHub](https://img.shields.io/badge/GitHub-abodjmal2004-black?logo=github)](https://github.com/abodjmal2004)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Abod%20Jamal-blue?logo=linkedin)](https://www.linkedin.com/in/abod-jamal-dev/)

---

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---
<p align="center">
  Developed with ❤️ for Automated News Delivery.
</p>
