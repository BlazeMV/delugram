# Delugram

Delugram is a Deluge plugin that integrates Telegram with your Deluge server, allowing you to manage torrents via Telegram chat.

---

## ✨ Features

- 📥 **Add torrents** to Deluge via Telegram chat
- 🏷 **Assign labels** when adding torrents
- 🔗 **Supports** `.torrent` files, magnet links, and direct URLs
- 🔔 **Receive notifications** when a download is completed
- 📋 **List active torrents** with their status

---

## 📌 Installation

### 🛠 Method 1: Using Prebuilt .egg

1. **Download** the latest `.egg` file from the releases section.\
   *Note: The Python version of the **`.egg`** file must match the Python version of Deluge. Run **`deluge --version`** to check the required version.*
2. **Place** the `.egg` file inside Deluge's config `plugins` directory.
3. **Enable** the Delugram plugin in Deluge preferences under the **Plugins** section.
4. **Configure** the bot token and telegram users in the **Delugram plugin UI** under Deluge preferences.

### 🏗 Method 2: Building from Source

1. **Clone** this repository
2. **Setup** python vertual environment (venv):
   ```sh
   cd delugram # repo dir name
   python3 -m venv .venv
   source .venv/bin/activate
   ```
3. **Install** the required dependencies:
   ```sh
   pip install -r requirements-dev.txt
   pip install --target=delugram/vendor -r requirements-vendor.txt

4. **Build** the `.egg` file from source:
   ```sh
   ./build.sh prod
   ```
   
   This will generate the `.egg` file inside the `dist/` folder.
5. **Follow** steps 2-4 from Method 1.

---

## 📝 Usage

Use the following Telegram commands to interact with Delugram:

- `/add` - **Add a new torrent**
- `/status` - **Show status of active torrents**
- `/cancel` - **Cancel the current operation**
- `/done` - **Finish adding one or more torrents**
- `/help` - **List all available commands**
- 🔔 **Get real-time notifications when torrents complete.**

---

## ℹ️ Disclaimer
This plugin is heavily inspired by [deluge-telegramer](https://github.com/noam09/deluge-telegramer).
I stripped out few features mainly to avoid compatibility issues with other plugins and for simplicity (for my personal use).
You should definitely check out [deluge-telegramer](https://github.com/noam09/deluge-telegramer) before using this plugin, as it offers more features.

---

## 🤝 Contributing

Feel free to submit issues and pull requests to improve Delugram!

---

## 📜 License

MIT License

