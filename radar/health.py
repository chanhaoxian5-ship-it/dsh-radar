"""Read-only local diagnostics. Never instantiate Service or migrate a database."""
import argparse
import importlib.util
import json
import sqlite3
import time
from pathlib import Path


def inspect(directory):
    root = Path(directory)
    checks = []
    def add(name, ok, message):
        checks.append({'name': name, 'ok': ok, 'message': message})
    dependency = importlib.util.find_spec('jsonschema') is not None
    add('python_dependencies', dependency, 'Python 和 jsonschema 可用' if dependency else '当前 Python 缺少 jsonschema；请修复插件运行环境')
    if not root.exists():
        add('data_directory', True, '首次启动，数据目录尚未创建')
        return checks
    if root.is_symlink() or (hasattr(root, 'is_junction') and root.is_junction()):
        add('data_directory', False, '数据目录为链接，需要人工核对')
        return checks
    database = root / 'radar.sqlite3'
    if not any(root.iterdir()):
        add('data_directory', True, '空目录，可初始化')
        return checks
    owned = (root / '.radar-owner').is_file() or database.is_file()
    add('data_directory', owned, '已有雷达目录' if owned else '非空目录无法确认为雷达所有，不自动初始化')
    if not owned:
        return checks
    if database.exists():
        try:
            with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=2) as db:
                db.execute('PRAGMA query_only=ON')
                deadline = time.monotonic() + 5
                db.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
                rows = db.execute('PRAGMA quick_check(1)').fetchall()
                recognized = db.execute("SELECT value FROM meta WHERE key='service_epoch'").fetchone()
                ok = rows == [('ok',)] and bool(recognized)
                add('database', ok, '数据库快速检查通过' if ok else '数据库完整性或身份检查未通过；保留原库，需人工处理')
        except sqlite3.Error:
            add('database', False, '数据库无法只读校验；可能被锁定、损坏或格式不符，不自动重建')
        missing = []
        for role in ('agent', 'owner', 'gateway', 'collector', 'runner', 'scheduler'):
            try:
                if not (root / (role + '.token')).read_text(encoding='ascii').strip():
                    missing.append(role)
            except (OSError, UnicodeError):
                missing.append(role)
        add('credentials', not missing, '本机凭据文件可读；身份由服务应答进一步验证' if not missing else '已有数据库的凭据缺失或不可读，不自动重置：' + ', '.join(missing))
    else:
        # An ownership marker without a database can be an interrupted first start,
        # but can also mean data loss. Never silently create a replacement here.
        add('database', False, '已有目录缺少数据库；请核对备份，不自动创建空库')
    return checks


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    args = parser.parse_args()
    try:
        result = inspect(args.data_dir)
    except (OSError, ValueError):
        result = [{'name': 'data_directory', 'ok': False, 'message': '无法检查数据目录访问权限'}]
    print(json.dumps(result, ensure_ascii=False))
