import os
import httpx
import asyncio
from pathlib import Path
from typing import List, Optional, Set
import json
import traceback
import re

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, Input, Button, Static, Markdown, DataTable, Label, ListItem, ListView, Checkbox, Select, TabbedContent, TabPane
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

    async def search_plugins(self, query: str, loaders: List[str], limit: int = 20, offset: int = 0) -> List[dict]:
        """Search for plugins on Modrinth."""
        try:
            facets = [["project_type:plugin"]]
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
                    "offset": offset,
                    "index": "relevance" 
                }
            )
            response.raise_for_status()
            return response.json().get("hits", [])
        except Exception:
            return []

    async def get_project(self, project_slug_or_id: str) -> Optional[dict]:
        """Get project details."""
        try:
            response = await self.client.get(f"/project/{project_slug_or_id}")
            response.raise_for_status()
            return response.json()
        except Exception:
            return None

    async def get_versions(self, project_slug_or_id: str, loaders: List[str] = None) -> List[dict]:
        """Get versions for a specific project, optionally filtered by loader."""
        try:
            params = {}
            if loaders:
                # Modrinth API allows filtering versions by loader
                # loaders=["paper", "spigot"] -> json string
                params["loaders"] = json.dumps(loaders)
            
            response = await self.client.get(f"/project/{project_slug_or_id}/version", params=params)
            response.raise_for_status()
            return response.json()
        except Exception:
            return []
    
    async def get_version(self, version_id: str) -> Optional[dict]:
        """Get a specific version."""
        try:
            response = await self.client.get(f"/version/{version_id}")
            response.raise_for_status()
            return response.json()
        except Exception:
            return None

    async def close(self):
        await self.client.aclose()

# --- UI Components ---

