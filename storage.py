import os
import vdf
import time
import shutil
import winreg
import sqlite3
import argparse
import requests
import traceback
from pathlib import Path
from multiprocessing.pool import ThreadPool
from multiprocessing.dummy import Pool, Lock

lock = Lock()


def get(sha, path):
    url_list = [f'https://cdn.jsdelivr.net/gh/{repo}@{sha}/{path}',
                f'https://ghproxy.com/https://raw.githubusercontent.com/{repo}/{sha}/{path}',
                f'https://raw.staticdn.net/{repo}/{sha}/{path}', f'https://raw.fastgit.org/{repo}/{sha}/{path}'
                ]
    retry = 8
    while True:
        for url in url_list:
            try:
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    return r.content
                elif r.status_code == 404:
                    return False
            except:
                print(f'获取失败: {path}')
                retry -= 1
                if not retry:
                    print(f'超过最大重试次数: {path}')
                    raise


def get_manifest(sha, path, steam_path: Path):
    try:
        if path.endswith('.manifest'):
            depot_cache_path = steam_path / 'depotcache'
            with lock:
                if not depot_cache_path.exists():
                    depot_cache_path.mkdir(exist_ok=True)
            save_path = depot_cache_path / path
            if save_path.exists():
                with lock:
                    print(f'已存在清单: {path}')
                return
            content = get(sha, path)
            with lock:
                print(f'清单下载成功: {path}')
            with save_path.open('wb') as f:
                f.write(content)
        elif path == 'config.vdf':
            content = get(sha, path)
            with lock:
                print(f'密钥下载成功: {path}')
            depots_config = vdf.loads(content.decode(encoding='utf-8'))
            if depotkey_merge(steam_path / 'config' / path, depots_config):
                print('合并config.vdf成功')
            if stool_add(
                    [(depot_id, '1', depots_config['depots'][depot_id]['DecryptionKey'])
                     for depot_id in depots_config['depots']]):
                print('导入steamtools成功')
    except KeyboardInterrupt:
        raise
    except:
        traceback.print_exc()
        raise
    return True


def depotkey_merge(config_path, depots_config):
    if not config_path.exists():
        with lock:
            print('config.vdf不存在')
        return
    with open(config_path, encoding='utf-8') as f:
        config = vdf.load(f)
    software = config['InstallConfigStore']['Software']
    valve = software.get('Valve') or software.get('valve')
    steam = valve.get('Steam') or valve.get('steam')
    if 'depots' not in steam:
        steam['depots'] = {}
    steam['depots'].update(depots_config['depots'])
    with open(config_path, 'w', encoding='utf-8') as f:
        vdf.dump(config, f, pretty=True)
    return True


def stool_add(depot_list):
    if args.greenluma:
        return False
    info_path = Path('~/AppData/Roaming/Stool/info.pak').expanduser()
    conn = sqlite3.connect(info_path)
    c = conn.cursor()
    for depot_id, type_, depot_key in depot_list:
        if depot_key:
            depot_key = f'"{depot_key}"'
        columns = dict(filter(lambda x: x[1], zip(['appid', 'type', 'DecryptionKey'], [depot_id, type_, depot_key])))
        sql = 'insert or replace into Appinfo ({}) values ({})'.format(','.join(columns), ','.join(columns.values()))
        c.execute(sql)
    conn.commit()
    return True


def get_steam_path():
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam')
    steam_path = Path(winreg.QueryValueEx(key, 'SteamPath')[0])
    return steam_path


def main(app_id):
    url = f'https://api.github.com/repos/{repo}/branches/{app_id}'
    r = requests.get(url)
    if 'commit' in r.json():
        sha = r.json()['commit']['sha']
        url = r.json()['commit']['commit']['tree']['url']
        r = requests.get(url)
        if 'tree' in r.json():
            stool_add([(app_id, '1', None)])
            result_list = []
            manifest_id = []
            with Pool(32) as pool:
                pool: ThreadPool
                for i in r.json()['tree']:
                    if args.greenluma:
                        if i['path'].endswith('.manifest'):
                            id = i['path'].split('_')[0]
                            manifest_id.append(id)
                    if not args.delete:
                        result_list.append(pool.apply_async(get_manifest, (sha, i['path'], get_steam_path())))
                if args.greenluma:
                    result_list.append(pool.apply_async(generate_applist, (app_id, get_dlc_id(app_id), manifest_id)))
                try:
                    while pool._state == 'RUN':
                        if all([result.ready() for result in result_list]):
                            break
                        time.sleep(0.1)
                except KeyboardInterrupt:
                    with lock:
                        pool.terminate()
                    raise
            if all([result.successful() for result in result_list]):
                print(f'入库成功: {app_id}')
                print('重启steam生效')
                return True
    elif r.status_code == 403:
        print('Github请求过于频繁, 请稍后再试')
    print(f'入库失败: {app_id}')
    return False


