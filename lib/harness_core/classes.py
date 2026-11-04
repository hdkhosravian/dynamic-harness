"""Irreversible-action classification (spec §Irreversible-action control).

``classify`` never raises: anything it cannot parse or recognise resolves to the
``unknown`` class, which is irreversible. Bash patterns are a best-effort first
line; the Claude Code sandbox set up by /harness:setup is the hard backstop.
"""
from __future__ import annotations

import glob
import os
import re
import shlex
import tempfile
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from . import egress, protected, query_guard
from .errors import Denied

# Most severe first; a compound Bash command takes its worst segment.
PRIORITY = [
    "protected_path", "money", "payee_change", "external_message", "destructive",
    "local_app_automation", "automation_create", "external_write", "price_publish",
    "accounting_entry", "egress_with_data", "egress_offlist", "unknown_mcp_write",
    "self_written_exec", "unknown", "draft_create", "local_reversible", "read",
]
MAX_DEPTH = 6
MAX_LEN = 20000


@dataclass(frozen=True)
class Classification:
    cls: str
    reversibility: str
    irreversible: bool
    reason: str
    url: Optional[str] = None
    query: Optional[str] = None


def load_table(ctx) -> dict:
    return protected.load_control("classes.json", ctx)


def _mk(cls, reason, ctx, url=None, query=None) -> Classification:
    try:
        table = load_table(ctx)["classes"]
        row = table.get(cls) or table["unknown"]
        return Classification(cls, row["reversibility"], bool(row["irreversible"]), reason, url, query)
    except Exception as e:  # unreadable class table: everything is irreversible
        return Classification(cls, "R3", True, f"{reason} [class table unreadable: {type(e).__name__}]", url, query)


def worst(items):
    return min(items, key=lambda c: PRIORITY.index(c.cls) if c.cls in PRIORITY else 0)


# ---- entry point ----------------------------------------------------------

READ_TOOLS = {"Read": "file_path", "NotebookRead": "notebook_path"}
SEARCH_TOOLS = {"Grep", "Glob", "LS"}
WRITE_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
INTERNAL_TOOLS = {
    "Task", "Agent", "TodoWrite", "TodoRead", "AskUserQuestion", "ExitPlanMode", "EnterPlanMode",
    "Skill", "SlashCommand", "BashOutput", "KillShell", "KillBash", "ToolSearch", "TaskOutput",
    "TaskStop", "ListMcpResourcesTool",
}


def classify(tool_name, tool_input, registry=None, ctx=None) -> Classification:
    if registry is None:
        registry = getattr(ctx, "connector_registry", None) or {}
    try:
        if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
            return _mk("unknown", "malformed tool call", ctx)
        return _classify(tool_name, tool_input, registry, ctx)
    except Exception as e:  # never crash: unknown is irreversible
        return _mk("unknown", f"classifier error: {type(e).__name__}: {e}", ctx)


def _classify(tool, inp, registry, ctx):
    if tool == "Bash":
        cmd = inp.get("command")
        if not isinstance(cmd, str):
            return _mk("unknown", "Bash command is not a string", ctx)
        return classify_bash(cmd, ctx)
    if tool in READ_TOOLS:
        p = inp.get(READ_TOOLS[tool])
        if isinstance(p, str) and p and protected.is_read_protected(p, ctx):
            return _mk("protected_path", f"{tool} of a read-protected path", ctx)
        return _mk("read", f"{tool}", ctx)
    if tool in SEARCH_TOOLS:
        roots = [inp.get("path") or str(ctx.workspace)]
        pat = inp.get("pattern")
        if tool == "Glob" and isinstance(pat, str) and (pat.startswith("/") or pat.startswith("~")):
            roots.append(re.split(r"[*?\[{]", pat, maxsplit=1)[0] or "/")
        for r in roots:
            if not isinstance(r, str):
                return _mk("unknown", f"{tool} path is not a string", ctx)
            if protected.contains_read_protected(r, ctx):
                return _mk("protected_path", f"{tool} would search a read-protected tree", ctx)
        return _mk("read", tool, ctx)
    if tool in WRITE_TOOLS:
        p = inp.get(WRITE_TOOLS[tool])
        if not isinstance(p, str) or not p:
            return _mk("unknown", f"{tool} without a path", ctx)
        if protected.is_protected(p, ctx):
            return _mk("protected_path", f"{tool} to a protected path", ctx)
        if protected.is_hardlinked(protected.resolve(p, ctx)):
            return _mk("protected_path", f"{tool} to a hard-linked file", ctx)
        if protected.outside_workspace(p, ctx):
            return _mk("destructive", f"{tool} outside the workspace", ctx)
        return _mk("local_reversible", f"{tool} in the workspace", ctx)
    if tool == "WebFetch":
        return classify_url(inp.get("url"), ctx)
    if tool == "WebSearch":
        q = inp.get("query")
        if not isinstance(q, str):
            return _mk("unknown", "WebSearch without a text query", ctx)
        return _mk("egress_with_data", "search queries leave the machine", ctx, query=q)
    if tool.startswith("mcp__"):
        return _classify_mcp(tool, inp, registry, ctx)
    if tool == "ReadMcpResourceTool":
        server = inp.get("server")
        if not any(k.startswith(f"mcp__{server}__") or k == server for k in registry):
            return _mk("unknown_mcp_write", f"MCP server {server!r} is not in the connector registry", ctx)
        return _mk("read", "MCP resource read", ctx)
    if tool in INTERNAL_TOOLS:
        return _mk("local_reversible", f"{tool} (harness-internal)", ctx)
    return _mk("unknown", f"unrecognised tool {tool!r}", ctx)


def classify_url(url, ctx, method="GET") -> Classification:
    try:
        on_list = egress.on_allowlist(url, ctx)
    except Denied as e:
        return _mk("egress_offlist", e.reason, ctx, url=url if isinstance(url, str) else None)
    u = urlsplit(url)
    if method != "GET" or u.query or u.fragment:
        return _mk("egress_with_data", "request carries data (method, query string or fragment)", ctx, url=url)
    try:
        query_guard.check(url, getattr(ctx, "protected_terms", ()), getattr(ctx, "owner_tokens", ()))
    except Denied as e:
        return _mk("egress_with_data", e.reason, ctx, url=url)
    if not on_list:
        return _mk("egress_offlist", f"{egress.host_of(url)} is not on the egress allowlist", ctx, url=url)
    return _mk("read", "GET to an allowlisted domain", ctx, url=url)


def _registry_entry(tool, registry):
    server = tool.split("__")[1] if tool.count("__") >= 2 else tool[5:]
    for key in (tool, f"mcp__{server}__*", server):
        if key in registry:
            return registry[key]
    return None


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)


def _classify_mcp(tool, inp, registry, ctx):
    entry = _registry_entry(tool, registry)
    if entry is None:
        return _mk("unknown_mcp_write", f"{tool} is not in the connector registry", ctx)
    tag, cls = (entry.get("tag"), entry.get("class")) if isinstance(entry, dict) else (entry, None)
    if tag == "read":
        for s in _strings(inp):
            p = s[7:] if s.startswith("file://") else s
            if p.startswith(("/", "~/", "./", "../")) and protected.is_read_protected(p, ctx):
                return _mk("protected_path", f"{tool} reads a read-protected path", ctx)
        return _mk("read", f"{tool} tagged read", ctx)
    if tag == "draft":
        return _mk("draft_create", f"{tool} tagged draft", ctx)
    if tag == "write":
        table = load_table(ctx)["classes"]
        c = cls if cls in table and table[cls]["irreversible"] else "external_write"
        return _mk(c, f"{tool} tagged write", ctx)
    return _mk("unknown_mcp_write", f"{tool} has unrecognised tag {tag!r}", ctx)


# ---- Bash -----------------------------------------------------------------


class ParseError(Exception):
    pass


class _Unresolved(Exception):
    pass


