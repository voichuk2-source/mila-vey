#!/usr/bin/env python3
"""Bounded private 3B trial for the managed Mila installation, with rollback."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

TARGET = Path('/opt/mila-vey')
DROPIN = Path('/etc/systemd/system/mila-vey-ai.service.d/30-model-3b.conf')
MODEL = 'qwen2.5:3b'
MIB = 1024**2
AI_LIMIT = 2300*MIB
RESERVE = 768*MIB


def run(*args, timeout=60, capture=False):
    return subprocess.run(args, check=True, timeout=timeout, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def available_memory():
    values = dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    available = int(values['MemAvailable'].split()[0])*1024
    # Only anonymous memory is added: file-backed cache is already in MemAvailable.
    group = run('systemctl','show','-p','ControlGroup','--value','mila-vey-ai.service',capture=True).strip()
    if not group.startswith('/') or '..' in Path(group).parts:
        raise RuntimeError('Cannot identify the isolated AI memory group')
    stat = Path('/sys/fs/cgroup') / group.lstrip('/') / 'memory.stat'
    fields = dict(line.split() for line in stat.read_text().splitlines())
    return available + int(fields.get('anon','0'))


def atomic_write(path, data, mode=0o600):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.mila-',dir=path.parent)
    try:
        os.fchmod(fd,mode)
        with os.fdopen(fd,'wb') as stream:
            stream.write(data)
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)


def request(body, timeout=240):
    req = urllib.request.Request('http://127.0.0.1:11435/api/chat',
        data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=timeout) as response:
        return json.load(response)


def wait_ai():
    for _ in range(30):
        try:
            with urllib.request.urlopen('http://127.0.0.1:11435/api/version',timeout=2):
                return
        except Exception:
            time.sleep(1)
    raise RuntimeError('AI did not start')


def main():
    if os.geteuid()!=0:
        os.execvp('sudo',['sudo','python3',str(Path(__file__).resolve())])
    lock = open('/run/lock/mila-model-upgrade.lock','a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if not (TARGET/'.mila-managed').is_file() or not (TARGET/'.env').is_file():
        raise RuntimeError('Managed Mila installation not found')
    for unit in ('mila-vey.service','mila-vey-ai.service'):
        run('systemctl','is-active','--quiet',unit)
    sys.dont_write_bytecode=True
    sys.path.insert(0,str(TARGET))
    from bot import Store, SYSTEM, CHARACTER
    settings=Store(TARGET/'data/mila.sqlite3')
    try:
        if settings.get('public','off')!='off' or settings.get('sales','off')!='off':
            raise RuntimeError('Model trial requires public=off and sales=off')
        character=settings.get('character',CHARACTER)
    finally:
        settings.db.close()
    capacity=available_memory()
    print(f'Доступно с учётом выгрузки текущей AI: {capacity//MIB} МБ.',flush=True)
    if capacity < AI_LIMIT+RESERVE:
        print(f'Для теста нужно {int((AI_LIMIT+RESERVE)//MIB)} МБ, включая запас для системы. '
              'Модель не изменена: недостаточно свободной памяти.')
        return 2
    if shutil.disk_usage(TARGET).free < 4*1024**3:
        raise RuntimeError('Need at least 4 GiB of free disk space')
    env_path=TARGET/'.env'
    old_env=env_path.read_bytes()
    previous_dropin=DROPIN.read_bytes() if DROPIN.exists() else None
    changed=False
    success=False
    try:
        changed=True
        run('systemctl','stop','mila-vey.service','mila-vey-ai.service')
        high = AI_LIMIT//MIB-200
        limit = AI_LIMIT//MIB
        atomic_write(DROPIN,f'[Service]\nMemoryHigh={high}M\nMemoryMax={limit}M\nMemorySwapMax=0\n'.encode(),0o644)
        run('systemctl','daemon-reload')
        run('systemctl','start','mila-vey-ai.service')
        wait_ai()
        print(f'Скачиваю {MODEL} для закрытого теста. Старую модель сохраняю.',flush=True)
        run('runuser','-u','mila-vey','--','env','OLLAMA_HOST=127.0.0.1:11435',
            str(TARGET/'runtime/bin/ollama'),'pull',MODEL,timeout=1800)
        print('Проверяю реальный диалог с характером Милы…',flush=True)
        messages=[{'role':'system','content':SYSTEM+'\nCharacter and style:\n'+character}]
        for question in ('Hey Mila, how are you?', 'My name is Alex. I like evening walks.',
                         'What is my name? Answer in one short sentence.'):
            messages.append({'role':'user','content':question})
            result=request({'model':MODEL,'messages':messages,'stream':False,'keep_alive':'2m',
                'options':{'num_ctx':2048,'num_thread':1,'num_predict':96,'temperature':0.6}})
            answer=result.get('message',{}).get('content','').strip()
            if not answer: raise RuntimeError('Empty trial response')
            print('Тест:',question,'\nMila:',answer[:600],flush=True)
            messages.append({'role':'assistant','content':answer})
        if 'alex' not in answer.lower():
            raise RuntimeError('Trial conversation memory check failed')
        lines=old_env.decode().splitlines()
        lines=[line for line in lines if not line.startswith('OLLAMA_MODEL=')]
        lines.append('OLLAMA_MODEL='+MODEL)
        atomic_write(env_path,('\n'.join(lines)+'\n').encode())
        run('systemctl','start','mila-vey.service')
        time.sleep(3)
        for unit in ('mila-vey.service','mila-vey-ai.service'):
            run('systemctl','is-active','--quiet',unit)
        success=True
        print(f'ГОТОВО: включена {MODEL}. Закрытый тест; память и настройки сохранены. '
              'Качество ответов в Telegram ещё нужно оценить.')
        return 0
    finally:
        if changed and not success:
            print('Тест не прошёл. Возвращаю прежнюю модель и лимиты Mila.',flush=True)
            run('systemctl','stop','mila-vey.service','mila-vey-ai.service')
            atomic_write(env_path,old_env)
            if previous_dropin is None:
                DROPIN.unlink(missing_ok=True)
            else:
                atomic_write(DROPIN,previous_dropin,0o644)
            run('systemctl','daemon-reload')
            run('systemctl','start','mila-vey-ai.service','mila-vey.service')


def cli():
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('Операция прервана.')
        sys.exit(1)
    except Exception:
        # Never print credentials or environment-file contents from exceptions.
        print('Переход не завершён. Пришли последние строки терминала без токенов.')
        sys.exit(1)


if __name__=='__main__':
    cli()