class PluginDetails(Static):
    """Widget to display plugin details."""
    
    current_plugin: Optional[dict] = None
    versions_map: dict = {}
    current_version: Optional[dict] = None

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label("Select a plugin to view details", id="details-title")
            yield Markdown("", id="details-desc")
            yield Label("Dependencies:", classes="section-header", id="lbl-dependencies")
            yield Label("None", id="deps-list")
            yield Label("Versions:", classes="section-header")
            yield Select([], prompt="Select a version", id="version-select")
            yield Button("Download", variant="primary", id="btn-download", disabled=True)

    def show_plugin(self, plugin: dict, versions: List[dict], allowed_loaders: List[str]):
        self.current_plugin = plugin
        self.versions_map = {}
        self.current_version = None
        
        # Update Title
        title = self.query_one("#details-title", Label)
        author = plugin.get('author', 'Unknown')
        title.update(f"{plugin['title']} (by {author})")
        
        # Update Description
        desc = self.query_one("#details-desc", Markdown)
        full_text = plugin.get('body') or plugin.get('description') or 'No description available.'
        desc.update(full_text)
        
        # Reset dependencies
        self.query_one("#deps-list", Label).update("Select a version to see dependencies")

        # Filter versions
        select_options = []
        allowed_set = set(allowed_loaders)
        
        for version in versions:
            v_loaders = set(version.get('loaders', []))
            if allowed_set and not (v_loaders & allowed_set):
                continue
                
            version_id = version['id']
            name = version.get('name', version['version_number'])
            files = version.get('files', [])
            if not files:
                continue
                
            # Store full version object for dependency checking
            self.versions_map[version_id] = {
                'url': files[0]['url'],
                'filename': files[0]['filename'],
                'data': version
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
        deps_lbl = self.query_one("#deps-list", Label)
        
        if event.value:
            btn.disabled = False
            version_id = event.value
            version_info = self.versions_map.get(version_id)
            if version_info:
                self.current_version = version_info['data']
                # Update dependencies display
                dependencies = self.current_version.get('dependencies', [])
                req_deps = [d for d in dependencies if d['dependency_type'] == 'required']
                
                if req_deps:
                    # We only have project_ids, normally we'd need to fetch names, 
                    # but for now let's just show count or ID. 
                    # Ideally we fetch names async, but that's complex for this sync handler.
                    # We'll just list them as "Required Dependency"
                    deps_text = f"{len(req_deps)} required dependencies."
                    deps_lbl.update(deps_text)
                else:
                    deps_lbl.update("No required dependencies.")
        else:
            btn.disabled = True
            deps_lbl.update("-")
            self.current_version = None

class InstalledPlugins(Static):
    """Widget to manage installed plugins."""
    
    def compose(self) -> ComposeResult:
        yield Container(
            Button("Refresh List", id="btn-refresh-installed"),
            DataTable(id="installed-table"),
            Button("Delete Selected", variant="error", id="btn-delete-installed", disabled=True),
            classes="installed-container"
        )

    def on_mount(self):
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("Filename", "Size (KB)")

class ModrinthBrowser(App):
    CSS = """
    /* --- Global --- */
    Screen {
        layout: vertical;
        background: #1a1b26;
        color: #a9b1d6;
    }

    /* --- Header/Footer --- */
    Header {
        background: #16161e;
        color: #7aa2f7;
        dock: top;
        height: 1;
    }

    Footer {
        background: #16161e;
        color: #565f89;
        dock: bottom;
        height: 1;
    }

    /* --- Search Section --- */
    #search-bar {
        height: auto;
        padding: 1 2;
        background: #1a1b26;
        border-bottom: solid #414868;
    }

    Input {
        background: #24283b;
        border: none;
        color: #c0caf5;
        padding: 0 1;
        width: 100%;
    }
    Input:focus {
        border: solid #7aa2f7;
    }

    #filter-container {
        layout: horizontal;
        height: auto;
        margin-top: 1;
        background: #1a1b26;
    }
    
    Checkbox {
        margin-right: 2;
        color: #565f89;
    }
    Checkbox.-on {
        color: #bb9af7;
        text-style: bold;
    }

    /* --- Main Content --- */
    #main-content {
        layout: horizontal;
        height: 1fr;
    }

    /* --- Results Pane --- */
    #results-pane {
        width: 40%;
        border-right: solid #414868;
        background: #1a1b26;
    }

    DataTable {
        background: #1a1b26;
        border: none;
        scrollbar-gutter: stable;
    }
    
    DataTable > .datatable--header {
        background: #24283b;
        color: #7aa2f7;
        text-style: bold;
    }

    DataTable > .datatable--cursor {
        background: #3d59a1;
        color: #ffffff;
    }

    DataTable > .datatable--hover {
        background: #292e42;
    }

    #btn-load-more {
        margin: 1;
        background: #24283b;
        color: #7aa2f7;
        border: none;
    }
    #btn-load-more:hover {
        background: #3d59a1;
        color: #ffffff;
    }
    #btn-load-more:disabled {
        background: #1a1b26;
        color: #565f89;
    }

    /* --- Details Pane --- */
    #details-pane {
        width: 60%;
        padding: 1 2;
        background: #1a1b26;
        overflow-y: scroll;
    }

    #details-title {
        text-style: bold;
        color: #9ece6a;
        padding-bottom: 1;
        border-bottom: solid #414868;
        margin-bottom: 1;
        text-align: left;
    }

    #details-desc {
        color: #c0caf5;
        padding-bottom: 1;
    }

    .section-header {
        margin-top: 2;
        margin-bottom: 1;
        text-style: bold;
        color: #bb9af7;
    }

    Select {
        background: #24283b;
        border: none;
        margin-bottom: 1;
    }

    #btn-download {
        background: #7aa2f7;
        color: #1a1b26;
        text-style: bold;
        margin-top: 2;
        border: none;
    }
    #btn-download:hover {
        background: #2ac3de;
    }
    #btn-download:disabled {
        background: #24283b;
        color: #565f89;
    }

    /* --- Installed Plugins --- */
    .installed-container {
        padding: 1;
    }
    #btn-refresh-installed {
        width: 100%;
        margin-bottom: 1;
        background: #24283b;
        color: #7aa2f7;
    }
    #btn-delete-installed {
        width: 100%;
        margin-top: 1;
        background: #f7768e;
        color: #1a1b26;
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
        self.current_offset = 0
        self.current_query = ""
        self.search_timer: Optional[asyncio.TimerHandle] = None

    def _determine_download_dir(self) -> Path:
        cwd = Path.cwd()
        if cwd.name == "plugins":
            return cwd
        
        plugins_dir = cwd / "plugins"
        if plugins_dir.exists() and plugins_dir.is_dir():
            return plugins_dir
        
        plugins_dir.mkdir(exist_ok=True)
        return plugins_dir

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            with TabPane("Browse Plugins"):
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
                        Button("Load More", variant="default", id="btn-load-more", disabled=True),
                        id="results-pane"
                    ),
                    PluginDetails(id="details-pane"),
                    id="main-content"
                )
            with TabPane("Installed Plugins"):
                yield InstalledPlugins(id="installed-pane")
        yield Footer()

    def on_mount(self):
        table = self.query_one("#results-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Plugin Name", "Downloads")
        self.query_one("#search-input").focus()
        self.refresh_installed_list()

    def action_focus_search(self):
        self.query_one("TabbedContent").active = "Browse Plugins" # Does this work? No, keys are "tab-1" etc usually unless id provided.
        # Actually TabPane labels are the keys usually.
        self.query_one("#search-input").focus()

    @on(Input.Changed)
    def on_input_changed(self, event: Input.Changed):
        query = event.value.strip()
        
        if self.search_timer:
            self.search_timer.stop()
            
        if not query:
            self.query_one("#results-table", DataTable).clear()
            self.query_one("#btn-load-more", Button).disabled = True
            return

        self.search_timer = self.set_timer(0.2, lambda: self.trigger_auto_search(query))

    def trigger_auto_search(self, query: str):
        if query != self.current_query:
            self.current_query = query
            self.current_offset = 0
            self.perform_search(reset=True)

    def get_active_loaders(self) -> List[str]:
        loaders = []
        if self.query_one("#chk-paper", Checkbox).value: loaders.append("paper")
        if self.query_one("#chk-spigot", Checkbox).value: loaders.append("spigot")
        if self.query_one("#chk-bukkit", Checkbox).value: loaders.append("bukkit")
        if self.query_one("#chk-purpur", Checkbox).value: loaders.append("purpur")
        if self.query_one("#chk-fabric", Checkbox).value: loaders.append("fabric")
        return loaders

    @work(exclusive=True)
    async def perform_search(self, reset: bool = False):
        try:
            table = self.query_one("#results-table", DataTable)
            if reset:
                table.clear()
            
            loaders = self.get_active_loaders()
            limit = 20
            hits = await self.api.search_plugins(self.current_query, loaders, limit=limit, offset=self.current_offset)
            
            for hit in hits:
                try:
                    table.add_row(hit['title'], str(hit['downloads']), key=hit['slug'])
                except Exception:
                    pass
            
            btn = self.query_one("#btn-load-more", Button)
            if len(hits) < limit:
                btn.disabled = True
                if not hits and reset:
                     self.notify("No results found.", severity="warning")
            else:
                btn.disabled = False
        except Exception as e:
            with open("error.log", "w") as f:
                f.write(traceback.format_exc())
            self.notify(f"Search error: {e}", severity="error")

    @on(Button.Pressed)
    async def on_button_click(self, event: Button.Pressed):
        btn_id = event.button.id
        
        if btn_id == "btn-load-more":
            self.current_offset += 20
            self.perform_search(reset=False)
            
        elif btn_id == "btn-download":
            details = self.query_one(PluginDetails)
            if details.current_version:
                self.start_install_process(details.current_version)

        elif btn_id == "btn-refresh-installed":
            self.refresh_installed_list()
        
        elif btn_id == "btn-delete-installed":
            self.delete_selected_plugin()

    @work(thread=True)
    def start_install_process(self, version_data: dict):
        self.call_from_thread(self.notify, f"Starting installation...")
        
        # 1. Install the main plugin
        files = version_data.get('files', [])
        if not files:
            self.call_from_thread(self.notify, "No files found for this version!", severity="error")
            return
            
        main_file = files[0]
        self.download_file_sync(main_file['url'], main_file['filename'])
        
        # 2. Check Dependencies
        dependencies = version_data.get('dependencies', [])
        required_deps = [d for d in dependencies if d['dependency_type'] == 'required']
        
        if required_deps:
            self.call_from_thread(self.notify, f"Checking {len(required_deps)} dependencies...")
            self.call_from_thread(self.run_worker, self.process_dependencies(required_deps))

    def download_file_sync(self, url: str, filename: str):
        dest = self.download_dir / filename
        # Check if already exists? Modrinth files might change but filename usually unique per version
        # If exists, maybe skip? But 'install' implies force.
        
        try:
            with httpx.Client() as client:
                resp = client.get(url)
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    f.write(resp.content)
            self.call_from_thread(self.notify, f"Installed {filename}", severity="information")
            self.call_from_thread(self.refresh_installed_list)
        except Exception as e:
            self.call_from_thread(self.notify, f"Failed to install {filename}: {e}", severity="error")

    async def process_dependencies(self, dependencies: List[dict]):
        # This runs in the main loop context but is async
        for dep in dependencies:
            project_id = dep.get('project_id')
            if not project_id:
                continue
                
            # Get project info to find name
            project = await self.api.get_project(project_id)
            if not project:
                continue
                
            slug = project.get('slug')
            title = project.get('title')
            
            # Check if exists
            if self.check_plugin_exists(slug, title):
                self.notify(f"Dependency '{title}' detected. Skipping.")
                continue
            
            self.notify(f"Dependency '{title}' missing. Finding compatible version...")
            
            # Find version
            # Use active loaders
            loaders = self.get_active_loaders()
            versions = await self.api.get_versions(project_id, loaders)
            
            if versions:
                # Pick latest
                best_version = versions[0]
                files = best_version.get('files', [])
                if files:
                    file_info = files[0]
                    self.notify(f"Downloading dependency: {title}")
                    
                    # We need to run download in thread again
                    self.download_dependency_worker(file_info['url'], file_info['filename'])
            else:
                 self.notify(f"Could not find compatible version for dependency '{title}'", severity="warning")

    @work(thread=True)
    def download_dependency_worker(self, url: str, filename: str):
        self.download_file_sync(url, filename)

    def check_plugin_exists(self, slug: str, title: str) -> bool:
        # Check files in download_dir
        # simple check: filename contains slug or title (case insensitive)
        # Regex check as requested
        # escape special chars
        patterns = [
            re.compile(re.escape(slug), re.IGNORECASE),
            re.compile(re.escape(title), re.IGNORECASE)
        ]
        
        try:
            for file in self.download_dir.iterdir():
                if file.is_file() and file.suffix == ".jar":
                    for pat in patterns:
                        if pat.search(file.name):
                            return True
        except Exception:
            pass
        return False

    def refresh_installed_list(self):
        try:
            table = self.query_one("#installed-table", DataTable)
            table.clear()
            
            for file in self.download_dir.iterdir():
                if file.is_file() and file.suffix == ".jar":
                    size_kb = file.stat().st_size / 1024
                    table.add_row(file.name, f"{size_kb:.2f}", key=file.name)
        except Exception:
            pass

    @on(DataTable.RowSelected, selector="#installed-table")
    def on_installed_selected(self, event: DataTable.RowSelected):
        self.query_one("#btn-delete-installed", Button).disabled = False

    @on(DataTable.RowSelected, selector="#results-table")
    async def on_plugin_selected(self, event: DataTable.RowSelected):
        plugin_slug = event.row_key.value
        loaders = self.get_active_loaders()
        self.fetch_plugin_details(plugin_slug, loaders)

    @work(exclusive=True)
    async def fetch_plugin_details(self, plugin_slug: str, loaders: List[str]):
        versions = await self.api.get_versions(plugin_slug)
        try:
             project = await self.api.get_project(plugin_slug)
             if project:
                 self.query_one(PluginDetails).show_plugin(project, versions, loaders)
        except Exception as e:
            self.notify(f"Error loading details: {e}", severity="error")

    def delete_selected_plugin(self):
        table = self.query_one("#installed-table", DataTable)
        try:
            # Get selected row
            # Textual 0.40+ changed how selections work slightly but cursor_row should work
            # or we iterate?
            # event driven is better but we have a button.
            # We can use table.cursor_row to get index, then table.get_row_at(index)
            # OR row_key if we set it.
            
            # If nothing selected, cursor might be valid but nothing 'selected'?
            # DataTable doesn't have 'selected_row' property easily accessible without event?
            # We used row key = filename.
            
            # If we don't have the key easily, we might struggle. 
            # But we set key=filename.
            
            # Let's try:
            row_index = table.cursor_row
            if row_index is not None:
                row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
                filename = row_key.value
                
                file_path = self.download_dir / filename
                if file_path.exists():
                    os.remove(file_path)
                    self.notify(f"Deleted {filename}")
                    self.refresh_installed_list()
                    self.query_one("#btn-delete-installed", Button).disabled = True
        except Exception as e:
            self.notify(f"Error deleting: {e}", severity="error")

    async def on_unmount(self):
        await self.api.close()

if __name__ == "__main__":
    app = ModrinthBrowser()
    app.run()