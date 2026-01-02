import os
import httpx
import asyncio
from pathlib import Path
from typing import List, Optional
import json

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, Input, Button, Static, Markdown, DataTable, Label, ListItem, ListView, Checkbox, Select
from textual.message import Message
from textual import on, work
from textual.binding import Binding

# --- Constants & Config ---
MODRINTH_API_URL = "https://api.modrinth.com/v2"
USER_AGENT = "gemini-cli/plugin-browser/1.0 (gemini-cli-agent)"

# --- API Client ---
class ModrinthAPI:
    def __init__(self):
        self.client = httpx.AsyncClient(
            base_url=MODRINTH_API_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=10.0
        )

    async def search_plugins(self, query: str, loaders: List[str], limit: int = 20) -> List[dict]:
        """Search for plugins on Modrinth."""
        try:
            # Base facet: plugins only
            facets = [["project_type:plugin"]]
            
            # Add loaders facet (OR logic within the list)
            if loaders:
                loader_facet = [f"categories:{loader}" for loader in loaders]
                facets.append(loader_facet)
            
            facets_json = json.dumps(facets)
            
            response = await self.client.get(
                "/search",
                params={
                    "query": query,
                    "facets": facets_json,
                    "limit": limit,
                    "index": "relevance" 
                }
            )
            response.raise_for_status()
            return response.json().get("hits", [])
        except Exception as e:
            return []

    async def get_versions(self, project_slug: str) -> List[dict]:
        """Get versions for a specific project."""
        try:
            response = await self.client.get(f"/project/{project_slug}/version")
            response.raise_for_status()
            return response.json()
        except Exception:
            return []

    async def close(self):
        await self.client.aclose()

# --- UI Components ---

class PluginDetails(Static):
    """Widget to display plugin details."""
    
    current_plugin: Optional[dict] = None
    versions_map: dict = {}

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label("Select a plugin to view details", id="details-title")
            yield Markdown("", id="details-desc")
            yield Label("Versions:", classes="section-header")
            yield Select([], prompt="Select a version", id="version-select")
            yield Button("Download", variant="primary", id="btn-download", disabled=True)

    def show_plugin(self, plugin: dict, versions: List[dict], allowed_loaders: List[str]):
        self.current_plugin = plugin
        self.versions_map = {}
        
        # Update Title
        title = self.query_one("#details-title", Label)
        author = plugin.get('author', 'Unknown')
        title.update(f"{plugin['title']} (by {author})")
        
        # Update Description
        desc = self.query_one("#details-desc", Markdown)
        # Prefer body (full markdown), fallback to description (short), then default text
        full_text = plugin.get('body') or plugin.get('description') or 'No description available.'
        desc.update(full_text)

        # Filter versions
        select_options = []
        allowed_set = set(allowed_loaders)
        
        for version in versions:
            v_loaders = set(version.get('loaders', []))
            # Check intersection if filters are active, else allow all if no filters? 
            # Assuming if no filters checked, we show nothing or everything? 
            # User behavior: "filter downloads too" implies strict filtering.
            # If allowed_loaders is empty, we probably shouldn't show anything or show all?
            # Let's assume strict: match allowed.
            if allowed_set and not (v_loaders & allowed_set):
                continue
                
            version_id = version['id']
            name = version.get('name', version['version_number'])
            files = version.get('files', [])
            if not files:
                continue
                
            # Use the first file as primary
            self.versions_map[version_id] = {
                'url': files[0]['url'],
                'filename': files[0]['filename']
            }
            
            display_loaders = ", ".join(v_loaders)
            label = f"{name} ({display_loaders})"
            select_options.append((label, version_id))
        
        # Update Select
        sel = self.query_one("#version-select", Select)
        sel.set_options(select_options)
        
        # Reset Download Button
        btn = self.query_one("#btn-download", Button)
        btn.disabled = True

    @on(Select.Changed)
    def on_version_select(self, event: Select.Changed):
        btn = self.query_one("#btn-download", Button)
        if event.value:
            btn.disabled = False
        else:
            btn.disabled = True

