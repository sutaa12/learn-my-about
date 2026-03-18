#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
SKIP_BRANCHES = {"gh-pages"}
INLINE_CODE_RE = re.compile(r"`([^`]+)`")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
ITALIC_RE = re.compile(r"\*(.+?)\*")
LINK_RE = re.compile(r"\[(.+?)\]\((.+?)\)")


@dataclass
class BranchEntry:
    name: str
    source: str
    link: str
    description_html: str
    depth: int


def run_git(args: list[str], *, capture_output: bool = True) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=capture_output,
        text=True,
    )
    return completed.stdout if capture_output else ""


def relative_to_repo(path: Path) -> str | None:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return None


def sanitize_href(target: str) -> str:
    parsed = urlparse(target)
    if parsed.scheme in {"http", "https", "mailto"}:
        return html.escape(target, quote=True)
    if target.startswith(("/", "./", "../", "#")):
        return html.escape(target, quote=True)
    return "#"


def render_inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = INLINE_CODE_RE.sub(lambda match: f"<code>{match.group(1)}</code>", escaped)
    escaped = BOLD_RE.sub(lambda match: f"<strong>{match.group(1)}</strong>", escaped)
    escaped = ITALIC_RE.sub(lambda match: f"<em>{match.group(1)}</em>", escaped)
    return LINK_RE.sub(
        lambda match: (
            f'<a href="{sanitize_href(match.group(2))}">{match.group(1)}</a>'
        ),
        escaped,
    )