_SEPS = {";", "&&", "||", "|", "&", "|&", "(", ")", ";;", ";&", ";;&"}
_W_REDIR = {">", ">>", ">|", "&>", "&>>", "<>", ">&"}
_R_REDIR = {"<", "<&", "<<<"}
_PUNCT = set(";&|()<>")
_OPS = _SEPS | _W_REDIR | _R_REDIR
_OPS_BY_LEN = sorted(_OPS, key=len, reverse=True)
_HEREDOC = re.compile(r"<<(-?)[ \t]*(?:'([^']*)'|\"([^\"]*)\"|\\?([^\s;&|<>()]+))")
_ASSIGN = re.compile(r"^[A-Za-z_]\w*=")
SUBST = "$__SUBST__"

KEYWORDS = {"!", "{", "}", "then", "do", "else", "elif", "fi", "done", "esac", "if", "while", "until", "time", "coproc"}
HEADERS = {"for", "case", "select", "in"}
DANGEROUS_ENV = {
    "PATH", "BASH_ENV", "ENV", "PROMPT_COMMAND", "IFS", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME",
    "NODE_OPTIONS", "NODE_PATH", "PERL5OPT", "PERL5LIB", "RUBYOPT", "RUBYLIB", "GIT_SSH_COMMAND",
    "GIT_EXEC_PATH", "GIT_DIR", "GIT_CONFIG", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_PARAMETERS", "SHELLOPTS",
    "BASHOPTS", "ZDOTDIR", "HOME", "EDITOR", "VISUAL", "PAGER", "GIT_PAGER", "LESSOPEN",
}
SYSTEM_DIRS = ("/bin", "/sbin", "/usr/bin", "/usr/sbin", "/usr/local/bin", "/usr/libexec", "/opt/homebrew/bin", "/System")
DEV_OK = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/zero"}

READ_ONLY = {
    "ls", "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg", "ag", "ack", "cut", "tr", "echo",
    "printf", "pwd", "true", "false", "test", "[", "[[", "]]", "date", "basename", "dirname", "realpath",
    "readlink", "which", "whereis", "type", "whoami", "id", "uname", "hostname", "printenv", "diff",
    "cmp", "comm", "file", "stat", "du", "df", "jq", "column", "paste", "join", "nl", "od", "hexdump",
    "shasum", "sha1sum", "sha256sum", "sha512sum", "md5", "md5sum", "cksum", "less", "more", "sleep",
    "seq", "expr", "bc", "wait", "read", "set", "unset", "shift", "local", "declare", "typeset", "exit",
    "return", ":", "ps", "pgrep", "jobs", "sw_vers", "man", "fold", "fmt", "rev", "tac", "strings",
    "locale", "getconf", "nproc", "uptime", "groups", "tput", "clear", "say", "pbpaste", "pbcopy",
    "mdls", "mdfind", "lsof", "ulimit", "umask", "shopt", "cal", "iconv",
    "fc-list", "sips", "afinfo", "plutil", "xmllint", "csvlook", "head", "look", "units", "env",
}
OUTPUT_OPT = {"sort": ("-o", "--output"), "tree": ("-o",), "base64": ("-o", "--output"), "shuf": ("-o", "--output"),
              "plutil": ("-o",), "sips": ("--out", "-o"), "iconv": ("-o", "--output")}
WRITERS = {"tee", "touch", "mkdir", "rmdir", "truncate", "mktemp", "patch", "split", "csplit",
           "gzip", "gunzip", "bzip2", "bunzip2", "xz", "unxz", "zstd", "compress", "uncompress"}
WRITER_OPT_ARGS = {
    "touch": {"-t", "-d", "-r"}, "mkdir": {"-m"}, "truncate": {"-s", "-r"}, "mktemp": {"-p", "-t"},
    "split": {"-l", "-b", "-n", "-a", "-C"}, "csplit": {"-f", "-n", "-b"}, "patch": {"-o", "-i", "-p", "-d", "-B"},
    "chown": {"--reference"}, "xattr": {"-w"}, "setfacl": {"-m", "-x", "-M", "-X"},
}
MODE_FIRST = {"chmod", "chown", "chgrp", "chflags", "xattr", "setfacl"}
COPIERS = {"cp", "mv", "ln", "install", "ditto", "rsync"}
REMOVERS = {"rm", "unlink", "shred", "srm"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh", "ash", "mksh"}
_INTERP = re.compile(r"^(python[0-9.]*|pypy[0-9.]*|node|nodejs|deno|bun|perl[0-9.]*|ruby|irb|php|lua[0-9.]*|luajit|rscript|tclsh|wish|swift|pwsh|powershell|osascript|jxa|julia|java|jshell|groovy|scala|kotlin|elixir|erl|ghci|runghc|ts-node|tsx)$")
INLINE_FLAGS = {
    "python": "c", "pypy": "c", "perl": "eE", "ruby": "e", "php": "rR", "lua": "e", "luajit": "e",
    "rscript": "e", "julia": "e", "elixir": "e", "pwsh": "c", "powershell": "c",
}
NODE_INLINE = {"-e", "--eval", "-p", "--print", "-r", "--require", "--import", "--loader", "-i", "--interactive"}
SAFE_PY_MODULES = {
    "pytest", "unittest", "json.tool", "py_compile", "compileall", "doctest", "timeit", "cprofile",
    "pstats", "black", "ruff", "mypy", "flake8", "pylint", "isort", "pyflakes", "coverage", "tabnanny",
    "venv", "pydoc", "pip", "ensurepip", "site", "this", "calendar", "zipfile", "tarfile", "base64",
}
NET_PY_MODULES = {"http.server", "smtpd", "smtplib", "ftplib", "urllib.request", "xmlrpc.server", "socketserver"}
NET_TOOLS = {
    "curl", "wget", "http", "https", "httpie", "xh", "nc", "ncat", "netcat", "telnet", "ssh", "scp",
    "sftp", "ftp", "socat", "aria2c", "lynx", "links", "w3m", "nslookup", "dig", "host", "ping", "ping6",
    "traceroute", "mtr", "whois", "openssl", "torsocks", "tor", "ngrok", "cloudflared", "mosh", "rclone",
    "s3cmd", "axel", "npx", "pnpx", "bunx", "uvx", "pipx", "networksetup", "scutil", "curlie", "grpcurl",
}
APP_AUTOMATION = {"osascript", "open", "shortcuts", "automator", "xdg-open", "osacompile", "screencapture",
                  "qlmanage", "lpr", "lp"}
AUTOMATION = {"crontab", "launchctl", "at", "batch", "atq", "systemctl", "systemd-run", "schtasks", "pmset"}
MESSAGE = {"mail", "mailx", "sendmail", "mutt", "msmtp", "swaks", "ssmtp", "twilio", "slack", "telegram-send"}
CLOUD = {"aws", "gcloud", "gsutil", "az", "kubectl", "helm", "terraform", "tofu", "pulumi", "heroku", "vercel",
         "netlify", "flyctl", "fly", "firebase", "wrangler", "doctl", "psql", "mysql", "mongo", "mongosh",
         "redis-cli", "sqlcmd", "docker", "podman", "gh", "glab", "hub", "twine", "supabase", "railway"}
MONEY = {"stripe", "paypal"}
PRIV = {"sudo", "doas", "su", "pkexec", "security", "dscl", "defaults", "csrutil", "spctl", "tccutil",
        "alias", "hash", "trap", "enable", "disown", "exec"}
PKG = {"npm", "pnpm", "yarn", "bun", "pip", "pip3", "uv", "poetry", "pdm", "conda", "mamba", "brew", "gem",
       "bundle", "cargo", "go", "apt", "apt-get", "yum", "dnf", "port", "composer", "mvn", "gradle", "deno"}
PKG_NET = {"install", "i", "ci", "add", "update", "up", "upgrade", "download", "fetch", "get", "sync", "search",
           "outdated", "clone", "tap", "lock", "wheel", "pull", "view", "info", "show", "audit", "dlx", "x",
           "create", "self-update", "selfupdate", "mod", "remove", "uninstall", "rm", "link", "global"}
PKG_PUBLISH = {"publish", "unpublish", "upload", "push", "release", "deploy", "login", "adduser", "owner",
               "dist-tag", "deprecate", "yank", "token"}
RUNNERS = {"test", "t", "run", "run-script", "start", "build", "exec", "check", "bench", "vet", "tests"}
RUNNER_CMDS = {"make", "gmake", "just", "rake", "ninja", "pytest", "py.test", "tox", "nox", "jest", "vitest",
               "mocha", "ctest", "meson", "cmake", "tsc", "eslint", "prettier", "ruff", "black", "mypy",
               "flake8", "pylint", "isort", "shellcheck", "bats", "phpunit", "rspec"}
CODE_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh", ".bash", ".zsh", ".rb", ".pl",
                 ".php", ".lua", ".r", ".swift", ".go", ".rs", ".java", ".kt", ".mk", ".cfg", ".toml", ".ini")