class ModrinthBrowser(App):
    CSS = """
    Screen {
        layout: vertical;
    }

    #search-bar {
        dock: top;
        height: auto;
        margin: 1;
        padding-bottom: 1;
        border-bottom: solid $secondary;
    }
    
    #filter-container {
        layout: horizontal;
        height: auto;
        margin-top: 1;
    }
    
    Checkbox {
        margin-right: 2;
    }

    #main-content {
        layout: horizontal;
        height: 1fr;
    }

    #results-pane {
        width: 40%;
        border-right: solid $primary;
    }

    #details-pane {
        width: 60%;
        padding: 1;
    }

    DataTable {
        height: 1fr;
    }

    .section-header {
        margin-top: 1;
        text-style: bold;
        color: $accent;
    }
    
    Button {
        margin-top: 1;
        width: 100%;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("/", "focus_search", "Search"),
    ]

    def __init__(self):
        super().__init__()
        self.api = ModrinthAPI()
        self.download_dir = self._determine_download_dir()

    def _determine_download_dir(self) -> Path:
        cwd = Path.cwd()
        if cwd.name == "plugins":
            return cwd
        
        plugins_dir = cwd / "plugins"
        if plugins_dir.exists() and plugins_dir.is_dir():
            return plugins_dir
        
        # Default: create plugins folder in cwd
        plugins_dir.mkdir(exist_ok=True)
        return plugins_dir

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Input(placeholder="Search for plugins...", id="search-input"),
            Container(
                Checkbox("Paper", value=True, id="chk-paper"),
                Checkbox("Spigot", value=True, id="chk-spigot"),
                Checkbox("Bukkit", value=True, id="chk-bukkit"),
                Checkbox("Purpur", value=True, id="chk-purpur"),
                Checkbox("Fabric", value=False, id="chk-fabric"),
                id="filter-container"
            ),
            id="search-bar"
        )
        yield Container(
            Container(
                DataTable(id="results-table"),
                id="results-pane"
            ),
            PluginDetails(id="details-pane"),
            id="main-content"
        )
        yield Footer()

    def on_mount(self):
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("Plugin Name", "Downloads")
        self.query_one("#search-input").focus()

    def action_focus_search(self):
        self.query_one("#search-input").focus()

    def get_active_loaders(self) -> List[str]:
        loaders = []
        if self.query_one("#chk-paper", Checkbox).value: loaders.append("paper")
        if self.query_one("#chk-spigot", Checkbox).value: loaders.append("spigot")
        if self.query_one("#chk-bukkit", Checkbox).value: loaders.append("bukkit")
        if self.query_one("#chk-purpur", Checkbox).value: loaders.append("purpur")
        if self.query_one("#chk-fabric", Checkbox).value: loaders.append("fabric")
        return loaders

    async def on_input_submitted(self, event: Input.Submitted):
        query = event.value
        if not query.strip():
            return
        
        self.run_search(query)

    @work(exclusive=True)
    async def run_search(self, query: str):
        table = self.query_one(DataTable)
        table.clear()
        
        loaders = self.get_active_loaders()
        hits = await self.api.search_plugins(query, loaders)
        
        for hit in hits:
            # Store the full hit object as the row key for retrieval later
            table.add_row(hit['title'], str(hit['downloads']), key=hit['slug'])

        if not hits:
             self.notify("No results found.", severity="warning")

    @on(DataTable.RowSelected)
    async def on_plugin_selected(self, event: DataTable.RowSelected):
        plugin_slug = event.row_key.value
        loaders = self.get_active_loaders()
        self.fetch_plugin_details(plugin_slug, loaders)

    @work(exclusive=True)
    async def fetch_plugin_details(self, plugin_slug: str, loaders: List[str]):
        versions = await self.api.get_versions(plugin_slug)
        
        try:
             project_resp = await self.api.client.get(f"/project/{plugin_slug}")
             project_resp.raise_for_status()
             project_data = project_resp.json()
             
             self.query_one(PluginDetails).show_plugin(project_data, versions, loaders)
        except Exception as e:
            self.notify(f"Error loading details: {e}", severity="error")

    @on(Button.Pressed)
    async def on_download_click(self, event: Button.Pressed):
        details = self.query_one(PluginDetails)
        select = details.query_one("#version-select", Select)
        
        if not select.value:
            return

        version_id = select.value
        file_info = details.versions_map.get(version_id)
        
        if file_info:
            self.download_file(file_info['url'], file_info['filename'])

    @work(thread=True)
    def download_file(self, url: str, filename: str):
        dest = self.download_dir / filename
        self.call_from_thread(self.notify, f"Downloading {filename}...")
        
        try:
            with httpx.Client() as client:
                resp = client.get(url)
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    f.write(resp.content)
            
            self.call_from_thread(self.notify, f"Saved to {dest}", severity="information")
            
        except Exception as e:
            self.call_from_thread(self.notify, f"Download failed: {e}", severity="error")

    async def on_unmount(self):
        await self.api.close()

if __name__ == "__main__":
    app = ModrinthBrowser()
    app.run()
