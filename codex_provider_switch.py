#!/usr/bin/env python3
"""Offline provider switcher for local Codex history; Python 3.11+."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib

import runtime_storage


def digest(path: Path) -> str:
    """Return the SHA256 of a file without loading conversation bodies."""
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    """Replace a file atomically, preserving its permission bits."""
    fd, name = tempfile.mkstemp(prefix='.provider-', dir=path.parent)
    try:
        os.fchmod(fd, path.stat().st_mode & 0o777 if path.exists() else 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def connect(db: Path, writable: bool = False) -> sqlite3.Connection:
    """Open SQLite with an explicit read-only default."""
    return sqlite3.connect(db.as_uri() + ('?mode=rw' if writable else '?mode=ro'), uri=True, timeout=10)


def require_offline() -> None:
    """Refuse writes while Codex app/CLI/app-server processes are running."""
    result = subprocess.run(['ps', '-axo', 'pid=,comm='], check=True, capture_output=True, text=True)
    found = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid, executable = parts
        base = Path(executable).name.lower()
        if base in {'codex', 'codex-cli', 'codex-app-server'} or '/Codex.app/' in executable:
            found.append(pid + ': ' + executable)
    if found:
        raise RuntimeError('请先完全退出 Codex 桌面版、CLI 和 VS Code 中的 Codex，再执行。检测到：\n' + '\n'.join(found))


def config_bytes(path: Path, target: str, model: str | None) -> bytes:
    """Patch only root routing/model keys and validate the resulting TOML."""
    original = path.read_text()
    lines = original.splitlines(keepends=True)
    end = next((i for i, line in enumerate(lines) if line.lstrip().startswith('[')), len(lines))
    updates = {'model_provider': target}
    if model:
        updates['model'] = model
    root = lines[:end]
    for key, value in updates.items():
        expression = re.compile(r'^\s*' + re.escape(key) + r'\s*=')
        hits = [i for i, line in enumerate(root) if expression.match(line)]
        replacement = key + ' = ' + json.dumps(value) + '\n'
        if hits:
            root[hits[0]] = replacement
        else:
            root.insert(0, replacement)
    text = ''.join(root + lines[end:])
    parsed = tomllib.loads(text)
    if parsed.get('model_provider') != target:
        raise RuntimeError('配置验证失败')
    return text.encode()


def inspect(home: Path) -> tuple[dict, Path, list[dict]]:
    """Read config and thread routing metadata, excluding conversation content."""
    config = tomllib.loads((home / 'config.toml').read_text())
    dbhome = Path(config.get('sqlite_home', str(home))).expanduser().resolve()
    dbs = list(dbhome.glob('state_*.sqlite'))
    if len(dbs) != 1:
        raise RuntimeError('需要唯一的 state_*.sqlite；检测到 ' + str(len(dbs)))
    with connect(dbs[0]) as con:
        con.row_factory = sqlite3.Row
        columns = {row[1] for row in con.execute('PRAGMA table_info(threads)')}
        history_column = 'history_mode' if 'history_mode' in columns else "'legacy' AS history_mode"
        rows = [dict(row) for row in con.execute(
            'SELECT id, model_provider, rollout_path, ' + history_column + ' FROM threads'
        )]
    return config, dbs[0], rows


def paginated_history(db: Path) -> tuple[Path | None, set[str]]:
    """Find threads registered in the paginated history DB beside the state DB.

    A projection record also represents a valid empty paginated conversation.
    Read only IDs; never deserialize or modify message content.
    """
    candidates = list(db.parent.glob('thread_history_*.sqlite'))
    if not candidates:
        return None, set()
    if len(candidates) != 1:
        raise RuntimeError('检测到多个分页历史数据库，停止以避免选择错误')
    with connect(candidates[0]) as con:
        registered = {row[0] for row in con.execute(
            'SELECT thread_id FROM thread_history_projection_state'
        )}
    return candidates[0], registered


def status(home: Path) -> None:
    """Display configured and stored providers without exposing credentials."""
    config, _, rows = inspect(home)
    print('Codex 目录:', home)
    print('默认 provider:', config.get('model_provider', 'openai'))
    print('当前模型:', config.get('model', '(默认)'))
    print('可选 provider:', ', '.join(dict.fromkeys(['openai'] + list(config.get('model_providers', {})))))
    counts: dict[str, int] = {}
    for row in rows:
        counts[row['model_provider']] = counts.get(row['model_provider'], 0) + 1
    print('对话索引:', json.dumps(counts, ensure_ascii=False))


def restore(folder: Path, home: Path) -> None:
    """Undo only recorded changes, refusing to overwrite later modifications."""
    require_offline()
    manifest_path = folder / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest['home'] != str(home):
        raise RuntimeError('备份属于另一个 Codex 目录')
    if manifest.get('state') == 'restored':
        print('此备份已经撤销。')
        return
    for item in manifest['files']:
        if digest(Path(item['path'])) not in {item['before'], item['after']}:
            raise RuntimeError('文件已被后续使用修改，拒绝覆盖：' + item['path'])
    with connect(Path(manifest['db']), True) as con:
        con.execute('BEGIN IMMEDIATE')
        for row in manifest['rows']:
            current = con.execute('SELECT model_provider FROM threads WHERE id=?', (row['id'],)).fetchone()
            if not current or current[0] not in {row['model_provider'], manifest['target']}:
                raise RuntimeError('对话索引已变化，停止撤销：' + row['id'])
        for item in manifest['files']:
            atomic_write(Path(item['path']), (folder / item['backup']).read_bytes())
        for row in manifest['rows']:
            con.execute('UPDATE threads SET model_provider=? WHERE id=?', (row['model_provider'], row['id']))
    manifest['state'] = 'restored'
    atomic_write(manifest_path, json.dumps(manifest, indent=2).encode())
    print('已撤销；请重新启动 Codex。')


def switch(args: argparse.Namespace, home: Path) -> None:
    """Preview or apply config, session header and index routing changes."""
    config, db, rows = inspect(home)
    target = args.provider
    if target != 'openai' and target not in config.get('model_providers', {}):
        raise RuntimeError('未知 provider；请先在 config.toml 中配置')
    if config.get('profile'):
        raise RuntimeError('检测到启用的 profile；请先取消或单独处理 profile，避免配置被覆盖')
    if target == 'openai' and any(config.get(k) for k in ('openai_base_url', 'chatgpt_base_url')):
        raise RuntimeError('检测到官方地址覆盖项，请先核对 openai_base_url/chatgpt_base_url')
    if args.thread and args.all:
        raise RuntimeError('--thread 与 --all 不能同时使用')
    if args.thread:
        unknown = set(args.thread) - {r['id'] for r in rows}
        if unknown:
            raise RuntimeError('找不到对话：' + ', '.join(sorted(unknown)))
    selected = [r for r in rows if (args.all or r['id'] in (args.thread or [])) and (not args.source or r['model_provider'] == args.source)]
    changes: list[tuple[Path, bytes, bool]] = []
    changed_rows = []
    missing = []
    paginated_only = []
    history_db, registered = paginated_history(db) if selected else (None, set())
    cfg = home / 'config.toml'
    new_config = config_bytes(cfg, target, args.model)
    if cfg.read_bytes() != new_config:
        changes.append((cfg, new_config, False))
    for row in selected:
        path = Path(row['rollout_path'])
        if not path.is_absolute():
            path = home / path
        path = path.resolve()
        if not path.is_relative_to(home):
            raise RuntimeError('会话文件位于 Codex 目录外，需人工检查：' + str(path))
        if not path.is_file():
            if row['history_mode'] == 'paginated' and row['id'] in registered:
                paginated_only.append(row['id'])
                if row['model_provider'] != target:
                    changed_rows.append(row)
            else:
                missing.append(row['id'])
            continue
        with path.open('rb') as stream:
            first = stream.readline()
        meta = json.loads(first)
        if meta.get('type') != 'session_meta':
            raise RuntimeError('未识别的会话首记录：' + str(path))
        payload = meta['payload']
        if payload.get('id') != row['id']:
            raise RuntimeError('会话 ID 与索引不匹配：' + str(path))
        if payload.get('model_provider') != target:
            payload['model_provider'] = target
            changes.append((path, json.dumps(meta, ensure_ascii=False, separators=(',', ':')).encode() + b'\n', True))
        if row['model_provider'] != target:
            changed_rows.append(row)
    if paginated_only:
        print(f'分页历史数据库中确认 {len(paginated_only)} 个无旧 JSONL 的对话；按索引切换，保留全部历史内容。')
    if missing:
        print('会话文件缺失的对话 ID：' + ', '.join(missing))
        if not args.skip_missing:
            raise RuntimeError('存在缺失文件；未修改任何内容。可加 --skip-missing 显式跳过这些对话')
        print(f'显式跳过 {len(missing)} 个缺失文件的对话，其索引保持原值。')
    print(f'目标 provider: {target}；匹配对话: {len(selected)}；可处理: {len(selected) - len(missing)}；跳过: {len(missing)}；索引修改: {len(changed_rows)}；文件修改: {len(changes)}')
    print('范围:', '全部对话' if args.all else ('指定对话' if args.thread else '仅默认配置'))
    if not args.apply:
        print('预览完成，未修改。加 --apply 执行。')
        return
    require_offline()
    if not changes and not changed_rows:
        print('已经是目标状态，无需修改。')
        return
    folder = runtime_storage.create_backup_folder('switch')
    # SQLite backup API includes committed WAL contents; retain the full snapshot.
    with connect(db) as source_db, sqlite3.connect(folder / 'state.snapshot.sqlite') as snapshot:
        source_db.backup(snapshot)
    os.chmod(folder / 'state.snapshot.sqlite', 0o600)
    if history_db is not None:
        with connect(history_db) as source_history, sqlite3.connect(folder / 'history.snapshot.sqlite') as snapshot:
            source_history.backup(snapshot)
        os.chmod(folder / 'history.snapshot.sqlite', 0o600)
    manifest = {'home': str(home), 'db': str(db), 'target': target, 'state': 'prepared', 'rows': changed_rows, 'files': [],
                'paginated_only': paginated_only}
    # Prepare complete backups and new files before changing any original.
    for i, (path, new_data, header_only) in enumerate(changes):
        backup = folder / f'{i}.original'
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
        prepared = folder / f'{i}.new'
        with prepared.open('wb') as out:
            out.write(new_data)
            if header_only:
                with backup.open('rb') as stream:
                    stream.readline()
                    shutil.copyfileobj(stream, out)
        os.chmod(prepared, 0o600)
        manifest['files'].append({'path': str(path), 'backup': backup.name, 'new': prepared.name, 'before': digest(backup), 'after': digest(prepared)})
    manifest_path = folder / 'manifest.json'
    atomic_write(manifest_path, json.dumps(manifest, indent=2).encode())
    try:
        with connect(db, True) as con:
            con.execute('BEGIN IMMEDIATE')
            for row in changed_rows:
                cur = con.execute('UPDATE threads SET model_provider=? WHERE id=? AND model_provider=?', (target, row['id'], row['model_provider']))
                if cur.rowcount != 1:
                    raise RuntimeError('索引在准备期间变化，请重新预览')
            for item in manifest['files']:
                if digest(Path(item['path'])) != item['before']:
                    raise RuntimeError('文件在准备期间变化：' + item['path'])
            for item in manifest['files']:
                atomic_write(Path(item['path']), (folder / item['new']).read_bytes())
            for item in manifest['files']:
                if digest(Path(item['path'])) != item['after']:
                    raise RuntimeError('写后验证失败')
    except BaseException:
        # SQLite transaction rolls back; restore files already changed by this run.
        for item in manifest['files']:
            path = Path(item['path'])
            if digest(path) == item['after']:
                atomic_write(path, (folder / item['backup']).read_bytes())
        raise
    manifest['state'] = 'applied'
    atomic_write(manifest_path, json.dumps(manifest, indent=2).encode())
    print('已完成；完整会话文件、配置及 SQLite 快照备份:', folder)
    print('请重新启动 Codex。官方订阅用量需要在 Codex 中使用 ChatGPT 登录。')


def main() -> None:
    """Expose interactive macOS launcher and scriptable commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))))
    commands = parser.add_subparsers(dest='command')
    commands.add_parser('status')
    s = commands.add_parser('switch')
    s.add_argument('provider')
    s.add_argument('--all', action='store_true', help='Include all indexed conversations, including archived ones')
    s.add_argument('--thread', action='append', help='Exact thread ID; repeat for multiple threads')
    s.add_argument('--from', dest='source', help='Only migrate threads currently indexed under this provider')
    s.add_argument('--model', help='Optional default model; historical model selections are preserved')
    s.add_argument('--skip-missing', action='store_true', help='Explicitly skip indexed threads whose rollout files are missing')
    s.add_argument('--apply', action='store_true')
    repair = commands.add_parser('repair-ids', help='Repair foreign persisted-item IDs without deleting messages')
    scope = repair.add_mutually_exclusive_group(required=True)
    scope.add_argument('--all', action='store_true')
    scope.add_argument('--thread', action='append')
    repair.add_argument('--apply', action='store_true')
    r = commands.add_parser('restore')
    r.add_argument('backup', type=Path)
    args = parser.parse_args()
    home = args.home.expanduser().resolve()
    if not args.command:
        status(home)
        print('\n切换前请退出所有 Codex 客户端。此程序不会自动退出应用。')
        choice = input('选择：1 官方 / 2 第三方 / 3 撤销 / 4 修复历史 ID 错误 / Enter 退出：').strip()
        if choice == '4':
            import history_id_repair
            threads = input('完整对话 ID，多个用空格分隔；Enter 检查全部本地历史：').split()
            repair_args = argparse.Namespace(all=not threads, thread=threads, apply=False)
            history_id_repair.perform(repair_args, home)
            if input('退出 Codex 后，输入 REPAIR 执行历史 ID 修复：').strip() == 'REPAIR':
                repair_args.apply = True
                history_id_repair.perform(repair_args, home)
            return
        if choice == '3':
            restore(Path(input('粘贴备份目录：').strip()).expanduser().resolve(), home)
            return
        if choice not in {'1', '2'}:
            return
        config, _, _ = inspect(home)
        provider = 'openai' if choice == '1' else input('输入第三方 provider ID（' + ', '.join(config.get('model_providers', {})) + '）：').strip()
        scope = input('范围：1 全部对话 / 2 指定对话 / 3 仅默认配置：').strip()
        if scope not in {'1', '2', '3'}:
            return
        args.provider, args.all, args.source, args.model = provider, scope == '1', None, None
        args.thread = input('输入完整对话 ID，多个用空格分隔：').split() if scope == '2' else None
        if scope == '2' and not args.thread:
            return
        args.skip_missing = input('是否跳过会话文件缺失的记录？输入 y 同意，其他输入保持严格检查：').strip().lower() == 'y'
        args.apply = False
        switch(args, home)
        if input('输入 SWITCH 执行（会先检查 Codex 是否已退出）：').strip() == 'SWITCH':
            args.apply = True
            switch(args, home)
    elif args.command == 'status':
        status(home)
    elif args.command == 'switch':
        switch(args, home)
    elif args.command == 'repair-ids':
        import history_id_repair
        history_id_repair.perform(args, home)
    else:
        restore(args.backup.expanduser().resolve(), home)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print('操作失败：' + str(error), file=sys.stderr)
        sys.exit(1)