CODE_NAMES = {"makefile", "gnumakefile", "justfile", "rakefile", "package.json", "conftest.py", "dockerfile"}
WRAPPERS = {"nohup", "time", "nice", "timeout", "gtimeout", "caffeinate", "stdbuf", "command", "builtin",
            "watch", "chronic", "unbuffer", "ionice", "taskpolicy", "arch", "xargs", "env"}
WRAPPER_OPTS = {
    "nice": {"-n"}, "timeout": {"-s", "-k", "--signal", "--kill-after"}, "gtimeout": {"-s", "-k"},
    "caffeinate": {"-t", "-w"}, "stdbuf": {"-i", "-o", "-e"}, "watch": {"-n", "-d", "--interval"},
    "ionice": {"-c", "-n"}, "taskpolicy": {"-c", "-d", "-g", "-b"}, "env": {"-u", "-C", "-P", "--unset", "--chdir"},
    "xargs": {"-I", "-i", "-n", "-P", "-L", "-l", "-s", "-d", "-E", "-a", "-J", "-R", "-S", "--max-args",
              "--max-procs", "--delimiter", "--arg-file", "--replace"},
}
GIT_READ = {"status", "log", "diff", "show", "rev-parse", "ls-files", "blame", "describe", "shortlog",
            "cat-file", "ls-tree", "help", "version", "whatchanged", "rev-list", "name-rev", "show-ref",
            "for-each-ref", "count-objects", "check-ignore", "var", "merge-base", "cherry", "range-diff",
            "grep", "annotate", "show-branch", "verify-commit", "fsck", "--version", "--help", "request-pull"}
GIT_NET = {"fetch", "pull", "clone", "ls-remote", "submodule", "archive-remote"}
GIT_OPT_ARGS = {"-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix"}
PY_OPT_ARGS = {"-W", "-X", "-Q"}


def _code_like(path: str) -> bool:
    b = os.path.basename(path).casefold()
    return b in CODE_NAMES or b.endswith(CODE_SUFFIXES)


def _split_opts(args, opt_args=()):
    """(options, positionals); options taking a value consume it; ``--`` ends options."""
    opts, pos, i = [], [], 0
    while i < len(args):
        a = args[i]
        if a == "--":
            pos += args[i + 1:]
            break
        if a.startswith("-") and a != "-":
            opts.append(a)
            if a in opt_args and i + 1 < len(args):
                opts.append(args[i + 1])
                i += 1
        else:
            pos.append(a)
        i += 1
    return opts, pos


def _opt_value(args, names):
    """Values of options like ``-o X``, ``-oX``, ``--output=X``, ``--output X``."""
    vals = []
    for i, a in enumerate(args):
        for n in names:
            if a == n and i + 1 < len(args):
                vals.append(args[i + 1])
            elif n.startswith("--") and a.startswith(n + "="):
                vals.append(a[len(n) + 1:])
            elif not n.startswith("--") and a.startswith(n) and len(a) > len(n) and len(n) == 2:
                vals.append(a[2:])
    return vals


def _brace_expand(s, limit=64):
    m = re.search(r"\{([^{}]*,[^{}]*)\}", s)
    if not m:
        return [s]
    out = []
    for part in m.group(1).split(","):
        out += _brace_expand(s[: m.start()] + part + s[m.end():], limit)
        if len(out) > limit:
            raise ParseError("brace expansion too large")
    return out


# -- scanner: substitutions, heredocs, comments, newlines -------------------


