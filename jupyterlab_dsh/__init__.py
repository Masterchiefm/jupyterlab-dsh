"""jupyterlab_dsh - DeepSeek Harness (dsh) inside JupyterLab.

Server extension: spawns `dsh web` on loopback and proxies it under
`<base_url>dsh/` so the GUI is same-origin with JupyterLab, satisfies the
dsh Host/Origin fence for every external address JupyterLab listens on, and
keeps the launch token server-side.
"""

import atexit

from traitlets import Bool, Int, Unicode
from traitlets.config import Configurable

from jupyter_server.utils import url_path_join

from .handlers import DshProxyHandler, DshStatusHandler
from .manager import DshManager


class DshExtensionConfig(Configurable):
    """User-tunable knobs (c.DshExtensionConfig.* in jupyter_server_config)."""

    port = Int(
        0,
        help="Loopback port for the dsh web listener; 0 lets the OS pick a free one.",
    ).tag(config=True)

    command = Unicode(
        "",
        help=(
            "Custom command to launch the dsh web GUI, e.g. "
            "'dsh web --no-open --port 3080'. The printed 'dsh web:' URL is "
            "parsed to discover the listener and launch token. Empty uses "
            "the built-in default."
        ),
    ).tag(config=True)

    autostart = Bool(
        True, help="Start the dsh backend together with the Jupyter server."
    ).tag(config=True)


def _load_jupyter_server_extension(server_app):
    """Called by jupyter_server when the extension is enabled."""
    config = DshExtensionConfig(parent=server_app)
    root_dir = getattr(server_app, "root_dir", None) or getattr(
        server_app, "notebook_dir", None
    )
    manager = DshManager(config, log=server_app.log, cwd=root_dir)
    server_app.web_app.settings["dsh_manager"] = manager
    atexit.register(manager.stop)

    base_url = server_app.web_app.settings["base_url"]
    handlers = [
        (url_path_join(base_url, "dsh") + r"/?", DshProxyHandler),
        (url_path_join(base_url, "dsh") + r"(/.*)", DshProxyHandler),
        (url_path_join(base_url, "dsh-ext/status"), DshStatusHandler),
    ]
    server_app.web_app.add_handlers(".*$", handlers)

    if config.autostart:
        manager.start_background()
        server_app.log.info("jupyterlab_dsh: dsh backend starting (entry %s)", base_url + "dsh/")
    else:
        server_app.log.info("jupyterlab_dsh: autostart disabled")
    return manager


# JupyterLab-style alias (jupyter_server also looks for this name)
load_jupyter_server_extension = _load_jupyter_server_extension
