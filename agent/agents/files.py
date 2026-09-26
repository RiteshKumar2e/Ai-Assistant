"""FileAgent (File/Document Agent) — list, search, read, create, edit, rename and
(with approval) delete files inside the configured workspaces, and read /
question / summarise / create documents (PDF, DOCX, XLSX, PPTX, CSV, JSON, text,
code). Relative paths are relative to the current project; a file the user
attached in the HUD is readable wherever it lives."""
from __future__ import annotations

from agent.agents.base import Agent, int_arg
from agent.tools.base import DESTRUCTIVE, READ, WRITE
from agent.tools.documents import Documents


class FileAgent(Agent):
    name = "FileAgent"
    description = ("Files and documents: list/find/search/read/create/edit/rename/delete (with approval) files; read, question, "
                   "summarise and create documents (PDF, DOCX, XLSX, PPTX, CSV, JSON, Markdown, code).")

    def tools(self):
        fs = self.cc.files
        docs = Documents(fs, self.llm)
        return [
            self.tool("read_document", "Extract the text of a document (PDF/DOCX/XLSX/PPTX/CSV/JSON/text).", {"path": "file (or the attached file)"},
                      lambda path: docs.read(path), READ),
            self.tool("ask_document", "Answer a question about a (large) document using retrieval over its chunks.",
                      {"path": "file", "question": "what to find out"}, lambda path, question: docs.ask(path, question), READ),
            self.tool("summarize_document", "Summarise a document of any size (map-reduce over chunks).", {"path": "file", "focus": "optional focus"},
                      lambda path, focus="": docs.summarize(path, focus), READ, long_running=True),
            self.tool("create_document", "Create a document: .md/.txt/.json/.csv as text, or .docx (Markdown headings/bullets converted).",
                      {"path": "new file path", "content": "document text (Markdown for docx)", "format": "md|txt|docx|json|csv"},
                      lambda path, content, format="": docs.create(path, content, format), WRITE),
            self.tool("list_dir", "List a folder.", {"path": "folder, default project root"}, lambda path=".": fs.list_dir(path), READ),
            self.tool("tree", "Folder tree (skips node_modules/.git/venv).", {"path": "folder", "depth": "int, default 3"},
                      lambda path=".", depth=3: fs.tree(path, int_arg(depth, 3)), READ),
            self.tool("find_files", "Find files by glob name pattern.", {"pattern": "e.g. *auth*.py", "path": "folder"},
                      lambda pattern, path=".": fs.find_files(pattern, path), READ),
            self.tool("search_code", "Regex/text search across files; returns file:line matches.",
                      {"pattern": "regex or text", "path": "folder", "glob": "optional filename filter e.g. *.ts"},
                      lambda pattern, path=".", glob="": fs.search(pattern, path, glob), READ),
            self.tool("read_file", "Read a file with line numbers (optionally a line range).",
                      {"path": "file", "start": "first line", "end": "last line"},
                      lambda path, start=1, end=None: fs.read_file(path, int_arg(start, 1), int_arg(end) or None), READ),
            self.tool("write_file", "Create or overwrite a file with full content.", {"path": "file", "content": "full text"},
                      lambda path, content: fs.write_file(path, content), WRITE),
            self.tool("edit_file", "Replace an exact, unique snippet of a file. Copy `old` exactly from read_file output (without line numbers).",
                      {"path": "file", "old": "exact existing text (must occur once)", "new": "replacement text"},
                      lambda path, old, new: fs.edit_file(path, old, new), WRITE),
            self.tool("rename_path", "Rename or move a file/folder.", {"src": "from", "dst": "to"},
                      lambda src, dst: fs.rename(src, dst), WRITE),
            self.tool("delete_path", "Move a file/folder to the recycle bin (always asks the user first).", {"path": "path"},
                      lambda path: fs.delete(path), DESTRUCTIVE,
                      confirm_detail=lambda a: ("Delete file", f"Move {fs.resolve(a.get('path', ''))} to the recycle bin?")),
        ]