def _match_paren(s, i):
    """s[i] == '('; index of the matching ')', respecting quotes."""
    depth, j, n = 0, i, len(s)
    while j < n:
        ch = s[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "'":
            k = s.find("'", j + 1)
            if k < 0:
                raise ParseError("unterminated single quote")
            j = k + 1
            continue
        if ch == '"':
            k = j + 1
            while k < n and s[k] != '"':
                k += 2 if s[k] == "\\" else 1
            if k >= n:
                raise ParseError("unterminated double quote")
            j = k + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    raise ParseError("unbalanced parenthesis")


def _scan(s, heredoc=False):
    """Rewrite ``s`` for shlex. Returns (text, inner_commands, heredoc_ids).

    Command/process substitutions are cut out (classified separately) and replaced
    by an unresolvable placeholder; heredoc bodies are removed and their command
    gets a stdin marker; unquoted newlines become ``;``; comments are dropped.
    """
    out, inner, hd_ids, pending = [], [], [], []
    i, n, in_dq = 0, len(s), False
    while i < n:
        ch = s[i]
        if ch == "\\":
            if s.startswith("\\\n", i):
                i += 2
                continue
            out.append(s[i:i + 2])
            i += 2
            continue
        if not heredoc and not in_dq and ch == "'":
            k = s.find("'", i + 1)
            if k < 0:
                raise ParseError("unterminated single quote")
            out.append(s[i:k + 1])
            i = k + 1
            continue
        if not heredoc and ch == '"':
            in_dq = not in_dq
            out.append(ch)
            i += 1
            continue
        if s.startswith("$((", i):
            k = _match_paren(s, i + 1)
            if "$(" in s[i + 3:k] or "`" in s[i + 3:k]:
                raise ParseError("command substitution inside arithmetic")
            out.append("0")
            i = k + 1
            continue
        if s.startswith("$(", i):
            k = _match_paren(s, i + 1)
            inner.append(s[i + 2:k])
            out.append(SUBST)
            i = k + 1
            continue
        if ch == "`":
            k = i + 1
            while k < n and s[k] != "`":
                k += 2 if s[k] == "\\" else 1
            if k >= n:
                raise ParseError("unterminated backtick")
            inner.append(s[i + 1:k].replace("\\`", "`"))
            out.append(SUBST)
            i = k + 1
            continue
        if s.startswith("${", i):
            k = s.find("}", i)
            if k < 0:
                raise ParseError("unterminated ${")
            body = s[i + 2:k]
            if "$(" in body or "`" in body:
                raise ParseError("command substitution inside ${...}")
            out.append(s[i:k + 1] if re.fullmatch(r"\w+", body) else "$__PARAM__")
            i = k + 1
            continue
        if heredoc or in_dq:
            out.append(ch)
            i += 1
            continue
        if ch in "<>" and s.startswith("(", i + 1):
            k = _match_paren(s, i + 1)
            inner.append(s[i + 2:k])
            out.append(" /dev/fd/63 ")
            i = k + 1
            continue
        if ch == "#" and (i == 0 or s[i - 1] in " \t\n;&|()"):
            k = s.find("\n", i)
            i = n if k < 0 else k
            continue
        if s.startswith("<<<", i):
            out.append("<<<")
            i += 3
            continue
        if s.startswith("<<", i):
            m = _HEREDOC.match(s, i)
            if not m:
                raise ParseError("malformed heredoc")
            quoted = m.group(4) is None or s[m.start(4) - 1] == "\\"
            delim = next(g for g in (m.group(2), m.group(3), m.group(4)) if g is not None)
            hid = f"__HEREDOC_{len(hd_ids)}__"
            hd_ids.append(hid)
            pending.append((delim, quoted, bool(m.group(1))))
            out.append(f" < {hid} ")
            i = m.end()
            continue
        if ch == "\n":
            out.append(" ; ")
            i += 1
            for delim, quoted, strip in pending:
                body = []
                while i < n:
                    k = s.find("\n", i)
                    line = s[i:] if k < 0 else s[i:k]
                    i = n if k < 0 else k + 1
                    if (line.lstrip("\t") if strip else line) == delim:
                        break
                    body.append(line)
                if not quoted:
                    inner += _scan("\n".join(body), heredoc=True)[1]
            pending = []
            continue
        out.append(ch)
        i += 1
    if in_dq:
        raise ParseError("unterminated double quote")
    return "".join(out), inner, hd_ids


def _tokens(s):
    lx = shlex.shlex(s, posix=True, punctuation_chars=True)
    lx.whitespace_split = True
    lx.commenters = ""
    try:
        raw = list(lx)
    except ValueError as e:
        raise ParseError(str(e)) from e
    out = []
    for t in raw:
        if not t or not set(t) <= _PUNCT or t in _OPS:
            out.append(t)
            continue
        while t:  # shlex glues adjacent operators (");" "&&("); split greedily
            op = next((o for o in _OPS_BY_LEN if t.startswith(o)), None)
            if op is None:
                raise ParseError(f"unexpected operator {t!r}")
            out.append(op)
            t = t[len(op):]
    return out


def classify_bash(command: str, ctx) -> Classification:
    if "\x00" in command or len(command) > MAX_LEN:
        return _mk("unknown", "command contains NUL or is too long", ctx)
    b = _Bash(ctx)
    try:
        b.run(command)
    except ParseError as e:
        return _mk("unknown", f"cannot parse command ({e}); unknown resolves to irreversible", ctx)
    if not b.results:
        return _mk("local_reversible", "no-op command", ctx)
    w = worst(b.results)
    if b.claims_url and b.simple_count == 1 and w.url == b.claims_url:
        return w
    return Classification(w.cls, w.reversibility, w.irreversible, w.reason)


class _Bash:
    def __init__(self, ctx, depth=0, cwds=None, written=None, parent=None):
        self.ctx = ctx
        self.depth = depth
        self.results = []
        self.cwds = cwds if cwds is not None else [os.path.realpath(os.fspath(ctx.workspace))]
        self.written = written if written is not None else set()
        self.simple_count = 0
        self.claims_url = None
        home = os.fspath(getattr(ctx, "home", None) or os.path.expanduser("~"))
        self.vars = {
            "HOME": home, "TMPDIR": tempfile.gettempdir(), "PWD": self.cwds[0],
            "CLAUDE_PROJECT_DIR": os.fspath(ctx.workspace),
        }
        for k, attr in (("CLAUDE_PLUGIN_DATA", "data_root"), ("CLAUDE_PLUGIN_ROOT", "plugin_root")):
            if getattr(ctx, attr, None):
                self.vars[k] = os.fspath(getattr(ctx, attr))

    # -- bookkeeping --
    def add(self, cls, reason, url=None):
        self.results.append(_mk(cls, reason, self.ctx, url=url))

    def child(self):
        if self.depth >= MAX_DEPTH:
            raise ParseError("nesting too deep")
        c = _Bash(self.ctx, self.depth + 1, self.cwds, self.written)
        c.vars = self.vars
        return c

    def nested(self, command):
        c = self.child()
        c.run(command)
        self.results += c.results
        self.simple_count += c.simple_count + 1
        return c

    # -- parsing --
    def run(self, command):
        text, inner, _ = _scan(command)
        for sub in inner:
            self.nested(sub)
        segs, cur, piped, depth = [], [], False, 0
        toks = _tokens(text)
        for t in toks:
            depth += (t == "(") - (t == ")")
            if depth < 0:
                raise ParseError("unbalanced parenthesis")
        if depth:
            raise ParseError("unbalanced parenthesis")
        for t in toks:
            if t in _SEPS:
                if cur:
                    segs.append((cur, piped))
                piped = t in ("|", "|&") or (t == "(" and piped and not cur)
                cur = []
                continue
            if t and set(t) <= _PUNCT and t not in _OPS:
                raise ParseError(f"unexpected operator {t!r}")
            cur.append(t)
        if cur:
            segs.append((cur, piped))
        for words, piped in segs:
            self.segment(words, piped)

    def segment(self, words, piped):
        args, stdin_code, stdin_file, i = [], piped, None, 0
        while i < len(words):
            t = words[i]
            if t in _W_REDIR or t in _R_REDIR:
                if i + 1 >= len(words):
                    raise ParseError("redirection without a target")
                tgt = words[i + 1]
                if tgt in _OPS:
                    raise ParseError(f"redirection target is an operator ({tgt!r})")
                i += 2
                if len(args) > 1 and args[-1].isdigit():
                    args.pop()  # fd number such as 2>
                if t in (">&", "<&") and re.fullmatch(r"\d*-?", tgt):
                    continue
                if t == "<<<" or tgt.startswith("__HEREDOC_"):
                    stdin_code = True
                    continue
                if t in ("<", "<&"):
                    stdin_file = tgt
                    self.read_target(tgt)
                    continue
                self.write_target(tgt)
                continue
            args.append(t)
            i += 1
        if args:
            self.simple(args, stdin_code=stdin_code, stdin_file=stdin_file)

    # -- path helpers --
    def expand(self, w):
        if w.startswith("~"):
            if w == "~" or w.startswith("~/"):
                w = self.vars["HOME"] + w[1:]
            else:
                return None

        def sub(m):
            v = self.vars.get(m.group(1) or m.group(2))
            if v is None:
                raise _Unresolved()
            return v

        try:
            w = re.sub(r"\$\{(\w+)\}|\$(\w+)", sub, w)
        except _Unresolved:
            return None
        return None if "$" in w else w

    def candidates(self, w):
        """Every realpath ``w`` may refer to (all cwd candidates, braces, globs); None if unresolvable."""
        e = self.expand(w)
        if e is None:
            return None
        outs = []
        for x in _brace_expand(e):
            for c in self.cwds:
                if c is None and not os.path.isabs(x):
                    return None
                base = x if os.path.isabs(x) else os.path.join(c, x)
                outs.append(os.path.realpath(base))
                if any(ch in x for ch in "*?["):
                    outs += [os.path.realpath(g) for g in glob.glob(base)]
        return outs

    def temp_roots(self):
        roots = getattr(self.ctx, "temp_roots", None)
        if roots is None:
            roots = ["/tmp", "/private/tmp", tempfile.gettempdir()]
        return [os.path.realpath(os.fspath(r)) for r in roots]

    def in_temp(self, p):
        if not protected.outside_workspace(p, self.ctx):
            return False  # the workspace is never "temp", even when it lives under /tmp
        # nor are home / plugin roots, anything inside them, or any ancestor of them (`..` traversal, `rm -rf ~`)
        for attr in ("workspace", "home", "data_root", "plugin_root", "loop_contract_root"):
            a = getattr(self.ctx, attr, None)
            if a and (protected._under(p, os.path.realpath(os.fspath(a))) or protected._under(os.path.realpath(os.fspath(a)), p)):
                return False
        return any(p.startswith(r.rstrip("/") + "/") for r in self.temp_roots())

    def is_written(self, p):
        listed = {os.path.realpath(os.fspath(x)) for x in getattr(self.ctx, "written_this_task", ()) or ()}
        if p in listed or p in self.written:
            return True
        started = getattr(self.ctx, "task_started", None)
        try:
            return bool(started) and os.path.getmtime(p) >= started
        except OSError:
            return False

    def any_written_code(self):
        listed = [os.fspath(x) for x in getattr(self.ctx, "written_this_task", ()) or ()]
        return any(_code_like(p) for p in list(self.written) + listed)

    def read_target(self, w):
        if re.match(r"^/dev/(tcp|udp)/", w):
            return self.add("egress_offlist", "bash /dev/tcp network redirection")
        for p in self.candidates(w) or ():
            if protected.is_read_protected(p, self.ctx):
                return self.add("protected_path", "reads keys/ or experience/")

    def write_target(self, w, verb="write"):
        if re.match(r"^/dev/(tcp|udp)/", w):
            return self.add("egress_with_data", "bash /dev/tcp network redirection")
        cands = self.candidates(w)
        if cands is None:
            return self.add("unknown", f"{verb} target {w!r} cannot be resolved")
        for p in cands:
            if p in DEV_OK or p.startswith("/dev/fd/"):
                continue
            if protected.is_protected(p, self.ctx):
                return self.add("protected_path", f"{verb} to protected path {p}")
            if protected.is_hardlinked(p):
                return self.add("protected_path", f"{verb} to hard-linked file {p}")
            if protected.outside_workspace(p, self.ctx) and not self.in_temp(p):
                return self.add("destructive", f"{verb} outside the workspace: {p}")
        self.written.update(cands)
        if any(p not in DEV_OK and not p.startswith("/dev/") for p in cands):
            self.add("local_reversible", f"{verb} in the workspace or temp")

    def exec_file(self, w, what="runs"):
        cands = self.candidates(w)
        if cands is None:
            return self.add("unknown", f"{what} a file whose path cannot be resolved")
        if any(p.startswith("/dev/fd/") for p in cands):
            return self.add("self_written_exec", f"{what} code from a process substitution")
        if any(self.is_written(p) for p in cands):
            return self.add("self_written_exec", f"{what} a file written this task; use harness-sandbox-exec")
        self.add("local_reversible", f"{what} an existing file")

    def runner(self, name):
        if self.any_written_code():
            return self.add("self_written_exec", f"{name} would execute code written this task; use harness-sandbox-exec")
        self.add("local_reversible", f"{name} on existing code")

    # -- simple command --
    def simple(self, args, stdin_code=False, stdin_file=None, dynamic=False):
        self.simple_count += 1
        while args and _ASSIGN.match(args[0]):
            name = args[0].split("=", 1)[0]
            if name in DANGEROUS_ENV or name.startswith(("DYLD_", "LD_")):
                return self.add("unknown", f"assignment to {name} changes what runs")
            args = args[1:]
        while args and args[0] in KEYWORDS:
            args = args[1:]
        if not args:
            return
        if args[0] in HEADERS:
            return self.add("local_reversible", "shell control header")
        for a in args:
            if "fold_ledger" in a.casefold():
                return self.add("protected_path", "direct call to fold_ledger.py is denied")
            for part in (a, a.split("=", 1)[-1]):
                if ("/" in part or part.startswith(("~", "."))) and self.read_protected(part):
                    return self.add("protected_path", "reads keys/ or experience/")
        cmd = args[0]
        if "$" in cmd:
            e = self.expand(cmd)
            if e is None:
                return self.add("unknown", "command name is dynamic")
            cmd = e
        if "/" in cmd:
            cands = self.candidates(cmd)
            if not cands:
                return self.add("unknown", "command path cannot be resolved")
            if any(self.is_written(p) for p in cands):
                return self.add("self_written_exec", "runs a file written this task; use harness-sandbox-exec")
            real = cands[0]
            name = os.path.basename(real).casefold()
            if self.dispatch(name, args, stdin_code, stdin_file, dynamic):
                return
            if not real.startswith(SYSTEM_DIRS):
                return self.add("local_reversible", "runs an existing file")
        else:
            name = cmd.casefold()
            if self.dispatch(name, args, stdin_code, stdin_file, dynamic):
                return
        self.add("unknown", f"unrecognised command {name!r}; unknown resolves to irreversible")

    def read_protected(self, w):
        return any(protected.is_read_protected(p, self.ctx) for p in (self.candidates(w) or ()))

    def recursive_read(self, paths):
        for w in paths:
            for p in self.candidates(w) or ():
                if protected.contains_read_protected(p, self.ctx):
                    return self.add("protected_path", "recursive read over keys/ or experience/")

    def dispatch(self, name, args, stdin_code, stdin_file, dynamic):
        """Classify a known command; return False when ``name`` is unknown."""
        rest = args[1:]
        if name in ("cd", "pushd"):
            target = rest[0] if rest and rest[0] != "-" else (None if rest else "~")
            new = []
            for c in self.cwds:
                e = self.expand(target) if target else None
                if e is None or c is None:
                    new.append(None)
                else:
                    new.append(os.path.realpath(os.path.join(c, e)))
            self.cwds = list(dict.fromkeys(self.cwds + new))
            return self.add("read", "cd") or True
        if name == "popd":
            return self.add("read", "popd") or True
        if name == "export":
            for a in rest:
                n = a.split("=", 1)[0]
                if "=" in a and (n in DANGEROUS_ENV or n.startswith(("DYLD_", "LD_"))):
                    return self.add("unknown", f"export of {n} changes what runs") or True
            return self.add("local_reversible", "export") or True
        if name.startswith("harness-"):
            return self.harness(name, rest, dynamic) or True
        if name in WRAPPERS:
            return self.wrapper(name, rest, stdin_code, stdin_file, dynamic) or True
        if name in SHELLS:
            return self.shell(name, rest, stdin_code, stdin_file) or True
        if name == "eval":
            return self.nested(" ".join(rest)) and True
        if name in ("source", "."):
            return (self.exec_file(rest[0], "sources") if rest else self.add("unknown", "source without file")) or True
        if name in APP_AUTOMATION:
            return self.add("local_app_automation", f"{name} drives local apps") or True
        m = _INTERP.match(name)
        if m:
            return self.interpreter(name, rest, stdin_code, stdin_file) or True
        if name in NET_TOOLS:
            return self.net(name, rest, stdin_code, stdin_file) or True
        if name == "git":
            return self.git(rest) or True
        if name in ("kill", "pkill", "killall"):
            return self.kill(name, rest, dynamic) or True
        if name in REMOVERS:
            return self.remove(name, rest, dynamic) or True
        if name in COPIERS:
            return self.copy(name, rest, dynamic) or True
        if name in WRITERS or name in MODE_FIRST:
            opts, pos = _split_opts(rest, WRITER_OPT_ARGS.get(name, set()))
            if name in MODE_FIRST and pos and not any(o.startswith("--reference") for o in opts):
                pos = pos[1:]
            if dynamic and not pos:
                return self.add("unknown", f"{name} with dynamic targets") or True
            if name in ("gzip", "gunzip", "bzip2", "bunzip2", "xz", "unxz", "zstd") and ("-c" in opts or "--stdout" in opts):
                return self.add("read", f"{name} to stdout") or True
            for p in pos:
                self.write_target(p, name)
            return self.add("local_reversible", f"{name} in the workspace") or True
        if name in OUTPUT_OPT or name in READ_ONLY:
            for v in _opt_value(rest, OUTPUT_OPT.get(name, ())):
                self.write_target(v, name)
            if name in ("grep", "egrep", "fgrep") and any(o.startswith("-") and not o.startswith("--") and ("r" in o or "R" in o) or o in ("--recursive", "--dereference-recursive") for o in rest):
                self.recursive_read(_split_opts(rest)[1][1:])
            if name in ("rg", "ag", "ack"):
                self.recursive_read(_split_opts(rest)[1][1:])
            if name == "env" and not rest:
                return self.add("read", "env") or True
            return self.add("read", name) or True
        if name in ("awk", "gawk", "nawk", "mawk"):
            return self.awk(rest) or True
        if name in ("sed", "gsed"):
            return self.sed(rest) or True
        if name == "find":
            return self.find(rest) or True
        if name == "dd":
            for a in rest:
                if a.startswith("of="):
                    self.write_target(a[3:], "dd")
            return self.add("local_reversible", "dd") or True
        if name in ("uniq", "xxd"):
            pos = _split_opts(rest, {"-f", "-s", "-c", "-l", "-g", "-o", "-s"})[1]
            if len(pos) >= 2:
                self.write_target(pos[1], name)
            return self.add("read", name) or True
        if name == "yq":
            if any(o in ("-i", "--inplace") for o in rest):
                for p in _split_opts(rest)[1][1:]:
                    self.write_target(p, "yq -i")
            return self.add("read", "yq") or True
        if name == "tar":
            return self.tar(rest) or True
        if name in ("zip", "unzip"):
            opts, pos = _split_opts(rest, {"-d", "-x", "-i", "-P"})
            if name == "zip":
                if pos:
                    self.write_target(pos[0], "zip")
                self.recursive_read(pos[1:])
            else:
                for d in _opt_value(rest, ("-d",)) or ["."]:
                    self.write_target(d, "unzip")
            return self.add("local_reversible", name) or True
        if name == "sqlite3":
            joined = " ".join(rest).casefold()
            if re.search(r"\b(drop|delete|truncate|update|insert|alter|replace|attach|vacuum|create)\b|\.(shell|system|output|once|import|save|backup|load)", joined) or stdin_code:
                return self.add("destructive", "sqlite3 statement that may modify data") or True
            return self.add("read", "sqlite3 query") or True
        if name == "launchctl" and rest[:1] in (["kill"], ["stop"], ["bootout"], ["remove"]):
            return self.add("destructive", f"launchctl {rest[0]} signals or removes a service") or True
        if name in AUTOMATION:
            if name == "crontab" and rest and all(a == "-l" for a in rest):
                return self.add("read", "crontab -l") or True
            if name in ("launchctl", "systemctl") and rest and rest[0] in ("list", "print", "status", "show", "blame"):
                return self.add("read", f"{name} {rest[0]}") or True
            return self.add("automation_create", f"{name} creates or changes scheduled automation") or True
        if name in MESSAGE:
            return self.add("external_message", f"{name} sends messages") or True
        if name in MONEY:
            return self.add("money", f"{name} can move money") or True
        if name in CLOUD:
            return self.add("external_write", f"{name} acts on remote systems") or True
        if name in PRIV:
            return self.add("unknown", f"{name} is not allowed (privilege, persistence or deferred execution)") or True
        if name in PKG:
            sub = next((a for a in rest if not a.startswith("-")), "")
            if sub in PKG_PUBLISH:
                return self.add("external_write", f"{name} {sub} publishes") or True
            if sub in PKG_NET:
                return self.add("egress_offlist", f"{name} {sub} uses the network") or True
            if sub in RUNNERS or not sub:
                return self.runner(f"{name} {sub}".strip()) or True
            return self.add("local_reversible", f"{name} {sub}") or True
        if name in RUNNER_CMDS:
            return self.runner(name) or True
        return False

    # -- per-command handlers --
    def harness(self, name, rest, dynamic):
        sub = rest[0] if rest else ""
        if name == "harness-sandbox-exec":
            i = 0
            while i < len(rest) and rest[i] != "--" and rest[i].startswith("--"):
                i += 2
            inner = rest[i + 1:] if i < len(rest) and rest[i] == "--" else rest[i:]
            if not inner:
                return self.add("unknown", "harness-sandbox-exec without a command")
            c = self.child()
            c.simple(list(inner), dynamic=dynamic)
            for r in c.results:
                if r.cls in ("self_written_exec", "egress_offlist", "egress_with_data"):
                    r = _mk("local_reversible", f"inside harness-sandbox-exec: {r.reason}", self.ctx)
                self.results.append(r)
            return
        if name == "harness-business" and sub in ("set", "register-source"):
            ok = getattr(self.ctx, "nonce_ok", None)
            if ok and ok(name, rest):
                return self.add("local_reversible", "harness-business with a typed-command nonce")
            return self.add("protected_path", f"harness-business {sub} needs the typed /harness:business nonce")
        if name == "harness-claims" and sub == "fetch":
            url = rest[1] if len(rest) > 1 else None
            c = classify_url(url, self.ctx)
            if not c.irreversible:  # even allowlisted, a Bash fetch is a hook egress: keep it gated
                c = _mk("egress_offlist", "harness-claims fetch is researcher-lane egress", self.ctx, url=url)
            self.results.append(c)
            self.claims_url = url
            return
        self.add("local_reversible", f"{name} prints a request; its PostToolUse writer validates it")

    def wrapper(self, name, rest, stdin_code, stdin_file, dynamic):
        if name == "command" and rest and rest[0] in ("-v", "-V"):
            return self.add("read", "command -v")
        i, opt_args = 0, WRAPPER_OPTS.get(name, set())
        while i < len(rest):
            a = rest[i]
            if a == "--":
                i += 1
                break
            if name == "env" and a in ("-S", "--split-string") and i + 1 < len(rest):
                return self.nested(rest[i + 1] + " " + " ".join(shlex.quote(x) for x in rest[i + 2:]))
            if name == "env" and _ASSIGN.match(a):
                n = a.split("=", 1)[0]
                if n in DANGEROUS_ENV or n.startswith(("DYLD_", "LD_")):
                    return self.add("unknown", f"env assignment to {n} changes what runs")
                i += 1
                continue
            if a.startswith("-"):
                i += 2 if a in opt_args else 1
                continue
            if name in ("timeout", "gtimeout") and re.fullmatch(r"[\d.]+[smhd]?", a):
                i += 1
                break
            if name == "arch" and a.startswith("-"):
                i += 1
                continue
            break
        inner = rest[i:]
        if not inner:
            return self.add("read", f"{name} without a command")
        self.simple(list(inner), stdin_code=stdin_code, stdin_file=stdin_file, dynamic=dynamic or name == "xargs")

    def shell(self, name, rest, stdin_code, stdin_file):
        i, cmd_string, script, reads_stdin = 0, False, None, False
        while i < len(rest):
            a = rest[i]
            if a in ("--rcfile", "--init-file", "-o", "+o", "-O", "+O"):
                if a in ("--rcfile", "--init-file") and i + 1 < len(rest):
                    self.exec_file(rest[i + 1], "loads")
                i += 2
                continue
            if a == "--":
                i += 1
                break
            if a.startswith("-") and len(a) > 1 and not a.startswith("--"):
                cmd_string = cmd_string or "c" in a[1:]
                reads_stdin = reads_stdin or "s" in a[1:]
                i += 1
                continue
            if a.startswith("--") or a.startswith("+"):
                i += 1
                continue
            break
        pos = rest[i:]
        if cmd_string:
            if not pos:
                raise ParseError(f"{name} -c without a command string")
            return self.nested(pos[0])
        if pos and not reads_stdin:
            return self.exec_file(pos[0], f"{name} runs")
        if stdin_code:
            return self.add("self_written_exec", f"code piped into {name}; use harness-sandbox-exec")
        if stdin_file:
            return self.exec_file(stdin_file, f"{name} runs")
        self.add("unknown", f"interactive {name}")

    def interpreter(self, name, rest, stdin_code, stdin_file):
        if name in ("osascript", "jxa"):
            return self.add("local_app_automation", "osascript drives local apps")
        base = re.sub(r"[0-9.]+$", "", name)
        if rest and all(a in ("--version", "-V", "-v", "--help", "-h", "-version") for a in rest):
            return self.add("read", f"{name} version/help")
        inline = INLINE_FLAGS.get(base, "")
        i = 0
        while i < len(rest):
            a = rest[i]
            if a == "--":
                i += 1
                break
            if a == "-":
                return self.add("self_written_exec", f"{name} reads code from stdin; use harness-sandbox-exec")
            if base in ("node", "nodejs", "bun", "deno", "ts-node", "tsx") and (a in NODE_INLINE or a.split("=", 1)[0] in NODE_INLINE):
                return self.add("self_written_exec", f"inline {name} code; use harness-sandbox-exec")
            node_like = base in ("node", "nodejs", "bun", "deno", "ts-node", "tsx")
            if a.startswith("-") and not inline and not node_like and base not in ("python", "pypy"):
                return self.add("unknown", f"unrecognised {name} option {a!r}")
            if a.startswith("-") and not a.startswith("--"):
                if inline and any(ch in a[1:] for ch in inline):
                    return self.add("self_written_exec", f"inline {name} code; use harness-sandbox-exec")
                if base in ("python", "pypy") and "m" in a[1:]:
                    mod = a[a.index("m") + 1:] or (rest[i + 1] if i + 1 < len(rest) else "")
                    after = rest[i + 1:] if a.endswith("m") else rest[i:]
                    return self.py_module(name, mod, after[1:] if a.endswith("m") else after)
                i += 2 if a in PY_OPT_ARGS else 1
                continue
            if a.startswith("--"):
                i += 1
                continue
            break
        pos = rest[i:]
        if base in ("deno", "bun") and pos and pos[0] in ("eval", "x", "repl"):
            return self.add("self_written_exec" if pos[0] != "x" else "egress_offlist", f"{name} {pos[0]}")
        if base in ("deno", "bun") and pos and pos[0] in ("run", "test") and len(pos) > 1:
            pos = pos[1:]
        if pos:
            if re.match(r"^[a-z]+://", pos[0]):
                return self.add("egress_offlist", f"{name} runs remote code")
            return self.exec_file(pos[0], f"{name} runs")
        if stdin_code:
            return self.add("self_written_exec", f"code piped into {name}; use harness-sandbox-exec")
        if stdin_file:
            return self.exec_file(stdin_file, f"{name} runs")
        self.add("unknown", f"interactive {name}")

    def py_module(self, name, mod, after):
        mod_l = mod.casefold()
        for c in self.cwds:
            if c is None:
                continue
            rel = mod.replace(".", "/")
            for cand in (rel + ".py", rel + "/__main__.py", rel + "/__init__.py"):
                if self.is_written(os.path.realpath(os.path.join(c, cand))):
                    return self.add("self_written_exec", f"{name} -m runs a module written this task")
        if mod_l in NET_PY_MODULES:
            return self.add("egress_offlist", f"{name} -m {mod} uses the network")
        if mod_l == "webbrowser":
            return self.add("local_app_automation", "python -m webbrowser opens a browser")
        if mod_l == "pip":
            sub = next((a for a in after if not a.startswith("-")), "")
            if sub in PKG_NET or sub in PKG_PUBLISH:
                return self.add("egress_offlist", f"pip {sub} uses the network")
            return self.add("local_reversible", f"pip {sub}")
        if mod_l in ("pytest", "unittest", "doctest", "coverage", "tox", "nox", "mypy"):
            return self.runner(f"{name} -m {mod}")
        if mod_l in SAFE_PY_MODULES:
            return self.add("local_reversible", f"{name} -m {mod}")
        self.add("unknown", f"unrecognised python module {mod!r}")

    def net(self, name, rest, stdin_code, stdin_file):
        data_flags = {"-d", "--data", "--data-raw", "--data-binary", "--data-urlencode", "-F", "--form",
                      "--form-string", "-T", "--upload-file", "--json", "-H", "--header", "-u", "--user",
                      "-b", "--cookie", "--post-data", "--post-file", "--body-data", "--body-file", "-e", "--referer"}
        method = None
        for i, a in enumerate(rest):
            key = a.split("=", 1)[0]
            if key in data_flags or (a.startswith("-d") and name == "curl" and len(a) > 2):
                return self.add("egress_with_data", f"{name} sends data")
            if key in ("-X", "--request", "--method"):
                method = (a.split("=", 1)[1] if "=" in a else (rest[i + 1] if i + 1 < len(rest) else "")).upper()
            elif a.startswith("-X") and len(a) > 2:
                method = a[2:].upper()
            if "?" in a and "://" in a:
                return self.add("egress_with_data", f"{name} URL carries a query string")
        if method and method not in ("GET", "HEAD"):
            return self.add("egress_with_data", f"{name} {method} sends data")
        if name in ("nc", "ncat", "netcat", "socat", "telnet", "ssh", "scp", "sftp", "rclone", "s3cmd") and (stdin_code or stdin_file):
            return self.add("egress_with_data", f"{name} sends stdin over the network")
        if name in ("scp", "sftp", "rclone", "s3cmd") or name == "ssh":
            return self.add("egress_with_data", f"{name} transfers data or runs remote commands")
        self.add("egress_offlist", f"{name} uses the network; the Bash sandbox has no network allowlist in v1")

    def git(self, rest):
        i, repo_dirs = 0, list(self.cwds)
        while i < len(rest):
            a = rest[i]
            if a == "-c" or a.startswith("--config-env") or a.startswith("--exec-path"):
                return self.add("unknown", "git -c/--exec-path can run arbitrary commands")
            if a in GIT_OPT_ARGS and i + 1 < len(rest):
                if a == "-C":
                    nxt = []
                    for c in repo_dirs:
                        e = self.expand(rest[i + 1])
                        nxt.append(None if (e is None or c is None) else os.path.realpath(os.path.join(c, e)))
                    repo_dirs = nxt
                i += 2
                continue
            if a.startswith("--git-dir=") or a.startswith("--work-tree=") or a in ("--no-pager", "-p", "-P", "--bare", "--paginate", "--no-replace-objects"):
                i += 1
                continue
            break
        sub = rest[i] if i < len(rest) else ""
        args = rest[i + 1:]
        if sub in GIT_READ:
            if sub == "grep" and any(a.startswith(("-O", "--open-files-in-pager")) for a in args):
                return self.add("unknown", "git grep -O runs a pager command")
            return self.add("read", f"git {sub}")
        if sub == "push":
            return self.add("destructive", "git push (any) is irreversible")
        if sub in GIT_NET or (sub == "remote" and args[:1] in (["show"], ["update"], ["prune"])):
            return self.add("egress_offlist", f"git {sub} uses the network")
        if sub == "send-email":
            return self.add("external_message", "git send-email")
        if sub in ("config",):
            if any(a in ("--global", "--system", "--file", "-f") or a.startswith("--file=") for a in args):
                return self.add("unknown", "git config outside the repository")
            reads = {"--get", "--get-all", "--get-regexp", "-l", "--list", "--get-urlmatch", "--show-origin", "--show-scope"}
            pos = [a for a in args if not a.startswith("-")]
            if any(a in reads for a in args) or (len(pos) == 1 and not any(a.startswith(("--unset", "--add", "--replace", "--edit", "-e", "--rename", "--remove")) for a in args)):
                return self.add("read", "git config read")
        # maintainer policy: index-only / recoverable ops are local_reversible despite writing .git/**
        worktree = any(a in ("-W", "--worktree") or re.fullmatch(r"-[A-Za-z]*W[A-Za-z]*", a) for a in args)
        staged = any(a in ("-S", "--staged") or re.fullmatch(r"-[A-Za-z]*S[A-Za-z]*", a) for a in args)
        if sub == "add" or (sub == "restore" and staged and not worktree) or (sub == "stash" and args[:1] not in (["drop"], ["clear"])):
            return self.add("local_reversible", f"git {sub} (index or stash only)")
        if sub in ("branch", "tag", "remote", "stash", "worktree"):
            listy = {"-l", "--list", "-a", "-r", "-v", "-vv", "--all", "--remotes", "--show-current",
                     "--contains", "--merged", "--no-merged", "list", "show", "get-url", "-n"}
            if not args or all(a in listy or a.startswith("--format") or a.startswith("--sort") for a in args):
                return self.add("read", f"git {sub} (list)")
            if sub == "branch" and any(a in ("-D", "-d", "--delete") for a in args):
                return self.add("destructive", "git branch delete")
            if sub == "stash" and args[:1] in (["drop"], ["clear"]):
                return self.add("destructive", f"git stash {args[0]}")
            if sub == "worktree" and args[:1] == ["remove"]:
                return self.add("destructive", "git worktree remove")
        if sub == "clean" or (sub == "reset" and any(a in ("--hard", "--merge", "--keep") for a in args)):
            return self.add("destructive", f"git {sub} discards uncommitted work")
        if sub in ("checkout", "restore", "switch") and any(
            a in ("-f", "--force", "--", ".", "--discard-changes", "-p", "--patch") or os.path.exists(os.path.join(self.cwds[0] or "", a))
            for a in args
        ):
            return self.add("destructive", f"git {sub} discards working-tree changes")
        if sub in ("filter-branch", "filter-repo", "prune", "gc", "update-ref", "reflog", "replace"):
            return self.add("destructive", f"git {sub} rewrites or drops history")
        if not re.fullmatch(r"[a-z][a-z-]*", sub or "x"):
            return self.add("unknown", f"unrecognised git subcommand {sub!r}")
        # everything else writes the repository: .git/** is protected
        for d in repo_dirs:
            gd = self.find_git_dir(d, init=sub in ("init", "clone"))
            if gd is None or protected.is_protected(gd, self.ctx):
                return self.add("protected_path", f"git {sub} writes .git/** (protected)")
        self.add("local_reversible", f"git {sub} outside the protected repository")

    @staticmethod
    def find_git_dir(d, init=False):
        if d is None:
            return None
        if init:
            return os.path.join(d, ".git")
        cur = d
        while True:
            g = os.path.join(cur, ".git")
            if os.path.exists(g):
                return os.path.realpath(g)
            parent = os.path.dirname(cur)
            if parent == cur:
                return os.path.join(d, ".git")  # no repo found: git will fail; judge the would-be path
            cur = parent

    def kill(self, name, rest, dynamic):
        if name != "kill" or dynamic:  # by name / by piped pids: cannot be tied to this task's own jobs
            return self.add("destructive", f"{name} signals processes not started by this task")
        if rest[:1] in (["-l"], ["-L"]):
            return self.add("read", "kill -l")
        i = 2 if rest[:1] in (["-s"], ["-n"]) else int(bool(rest) and rest[0].startswith("-") and rest[0] != "--")
        targets = rest[i + (rest[i:i + 1] == ["--"]):]
        # shortcut: only job specs (%1, %%) and $! are known to be our own; any pid (1, -1, 4242) is outside
        if targets and all(re.fullmatch(r"%[\w%+?-]*|\$!", t) for t in targets):
            return self.add("local_reversible", "kill of this shell's own background jobs")
        self.add("destructive", "kill of a process not started by this task (pid, -1 or init)")

    def remove(self, name, rest, dynamic):
        pos = _split_opts(rest)[1]
        if dynamic or not pos:
            return self.add("destructive", f"{name} with dynamic or missing targets")
        for w in pos:
            cands = self.candidates(w)
            if cands is None:
                return self.add("destructive", f"{name} target {w!r} cannot be resolved")
            dotdot = ".." in (self.expand(w) or w).split("/")  # traversal may only land in job scratch
            for p in cands:
                if protected.is_protected(p, self.ctx):
                    return self.add("protected_path", f"{name} of protected path {p}")
                if not (protected.is_scratch(p, self.ctx) or (self.in_temp(p) and not dotdot)):
                    return self.add("destructive", f"{name} deletes files outside run state and job scratch")
        self.add("local_reversible", f"{name} inside job scratch or temp")

    def copy(self, name, rest, dynamic):
        if name == "rsync" and any(":" in a and not a.startswith("-") for a in rest):
            return self.add("egress_with_data", "rsync to a remote host")
        opts, pos = _split_opts(rest, {"-t", "-S", "-m", "-o", "-g", "-e", "--target-directory"})
        tdir = _opt_value(rest, ("-t", "--target-directory"))
        if dynamic and not tdir:
            return self.add("unknown", f"{name} with dynamic arguments")
        if name == "cp" and any(o in ("-r", "-R", "-a") or (o.startswith("-") and not o.startswith("--") and ("r" in o or "R" in o or "a" in o)) for o in opts):
            self.recursive_read(pos[:-1] if not tdir else pos)
        if tdir:
            dest, srcs = tdir[0], pos
        elif len(pos) >= 2:
            dest, srcs = pos[-1], pos[:-1]
        else:
            return self.add("unknown", f"{name} without a destination")
        if name in ("mv", "ln"):
            for s in srcs:
                for p in self.candidates(s) or ():
                    if protected.is_protected(p, self.ctx):
                        return self.add("protected_path", f"{name} of protected path {p}")
        for p in self.candidates(dest) or ():
            if protected.outside_workspace(p, self.ctx) and not protected.is_protected(p, self.ctx) and p not in DEV_OK:
                return self.add("external_write", f"{name} destination outside the workspace (data leaves it): {p}")
        self.write_target(dest, name)
        for d in self.candidates(dest) or ():
            if os.path.isdir(d):
                for s in srcs:
                    e = self.expand(s)
                    if e:
                        self.write_target(os.path.join(d, os.path.basename(e.rstrip("/"))), name)
        self.add("local_reversible", f"{name} in the workspace")

    def awk(self, rest):
        files = _opt_value(rest, ("-f",))
        for f in files:
            self.exec_file(f, "awk runs")
        if any(a.startswith("-i") or a == "--inplace" for a in rest):
            return self.add("unknown", "awk in-place editing")
        if not files:
            pos = _split_opts(rest, {"-v", "-F", "-f"})[1]
            prog = pos[0] if pos else ""
            if re.search(r"system\s*\(|\|\s*\"|\"\s*\||\|&|print[f]?[^;}]*>", prog):
                return self.add("unknown", "awk program runs commands or writes files")
        self.add("read", "awk")

    def sed(self, rest):
        inplace = any(a in ("-i", "-I", "--in-place") or (a.startswith("-i") and not a.startswith("--")) or a.startswith("--in-place=") for a in rest)
        scripts = _opt_value(rest, ("-e", "--expression"))
        for f in _opt_value(rest, ("-f", "--file")):
            self.exec_file(f, "sed reads a script")
        opts, pos = _split_opts(rest, {"-e", "-f", "-l", "--expression", "--file"})
        if not scripts and not _opt_value(rest, ("-f", "--file")):
            if "-i" in rest and pos and pos[0] == "":  # BSD sed -i '' suffix
                pos = pos[1:]
            scripts, pos = pos[:1], pos[1:]
        for s in scripts:
            if re.search(r"(^|[;\n{}])\s*(\d+|\$|/[^/]*/)?\s*[wWe](\s|$)", s) or re.search(r"/[gpiIme0-9]*[we](\s|$)", s):
                return self.add("unknown", "sed script writes files or executes commands")
        if inplace:
            for p in pos:
                if p:
                    self.write_target(p, "sed -i")
            return self.add("local_reversible", "sed -i in the workspace")
        self.add("read", "sed")

    def find(self, rest):
        starts = []
        for a in rest:
            if a.startswith("-") or a in ("(", "!", ")"):
                break
            starts.append(a)
        self.recursive_read(starts or ["."])
        for i, a in enumerate(rest):
            if a == "-delete":
                if not starts or not all(
                    all(protected.is_scratch(p, self.ctx) or self.in_temp(p) for p in (self.candidates(s) or [""]))
                    for s in starts
                ):
                    return self.add("destructive", "find -delete outside job scratch")
            if a in ("-exec", "-execdir", "-ok", "-okdir"):
                inner = []
                for t in rest[i + 1:]:
                    if t in (";", "+"):
                        break
                    inner.append(t)
                if not inner:
                    return self.add("unknown", "find -exec without a command")
                c = self.child()
                c.simple(inner, dynamic=True)
                self.results += c.results
            if a in ("-fprint", "-fprint0", "-fprintf", "-fls") and i + 1 < len(rest):
                self.write_target(rest[i + 1], "find")
        self.add("read", "find")

    def tar(self, rest):
        mode = rest[0] if rest else ""
        creating = ("c" in mode or "r" in mode or "u" in mode) and not mode.startswith("--") or any(
            a in ("--create", "--append", "--update") for a in rest)
        extracting = ("x" in mode and not mode.startswith("--")) or "--extract" in rest or "--get" in rest
        f = _opt_value(rest, ("-f", "--file"))
        if not f and not mode.startswith("-") and "f" in mode:
            pos = [a for a in rest[1:] if not a.startswith("-")]
            f = pos[:1]
        if creating:
            for x in f:
                self.write_target(x, "tar")
            self.recursive_read([a for a in rest[1:] if not a.startswith("-") and a not in f])
        if extracting:
            for d in _opt_value(rest, ("-C", "--directory")) or ["."]:
                self.write_target(d, "tar -x")
        self.add("local_reversible", "tar")
