# Plugin Browser

A terminal-based UI (TUI) for browsing and managing Minecraft plugins from [Modrinth](https://modrinth.com/). Built with Python and [Textual](https://textual.textualize.io/).

## Features

- **Search Modrinth:** Easily search for plugins using the Modrinth API.
- **Filter by Loader:** Filter search results by server loaders like Paper, Spigot, Bukkit, Purpur, and Fabric.
- **Detailed Information:** View plugin descriptions, authors, and available versions.
- **Direct Download:** Download plugins directly into a `plugins/` directory.
- **Dependency Handling:** Automatically detects and offers to download required dependencies.
- **Manage Installed Plugins:** View and delete `.jar` files in your plugins folder directly from the TUI.
- **Dark Mode UI:** A clean, modern terminal interface powered by Textual.

## Installation

### Prerequisites

- Python 3.8 or higher
- pip (Python package installer)

### Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/plugin-browser.git
   cd plugin-browser
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

## Usage

Run the application using Python:

```bash
python browser.py
```

### Navigation

- **Search:** Use the search bar at the top to find plugins.
- **Tabs:** Switch between "Browse Plugins" and "Installed Plugins" using the tabs at the top.
- **Selection:** Use arrow keys or your mouse to select plugins from the list.
- **Download:** Select a version from the dropdown in the details pane and click "Download".
- **Shortcuts:**
    - `/`: Focus search input
    - `q`: Quit the application

## Project Structure

- `browser.py`: The main application script.
- `plugins/`: Default directory where plugins are downloaded.
- `requirements.txt`: List of Python dependencies.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.