def markdown_to_html(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    output: list[str] = []
    paragraph: list[str] = []
    in_list = False

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            output.append(f"<p>{render_inline(' '.join(paragraph))}</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            output.append("</ul>")
            in_list = False

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            close_list()
            continue

        if stripped.startswith("### "):
            flush_paragraph()
            close_list()
            output.append(f"<h3>{render_inline(stripped[4:])}</h3>")
            continue
        if stripped.startswith("## "):
            flush_paragraph()
            close_list()
            output.append(f"<h2>{render_inline(stripped[3:])}</h2>")
            continue
        if stripped.startswith("# "):
            flush_paragraph()
            close_list()
            output.append(f"<h1>{render_inline(stripped[2:])}</h1>")
            continue
        if stripped.startswith(("- ", "* ")):
            flush_paragraph()
            if not in_list:
                output.append("<ul>")
                in_list = True
            output.append(f"<li>{render_inline(stripped[2:])}</li>")
            continue

        paragraph.append(stripped)

    flush_paragraph()
    close_list()
    return "\n".join(output)


def branch_output_parts(branch_name: str) -> list[str]:
    return [quote(segment, safe="") for segment in branch_name.split("/") if segment]


def read_branch_file(branch_name: str, source: str, relative_path: str) -> str | None:
    file_path = REPO_ROOT / relative_path
    if source == "worktree":
        return file_path.read_text(encoding="utf-8") if file_path.exists() else None

    ref = source
    try:
        return run_git(["show", f"{ref}:{relative_path}"])
    except subprocess.CalledProcessError:
        return None


def branch_has_index(branch_name: str, source: str) -> bool:
    return read_branch_file(branch_name, source, "index.html") is not None


def discover_branch_sources() -> list[tuple[str, str]]:
    current_branch = run_git(["branch", "--show-current"]).strip()
    local_branches = {
        line.strip(): line.strip()
        for line in run_git(["for-each-ref", "--format=%(refname:short)", "refs/heads"]).splitlines()
        if line.strip()
    }
    remote_branches = {
        line.split("origin/", 1)[1]: line.strip()
        for line in run_git(["for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"]).splitlines()
        if line.strip() and line.strip() != "origin/HEAD"
    }

    names = sorted((set(local_branches) | set(remote_branches)) - SKIP_BRANCHES)
    discovered: list[tuple[str, str]] = []
    for name in names:
        if name == current_branch:
            discovered.append((name, "worktree"))
        elif name in remote_branches:
            discovered.append((name, remote_branches[name]))
        elif name in local_branches:
            discovered.append((name, local_branches[name]))
    return discovered


def safe_extract_tar(archive_path: Path, destination: Path) -> None:
    destination_root = destination.resolve()
    with tarfile.open(archive_path) as archive:
        for member in archive.getmembers():
            if member.issym() or member.islnk():
                raise ValueError(f"Refused to extract link entry: {member.name}")
            member_path = destination / member.name
            resolved_member = member_path.resolve()
            if destination_root not in resolved_member.parents and resolved_member != destination_root:
                raise ValueError(f"Refused to extract outside destination: {member.name}")
            archive.extract(member, destination)


def export_branch_files(branch_name: str, source: str, destination: Path, output_dir: Path) -> None:
    if source == "worktree":
        output_relative = relative_to_repo(output_dir)
        tracked = run_git(["ls-files", "--cached", "--others", "--exclude-standard", "-z"])
        for raw_path in tracked.split("\0"):
            if not raw_path:
                continue
            if output_relative and (raw_path == output_relative or raw_path.startswith(f"{output_relative}/")):
                continue
            source_path = REPO_ROOT / raw_path
            if not source_path.is_file():
                continue
            target_path = destination / raw_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
        return

    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as temp_archive:
        temp_archive_path = Path(temp_archive.name)
    try:
        run_git(["archive", "--format=tar", "-o", str(temp_archive_path), source], capture_output=False)
        safe_extract_tar(temp_archive_path, destination)
    finally:
        temp_archive_path.unlink(missing_ok=True)


def build_branch_tree(entries: list[BranchEntry]) -> dict[str, dict[str, object]]:
    tree: dict[str, dict[str, object]] = {}
    for entry in entries:
        cursor = tree
        parts = entry.name.split("/")
        for segment in parts:
            node = cursor.setdefault(segment, {"entry": None, "children": {}})
            cursor = node["children"]  # type: ignore[assignment]
        node["entry"] = entry
    return tree


def render_tree(tree: dict[str, dict[str, object]], *, depth: int = 0) -> str:
    if not tree:
        return ""

    items: list[str] = ['<ul class="branch-tree" role="tree">']
    for key in sorted(tree):
        node = tree[key]
        children = node["children"]  # type: ignore[assignment]
        entry = node["entry"]
        children_html = render_tree(children, depth=depth + 1) if children else ""
        if entry is None:
            items.append(
                f'<li class="branch-node" role="treeitem"><div class="branch-node__label">{html.escape(key)}</div>{children_html}</li>'
            )
            continue

        description = entry.description_html or "<p>このブランチにはまだ説明がありません。</p>"
        node_label = (
            f'<div class="branch-node__label">{html.escape(key)}</div>' if children else ""
        )
        items.append(
            """
<li class="branch-leaf" role="treeitem">
  {node_label}
  <article class="branch-card">
    <div class="branch-card__header">
      <span class="branch-card__depth">階層 {depth}</span>
      <h3>{name}</h3>
    </div>
    <div class="branch-card__description">{description}</div>
    <a class="branch-card__link" href="{link}">このブランチの index.html を開く</a>
  </article>
  {children_html}
</li>
""".format(
                node_label=node_label,
                depth=entry.depth,
                name=html.escape(entry.name),
                description=description,
                link=entry.link,
                children_html=children_html,
            )
        )
    items.append("</ul>")
    return "\n".join(items)


def changelog_html() -> str:
    changelog_path = REPO_ROOT / "CHANGELOG.md"
    if not changelog_path.exists():
        return "<p>更新履歴はまだありません。</p>"
    return markdown_to_html(changelog_path.read_text(encoding="utf-8"))


def write_top_page(output_dir: Path, repository: str, entries: list[BranchEntry]) -> None:
    tree_html = render_tree(build_branch_tree(entries))
    repository_name = repository or REPO_ROOT.name
    total = len(entries)
    empty_state = ""
    if not entries:
        empty_state = """
<section class="empty-state">
  <h2>index.html を持つブランチはまだありません</h2>
  <p>各ブランチのルートに <code>index.html</code> を置くと、このトップページに自動で追加されます。</p>
</section>
"""

    page = f"""<!DOCTYPE html>
<html lang="ja">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{html.escape(repository_name)} | Branch Pages Hub</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f7f8ff;
        --panel: #ffffff;
        --panel-soft: #f2f6ff;
        --line: #d8def5;
        --text: #1d2440;
        --muted: #5d6685;
        --brand: #5b6cff;
        --brand-strong: #3948d6;
        --accent: #ff8a65;
        --shadow: 0 20px 45px rgba(91, 108, 255, 0.14);
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: linear-gradient(180deg, #eef2ff 0%, #f9fbff 36%, #ffffff 100%);
        color: var(--text);
        line-height: 1.65;
      }}
      a {{ color: var(--brand-strong); }}
      code {{
        background: #eef2ff;
        border-radius: 0.5rem;
        padding: 0.1rem 0.4rem;
        font-size: 0.9em;
      }}
      .page {{
        width: min(1120px, calc(100% - 2rem));
        margin: 0 auto;
        padding: 1.25rem 0 4rem;
      }}
      .hero {{
        background: radial-gradient(circle at top left, rgba(91,108,255,0.2), transparent 40%), var(--panel);
        border: 1px solid rgba(91,108,255,0.18);
        border-radius: 1.75rem;
        box-shadow: var(--shadow);
        padding: clamp(1.5rem, 4vw, 3rem);
        overflow: hidden;
      }}
      .hero__eyebrow {{
        display: inline-flex;
        padding: 0.35rem 0.8rem;
        border-radius: 999px;
        background: #eef2ff;
        color: var(--brand-strong);
        font-size: 0.9rem;
        font-weight: 700;
      }}
      .hero h1 {{
        margin: 1rem 0 0.75rem;
        font-size: clamp(2rem, 6vw, 3.8rem);
        line-height: 1.05;
      }}
      .hero p {{
        margin: 0;
        max-width: 44rem;
        color: var(--muted);
        font-size: 1.02rem;
      }}
      .hero__grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 1rem;
        margin-top: 1.6rem;
      }}
      .hero__card {{
        padding: 1rem;
        border-radius: 1.2rem;
        background: var(--panel-soft);
        border: 1px solid var(--line);
      }}
      .hero__card strong {{ display: block; margin-bottom: 0.3rem; }}
      .section {{ margin-top: 1.5rem; }}
      .section-header {{
        display: flex;
        flex-wrap: wrap;
        gap: 0.75rem;
        justify-content: space-between;
        align-items: end;
        margin-bottom: 1rem;
      }}
      .section-header h2 {{ margin: 0; font-size: clamp(1.35rem, 4vw, 2rem); }}
      .section-header p {{ margin: 0; color: var(--muted); }}
      .summary-chip {{
        display: inline-flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.55rem 0.9rem;
        background: #fff5ef;
        border: 1px solid #ffd0bf;
        border-radius: 999px;
        color: #b24c24;
        font-weight: 700;
      }}
      .branch-tree {{
        list-style: none;
        margin: 0;
        padding-left: 1rem;
        border-left: 2px solid #dbe2ff;
      }}
      .branch-tree > li {{ margin-top: 1rem; }}
      .branch-node__label {{
        display: inline-flex;
        align-items: center;
        gap: 0.5rem;
        font-weight: 800;
        color: var(--brand-strong);
        padding: 0.35rem 0.75rem;
        border-radius: 999px;
        background: #eef2ff;
        border: 1px solid #d7ddff;
      }}
      .branch-card {{
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 1.4rem;
        padding: 1.2rem;
        box-shadow: 0 12px 30px rgba(79, 92, 167, 0.08);
      }}
      .branch-card__header {{
        display: flex;
        flex-wrap: wrap;
        justify-content: space-between;
        gap: 0.6rem;
        align-items: center;
      }}
      .branch-card__header h3 {{ margin: 0; font-size: 1.1rem; }}
      .branch-card__depth {{
        display: inline-flex;
        align-items: center;
        padding: 0.3rem 0.7rem;
        border-radius: 999px;
        background: #fff5ef;
        color: #b24c24;
        font-size: 0.85rem;
        font-weight: 700;
      }}
      .branch-card__description {{ margin-top: 0.85rem; color: var(--muted); }}
      .branch-card__description > *:first-child {{ margin-top: 0; }}
      .branch-card__description > *:last-child {{ margin-bottom: 0; }}
      .branch-card__link {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        margin-top: 1rem;
        padding: 0.85rem 1.15rem;
        border-radius: 999px;
        background: linear-gradient(135deg, var(--brand) 0%, #7f87ff 100%);
        color: #ffffff;
        font-weight: 800;
        text-decoration: none;
      }}
      .branch-card__link:hover {{ background: linear-gradient(135deg, var(--brand-strong) 0%, #6b78ff 100%); }}
      .panel {{
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 1.5rem;
        padding: 1.4rem;
        box-shadow: 0 10px 28px rgba(79, 92, 167, 0.08);
      }}
      .changelog {{ color: var(--muted); }}
      .empty-state {{
        margin-top: 1.5rem;
        text-align: center;
        background: var(--panel);
        border: 1px dashed #c7d0ff;
        border-radius: 1.5rem;
        padding: 2rem 1.25rem;
      }}
      @media (max-width: 720px) {{
        .page {{ width: min(100% - 1rem, 1120px); }}
        .hero {{ border-radius: 1.25rem; }}
        .branch-tree {{ padding-left: 0.85rem; }}
        .branch-card {{ padding: 1rem; }}
        .branch-card__link {{ width: 100%; }}
      }}
    </style>
  </head>
  <body>
    <main class="page">
      <section class="hero">
        <span class="hero__eyebrow">GitHub Action で自動生成</span>
        <h1>ブランチごとの <code>index.html</code> を、1つの見やすいトップページにまとめました。</h1>
        <p>
          このトップページは、各ブランチのルートにある <code>index.html</code> と任意の <code>description.md</code> を集めて、
          GitHub Pages 向けの静的サイトとして公開する仕組みで生成されています。スラッシュ区切りのブランチ名は階層として整理し、
          スマホでも辿りやすい構成にしています。
        </p>
        <div class="hero__grid">
          <div class="hero__card">
            <strong>1. ブランチに配置</strong>
            <span><code>index.html</code> を置くと自動で候補になります。<code>description.md</code> があれば説明も表示します。</span>
          </div>
          <div class="hero__card">
            <strong>2. Action が収集</strong>
            <span>ワークフローが全ブランチを走査し、表示用のトップページと各ブランチの静的ファイルをまとめます。</span>
          </div>
          <div class="hero__card">
            <strong>3. GitHub Pages に公開</strong>
            <span>生成された成果物を GitHub Pages へデプロイし、ブラウザから一覧と各サイトへアクセスできます。</span>
          </div>
        </div>
      </section>

      <section class="section">
        <div class="section-header">
          <div>
            <h2>公開されるブランチ一覧</h2>
            <p>名前の階層が深いブランチほど、リストの中で段差をつけて整理しています。</p>
          </div>
          <span class="summary-chip">公開中 {total} ブランチ</span>
        </div>
        {empty_state if empty_state else tree_html}
      </section>

      <section class="section panel">
        <div class="section-header">
          <div>
            <h2>更新履歴</h2>
            <p>公開サイトに載せる履歴はこのセクションだけに絞っています。</p>
          </div>
        </div>
        <div class="changelog">{changelog_html()}</div>
      </section>
    </main>
  </body>
</html>
"""
    (output_dir / "index.html").write_text(page, encoding="utf-8")


def generate(output_dir: Path, repository: str) -> list[BranchEntry]:
    output_dir.mkdir(parents=True, exist_ok=True)
    branches_dir = output_dir / "branches"
    branches_dir.mkdir(exist_ok=True)

    entries: list[BranchEntry] = []
    for branch_name, source in discover_branch_sources():
        if not branch_has_index(branch_name, source):
            continue
        output_parts = branch_output_parts(branch_name)
        branch_dir = branches_dir.joinpath(*output_parts)
        branch_dir.mkdir(parents=True, exist_ok=True)
        export_branch_files(branch_name, source, branch_dir, output_dir)
        description = read_branch_file(branch_name, source, "description.md") or ""
        relative_link = "/".join(["branches", *output_parts, "index.html"])
        entries.append(
            BranchEntry(
                name=branch_name,
                source=source,
                link=relative_link,
                description_html=markdown_to_html(description) if description else "",
                depth=max(len(branch_name.split("/")) - 1, 0),
            )
        )

    write_top_page(output_dir, repository, entries)
    return entries


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a GitHub Pages hub for branch index.html files.")
    parser.add_argument("--output-dir", required=True, help="Directory where the generated site will be written.")
    parser.add_argument("--repository", default="", help="Repository name used in the page title.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    generate(Path(args.output_dir).resolve(), args.repository)


if __name__ == "__main__":
    main()