def get_dlc_id(app_id):
    url = f'https://api.github.com/repos/{repo}/branches/data'
    r = requests.get(url)
    if 'commit' in r.json():
        sha = r.json()['commit']['sha']
        idList = get(sha, 'ids.json')
        idList = {} if idList == False else eval(idList)
        if app_id in idList:
            dlc_id = idList[app_id]['dlcid']
            return dlc_id


def app(app_path):
    app_path = Path(app_path)
    if not app_path.is_dir():
        raise NotADirectoryError(app_path)
    steam_path = get_steam_path()
    app_id_list = list(filter(str.isdecimal, app_path.name.strip().split('-')))
    if app_id_list:
        app_id = app_id_list[0]
        stool_add([(app_id, '1', None)])
    else:
        raise Exception('目录名称不是app_id')
    for file in app_path.iterdir():
        if file.is_file():
            if file.suffix == '.manifest':
                depot_cache_path = steam_path / 'depotcache'
                shutil.copy(file, depot_cache_path)
                print(f'导入清单成功: {file.name}')
            elif file.name == 'config.vdf':
                with file.open('r', encoding='utf-8') as f:
                    depots_config = vdf.loads(f.read())
                if depotkey_merge(steam_path / 'config' / 'config.vdf', depots_config):
                    print('合并config.vdf成功')
                if stool_add([(depot_id, '1',
                               depots_config['depots'][depot_id]['DecryptionKey']) for depot_id in
                              depots_config['depots']]):
                    print('导入steamtools成功')


def generate_applist(app_id, dlc_id, manifest_id):
    try:
        steam_path = get_steam_path()
        applist_path = steam_path / 'AppList'
        id_list = [app_id]
        if dlc_id is not None:
            id_list.extend(dlc_id)
        if manifest_id is not None:
            id_list.extend(manifest_id)
        if not applist_path.exists():
            applist_path.mkdir(exist_ok=True)
        depot_dict = {}
        for i in applist_path.iterdir():
            if i.suffix == '.txt':
                with i.open('r', encoding='utf-8') as f:
                    app_id = f.read().strip()
                    if args.delete:
                        if app_id in id_list:
                            f.close()
                            os.remove(applist_path / i.name)
                            print(f'删除{i.name}')
                    depot_dict[int(i.stem)] = None
                    if app_id.isdecimal():
                        depot_dict[int(i.stem)] = int(app_id)
        if args.delete:
            return True
        for id in id_list:
            if int(id) not in depot_dict.values():
                index = max(depot_dict.keys()) + 1 if depot_dict.keys() else 0
                if index != 0:
                    for i in range(max(depot_dict.keys())):
                        if i not in depot_dict.keys():
                            index = i
                with (applist_path / f'{index}.txt').open('w', encoding='utf-8') as f:
                    f.write(str(id))
                depot_dict[index] = int(id)
            else:
                print(f'{id} 已存在')
        appcache_path = steam_path / 'appcache'
        if appcache_path.exists():
            shutil.rmtree(appcache_path)
    except:
        traceback.print_exc()
        raise
    print('导入greenluma成功')
    return True


parser = argparse.ArgumentParser()
parser.add_argument('-r', '--repo', default='isKoi/Manifest-AutoUpdate')
parser.add_argument('-a', '--app-id', nargs='+')
parser.add_argument('-p', '--app-path')
parser.add_argument('-g', '--greenluma', default=False, action='store_true')
parser.add_argument('-d', '--delete', default=False, action='store_true')
args = parser.parse_args()
repo = args.repo
if __name__ == '__main__':
    try:
        if args.app_path:
            app(args.app_path)
        else:
            if not args.app_id:
                args.app_id = input('appid:')
            # 以空格为分隔符将输入的字符串拆分成多个ID
            app_ids = args.app_id.split()
            for app_id in app_ids:
                main(app_id)
    except KeyboardInterrupt:
        exit()
    except:
        traceback.print_exc()
    if not args.app_id and not args.app_path:
        os.system('pause')
