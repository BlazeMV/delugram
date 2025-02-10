# Delugram

Delugram is a Deluge plugin that integrates Telegram with your Deluge server, allowing you to manage torrents via Telegram chat.

---

## ✨ Features

- 📥 **Add torrents** to Deluge via Telegram chat
- 🏷 **Assign labels** when adding torrents
- 🔗 **Supports** `.torrent` files, magnet links, and direct URLs
- 🔔 **Receive notifications** when a download is completed
- 📋 **List active torrents**

---

## 📌 Installation

### 🛠 Method 1: Using Prebuilt .egg

1. **Download** the latest `.egg` file from the releases section.\
   *Note: The Python version of the **`.egg`** file must match the Python version of Deluge. Run **`deluge --version`** to check the required version.*
2. **Place** the `.egg` file inside Deluge's config `plugins` directory.
3. **Enable** the Delugram plugin in Deluge preferences under the **Plugins** section.
4. **Configure** the bot token and telegram users in the **Delugram plugin UI** under Deluge preferences.

### 🏗 Method 2: Building from Source

1. **Clone** this repository using:
2. **Build** the `.egg` file from source:
   ```sh
   python setup.py bdist_egg
   ```
   This will generate the `.egg` file inside the `dist/` folder.
3. **Follow** steps 2-4 from Method 1.

---

## 📝 Usage

Use the following Telegram commands to interact with Delugram:

- `/add` - **Add a new torrent**
- `/status` - **Show status of active torrents**
- `/cancel` - **Cancel the current operation**
- `/help` - **List all available commands**
- 🔔 **Get real-time notifications when torrents complete.**

---

## ✅ Checklist

- ✅ Plugin shell
- ✅ Integrate python-telegram-bot
- ✅ Polling for telegram updates
- ✅ Bot response to all commands
- ☑️ Maintain torrent owner
- ☑️ Torrent added notification
- ☑️ Torrent download completed notification
- ☑️ Status command
- ☑️ Web UI
- ☑️ GTK UI
- ☑️ Access Control

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

