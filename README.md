# 👑 Royal God Aryan's Idle Explorer Bot

A powerful, asynchronous Telegram userbot designed to automate background tasks and resource gathering. Built to handle multiple accounts with perfect synchronization, this bot mimics human behavior to bypass rate limits while effortlessly managing a massive digital workforce.

## 🆕 Added Features

*   **Claim reward and re-explore :** Now can claim reward and reexplore with one touch, altho old only claim button exists.
  
## 🚀 Key Features

*   **Sequential Automation Engine:** Processes hundreds of accounts one by one with randomized, human-like delays to prevent Telegram flood waits and bans.
*   **Smart Button Clicking:** Automatically parses incoming messages from target bots and clicks specific inline buttons (e.g., "Simple Quick") without manual intervention.
*   **Multi-User Isolation:** Secure database architecture ensures every authorized user only sees, manages, and triggers their own sessions.
*   **Supreme Admin Controls:** Complete authority over the bot ecosystem with global `/kill`, `/revive`, `/auth`, and `/deauth` commands.
*   **Zero-Footprint Sessions:** Runs entirely in-memory using cloud storage. No local `.session` files cluttering the server environment.

## 🛠️ Tech Stack

*   **Language:** Python 3.11
*   **Framework:** Pyrogram (MTProto API) & `asyncio`
*   **Database:** MongoDB Atlas (via `motor` async driver)
*   **Deployment:** Stack Host

## 📦 Repository Structure

*   `api.py` — The core application logic, custom filters, and MTProto client setup.
*   `requirements.txt` — Python dependencies for the build environment.
*   `stackhost.yaml` — CI/CD configuration for automated deployment.

## ⚙️ Deployment Instructions

This repository is pre-configured for instant auto-deployment. 

1. Verify that your MongoDB Atlas cluster is active and the connection string is correctly configured.
2. Ensure your Telegram API credentials and Bot Tokens are present.
3. Commit and push your changes to the `main` branch.
4. The host will automatically package the dependencies and launch the bot in the background.

---
*Architected and maintained by **Royal God Aryan**.*
