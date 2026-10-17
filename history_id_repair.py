"""Backed-up repair of foreign server item IDs in Codex rollout history."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil

import codex_provider_switch as core

ID_PATTERN = re.compile(r'[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_-]+\Z')
DECODER = json.JSONDecoder()


def repair_line(line: bytes) -> tuple[bytes, int]:
    """Localize only response IDs and retained-message references, at equal length.

    Preserve every message, tool call_id, encrypted payload, whitespace and byte
    offset. Unprefixed IDs are omitted by Codex's request preparation code.
    """
    record = json.loads(line)
    kind = record.get('type')
    if kind not in {'response_item', 'compacted'}:
        return line, 0
    text = line.decode('utf-8')
    edits: list[tuple[int, int, str]] = []

    def eligible(path: tuple[str | int, ...]) -> bool:
        """Select raw IDs and their explicit retained-context references."""
        if kind == 'response_item':
            return path == ('payload', 'id')
        return (
            len(path) == 4
            and path[0] == 'payload'
            and path[1] in {'replacement_history', 'guardian_history'}
            and isinstance(path[2], int)
            and path[3] == 'id'
        ) or (
            len(path) == 5
            and path[:2] == ('payload', 'retained_context')
            and path[2] in {'user_messages', 'assistant_messages'}
            and isinstance(path[3], int)
            and path[4] == 'message_id'
        )

    def skip(index: int) -> int:
        """Skip JSON whitespace without changing serialized formatting."""
        while index < len(text) and text[index].isspace():
            index += 1
        return index

    def visit(index: int, path: tuple[str | int, ...]) -> int:
        """Walk JSON token spans and record only eligible string replacements."""
        index = skip(index)
        if text[index] == '{':
            index = skip(index + 1)
            if text[index] == '}':
                return index + 1
            while True:
                key, index = DECODER.raw_decode(text, index)
                index = skip(index)
                if text[index] != ':':
                    raise ValueError('Invalid JSON object delimiter')
                index = skip(visit(index + 1, path + (key,)))
                if text[index] == '}':
                    return index + 1
                if text[index] != ',':
                    raise ValueError('Invalid JSON object separator')
                index = skip(index + 1)
        if text[index] == '[':
            index = skip(index + 1)
            if text[index] == ']':
                return index + 1
            ordinal = 0
            while True:
                index = skip(visit(index, path + (ordinal,)))
                if text[index] == ']':
                    return index + 1
                if text[index] != ',':
                    raise ValueError('Invalid JSON array separator')
                index = skip(index + 1)
                ordinal += 1
        value, end = DECODER.raw_decode(text, index)
        if eligible(path) and isinstance(value, str) and ID_PATTERN.fullmatch(value):
            replacement = json.dumps(value.replace('_', '-'))
            if len(replacement) != end - index:
                raise RuntimeError('ID 使用转义格式，停止以保护字节偏移')
            edits.append((index, end, replacement))
        return end

    visit(0, ())
    for start, end, replacement in reversed(edits):
        text = text[:start] + replacement + text[end:]
    result = text.encode('utf-8')
    if len(result) != len(line):
        raise RuntimeError('字节长度发生变化，停止修复')
    return result, len(edits)


def perform(args: argparse.Namespace, home: Path) -> None:
    """Preview or repair matching rollout files plus their available base lineage."""
    _, db, rows = core.inspect(home)
    ids = {row['id'] for row in rows} if args.all else set(args.thread or [])
    unknown = ids - {row['id'] for row in rows}
    if unknown:
        raise RuntimeError('找不到对话：' + ', '.join(sorted(unknown)))
    candidates: list[tuple[Path, dict]] = []
    for folder in ['sessions', 'archived_sessions']:
        for path in (home / folder).rglob('*.jsonl'):
            path = path.resolve()
            if not path.is_relative_to(home):
                raise RuntimeError('历史文件位于 Codex 目录外：' + str(path))
            with path.open('rb') as stream:
                first = json.loads(stream.readline())
            if first.get('type') == 'session_meta':
                candidates.append((path, first['payload']))
    # Include every segment of a selected thread and its recursively referenced base.
    wanted = set(ids)
    while True:
        bases = {meta['history_base']['thread_id'] for _, meta in candidates
                 if meta.get('id') in wanted and isinstance(meta.get('history_base'), dict)
                 and meta['history_base'].get('thread_id')}
        if bases <= wanted:
            break
        wanted.update(bases)
    sources = [(path, meta) for path, meta in candidates if meta.get('id') in wanted]
    unavailable = ids - {meta.get('id') for _, meta in sources}
    changes: list[tuple[Path, bytes, int]] = []
    for path, _ in sources:
        result = bytearray()
        count = 0
        with path.open('rb') as stream:
            for line in stream:
                fixed, changed = repair_line(line)
                result.extend(fixed)
                count += changed
        if count:
            changes.append((path, bytes(result), count))
    print(f'历史 ID 修复：检查 {len(sources)} 个历史分段；待改 {len(changes)} 个文件；'
          f'ID/引用 {sum(count for _, _, count in changes)} 处。')
    if unavailable:
        print(f'{len(unavailable)} 个对话没有可检查的原始 rollout；保留其 SQLite 历史，不对其作兼容性保证。')
    if not args.apply:
        print('仅预览，未修改。使用 --apply 执行；此功能不改变 provider。')
        return
    core.require_offline()
    if not changes:
        print('没有需要修复的本地历史 ID。')
        return
    folder = core.runtime_storage.create_backup_folder('ids')
    with core.connect(db) as source, core.sqlite3.connect(folder / 'state.snapshot.sqlite') as dest:
        source.backup(dest)
    os.chmod(folder / 'state.snapshot.sqlite', 0o600)
    history, _ = core.paginated_history(db)
    if history is not None:
        with core.connect(history) as source, core.sqlite3.connect(folder / 'history.snapshot.sqlite') as dest:
            source.backup(dest)
        os.chmod(folder / 'history.snapshot.sqlite', 0o600)
    manifest = {'home': str(home), 'db': str(db), 'target': None, 'rows': [],
                'operation': 'repair_ids', 'state': 'prepared', 'files': []}
    for index, (path, content, count) in enumerate(changes):
        original = folder / f'{index}.original'
        prepared = folder / f'{index}.new'
        shutil.copy2(path, original)
        os.chmod(original, 0o600)
        core.atomic_write(prepared, content)
        manifest['files'].append({'path': str(path), 'backup': original.name,
                                  'new': prepared.name, 'before': core.digest(original),
                                  'after': core.digest(prepared), 'id_changes': count})
    journal = folder / 'manifest.json'
    core.atomic_write(journal, json.dumps(manifest, indent=2).encode())
    for item in manifest['files']:
        if core.digest(Path(item['path'])) != item['before']:
            raise RuntimeError('准备期间历史文件发生变化，未执行修复')
    try:
        for item in manifest['files']:
            core.atomic_write(Path(item['path']), (folder / item['new']).read_bytes())
        for item in manifest['files']:
            if core.digest(Path(item['path'])) != item['after']:
                raise RuntimeError('历史 ID 修复写后验证失败')
    except BaseException:
        for item in manifest['files']:
            path = Path(item['path'])
            if core.digest(path) == item['after']:
                core.atomic_write(path, (folder / item['backup']).read_bytes())
        raise
    manifest['state'] = 'applied'
    core.atomic_write(journal, json.dumps(manifest, indent=2).encode())
    print('历史请求 ID 已本地化；原始文件完整保留于：', folder)
    print('请重启 Codex 并在原对话重试。可通过原撤销菜单恢复本次修改。')
