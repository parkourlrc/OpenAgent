from .filesystem import register_filesystem_tools
from .shell import register_shell_tools
from .browser import register_browser_tools
from .media import register_media_tools
from .docs import register_docs_tools
from .rag import register_kb_tools
from .translate import register_translate_tools


def register_all_tools() -> None:
    # Idempotent registration isn't supported; only call once at startup.
    register_filesystem_tools()
    register_shell_tools()
    register_browser_tools()
    register_media_tools()
    register_docs_tools()
    register_kb_tools()
    register_translate_tools()
